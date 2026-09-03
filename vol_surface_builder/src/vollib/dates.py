"""
dates.py — Capa de fechas del Módulo 2.

Responsabilidad ÚNICA: aritmética de fechas para superficies de volatilidad.

DIFERENCIA CON EL MÓDULO 1: `curvelib.dates` envuelve QuantLib porque tiene que
GENERAR los pilares (schedules de swaps, spot lag, roll conventions). Aquí las
fechas de expiración vienen DADAS en el export de Calypso (columna `Exp` de la
hoja de quotes), así que este módulo solo necesita:

  - fracción de año ACT/365 (el `Volatility Day Count` de las 6 superficies)
  - avanzar N días hábiles para la fecha de entrega (expiry + delivery lag)

El calendario de feriados NO se modela: se usa solo fin de semana. Es suficiente
mientras las expiraciones vengan del export; el día que haya que generarlas para
una fecha nueva hay que enchufar `curvelib.dates.get_calendar` (que sí carga los
calendarios reales NYC/MEX/LIM/SPO/SAN/BOG). Está señalado en `advance_business_days`.
"""
from __future__ import annotations

import datetime as _dt
from typing import List

_DAY_COUNTS = {"ACT/365", "ACT/365F", "ACT/360"}


def parse_date(s: str) -> _dt.date:
    """Acepta las dos formas que aparecen en los exports de Calypso:
    `dd/mm/yyyy` (hojas de quotes y curvas) e ISO `yyyy-mm-dd`."""
    s = str(s).strip()
    if "/" in s:
        d, m, y = s.split("/")
        return _dt.date(int(y), int(m), int(d))
    return _dt.date.fromisoformat(s[:10])


def year_fraction(day_count: str, d1: _dt.date, d2: _dt.date) -> float:
    """Fracción de año. Las 6 superficies usan ACT/365 (`Volatility Day Count`);
    se soporta ACT/360 por si alguna superficie futura lo usa (el manual menciona
    BUS/252 para BRL onshore, que NO está implementado — ver DOCUMENTACION)."""
    if day_count not in _DAY_COUNTS:
        raise ValueError(
            f"Day count '{day_count}' no soportado. Disponibles: {sorted(_DAY_COUNTS)}. "
            f"(BUS/252 requiere calendario de feriados y no está implementado.)"
        )
    days = (d2 - d1).days
    return days / (360.0 if day_count == "ACT/360" else 365.0)


# ---------------------------------------------------------------------------
# Calendarios de feriados
# ---------------------------------------------------------------------------
# Validados contra la grilla DAILY de Calypso para USD/MXN (NYC+MEX) y USD/BRL
# (NYC+BRA) en la ventana 02/09/2026 - 01/09/2028: 36 y 38 días hábiles excluidos,
# explicados uno a uno, cero falsos positivos. Ver
# `claude/validacion-superficies-calypso-mxn-brl.md` §3-bis.
#
# NOTA sobre NYC: Calypso traslada los feriados que caen sábado al viernes previo
# (Juneteenth 19/06/2027, Navidad 25/12/2027) EXCEPTO el Año Nuevo del 01/01/2028,
# cuya observancia caería en el año anterior. Aquí se aplica la regla de forma
# CONSISTENTE, de modo que este calendario difiere de Calypso en 31/12/2027. Es
# deliberado: la diferencia se reporta, no se replica el defecto.

def _easter(y: int) -> _dt.date:
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    da = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(y, mo, da)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> _dt.date:
    d = _dt.date(y, month, 1)
    d += _dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + _dt.timedelta(days=7 * (n - 1))


def _last_weekday(y: int, month: int, weekday: int) -> _dt.date:
    d = (_dt.date(y, month + 1, 1) - _dt.timedelta(days=1)) if month < 12 else _dt.date(y, 12, 31)
    while d.weekday() != weekday:
        d -= _dt.timedelta(days=1)
    return d


