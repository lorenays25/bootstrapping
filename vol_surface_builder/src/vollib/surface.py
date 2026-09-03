"""
surface.py — La superficie completa: interpolación entre tenores.

QUÉ SE INTERPOLA EN EL EJE DE PLAZOS
------------------------------------
No se interpola la superficie punto a punto: se interpolan las CINCO
COTIZACIONES (ATM, RR25, BF25, RR10, BF10) y con ellas se RECONSTRUYE el smile en
la fecha pedida. La evidencia es directa: en el export DAILY de Calypso, las
columnas cotizadas (10/25 delta) se reproducen interpolando por etiqueta con error
0.0000, mientras que las columnas derivadas (5, 15, 20, 30, … delta) quedan con
≤0.0029 — si Calypso interpolase la malla completa, las derivadas también darían
cero.

Cada cotización se interpola en VARIANZA TOTAL lineal en TIEMPO CALENDARIO
(`Interpolate Outright Variance = true`, `Interpolate on Trading Time = false`):

    w(n) = σ(n)² · n / 365     lineal en n (días calendario)

y **a etiqueta fija**: lo que se mantiene constante al moverse en el tiempo es
"la vol del put de 25 delta", no una posición del eje de delta. Los dos criterios
coinciden en los puntos call pero no en los put ni en el ATM, cuya posición en el
eje se mueve con el tenor.

EXTRAPOLACIÓN ANTES DEL PRIMER PILAR
------------------------------------
No es vol plana. Calypso mide la varianza sobre (n + 1/24) días y la reporta
dividiendo por el conteo entero ACT/365:

    σ(n) = σ(n₁) · sqrt[ n₁·(n + δ) / ( n·(n₁ + δ) ) ]      δ = 1/24 día

Verificado en USD/MXN y USD/PEN con error ≤ 4.5e-6 vol pts en los puntos
cotizados. El mismo 0.042 aparece en la columna `Trade Days` del panel de
parámetros de los dos pares.

CORTE spot delta → forward delta
--------------------------------
`Spot Delta Last Tenor` se resuelve por FECHA DE VENCIMIENTO. Al interpolar entre
un pilar cotizado en spot delta y otro en forward delta, el pilar que está en la
convención equivocada se REEXPRESA primero (`SmileSlice.quotes_in_convention`),
porque "el put de 25 delta" no es el mismo strike en las dos convenciones. Sin
esa reexpresión las alas quedan desplazadas hasta 0.095 vol pts para plazos
mayores al corte.

CONSULTA PRINCIPAL
------------------
`vol(expiry, strike)` — la volatilidad implícita de una operación con ese
vencimiento y ese strike. Es lo que consume el Módulo 3.
"""
from __future__ import annotations

import datetime as _dt
import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import dates as dt
from . import deltas as dl
from .dates import Calendar
from .deltas import DeltaConvention
from .smile import SmileSlice, build_slice, AXIS_CONVENTION

#: Desfase intradía del reloj de Calypso, en días. Ver encabezado.
CLOCK_OFFSET_DAYS = 1.0 / 24.0

_QK = ("atm", "rr25", "bf25", "rr10", "bf10")


@dataclass
class ForwardModel:
    """Forward FX y factor de descuento de la divisa base, a cualquier fecha.

        F(T) = S · Df_base(T) / Df_quote(T)
    """

    pair: str
    spot: float
    base_curve: object
    quote_curve: object

    def df_base(self, d: _dt.date) -> float:
        return self.base_curve.df(d)

    def forward(self, d: _dt.date) -> float:
        return self.spot * self.base_curve.df(d) / self.quote_curve.df(d)


