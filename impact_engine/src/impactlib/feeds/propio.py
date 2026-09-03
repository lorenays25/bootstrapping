"""
Factores producidos por el propio motor: curvas del Módulo 1 y superficie del
Módulo 2.

CURVAS — cómo se arma el forward
--------------------------------
Cada moneda se descuenta con su curva, y el forward sale del cociente:

    F(T) = Spot · DF_base(T) / DF_cotizada(T)

La curva de la moneda local es la CROSS-CURRENCY (PEN_X_SOFR, MXN_X_SOFR, …), no
la local pura (PEN_OIS_TIBO, MXN_TIIE_28D, …). La diferencia no es cosmética: la
curva cross se calibra contra los forwards FX del mercado, así que lleva
incorporada la base cross-currency; la local pura no. Medido contra el reporte
de Calypso, el forward armado con tasas de depósito sin base se aparta hasta
2.5 pb, y la brecha crece con el plazo — que es exactamente la forma de una base.

Las curvas devuelven factores desde la FECHA DE VALORIZACIÓN, mientras que las
convenciones de Calypso descuentan desde la FECHA SPOT. La conversión es el
cociente: DF(spot→T) = DF(val→T) / DF(val→spot).

VOLATILIDAD
-----------
`vs.vol(par, vencimiento, strike)` del Módulo 2, lado mid. La consulta se
memoriza porque resolver el punto fijo vol↔delta para 973 operaciones cuesta.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, Optional

from ..core.conventions import depo_rate, tau_vol
from ..core.market import Factors
from .base import FactorFeed

#: Curva de descuento por moneda. La base siempre es USD porque todos los pares
#: del portafolio son USD/XXX. Configurable desde el YAML del Módulo 3.
CURVA_POR_MONEDA = {
    "USD": "USD_SOFR",
    "PEN": "PEN_X_SOFR",
    "MXN": "MXN_X_SOFR",
    "BRL": "BRL_X_SOFR",
    "CLP": "CLP_X_SOFR",
    "COP": "COP_X_SOFR",
    "EUR": "EUR_X_USD",
}


class CurvasPropiasFeed(FactorFeed):
    """Descuento y forward de las curvas del Módulo 1."""

    nombre = "curvas propias"
    descripcion = "Descuento y forward de las curvas bootstrapeadas por el motor."

    def __init__(self, curvas: Dict[str, object], valuation_date: _dt.date,
                 spot_date: _dt.date, spots: Dict[str, float],
                 curva_por_moneda: Optional[Dict[str, str]] = None):
        self.curvas = curvas
        self.val, self.spot_date, self.spots = valuation_date, spot_date, spots
        self.mapa = dict(curva_por_moneda or CURVA_POR_MONEDA)
        self._faltan = set()
        self._df_spot = {}          # DF(val -> fecha spot) por curva, se calcula una vez

    def _curva(self, ccy: str):
        nombre = self.mapa.get(ccy)
        c = self.curvas.get(nombre) if nombre else None
        if c is None:
            self._faltan.add(f"{ccy} → {nombre or 'sin mapear'}")
        return c

    def _df(self, ccy: str, hasta: _dt.date) -> Optional[float]:
        """Descuento de la FECHA SPOT a `hasta`, que es la convención de Calypso."""
        c = self._curva(ccy)
        if c is None:
            return None
        if ccy not in self._df_spot:
            self._df_spot[ccy] = c.df(self.spot_date)
        base = self._df_spot[ccy] or 1.0
        return c.df(hasta) / base

    def factores(self, fila) -> Optional[Factors]:
        o = fila.opt
        S = self.spots.get(o.pair)
        if S is None or "/" not in o.pair:
            return None
        base_ccy, quote_ccy = o.pair.split("/")
        dfb, dfq = self._df(base_ccy, o.delivery), self._df(quote_ccy, o.delivery)
        if dfb is None or dfq is None:
            return None
        d = (o.delivery - self.spot_date).days
        return Factors(spot=S, df_base=dfb, df_quote=dfq, forward=S * dfb / dfq,
                       tau=tau_vol(self.val, o.expiry), days=d,
                       rate_base=depo_rate(dfb, d), rate_quote=depo_rate(dfq, d),
                       vol=None,
                       fuente={"curvas": "propias", "forward": "curvas propias"})

    def avisos(self) -> list:
        if not self._faltan:
            return []
        return [f"Faltan curvas para: {', '.join(sorted(self._faltan))}. "
                f"Esas operaciones quedaron fuera."]


class SuperficiePropiaFeed(FactorFeed):
    """Solo la volatilidad, de la superficie del Módulo 2."""

    nombre = "superficie propia"
    descripcion = "Volatilidad implícita de la superficie construida por el motor."

    def __init__(self, vs, lado: str = "mid"):
        self.vs, self.lado = vs, lado
        self._cache = {}
        self._faltan = set()

    def vol_de(self, pair: str, expiry: _dt.date, strike: float) -> Optional[float]:
        k = pair.replace("/", "")
        if k not in self.vs.sides[self.lado]:
            self._faltan.add(pair)
            return None
        ck = (k, expiry, round(strike, 8))
        if ck not in self._cache:
            try:
                self._cache[ck] = self.vs.vol(k, expiry, strike)[self.lado]
            except Exception:
                self._cache[ck] = None
        return self._cache[ck]

    def factores(self, fila) -> Optional[Factors]:
        o = fila.opt
        v = self.vol_de(o.pair, o.expiry, o.strike)
        if v is None:
            return None
        return Factors(spot=1.0, df_base=1.0, df_quote=1.0, forward=1.0,
                       tau=0.0, days=0, rate_base=0.0, rate_quote=0.0, vol=v,
                       fuente={"vol": "superficie propia"})

    def avisos(self) -> list:
        if not self._faltan:
            return []
        return [f"Sin superficie propia para: {', '.join(sorted(self._faltan))}. "
                f"En esos pares se usó la volatilidad de Calypso."]