def _observed_us(d: _dt.date) -> _dt.date:
    """Regla de observancia del mercado de Nueva York: sábado -> viernes previo,
    domingo -> lunes siguiente."""
    if d.weekday() == 5:
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + _dt.timedelta(days=1)
    return d


def _holidays_NYC(y: int) -> dict:
    E = _easter(y)
    out = {}
    for m, dd, name in [(1, 1, "Ano Nuevo"), (6, 19, "Juneteenth"),
                        (7, 4, "Independencia EEUU"), (12, 25, "Navidad")]:
        out[_observed_us(_dt.date(y, m, dd))] = name
    out[_nth_weekday(y, 1, 0, 3)] = "MLK"
    out[_nth_weekday(y, 2, 0, 3)] = "Presidents Day"
    out[E - _dt.timedelta(days=2)] = "Viernes Santo"
    out[_last_weekday(y, 5, 0)] = "Memorial Day"
    out[_nth_weekday(y, 9, 0, 1)] = "Labor Day"
    out[_nth_weekday(y, 10, 0, 2)] = "Columbus Day"
    out[_observed_us(_dt.date(y, 11, 11))] = "Veterans Day"
    out[_nth_weekday(y, 11, 3, 4)] = "Thanksgiving"
    return out


def _holidays_MEX(y: int) -> dict:
    E = _easter(y)
    out = {_dt.date(y, 1, 1): "Ano Nuevo", _dt.date(y, 5, 1): "Dia del Trabajo",
           _dt.date(y, 9, 16): "Independencia", _dt.date(y, 11, 2): "Dia de Muertos",
           _dt.date(y, 12, 12): "Virgen de Guadalupe", _dt.date(y, 12, 25): "Navidad"}
    out[_nth_weekday(y, 2, 0, 1)] = "Constitucion"
    out[_nth_weekday(y, 3, 0, 3)] = "Natalicio Benito Juarez"
    out[_nth_weekday(y, 11, 0, 3)] = "Revolucion Mexicana"
    out[E - _dt.timedelta(days=3)] = "Jueves Santo"
    out[E - _dt.timedelta(days=2)] = "Viernes Santo"
    if y >= 2024 and (y - 2024) % 6 == 0:
        out[_dt.date(y, 10, 1)] = "Transmision del Poder Ejecutivo"
    return out


def _holidays_BRA(y: int) -> dict:
    E = _easter(y)
    out = {_dt.date(y, 1, 1): "Confraternizacao", _dt.date(y, 4, 21): "Tiradentes",
           _dt.date(y, 5, 1): "Dia do Trabalho", _dt.date(y, 9, 7): "Independencia",
           _dt.date(y, 10, 12): "N.Sra Aparecida", _dt.date(y, 11, 2): "Finados",
           _dt.date(y, 11, 15): "Proclamacao da Republica", _dt.date(y, 12, 25): "Natal"}
    out[E - _dt.timedelta(days=48)] = "Carnaval (seg)"
    out[E - _dt.timedelta(days=47)] = "Carnaval (ter)"
    out[E - _dt.timedelta(days=2)] = "Sexta-feira Santa"
    out[E + _dt.timedelta(days=60)] = "Corpus Christi"
    if y >= 2024:
        out[_dt.date(y, 11, 20)] = "Consciencia Negra"
    return out


def _holidays_LIM(y: int) -> dict:
    E = _easter(y)
    out = {_dt.date(y, 1, 1): "Ano Nuevo", _dt.date(y, 5, 1): "Dia del Trabajo",
           _dt.date(y, 6, 29): "San Pedro y San Pablo", _dt.date(y, 7, 28): "Fiestas Patrias",
           _dt.date(y, 7, 29): "Fiestas Patrias", _dt.date(y, 8, 30): "Santa Rosa de Lima",
           _dt.date(y, 10, 8): "Combate de Angamos", _dt.date(y, 11, 1): "Todos los Santos",
           _dt.date(y, 12, 8): "Inmaculada Concepcion", _dt.date(y, 12, 9): "Batalla de Ayacucho",
           _dt.date(y, 12, 25): "Navidad", _dt.date(y, 6, 7): "Batalla de Arica"}
    out[E - _dt.timedelta(days=3)] = "Jueves Santo"
    out[E - _dt.timedelta(days=2)] = "Viernes Santo"
    return out


