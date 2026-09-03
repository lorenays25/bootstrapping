"""
deltas.py — Convenciones de delta y conversión delta ↔ strike.

Es el corazón del Módulo 2 y donde se concentran los errores típicos de
replicación. Las cotizaciones dan la vol en DELTA; para tener una superficie
utilizable hay que pasar cada punto a STRIKE, y eso depende de tres decisiones
de convención que Calypso expone como parámetros (ver METODOLOGIA):

  - `Quotes are Delta with Premium`  -> delta ajustado por prima o Black-Scholes puro
  - `Spot Delta Last Tenor`          -> spot delta hasta ese tenor, forward delta después
  - `ATM Zero Straddle Last Tenor`   -> ATM = straddle delta-neutral, o ATM-forward

NOTACIÓN
--------
    F   forward a la fecha de ENTREGA (delivery), no a la de expiración
    K   strike
    σ   volatilidad (decimal, no puntos)
    τ   plazo a EXPIRACIÓN en años (ACT/365)
    Df  factor de descuento de la divisa EXTRANJERA (base del par) a la entrega

    d1 = [ln(F/K) + σ²τ/2] / (σ√τ)
    d2 = d1 − σ√τ

LAS CUATRO CONVENCIONES DE DELTA (call, con signo positivo)
-----------------------------------------------------------
    forward, sin ajuste  :  Δ = N(d1)
    spot,    sin ajuste  :  Δ = Df · N(d1)
    forward, ajustado    :  Δ = (K/F) · N(d2)
    spot,    ajustado    :  Δ = Df · (K/F) · N(d2)

El delta ajustado por prima aparece cuando la prima se paga en la divisa BASE
(USD en USD/MXN). Derivación: prima en divisa base = precio/S, y con
S = F·Df_dom/Df_for se cancela el término N(d1):

    Δ_pa = Df·N(d1) − Df_dom·(F·N(d1) − K·N(d2))/S = Df·(K/F)·N(d2)

STRIKE DEL ATM ZERO-DELTA STRADDLE — el detalle que más se equivoca
-------------------------------------------------------------------
Se pide Δ_call + Δ_put = 0. El resultado CAMBIA DE SIGNO en el exponente según
si el delta es ajustado por prima o no:

    sin ajuste :  N(d1) − N(−d1) = 0  ⟹  d1 = 0  ⟹  K = F·exp(+σ²τ/2)
    ajustado   :  (K/F)[N(d2) − N(−d2)] = 0  ⟹  d2 = 0  ⟹  K = F·exp(−σ²τ/2)

Las 6 superficies tienen `Quotes are Delta with Premium = true`, así que aplica
la SEGUNDA. Usar la primera pondría el ATM del lado equivocado del forward.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq
from scipy.stats import norm


@dataclass(frozen=True)
class DeltaConvention:
    """Convención de delta efectiva para UN punto de la superficie."""

    premium_adjusted: bool
    spot_delta: bool          # True = spot delta (multiplica por Df); False = forward delta

    def label(self) -> str:
        return (("spot" if self.spot_delta else "forward")
                + ("/premium-adj" if self.premium_adjusted else "/plain"))


def _d1_d2(F: float, K: float, sigma: float, tau: float):
    v = sigma * math.sqrt(tau)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    return d1, d1 - v


def call_delta(F: float, K: float, sigma: float, tau: float, df_for: float,
               conv: DeltaConvention) -> float:
    """Delta de la CALL en la convención indicada. Devuelve un número en (0, ~1)."""
    d1, d2 = _d1_d2(F, K, sigma, tau)
    if conv.premium_adjusted:
        delta = (K / F) * norm.cdf(d2)
    else:
        delta = norm.cdf(d1)
    return delta * (df_for if conv.spot_delta else 1.0)


def put_delta(F: float, K: float, sigma: float, tau: float, df_for: float,
              conv: DeltaConvention) -> float:
    """Delta de la PUT (negativo)."""
    d1, d2 = _d1_d2(F, K, sigma, tau)
    if conv.premium_adjusted:
        delta = -(K / F) * norm.cdf(-d2)
    else:
        delta = -norm.cdf(-d1)
    return delta * (df_for if conv.spot_delta else 1.0)


# ---------------------------------------------------------------------------
# ATM
# ---------------------------------------------------------------------------
def atm_strike(F: float, sigma: float, tau: float, conv: DeltaConvention,
               zero_delta_straddle: bool) -> float:
    """Strike del punto ATM.

    zero_delta_straddle=True  -> straddle delta-neutral (ver encabezado)
    zero_delta_straddle=False -> ATM forward, K = F  (es el caso `ATM Zero
                                 Straddle Last Tenor = 0D`, p.ej. USD/PEN)
    """
    if not zero_delta_straddle:
        return F
    e = -0.5 * sigma * sigma * tau if conv.premium_adjusted else 0.5 * sigma * sigma * tau
    return F * math.exp(e)


# ---------------------------------------------------------------------------
# delta -> strike
# ---------------------------------------------------------------------------
def _pa_call_delta_peak_d2(sigma: float, tau: float) -> float:
    """d2 donde el delta call ajustado por prima alcanza su MÁXIMO.

    Necesario porque Δ_pa,call(K) = (K/F)·N(d2) NO es monótona: vale 0 en K→0,
    sube, alcanza un máximo y vuelve a 0 en K→∞. Para un delta objetivo hay DOS
    strikes que lo producen; el que corresponde a una call OTM es el de la rama
    de strikes ALTOS (d2 por debajo del pico). Sin acotar la búsqueda a esa rama,
    el root-finder puede devolver el strike equivocado sin ningún síntoma.

    Condición de primer orden:  N(d2)·σ√τ = n(d2).
    """
    v = sigma * math.sqrt(tau)
    f = lambda x: norm.cdf(x) * v - norm.pdf(x)
    lo, hi = -20.0, 20.0
    return brentq(f, lo, hi, xtol=1e-14, rtol=1e-14, maxiter=200)


def strike_from_call_delta(target: float, F: float, sigma: float, tau: float,
                           df_for: float, conv: DeltaConvention) -> float:
    """Strike de una CALL con delta `target` (positivo, p.ej. 0.25)."""
    v = sigma * math.sqrt(tau)
    if not conv.premium_adjusted:
        # forma cerrada: N(d1) = target / (df si spot delta)
        p = target / (df_for if conv.spot_delta else 1.0)
        if not (0.0 < p < 1.0):
            raise ValueError(f"delta call {target} fuera de rango para df={df_for}")
        d1 = norm.ppf(p)
        return F * math.exp(-d1 * v + 0.5 * v * v)

    # ajustado por prima: root-finding acotado a la rama de strikes altos
    scale = df_for if conv.spot_delta else 1.0
    d2_peak = _pa_call_delta_peak_d2(sigma, tau)
    k_peak = F * math.exp(-d2_peak * v - 0.5 * v * v)
    peak = scale * (k_peak / F) * norm.cdf(d2_peak)
    if target >= peak:
        raise ValueError(
            f"delta call {target:.4f} inalcanzable: el máximo del delta ajustado "
            f"por prima para σ={sigma:.4f}, τ={tau:.4f} es {peak:.4f}. "
            f"Revisa la convención o el nivel de vol."
        )

    def f(K):
        _, d2 = _d1_d2(F, K, sigma, tau)
        return scale * (K / F) * norm.cdf(d2) - target

    lo, hi = k_peak, k_peak * 2.0
    for _ in range(80):
        if f(hi) < 0:
            break
        hi *= 1.5
    else:
        raise ValueError(f"no se pudo acotar el strike para delta call {target}")
    return brentq(f, lo, hi, xtol=1e-12, rtol=1e-14, maxiter=200)


def strike_from_put_delta(target: float, F: float, sigma: float, tau: float,
                          df_for: float, conv: DeltaConvention) -> float:
    """Strike de una PUT con delta `target` en VALOR ABSOLUTO (p.ej. 0.25)."""
    v = sigma * math.sqrt(tau)
    scale = df_for if conv.spot_delta else 1.0
    if not conv.premium_adjusted:
        p = target / scale
        if not (0.0 < p < 1.0):
            raise ValueError(f"delta put {target} fuera de rango para df={df_for}")
        d1 = -norm.ppf(p)
        return F * math.exp(-d1 * v + 0.5 * v * v)

    # ajustado por prima: |Δ_put| = (K/F)·N(−d2) es ESTRICTAMENTE CRECIENTE en K
    # (derivada = [N(−d2) + n(d2)/(σ√τ)]/F > 0), así que hay una sola raíz.
    def f(K):
        _, d2 = _d1_d2(F, K, sigma, tau)
        return scale * (K / F) * norm.cdf(-d2) - target

    lo, hi = F * 1e-6, F
    for _ in range(80):
        if f(lo) < 0:
            break
        lo *= 0.5
    for _ in range(80):
        if f(hi) > 0:
            break
        hi *= 1.5
    return brentq(f, lo, hi, xtol=1e-12, rtol=1e-14, maxiter=200)
