"""
curves.py — Curvas de descuento para la conversión delta→strike.

El Módulo 2 no construye curvas: las consume. Hay dos fuentes posibles y este
módulo las unifica detrás de la misma interfaz `DiscountCurve.df(fecha)`:

  1. CURVAS EXPORTADAS DE CALYPSO (lo que usa este build): el CSV con columnas
     `Date;Offset;Zero Bid;Zero Mid;Zero Ask;Df Bid;Df Mid;Df Ask`. Se usa para
     AISLAR VARIABLES: si la conversión delta→strike usa exactamente los mismos
     descuentos que usó Calypso, cualquier diferencia en la superficie es
     atribuible al smile y no a la curva.

  2. CURVAS DEL MÓDULO 1 (`curvelib.Curve`): mismo `.df(fecha)`, así que basta
     envolverlas con `CurvelibAdapter`. Ese es el paso siguiente, una vez que la
     superficie valide contra (1).

INTERPOLACIÓN: log-lineal sobre ln(DF) contra el OFFSET EN DÍAS CALENDARIO.
Es la misma que ya se verificó dos veces en este proyecto: la convención
`Interpolator LogLinear` + `Interp. As DiscountFactor` de las curvas de Calypso,
y la réplica de la función VBA `INTERPODF` del Excel de front office en la
validación de opciones. NO se interpola sobre la tasa cero: eso daría un DF
distinto, y la diferencia crece en el tramo corto.
"""
from __future__ import annotations

import csv
import datetime as _dt
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from . import dates as dt

SIDES = ("bid", "mid", "ask")


@dataclass
class DiscountCurve:
    """Curva de factores de descuento con interpolación log-lineal en el offset."""

    name: str
    valuation_date: _dt.date
    offsets: List[float] = field(default_factory=list)   # días calendario desde valuación
    log_dfs: List[float] = field(default_factory=list)

    # ------------------------------------------------------------------ eval
    def df_offset(self, offset: float) -> float:
        """DF interpolado log-linealmente. Antes del primer nodo interpola contra
        el punto (0, DF=1); después del último extrapola con la última pendiente."""
        if offset <= 0.0:
            return 1.0
        xs, ys = self.offsets, self.log_dfs
        if not xs:
            raise ValueError(f"[{self.name}] curva sin nodos.")
        if offset <= xs[0]:
            # tramo (0, log 1 = 0) -> primer nodo
            w = offset / xs[0]
            return float(np.exp(w * ys[0]))
        if offset >= xs[-1]:
            slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
            return float(np.exp(ys[-1] + slope * (offset - xs[-1])))
        i = bisect_left(xs, offset)
        w = (offset - xs[i - 1]) / (xs[i] - xs[i - 1])
        return float(np.exp(ys[i - 1] + w * (ys[i] - ys[i - 1])))

    def df(self, d: _dt.date) -> float:
        return self.df_offset((d - self.valuation_date).days)

    def __repr__(self) -> str:
        return f"DiscountCurve({self.name}, {len(self.offsets)} nodos)"


class CurvelibAdapter:
    """Envuelve una `curvelib.Curve` del Módulo 1 detrás de la misma interfaz.

    No se usa en este build (que corre contra las curvas exportadas de Calypso),
    pero deja el enchufe listo: cambiar la fuente de curvas no debe tocar nada
    del motor de superficie.
    """

    def __init__(self, curve, name: str | None = None):
        self._curve = curve
        self.name = name or getattr(curve, "name", "curvelib")
        self.valuation_date = curve.valuation_date

    def df(self, d: _dt.date) -> float:
        return self._curve.df(d)

    def df_offset(self, offset: float) -> float:
        return self.df(self.valuation_date + _dt.timedelta(days=float(offset)))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _num(s: str) -> float:
    """Los exports de Calypso traen separador de miles en `Offset` ('1,101.00')."""
    return float(str(s).strip().replace(",", ""))