def _holidays_TGT(y: int) -> dict:
    E = _easter(y)
    return {_dt.date(y, 1, 1): "Ano Nuevo", E - _dt.timedelta(days=2): "Viernes Santo",
            E + _dt.timedelta(days=1): "Lunes de Pascua", _dt.date(y, 5, 1): "Dia del Trabajo",
            _dt.date(y, 12, 25): "Navidad", _dt.date(y, 12, 26): "San Esteban"}


def _holidays_SCL(y: int) -> dict:
    E = _easter(y)
    return {_dt.date(y, 1, 1): "Ano Nuevo", E - _dt.timedelta(days=2): "Viernes Santo",
            _dt.date(y, 5, 1): "Dia del Trabajo", _dt.date(y, 5, 21): "Glorias Navales",
            _dt.date(y, 6, 29): "San Pedro y San Pablo", _dt.date(y, 7, 16): "Virgen del Carmen",
            _dt.date(y, 8, 15): "Asuncion", _dt.date(y, 9, 18): "Independencia",
            _dt.date(y, 9, 19): "Glorias del Ejercito", _dt.date(y, 10, 31): "Iglesias Evangelicas",
            _dt.date(y, 11, 1): "Todos los Santos", _dt.date(y, 12, 8): "Inmaculada Concepcion",
            _dt.date(y, 12, 25): "Navidad"}


def _holidays_BOG(y: int) -> dict:
    E = _easter(y)

    def monday_on_or_after(d):
        return d + _dt.timedelta(days=(0 - d.weekday()) % 7)

    out = {_dt.date(y, 1, 1): "Ano Nuevo", _dt.date(y, 5, 1): "Dia del Trabajo",
           _dt.date(y, 7, 20): "Independencia", _dt.date(y, 8, 7): "Batalla de Boyaca",
           _dt.date(y, 12, 8): "Inmaculada Concepcion", _dt.date(y, 12, 25): "Navidad"}
    out[E - _dt.timedelta(days=3)] = "Jueves Santo"
    out[E - _dt.timedelta(days=2)] = "Viernes Santo"
    for d, n in [(_dt.date(y, 1, 6), "Reyes"), (_dt.date(y, 3, 19), "San Jose"),
                 (_dt.date(y, 6, 29), "San Pedro y San Pablo"),
                 (_dt.date(y, 8, 15), "Asuncion"), (_dt.date(y, 10, 12), "Raza"),
                 (_dt.date(y, 11, 1), "Todos los Santos"),
                 (_dt.date(y, 11, 11), "Independencia de Cartagena")]:
        out[monday_on_or_after(d)] = n
    out[monday_on_or_after(E + _dt.timedelta(days=43))] = "Ascension"
    out[monday_on_or_after(E + _dt.timedelta(days=64))] = "Corpus Christi"
    out[monday_on_or_after(E + _dt.timedelta(days=71))] = "Sagrado Corazon"
    return out


_HOLIDAY_RULES = {"NYC": _holidays_NYC, "MEX": _holidays_MEX, "BRA": _holidays_BRA,
                  "LIM": _holidays_LIM, "TGT": _holidays_TGT, "SCL": _holidays_SCL,
                  "BOG": _holidays_BOG}
# Alias que aparecen en el campo `Holidays` de Calypso
_HOLIDAY_ALIAS = {"NYK": "NYC", "USD": "NYC", "MXN": "MEX", "MEXICO": "MEX",
                  "BRL": "BRA", "SPO": "BRA", "SAO": "BRA", "PEN": "LIM",
                  "EUR": "TGT", "TARGET": "TGT", "CLP": "SCL", "SAN": "SCL",
                  "COP": "BOG"}


