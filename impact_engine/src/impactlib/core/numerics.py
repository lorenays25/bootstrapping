"""Funciones numéricas compartidas. Sin dependencias externas a propósito: el
motor tiene que poder correr donde solo hay Python estándar."""
from __future__ import annotations

import math

SQRT2PI = math.sqrt(2.0 * math.pi)


def N(x: float) -> float:
    """Normal acumulada."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def n(x: float) -> float:
    """Densidad normal."""
    return math.exp(-0.5 * x * x) / SQRT2PI


def norm_ppf(p: float) -> float:
    """Inversa de la normal acumulada (Acklam). Error máximo medido 5e-9 sobre
    20 001 puntos entre 1e-6 y 1-1e-6, más que suficiente para recuperar d1 de
    un delta reportado con cinco decimales."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p <= 0.0 or p >= 1.0:
        raise ValueError(f"norm_ppf fuera de rango: {p}")
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
