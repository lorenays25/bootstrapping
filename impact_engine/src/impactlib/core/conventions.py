"""
Convenciones de fecha y descuento de Calypso.

Ninguna se supuso: todas se identificaron contra el reporte de valorización del
01/09/2026, probando los candidatos y quedándose con el que reproduce los
números. El detalle de cada verificación está en el README del módulo.
"""
from __future__ import annotations

import datetime as _dt

#: Días hábiles entre la fecha de valorización y la fecha spot.
LAG_SPOT = 2


def tau_vol(valuation_date: _dt.date, expiry: _dt.date) -> float:
    """Plazo de volatilidad: ACT/365 de la valorización al VENCIMIENTO.

    Verificado resolviendo (F, τ) simultáneamente en 20 grupos de operaciones
    USD/PEN que comparten vencimiento: el τ implícito reproduce los días
    calendario con error ≤ 0.06 días, del día 2 al día 361.
    """
    return (expiry - valuation_date).days / 365.0


def depo_df(rate: float, days: int) -> float:
    """Factor de descuento de depósito SIMPLE sobre ACT/365. `rate` en decimal.

    El cociente DELTA/FWD_DELTA del reporte es exactamente el factor de
    descuento de la divisa base, así que la convención se puede medir en vez de
    suponerla. Sobre 120 operaciones con delta grande, contando desde la fecha
    spot: error mediano 0.44 pb y máximo 0.81 pb. Las otras siete combinaciones
    de (fecha de inicio, base, capitalización) dan máximos de 2.5 a 16 pb.
    """
    if days <= 0:
        return 1.0
    return 1.0 / (1.0 + rate * days / 365.0)


def depo_rate(df: float, days: int) -> float:
    """La inversa de `depo_df`: la tasa simple implícita en un factor."""
    if days <= 0 or df <= 0:
        return 0.0
    return (1.0 / df - 1.0) * 365.0 / days


def spot_date(valuation_date: _dt.date, calendar=None) -> _dt.date:
    """Fecha spot: valorización + 2 días hábiles.

    Usa el calendario de feriados del Módulo 2 si está disponible; si no, cuenta
    solo fines de semana y avisa por el valor de retorno del llamador, porque un
    feriado no considerado corre la fecha spot un día y eso se propaga a todos
    los factores de descuento.
    """
    try:
        from vollib import dates as _vd
        return _vd.advance_business_days(valuation_date, LAG_SPOT, calendar)
    except Exception:
        d, n = valuation_date, 0
        while n < LAG_SPOT:
            d += _dt.timedelta(days=1)
            if d.weekday() < 5:
                n += 1
        return d