@dataclass
class VolSurface:
    """Superficie de volatilidad de un par, para un lado (bid / mid / ask)."""

    pair: str
    side: str
    valuation_date: _dt.date
    vol_day_count: str
    conv_by_tenor: Dict[str, DeltaConvention]
    fwd: ForwardModel
    delivery_lag: int
    slices: List[SmileSlice] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    calendar: Optional[Calendar] = None
    wing_slope_factor: float = 1.0
    wing_ext_lambda: Optional[float] = 1.0
    wing_flat_delta: float = 0.5
    zero_delta_straddle: bool = True

    def __post_init__(self):
        self.slices.sort(key=lambda s: s.expiry)
        self._days = [(s.expiry - self.valuation_date).days for s in self.slices]

    # ------------------------------------------------------------------ info
    def tenors(self) -> List[str]:
        return [s.tenor for s in self.slices]

    def slice_by_tenor(self, tenor: str) -> SmileSlice:
        for s in self.slices:
            if s.tenor == tenor:
                return s
        raise KeyError(f"[{self.pair}] tenor '{tenor}' no está en la superficie")

    def conv_for(self, expiry: _dt.date) -> DeltaConvention:
        """Convención de delta aplicable a un expiry: el corte de
        `Spot Delta Last Tenor` se resuelve por FECHA."""
        base = next(iter(self.conv_by_tenor.values()))
        cut = getattr(self, "_spot_delta_cut_date", None)
        if cut is None:
            return base
        return DeltaConvention(premium_adjusted=base.premium_adjusted,
                               spot_delta=(expiry <= cut))

    def delivery_for(self, expiry: _dt.date) -> _dt.date:
        return dt.advance_business_days(expiry, self.delivery_lag, self.calendar)

    # --------------------------------------------------- interpolación en plazo
    def _quotes_at(self, expiry: _dt.date) -> Dict[str, float]:
        """Las 5 cotizaciones interpoladas a `expiry`, en la convención que
        corresponde a esa fecha."""
        n = (expiry - self.valuation_date).days
        if n <= 0:
            raise ValueError(f"[{self.pair}] expiry {expiry} no es posterior a la valuación")
        days, sl = self._days, self.slices
        target_conv = self.conv_for(expiry)

        def q_of(i: int) -> Dict[str, float]:
            return sl[i].quotes_in_convention(target_conv)

        if len(sl) == 1 or n <= days[0]:
            q = q_of(0)
            n1 = days[0]
            if n >= n1:
                return q
            # extrapolación corta con el desfase de reloj
            f = math.sqrt(n1 * (n + CLOCK_OFFSET_DAYS) / (n * (n1 + CLOCK_OFFSET_DAYS)))
            return {k: v * f for k, v in q.items()}
        if n >= days[-1]:
            return q_of(len(sl) - 1)             # vol plana más allá del último pilar

        i = bisect_left(days, n)
        if days[i] == n:
            return q_of(i)
        n0, n1 = days[i - 1], days[i]
        q0, q1 = q_of(i - 1), q_of(i)
        u = (n - n0) / (n1 - n0)

        # Se interpola sobre las 5 VOLS (10C, 25C, ATM, 25P, 10P), no sobre RR/BF:
        # RR y BF no son volatilidades y su varianza total no es la que se
        # interpola. Se reconstruyen al final por la misma álgebra 2vol (CP Avg).
        def vols(q):
            a = q["atm"]
            return {"10C": a + q["bf10"] + q["rr10"] / 2, "25C": a + q["bf25"] + q["rr25"] / 2,
                    "ATM": a, "25P": a + q["bf25"] - q["rr25"] / 2,
                    "10P": a + q["bf10"] - q["rr10"] / 2}
        v0, v1 = vols(q0), vols(q1)
        v = {}
        for lab in v0:
            w0 = v0[lab] ** 2 * n0
            w1 = v1[lab] ** 2 * n1
            v[lab] = math.sqrt((w0 + u * (w1 - w0)) / n)
        return {"atm": v["ATM"], "rr25": v["25C"] - v["25P"],
                "bf25": (v["25C"] + v["25P"]) / 2 - v["ATM"],
                "rr10": v["10C"] - v["10P"],
                "bf10": (v["10C"] + v["10P"]) / 2 - v["ATM"]}

    def slice_at(self, expiry: _dt.date) -> SmileSlice:
        """El smile reconstruido en una fecha cualquiera."""
        for s in self.slices:
            if s.expiry == expiry:
                return s
        q = self._quotes_at(expiry)
        tau = dt.year_fraction(self.vol_day_count, self.valuation_date, expiry)
        delivery = self.delivery_for(expiry)
        return build_slice("interp", expiry, delivery, tau,
                           self.fwd.forward(delivery), self.fwd.df_base(delivery),
                           self.conv_for(expiry),
                           q["atm"], q["rr25"], q["bf25"], q["rr10"], q["bf10"],
                           zero_delta_straddle=self.zero_delta_straddle,
                           wing_slope_factor=self.wing_slope_factor,
                           wing_ext_lambda=self.wing_ext_lambda,
                           wing_flat_delta=self.wing_flat_delta)

    # ------------------------------------------------------- consulta principal
    def vol(self, expiry: _dt.date, strike: float) -> float:
        """Volatilidad implícita (DECIMAL) de una operación con ese vencimiento y
        ese strike. Es la consulta que usa el Módulo 3."""
        return self.slice_at(expiry).vol_at_strike(strike)

    def vol_pct(self, expiry: _dt.date, strike: float) -> float:
        return 100.0 * self.vol(expiry, strike)

    def vol_at_call_delta(self, expiry: _dt.date, x_call_delta: float) -> float:
        return self.slice_at(expiry).vol_at_call_delta(x_call_delta)

    def vol_at_delta(self, expiry: _dt.date, delta: float, side: str) -> tuple:
        """(strike, vol) del punto de `delta` (0.25 = 25) del lado 'C' o 'P'."""
        return self.slice_at(expiry).strike_at_delta(delta, side)

    def forward(self, expiry: _dt.date) -> float:
        return self.fwd.forward(self.delivery_for(expiry))

    # ------------------------------------------------------------------ salida
    def table(self) -> List[dict]:
        rows = []
        for s in self.slices:
            for p in s.points:
                rows.append({"tenor": s.tenor, "expiry": s.expiry, "delivery": s.delivery,
                             "tau": s.tau, "forward": s.forward, "point": p.label,
                             "call_delta": p.call_delta, "vol_pct": p.vol * 100.0,
                             "strike": p.strike})
        return rows

    def __repr__(self) -> str:
        return f"VolSurface({self.pair}[{self.side}], {len(self.slices)} tenores)"


