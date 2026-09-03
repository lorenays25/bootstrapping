"""
Factores tomados del propio reporte de Calypso.

Sirve para aislar el pricer: si valorizando con los factores de Calypso el PV no
cuadra, el problema es la fórmula y no los insumos. Es el punto de partida de
cualquier diagnóstico, y el que fija la vara de las otras dos comparaciones.

EL FORWARD NO ESTÁ EN EL REPORTE COMO COLUMNA, pero se recupera: `FWD_DELTA_PCT`
es N(d₁) en porcentaje, así que

    F = K · exp( N⁻¹(Δ_fwd) · σ√τ − σ²τ/2 )

Con varias operaciones del mismo vencimiento el forward recuperado coincide
entre ellas con dispersión del orden de 1e-6, lo que confirma de paso la fórmula
y el plazo. Sin esta recuperación habría que armar el forward con las dos tasas
de depósito del reporte, y eso se aparta hasta 2.5 pb del que Calypso usó de
verdad: la base cross-currency.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from typing import Dict, Optional, Tuple

from ..core.conventions import depo_df, tau_vol
from ..core.market import Factors
from ..core.numerics import norm_ppf
from .base import FactorFeed


class CalypsoFeed(FactorFeed):
    nombre = "calypso"
    descripcion = ("Volatilidad, forward y tasas del propio reporte. "
                   "Aísla el pricer de los factores.")

    def __init__(self, filas, valuation_date: _dt.date, spot_date: _dt.date,
                 spots: Dict[str, float]):
        self.val, self.spot_date, self.spots = valuation_date, spot_date, spots
        self.fwd = self._forwards(filas, valuation_date)
        self._sin_forward = defaultdict(int)
        self._con_forward = 0

    @staticmethod
    def _forwards(filas, val: _dt.date
                  ) -> Dict[Tuple[str, _dt.date, _dt.date], float]:
        acc = defaultdict(list)
        for r in filas:
            fd, v, o = r.fwd_delta_pct, r.vol_calypso, r.opt
            if fd is None or v is None or not (0.5 < abs(fd) < 99.5):
                continue
            tau = tau_vol(val, o.expiry)
            if tau <= 0:
                continue
            st = v * math.sqrt(tau)
            x = abs(fd) / 100.0
            d1 = norm_ppf(x) if o.call else norm_ppf(1 - x)
            acc[(o.pair, o.expiry, o.delivery)].append(
                o.strike * math.exp(d1 * st - 0.5 * st * st))
        out = {}
        for k, v in acc.items():
            v = sorted(v)
            out[k] = v[len(v)//2] if len(v) % 2 else 0.5*(v[len(v)//2-1]+v[len(v)//2])
        return out

    def factores(self, fila) -> Optional[Factors]:
        o = fila.opt
        S = self.spots.get(o.pair)
        if S is None or fila.rate_base is None or fila.rate_quote is None:
            return None
        d = (o.delivery - self.spot_date).days
        dfb = depo_df(fila.rate_base, d)
        dfq = depo_df(fila.rate_quote, d)
        F = self.fwd.get((o.pair, o.expiry, o.delivery))
        origen = "reporte"
        if F is None:
            F = S * dfb / dfq
            origen = "paridad de depósitos"
            # Solo cuenta como carencia cuando el forward SE PODÍA recuperar:
            # hace falta la volatilidad implícita y el delta forward. Un export
            # de forwards no trae ninguna de las dos, así que ahí la paridad no
            # es un plan B sino la única vía, y avisarlo sería ruido.
            if fila.vol_calypso is not None and fila.fwd_delta_pct is not None:
                self._sin_forward[o.pair] += 1
        else:
            self._con_forward += 1
        return Factors(spot=S, df_base=dfb, df_quote=dfq, forward=F,
                       tau=tau_vol(self.val, o.expiry), days=d,
                       rate_base=fila.rate_base, rate_quote=fila.rate_quote,
                       vol=fila.vol_calypso,
                       fuente={"vol": "calypso", "curvas": "calypso",
                               "forward": origen})

    def avisos(self) -> list:
        if not self._sin_forward:
            return []
        n = sum(self._sin_forward.values())
        det = ", ".join(f"{p} ({c})" for p, c in sorted(self._sin_forward.items()))
        return [f"En {n} de {n + self._con_forward} operaciones no se pudo recuperar "
                f"el forward de Calypso y se usó la paridad de depósitos, que se "
                f"aparta hasta 2.5 pb. Pasa cuando el vencimiento no tiene ninguna "
                f"operación con delta entre 0.5 % y 99.5 %, es decir cuando todas "
                f"están muy fuera del dinero. Por par: {det}."]