class Calendar:
    """Calendario conjunto de una o más plazas.

    `Calendar("NYC,MEX")` reproduce la grilla DAILY de USD/MXN. Las fechas se
    generan por regla y se cachean por año, así que el rango es ilimitado.
    """

    def __init__(self, venues: str | List[str] = ""):
        if isinstance(venues, str):
            venues = [v.strip() for v in venues.replace(";", ",").split(",") if v.strip()]
        names = []
        for v in venues:
            u = v.upper()
            u = _HOLIDAY_ALIAS.get(u, u)
            if u not in _HOLIDAY_RULES:
                raise ValueError(
                    f"Plaza '{v}' sin calendario. Disponibles: {sorted(_HOLIDAY_RULES)} "
                    f"(alias: {sorted(_HOLIDAY_ALIAS)})."
                )
            if u not in names:
                names.append(u)
        self.venues = names
        self._cache: dict = {}

    def holidays(self, year: int) -> dict:
        if year not in self._cache:
            out = {}
            for v in self.venues:
                for d, n in _HOLIDAY_RULES[v](year).items():
                    out[d] = f"{out[d]} / {n}" if d in out else f"{v}:{n}"
            self._cache[year] = out
        return self._cache[year]

    def is_business_day(self, d: _dt.date) -> bool:
        return d.weekday() < 5 and d not in self.holidays(d.year)

    def adjust(self, d: _dt.date, following: bool = True) -> _dt.date:
        step = 1 if following else -1
        while not self.is_business_day(d):
            d += _dt.timedelta(days=step)
        return d

    def advance(self, d: _dt.date, n: int) -> _dt.date:
        step = 1 if n >= 0 else -1
        out = d
        for _ in range(abs(int(n))):
            out += _dt.timedelta(days=step)
            while not self.is_business_day(out):
                out += _dt.timedelta(days=step)
        return out

    def business_days(self, start: _dt.date, end: _dt.date) -> List[_dt.date]:
        out, cur = [], start
        while cur <= end:
            if self.is_business_day(cur):
                out.append(cur)
            cur += _dt.timedelta(days=1)
        return out

    def __repr__(self) -> str:
        return f"Calendar({'+'.join(self.venues) or 'solo fines de semana'})"


WEEKENDS_ONLY = Calendar()


def advance_business_days(d: _dt.date, n: int, calendar: "Calendar | None" = None) -> _dt.date:
    """Avanza n días hábiles.

    `calendar=None` cuenta solo fines de semana (comportamiento histórico). Pasar
    un `Calendar` con las plazas del par —el campo `Holidays` de Calypso— reproduce
    las fechas de entrega reales.
    """
    return (calendar or WEEKENDS_ONLY).advance(d, n)


def normalize_tenor(t: str) -> str:
    """Normaliza la etiqueta del tenor.

    Existe porque los exports no son consistentes: USD/BRL escribe el overnight
    como `O/N` y USD/CLP, USD/COP y EUR/USD como `1D`. Sin normalizar, el mismo
    punto de la curva quedaría con dos nombres distintos según el par y el
    emparejamiento entre lados bid/mid/ask fallaría.
    """
    s = str(t).strip().upper()
    if s in ("O/N", "ON", "OVERNIGHT", "1D"):
        return "1D"
    if s in ("T/N", "TN"):
        return "TN"
    return s


def tenor_sort_key(t: str) -> float:
    """Orden aproximado en años, para ordenar los slices de la superficie."""
    s = normalize_tenor(t)
    if s == "1D":
        return 1.0 / 365.0
    if s == "TN":
        return 2.0 / 365.0
    unit, n = s[-1], float(s[:-1])
    return {"D": n / 365.0, "W": n / 52.0, "M": n / 12.0, "Y": n}[unit]


def sorted_tenors(tenors: List[str]) -> List[str]:
    return sorted(tenors, key=tenor_sort_key)