class VolSurfaceSet:
    """Agrupa las tres superficies (bid/mid/ask) de cada par.

    El pipeline corre entero tres veces (enfoque A). Confirmado contra Calypso:
    los spreads se aplican a nivel de COTIZACIÓN y se propagan por la misma
    álgebra usando el mismo lado del risk reversal en ambas alas, de modo que
    spread(P25) = sATM + sBF25 − sRR25/2 queda más angosto que spread(C25).
    No es una envolvente.
    """

    def __init__(self, bid: dict, mid: dict, ask: dict, valuation_date: _dt.date):
        self.sides = {"bid": bid, "mid": mid, "ask": ask}
        self.valuation_date = valuation_date

    def pairs(self) -> List[str]:
        return list(self.sides["mid"].keys())

    def vol(self, pair: str, expiry: _dt.date, strike: float) -> Dict[str, float]:
        return {side: self.sides[side][pair].vol(expiry, strike) for side in
                ("bid", "mid", "ask")}

    def table(self, pair: str) -> List[dict]:
        mid = self.sides["mid"][pair]
        out = []
        for s in mid.slices:
            for p in s.points:
                row = {"tenor": s.tenor, "expiry": s.expiry, "point": p.label}
                for side in ("bid", "mid", "ask"):
                    sl = self.sides[side][pair].slice_by_tenor(s.tenor)
                    q = next(q for q in sl.points if q.label == p.label)
                    row[f"vol_{side}"] = q.vol * 100.0
                    row[f"strike_{side}"] = q.strike
                    row[f"delta_{side}"] = q.call_delta
                out.append(row)
        return out

    def to_csv(self, pair: str, path: str) -> str:
        import csv
        rows = self.table(pair)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Tenor", "Expiry", "Point", "Delta Bid", "Delta Mid", "Delta Ask",
                        "Strike Bid", "Strike Mid", "Strike Ask",
                        "Vol Bid", "Vol Mid", "Vol Ask"])
            for r in rows:
                w.writerow([r["tenor"], r["expiry"].isoformat(), r["point"],
                            r["delta_bid"], r["delta_mid"], r["delta_ask"],
                            r["strike_bid"], r["strike_mid"], r["strike_ask"],
                            r["vol_bid"], r["vol_mid"], r["vol_ask"]])
        return path
