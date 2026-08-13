"""
dates.py — Capa de fechas basada en QuantLib.

Responsabilidad ÚNICA de este módulo: aritmética de fechas correcta.
  - Calendarios por moneda (con fallback a fines de semana si QuantLib
    no tiene el calendario, p.ej. Perú y Colombia).
  - Day counts (ACT/360, ACT/365F, 30/360).
  - Generación de schedules de pago (anual, semestral, trimestral...).
  - Spot lag y ajuste de fechas (Modified Following por defecto).

El motor de bootstrapping NUNCA toca QuantLib directamente: solo pasa
por estas funciones. Así, si un día quieres reemplazar QuantLib por
otra librería de fechas, solo cambias este archivo.
"""
from __future__ import annotations

import datetime as _dt
from typing import List

import QuantLib as ql

# ---------------------------------------------------------------------------
# Calendarios por código. Si QuantLib no tiene el calendario del país
# (Perú, Colombia), usamos WeekendsOnly como aproximación y lo dejamos
# documentado: en producción se cargan los feriados manualmente con
# calendar.addHoliday(...).
# ---------------------------------------------------------------------------
_CAL_FACTORY = {
    "US": lambda: ql.UnitedStates(ql.UnitedStates.FederalReserve),
    "US_SIFMA": lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
    "TARGET": lambda: ql.TARGET(),
    "UK": lambda: ql.UnitedKingdom(),
    "JP": lambda: ql.Japan(),
    "CH": lambda: ql.Switzerland(),
    "CA": lambda: ql.Canada(),
    "MX": lambda: ql.Mexico(),
    "BR": lambda: ql.Brazil(),
    "SE": lambda: ql.Sweden(),
    "CN": lambda: ql.China(),
    "CL": lambda: ql.Chile(),
    # QuantLib no trae Perú ni Colombia -> fallback fines de semana.
    "PE": lambda: ql.WeekendsOnly(),
    "CO": lambda: ql.WeekendsOnly(),
    "WEEKENDS": lambda: ql.WeekendsOnly(),
}

_DC_FACTORY = {
    "ACT/360": lambda: ql.Actual360(),
    "ACT/365": lambda: ql.Actual365Fixed(),
    "ACT/365F": lambda: ql.Actual365Fixed(),
    "30/360": lambda: ql.Thirty360(ql.Thirty360.BondBasis),      # US Bond Basis
    "30E/360": lambda: ql.Thirty360(ql.Thirty360.European),      # Eurobond Basis (pata fija EUR)
    "ACT/ACT": lambda: ql.ActualActual(ql.ActualActual.ISDA),
    "ACT/ACT_ISMA": lambda: ql.ActualActual(ql.ActualActual.ISMA),  # corrido de bonos
}

_BDC = {
    "MF": ql.ModifiedFollowing,
    "F": ql.Following,
    "P": ql.Preceding,
    "NONE": ql.Unadjusted,
}

_FREQ_MONTHS = {"A": 12, "S": 6, "Q": 3, "M": 1}
_UNIT_MAP = {"D": ql.Days, "W": ql.Weeks, "M": ql.Months, "Y": ql.Years}


def _period_from_freq(freq: str) -> ql.Period:
    """Convierte un código de frecuencia a ql.Period.
    Acepta los alias A/S/Q/M (anual/semestral/trimestral/mensual) y también
    tenores explícitos tipo '4W', '2W', '13W', '1M' — necesario para curvas
    con periodicidad no calendario, p.ej. TIIE 28D (periodos de 4 semanas
    exactas, no "mensual calendario")."""
    f = freq.upper().strip()
    if f in _FREQ_MONTHS:
        return ql.Period(_FREQ_MONTHS[f], ql.Months)
    unit = _UNIT_MAP[f[-1]]
    n = int(f[:-1])
    return ql.Period(n, unit)


# ---------------------------------------------------------------------------
# Conversores date <-> QuantLib
# ---------------------------------------------------------------------------
def to_ql(d: _dt.date) -> ql.Date:
    return ql.Date(d.day, d.month, d.year)


def from_ql(d: ql.Date) -> _dt.date:
    return _dt.date(d.year(), d.month(), d.dayOfMonth())


def get_calendar(codes: str | List[str]) -> ql.Calendar:
    """Devuelve el calendario. Acepta 'US' o ['US','PE'] (calendario conjunto)."""
    if isinstance(codes, str):
        codes = [codes]
    cals = []
    for c in codes:
        if c not in _CAL_FACTORY:
            raise KeyError(f"Calendario desconocido: {c}. Disponibles: {list(_CAL_FACTORY)}")
        cals.append(_CAL_FACTORY[c]())
    if len(cals) == 1:
        return cals[0]
    joint = ql.JointCalendar(cals[0], cals[1])
    for extra in cals[2:]:
        joint = ql.JointCalendar(joint, extra)
    return joint


def get_day_counter(code: str) -> ql.DayCounter:
    if code not in _DC_FACTORY:
        raise KeyError(f"Day count desconocido: {code}. Disponibles: {list(_DC_FACTORY)}")
    return _DC_FACTORY[code]()


