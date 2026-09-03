"""
smile.py — Un slice de la superficie: el smile de UN tenor.

FLUJO
-----
 1. De los 5 quotes del tenor (ATM, RR25, BF25, RR10, BF10) a 5 volatilidades,
    por ÁLGEBRA DIRECTA. Con `Strangle/Fly Quotes = 2vol (CP Avg)` (las 6
    superficies):

        σ_call(Δ) = ATM + BF(Δ) + RR(Δ)/2
        σ_put(Δ)  = ATM + BF(Δ) − RR(Δ)/2

 2. De cada (delta, σ) al STRIKE, resolviendo el delta en la convención COTIZADA
    del par (`Quotes are Delta with Premium = true` en las 6, y spot o forward
    según `Spot Delta Last Tenor`).

 3. De cada strike a su posición en el EJE del interpolador.

    El eje NO puede ser el delta ajustado por prima. Δ_pa,call = Df·(K/F)·N(d2)
    no es monótona en K: tiene un máximo en d2* que resuelve N(d2)·σ√τ = n(d2), y
    el put de 5 delta cae en la vecindad de ese máximo o pasado él (verificado:
    USD/BRL 1Y tiene d2(P5) = 1.5200 contra d2* = 1.4723). Un eje no inyectivo no
    sirve para parametrizar el smile. El eje es por tanto el CALL DELTA PLANO
    (Df·N(d1)), estrictamente decreciente en K. Ver `validacion-...` §6-bis.

 4. Spline cúbica NOT-A-KNOT sobre los 5 nodos cotizados — y solo sobre ellos.
    Meter nodos sintéticos de 1 y 99 delta dentro del ajuste deforma las derivadas
    en los nodos cotizados y empeora todo el interior (medido: 0.087 vs 0.023 vol
    pts de error máximo contra Calypso sobre 26 pilares).

 5. ALA (fuera del rango de los nodos de 10 delta). Calypso APLANA el smile más
    allá de un delta muy chico. Se comprueba en el portafolio: al 16/12/2026 los
    strikes 21.1, 22.7, 22.8 y 22.9 de USD/MXN devuelven todos exactamente
    12.66798. Ese valor tope es medible, y resulta ser

        σ_tope = σ_10Δ + λ·(σ_10Δ − σ_25Δ),   con λ = 1

    o sea: se repite una vez más el último tramo cotizado del smile. Medido sobre
    17 (par, vencimiento) del portafolio de valorización: USD/PEN λ = 1.0009
    (sd 0.0007, error ≤ 0.0009 vol pts, incluye un caso del lado put), USD/BRL
    0.9815, USD/MXN 0.977–0.991 de dos meses en adelante.

    Entre el nodo de 10 delta y el tope va una PARÁBOLA que empalma valor y
    pendiente del spline en el nodo (la pendiente escalada por
    `wing_slope_factor`) y llega a σ_tope en `wing_flat_delta`; de ahí hacia
    afuera es constante. El valor se acota además al intervalo [σ_nodo, σ_tope]
    para que el ala sea siempre monótona y no se pase del tope.

    Contra el portafolio (207 operaciones fuera del rango de nodos, con el spot
    del día a precisión completa): la recta tangente sin tope daba rms 0.137 y
    máximo 0.303 vol pts; esta regla da rms 0.041 y máximo 0.195. En la grilla
    de deltas de Calypso (dato independiente, 487 fechas) el punto de 5 delta no
    se degrada: C5 pasa de −0.037 a +0.043 de mediana y P5 mejora de +0.070 a
    +0.051.

    `wing_ext_lambda = None` desactiva el tope y deja la recta tangente pura
    (comportamiento anterior).

UNIDADES: las hojas de Calypso vienen en PUNTOS DE VOL (4.72 = 4.72%). Adentro
todo se maneja en DECIMAL (0.0472). La conversión ocurre solo en el loader.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from scipy.interpolate import CubicSpline
from scipy.optimize import brentq

from . import deltas as dl
from .deltas import DeltaConvention

#: Convención del EJE del interpolador. Plana (sin ajuste por prima) porque el
#: delta ajustado por prima no es monótono — ver encabezado, paso 3.
#: `spot_delta` es irrelevante: reescalar el eje por un factor constante deja la
#: spline invariante (verificado numéricamente), así que se fija en True.
AXIS_CONVENTION = DeltaConvention(premium_adjusted=False, spot_delta=True)

QUOTE_LABELS = ("10C", "25C", "ATM", "25P", "10P")


@dataclass
class SmilePoint:
    label: str            # "10C", "25C", "ATM", "25P", "10P"
    call_delta: float     # posición en el EJE (call delta plano), en %
    vol: float            # decimal
    strike: Optional[float] = None


@dataclass
class SmileSlice:
    """Smile calibrado de un tenor."""

    tenor: str
    expiry: _dt.date
    delivery: _dt.date
    tau: float                     # plazo a EXPIRACIÓN, años (Volatility Day Count)
    forward: float                 # forward a la ENTREGA
    df_for: float                  # DF de la divisa base a la entrega
    conv: DeltaConvention          # convención COTIZADA (para delta -> strike)
    atm_vol: float
    quotes: Dict[str, float] = field(default_factory=dict)   # atm/rr25/bf25/rr10/bf10, decimal
    points: List[SmilePoint] = field(default_factory=list)
    wing_slope_factor: float = 1.0
    #: Cuántos tramos [25Δ→10Δ] más se repiten para fijar el tope del ala.
    #: `None` desactiva el tope (recta tangente pura, comportamiento anterior).
    wing_ext_lambda: Optional[float] = 1.0
    #: Delta (en %) a partir del cual el ala es plana.
    wing_flat_delta: float = 0.5
    axis_conv: DeltaConvention = AXIS_CONVENTION
    _spline: CubicSpline = field(default=None, repr=False)
    _dspline: CubicSpline = field(default=None, repr=False)
    _wingC: tuple = field(default=None, repr=False)
    _wingP: tuple = field(default=None, repr=False)

    # ------------------------------------------------------------------ build
    def fit(self) -> "SmileSlice":
        xs = [p.call_delta for p in self.points]
        ys = [p.vol for p in self.points]
        if any(b <= a for a, b in zip(xs, xs[1:])):
            raise ValueError(
                f"[{self.tenor}] el eje no queda ordenado: {xs}. "
                f"Suele indicar una convención de delta equivocada."
            )
        self._spline = CubicSpline(xs, ys, bc_type="not-a-knot")
        self._dspline = self._spline.derivative()
        self._fit_wings()
        return self

    def _fit_wings(self) -> None:
        """Precalcula la parábola y el tope de cada ala. Ver encabezado, paso 5."""
        self._wingC = self._wingP = None
        lam = self.wing_ext_lambda
        if lam is None:
            return
        lo, hi = self.axis_range()
        for side in ("C", "P"):
            if side == "C":
                x_node, x_flat = lo, self.wing_flat_delta
                v_ext = (self.points[0].vol
                         + lam * (self.points[0].vol - self.points[1].vol))
                ok = x_flat < x_node
            else:
                x_node, x_flat = hi, 100.0 - self.wing_flat_delta
                v_ext = (self.points[-1].vol
                         + lam * (self.points[-1].vol - self.points[-2].vol))
                ok = x_flat > x_node
            if not ok:                       # geometría degenerada: queda la recta
                continue
            v_node = float(self._spline(x_node))
            slope = self.wing_slope_factor * float(self._dspline(x_node))
            d = x_flat - x_node
            curv = (v_ext - v_node - slope * d) / (d * d)
            packed = (x_node, x_flat, v_node, slope, curv,
                      min(v_node, v_ext), max(v_node, v_ext), v_ext)
            if side == "C":
                self._wingC = packed
            else:
                self._wingP = packed

    # ------------------------------------------------------------------ eval
    def axis_range(self) -> tuple:
        return self.points[0].call_delta, self.points[-1].call_delta

    def vol_at_call_delta(self, x: float) -> float:
        """Vol en una posición del eje. Dentro del rango de los nodos de 10 delta
        es el spline; fuera, el ala del paso 5 del encabezado."""
        lo, hi = self.axis_range()
        if lo <= x <= hi:
            return float(self._spline(x))
        k = self.wing_slope_factor
        w = self._wingC if x < lo else self._wingP
        if w is None:                          # sin tope: recta tangente pura
            a = lo if x < lo else hi
            return float(self._spline(a)) + k * float(self._dspline(a)) * (x - a)
        x_node, x_flat, v_node, slope, curv, v_min, v_max, v_ext = w
        if (x <= x_flat) if x < lo else (x >= x_flat):
            return v_ext
        d = x - x_node
        return min(max(v_node + slope * d + curv * d * d, v_min), v_max)

    def _x_of(self, K: float, sigma: float) -> float:
        return 100.0 * dl.call_delta(self.forward, K, sigma, self.tau,
                                     self.df_for, self.axis_conv)

    def _solve_sigma(self, residual, tol: float, max_iter: int) -> float:
        """Resuelve residual(σ) = 0.

        Primero itera el punto fijo, que converge en 3-5 vueltas para smiles
        normales. Si no converge —pasa cuando el smile es muy empinado en delta y
        la derivada del punto fijo supera 1, p.ej. el pilar O/N del lado bid, con
        vols de 0.57% a 5.76% a un día— cae a Brent sobre un intervalo que cubre
        todo el smile más sus alas.
        """
        sigma = self.atm_vol
        for _ in range(max_iter):
            nxt = sigma + residual(sigma)
            if not (nxt > 0):
                break
            if abs(nxt - sigma) < tol:
                return nxt
            sigma = nxt

        vols = [p.vol for p in self.points]
        lo, hi = 0.2 * min(vols), 3.0 * max(vols)
        for _ in range(30):
            try:
                if residual(lo) * residual(hi) <= 0:
                    return brentq(residual, lo, hi, xtol=1e-15, rtol=1e-14, maxiter=200)
            except (ValueError, ZeroDivisionError):
                pass
            lo *= 0.5
            hi *= 1.5
        return sigma

    def vol_at_strike(self, K: float, tol: float = 1e-13, max_iter: int = 60) -> float:
        """Volatilidad implícita para un strike. Punto fijo: el eje depende de la
        vol y la vol depende del eje. Es la consulta que usa el Módulo 3."""
        return self._solve_sigma(
            lambda s: self.vol_at_call_delta(self._x_of(K, s)) - s, tol, max_iter)

    def strike_at_delta(self, target: float, side: str,
                        conv: DeltaConvention | None = None,
                        tol: float = 1e-13, max_iter: int = 60) -> tuple:
        """(strike, vol) del punto de delta `target` (0.25 = 25 delta) en la
        convención COTIZADA (o en `conv` si se pide otra)."""
        conv = conv or self.conv

        def K_of(sigma):
            return (dl.strike_from_call_delta(target, self.forward, sigma, self.tau,
                                              self.df_for, conv) if side == "C"
                    else dl.strike_from_put_delta(target, self.forward, sigma, self.tau,
                                                  self.df_for, conv))

        sigma = self._solve_sigma(
            lambda s: self.vol_at_call_delta(self._x_of(K_of(s), s)) - s, tol, max_iter)
        return K_of(sigma), sigma

    def quotes_in_convention(self, conv: DeltaConvention) -> Dict[str, float]:
        """Las 5 cotizaciones REEXPRESADAS en otra convención de delta.

        Necesario en el corte `Spot Delta Last Tenor`: para interpolar en plazo
        entre un pilar cotizado en spot delta y otro en forward delta hay que
        llevarlos primero a una convención común, porque "el put de 25 delta" no
        es el mismo strike en las dos.
        """
        if conv == self.conv:
            return dict(self.quotes)
        _, c25 = self.strike_at_delta(0.25, "C", conv)
        _, c10 = self.strike_at_delta(0.10, "C", conv)
        _, p25 = self.strike_at_delta(0.25, "P", conv)
        _, p10 = self.strike_at_delta(0.10, "P", conv)
        atm = self.quotes["atm"]      # ATM Zero Straddle Last Tenor = 10Y: no corta
        return {"atm": atm, "rr25": c25 - p25, "bf25": (c25 + p25) / 2 - atm,
                "rr10": c10 - p10, "bf10": (c10 + p10) / 2 - atm}

    def table(self) -> List[dict]:
        return [{"label": p.label, "call_delta": p.call_delta,
                 "vol_pct": p.vol * 100.0, "strike": p.strike} for p in self.points]


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------
def wing_vols(atm: float, rr: float, bf: float) -> tuple:
    """(σ_call, σ_put) bajo la convención `2vol (CP Avg)`. Todo en decimal."""
    return atm + bf + rr / 2.0, atm + bf - rr / 2.0


def build_slice(tenor: str, expiry: _dt.date, delivery: _dt.date, tau: float,
                forward: float, df_for: float, conv: DeltaConvention,
                atm: float, rr25: float, bf25: float, rr10: float, bf10: float,
                zero_delta_straddle: bool = True,
                wing_slope_factor: float = 1.0,
                wing_ext_lambda: Optional[float] = 1.0,
                wing_flat_delta: float = 0.5,
                axis_conv: DeltaConvention = AXIS_CONVENTION,
                ) -> SmileSlice:
    """Construye el smile de un tenor. Todas las vols en DECIMAL."""
    s25c, s25p = wing_vols(atm, rr25, bf25)
    s10c, s10p = wing_vols(atm, rr10, bf10)

    k_atm = dl.atm_strike(forward, atm, tau, conv, zero_delta_straddle)
    raw = [
        ("10C", dl.strike_from_call_delta(0.10, forward, s10c, tau, df_for, conv), s10c),
        ("25C", dl.strike_from_call_delta(0.25, forward, s25c, tau, df_for, conv), s25c),
        ("ATM", k_atm, atm),
        ("25P", dl.strike_from_put_delta(0.25, forward, s25p, tau, df_for, conv), s25p),
        ("10P", dl.strike_from_put_delta(0.10, forward, s10p, tau, df_for, conv), s10p),
    ]
    pts = [SmilePoint(label=lab,
                      call_delta=100.0 * dl.call_delta(forward, K, sig, tau, df_for, axis_conv),
                      vol=sig, strike=K)
           for lab, K, sig in raw]
    pts.sort(key=lambda p: p.call_delta)

    return SmileSlice(tenor=tenor, expiry=expiry, delivery=delivery, tau=tau,
                      forward=forward, df_for=df_for, conv=conv, atm_vol=atm,
                      quotes={"atm": atm, "rr25": rr25, "bf25": bf25,
                              "rr10": rr10, "bf10": bf10},
                      points=pts, wing_slope_factor=wing_slope_factor,
                      wing_ext_lambda=wing_ext_lambda,
                      wing_flat_delta=wing_flat_delta,
                      axis_conv=axis_conv).fit()