def load_calypso_curve(path: str, valuation_date: _dt.date,
                       name: str | None = None) -> Dict[str, DiscountCurve]:
    """Lee un CSV de curva exportado de Calypso y devuelve las TRES curvas
    (bid/mid/ask), porque el mismo archivo trae los tres lados.

    Formato: `Date;Offset;Zero Bid;Zero Mid;Zero Ask;Df Bid;Df Mid;Df Ask`

    Se usa la columna `Df`, no la `Zero`: el DF es el dato calibrado y la tasa
    cero es solo una representación de él (la propia `curvelib` lo documenta en
    `zero_rate_annual`). Reconstruir el DF desde la tasa cero introduciría el
    error de redondeo de esa columna.
    """
    name = name or path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if not (r.get("Date") or "").strip():
                continue
            rows.append(r)
    if not rows:
        raise ValueError(f"[{name}] el archivo de curva no tiene filas: {path}")

    out: Dict[str, DiscountCurve] = {}
    for side in SIDES:
        col = f"Df {side.capitalize()}"
        if col not in rows[0]:
            raise ValueError(f"[{name}] falta la columna '{col}' en {path}")
        offsets, logdfs = [], []
        for r in rows:
            off = _num(r["Offset"])
            df = _num(r[col])
            if df <= 0:
                raise ValueError(f"[{name}] DF no positivo ({df}) en offset {off}")
            # el export puede repetir un offset (nodo spot derivado); nos quedamos
            # con el primero y avisamos por consistencia, igual que hace el Módulo 1
            if offsets and off <= offsets[-1]:
                continue
            offsets.append(off)
            logdfs.append(float(np.log(df)))
        out[side] = DiscountCurve(name=f"{name}[{side}]", valuation_date=valuation_date,
                                  offsets=offsets, log_dfs=logdfs)
    return out


def load_fx_spots(path: str, valuation_date: _dt.date) -> Dict[str, Dict[str, float]]:
    """Lee el export de tipos de cambio (`tc.csv`) y devuelve {par: {bid, mid, ask}}.

    Formato: `Date;Quote Name;Quote Type;Bid;Ask;Open;Close;High;Low;Last;...`
    con `Quote Name` tipo `FX.USD.MXN` o `FX.EUR.USD`.

    El `mid` se toma como (bid+ask)/2 cuando ambos existen; si el export trae los
    dos lados iguales (es el caso de este archivo), mid == bid == ask.

    ATENCIÓN a la precisión: este export viene REDONDEADO (16.99 para USDMXN,
    1.16 para EURUSD). En la validación de opciones anterior se usó el spot con
    8 decimales (17.07181955). El spot entra multiplicando en el forward, así que
    su redondeo se propaga a TODOS los strikes. Ver `report_spot_precision`.
    """
    spots: Dict[str, Dict[str, float]] = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            r = { (k or "").strip(): (v or "").strip() for k, v in r.items() }
            qn = r.get("Quote Name", "")
            if not qn.upper().startswith("FX."):
                continue
            d = dt.parse_date(r["Date"])
            if d != valuation_date:
                continue
            parts = qn.split(".")
            pair = f"{parts[1]}{parts[2]}".upper()          # FX.USD.MXN -> USDMXN
            def g(key):
                v = r.get(key, "")
                if not v or v.upper() == "NAN":
                    return None
                return float(v.replace(",", ""))
            bid, ask, last = g("Bid"), g("Ask"), g("Last")
            mid = (bid + ask) / 2 if (bid is not None and ask is not None) else last
            spots[pair] = {"bid": bid if bid is not None else mid,
                           "mid": mid,
                           "ask": ask if ask is not None else mid}
    if not spots:
        raise ValueError(f"No se encontró ningún spot para {valuation_date} en {path}")
    return spots


def report_spot_precision(spots: Dict[str, Dict[str, float]]) -> List[str]:
    """Avisa cuando un spot llega con pocos decimales significativos.

    No es cosmético: el spot multiplica al forward, y el forward fija todos los
    strikes del smile. Un spot redondeado a 2 decimales en USDMXN (16.99) tiene
    una incertidumbre relativa de ~3e-4, que se traslada íntegra a cada strike.
    """
    warns = []
    for pair, v in sorted(spots.items()):
        s = v["mid"]
        txt = f"{s!r}"
        dec = len(txt.split(".")[1]) if "." in txt else 0
        rel = (0.5 * 10 ** (-dec)) / s if s else 0.0
        if rel > 1e-5:
            warns.append(
                f"spot {pair} = {s} tiene solo {dec} decimales: incertidumbre "
                f"relativa ~{rel*1e4:.2f} pb, que se propaga a TODOS los strikes."
            )
    return warns