# ---------------------------------------------------------------------------
# Accessors de solo lectura para exponer los códigos válidos (p.ej. al
# catálogo de convenciones de la UI) sin duplicar las claves de los dicts
# privados de arriba.
# ---------------------------------------------------------------------------
def calendar_codes() -> List[str]:
    return sorted(_CAL_FACTORY)


def day_count_codes() -> List[str]:
    return sorted(_DC_FACTORY)


def business_day_convention_codes() -> List[str]:
    return sorted(_BDC)


# ---------------------------------------------------------------------------
# Operaciones de fechas
# ---------------------------------------------------------------------------
def year_fraction(day_count: str, d1: _dt.date, d2: _dt.date) -> float:
    """Fracción de año entre dos fechas según el day count."""
    return get_day_counter(day_count).yearFraction(to_ql(d1), to_ql(d2))


def adjust(d: _dt.date, calendar_codes, convention: str = "MF") -> _dt.date:
    cal = get_calendar(calendar_codes)
    return from_ql(cal.adjust(to_ql(d), _BDC[convention]))


def add_tenor(
    d: _dt.date,
    tenor: str,
    calendar_codes="WEEKENDS",
    convention: str = "MF",
    end_of_month: bool = False,
) -> _dt.date:
    """Suma un tenor ('3M', '1Y', '2W', '1D', 'ON', 'TN') con ajuste de calendario."""
    cal = get_calendar(calendar_codes)
    t = tenor.upper().strip()
    if t in ("ON", "O/N"):
        return from_ql(cal.advance(to_ql(d), 1, ql.Days, _BDC[convention]))
    if t in ("TN", "T/N"):
        return from_ql(cal.advance(to_ql(d), 2, ql.Days, _BDC[convention]))
    unit_map = {"D": ql.Days, "W": ql.Weeks, "M": ql.Months, "Y": ql.Years}
    unit = unit_map[t[-1]]
    n = int(t[:-1])
    return from_ql(cal.advance(to_ql(d), n, unit, _BDC[convention], end_of_month))


def add_months(d: _dt.date, n: int) -> _dt.date:
    """Suma n meses calendario (n negativo => resta). SIN ajuste de día hábil.
    Se usa para generar el schedule de cupones de un bono retrocediendo desde
    el vencimiento (p.ej. 12-Feb / 12-Ago). Las fechas de devengo son las no
    ajustadas; el ajuste a día hábil se aplica solo a la fecha de pago."""
    return from_ql(to_ql(d) + ql.Period(n, ql.Months))


def spot_date(valuation_date: _dt.date, spot_lag: int, calendar_codes) -> _dt.date:
    """Fecha spot = valuación + spot_lag días hábiles."""
    cal = get_calendar(calendar_codes)
    return from_ql(cal.advance(to_ql(valuation_date), spot_lag, ql.Days, ql.Following))


def advance_business_days(d: _dt.date, n: int, calendar_codes) -> _dt.date:
    """Avanza (n>0) o retrocede (n<0) n días HÁBILES exactos según `calendar_codes`.
    Usado para pay_delay_days (avanza, desde el fin de periodo hasta la fecha de
    pago real) y rate_cutoff_days (retrocede, para ubicar la fecha de corte del
    fixing compuesto diario) de patas flotantes RFR/OIS."""
    cal = get_calendar(calendar_codes)
    return from_ql(cal.advance(to_ql(d), n, ql.Days, ql.Following))


def tenor_years(tenor: str) -> float:
    """Aproximación en años de un tenor (para ordenar instrumentos)."""
    t = tenor.upper().strip()
    if t in ("ON", "O/N"):
        return 1.0 / 365.0
    if t in ("TN", "T/N"):
        return 2.0 / 365.0
    n = float(t[:-1])
    return {"D": n / 365.0, "W": n / 52.0, "M": n / 12.0, "Y": n}[t[-1]]


def make_schedule(
    start: _dt.date,
    end: _dt.date,
    frequency: str,
    calendar_codes,
    convention: str = "MF",
    end_of_month: bool = False,
) -> List[_dt.date]:
    """
    Genera las fechas de pago (excluye start, incluye end ajustado).
    frequency: 'A' anual, 'S' semestral, 'Q' trimestral, 'M' mensual,
               'Z' cupón cero (un solo pago al final), o un tenor explícito
               como '4W' (periodicidad exacta de 28 días, p.ej. TIIE 28D).
    Se construye hacia atrás desde `end` (convención de mercado habitual).
    end_of_month: si True, aplica la regla EOM (si `start` cae en el último
    día hábil de su mes, todas las fechas del schedule ruedan a fin de mes).
    """
    cal = get_calendar(calendar_codes)
    bdc = _BDC[convention]
    if frequency == "Z":
        return [from_ql(cal.adjust(to_ql(end), bdc))]
    sched = ql.Schedule(
        to_ql(start),
        to_ql(end),
        _period_from_freq(frequency),
        cal,
        bdc,
        bdc,
        ql.DateGeneration.Backward,
        end_of_month,
    )
    dates = [from_ql(d) for d in sched]
    return dates[1:]  # excluye la fecha inicial
