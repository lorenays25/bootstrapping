"""
quotes_loader.py — Lectura de los exports de Calypso.

Separa CONVENCIÓN (el panel de parámetros, estable) de PRECIO (la hoja de
quotes, diaria), igual que hace `curvelib.quotes_loader` en el Módulo 1.

TRES ARCHIVOS POR SUPERFICIE
----------------------------
 1. quotes      `Term;Exp;Day;Cal Days;[Trade Days;Trade Vol;]RR25;RR10;ATM;BF25;BF10`
 2. parámetros  `Parameter;Value` (bloques SURFACE CONFIG / QUOTE CONVENTIONS /
                 INTERPOLATION CONFIG / ROLLING CONFIG / INFO)
 3. underlyings `Id;Type;Description` — se usa para VALIDAR que la grilla de
                 instrumentos coincide con la de los quotes

DOS CABECERAS DISTINTAS EN LA HOJA DE QUOTES
--------------------------------------------
EUR/USD viene con 9 columnas (sin `Trade Days` ni `Trade Vol`) y los otros cinco
con 11. No es un error del export: EUR/USD tiene `Weighting = false`, así que no
hay cálculo de trading time. El loader acepta las dos formas. Ninguna de las dos
afecta el cálculo, porque los seis pares tienen
`Interpolate on Trading Time = false`.

COLUMNAS QUE SE IGNORAN
-----------------------
La pantalla de Calypso muestra además `xBF10` y `xRR10`, que no entran al cálculo
(confirmado con el usuario). El loader trabaja solo con la lista blanca de arriba
y descarta cualquier otra columna en silencio.

BID / MID / ASK
---------------
Calypso exporta un archivo POR LADO, con la MISMA cabecera. `load_quotes` recibe
hasta tres rutas y arma la forma `{bid, mid, ask}` por tenor e instrumento. Un
solo archivo se lee como "los tres lados iguales". Antes de combinar valida que
los tres lados compartan grilla de tenores y fechas de expiración: si un lado
trae un tenor de más o un expiry corrido, combinarlos en silencio armaría un
smile mezclando fechas.
"""
from __future__ import annotations

import csv
import datetime as _dt
from typing import Dict, List, Optional

from . import dates as dt

SIDES = ("bid", "mid", "ask")
QUOTE_FIELDS = ("ATM", "RR25", "RR10", "BF25", "BF10")

# Nombres de parámetro que el motor consume. Cualquier otro se conserva en el
# dict pero no participa del cálculo.
_BOOL = {"true": True, "false": False, "": None}


class QuoteError(ValueError):
    """Error de datos de entrada de la superficie."""


# ---------------------------------------------------------------------------
def _clean(s) -> str:
    return ("" if s is None else str(s)).strip()


def load_parameters(path: str) -> dict:
    """Lee el panel de parámetros. Descarta los encabezados de sección, que vienen
    con las letras separadas por espacios (`S U R F A C E  C O N F I G`)."""
    out: dict = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.reader(f, delimiter=";"):
            if len(row) < 2:
                continue
            k, v = _clean(row[0]), _clean(row[1])
            if not k or k.count(" ") >= len(k) * 0.4:      # encabezado de sección
                continue
            out[k] = v
    if not out:
        raise QuoteError(f"El archivo de parámetros no tiene filas útiles: {path}")
    return out


def param_bool(params: dict, key: str, default: Optional[bool] = None) -> bool:
    raw = _clean(params.get(key, "")).lower()
    if raw in _BOOL and _BOOL[raw] is not None:
        return _BOOL[raw]
    if default is None:
        raise QuoteError(f"Parámetro '{key}' ausente o vacío y sin default: {params.get(key)!r}")
    return default


def param_float(params: dict, key: str, default: Optional[float] = None) -> float:
    raw = _clean(params.get(key, ""))
    if raw:
        return float(raw)
    if default is None:
        raise QuoteError(f"Parámetro '{key}' ausente o vacío y sin default.")
    return default


# ---------------------------------------------------------------------------
def parse_quotes_csv(text: str, rate_scale: float = 0.01,
                     label: str = "<texto>") -> Dict[str, dict]:
    """Parsea UNA hoja de quotes desde una cadena.

    Separado de la lectura de archivo a propósito: la interfaz web sube el CSV
    pegado o cargado por el usuario y necesita parsearlo sin escribirlo a disco.
    El delimitador se detecta (';' en los exports de Calypso, ',' si alguien lo
    reexporta desde Excel en otra configuración regional).
    """
    out: Dict[str, dict] = {}
    if not (text or "").strip():
        raise QuoteError(f"La hoja de quotes está vacía ({label}).")
    first = text.splitlines()[0]
    delim = ";" if first.count(";") >= first.count(",") else ","
    import io as _io
    with _io.StringIO(text) as f:
        reader = csv.DictReader(f, delimiter=delim)
        cols = {(_clean(c)): c for c in (reader.fieldnames or [])}
        missing = [c for c in ("Term", "Exp", "Cal Days") + QUOTE_FIELDS if c not in cols]
        if missing:
            raise QuoteError(f"Faltan columnas {missing} en {label}. "
                             f"Encontradas: {list(cols)}")
        for r in reader:
            term = _clean(r.get(cols["Term"]))
            if not term:
                continue
            tenor = dt.normalize_tenor(term)
            if tenor in out:
                raise QuoteError(f"Tenor duplicado '{term}' en {label}")
            rec = {
                "tenor": tenor,
                "term_raw": term,
                "expiry": dt.parse_date(r[cols["Exp"]]),
                "cal_days": float(_clean(r[cols["Cal Days"]]).replace(",", "")),
            }
            for fld in QUOTE_FIELDS:
                rec[fld.lower()] = float(_clean(r[cols[fld]])) * rate_scale
            # columnas opcionales de trading time (ausentes en EUR/USD)
            for opt, key in (("Trade Days", "trade_days"), ("Trade Vol", "trade_vol")):
                if opt in cols and _clean(r.get(cols[opt])):
                    v = float(_clean(r[cols[opt]]).replace(",", ""))
                    rec[key] = v * (rate_scale if key == "trade_vol" else 1.0)
            out[tenor] = rec
    if not out:
        raise QuoteError(f"La hoja de quotes no tiene filas: {label}")
    return out


def load_quotes_one_side(path: str, rate_scale: float = 0.01) -> Dict[str, dict]:
    """Lee UNA hoja de quotes desde archivo."""
    with open(path, encoding="utf-8-sig") as f:
        return parse_quotes_csv(f.read(), rate_scale, label=path)


def load_quotes(paths_by_side: Optional[Dict[str, str]] = None,
                rate_scale: float = 0.01,
                texts_by_side: Optional[Dict[str, str]] = None
                ) -> Dict[str, Dict[str, dict]]:
    """Lee uno o más lados (de archivo o de texto) y valida que sean combinables.

    paths_by_side: {'mid': ruta} o {'bid': .., 'mid': .., 'ask': ..}
    texts_by_side: lo mismo pero con el CSV como cadena (lo que sube la interfaz).
                   Si se pasan los dos, el TEXTO manda — es lo que el usuario
                   acaba de cargar.

    Devuelve {lado: {tenor: registro}} con los tres lados siempre presentes
    (los ausentes se copian del mid, igual que el `quote:` escalar del Módulo 1).
    """
    paths_by_side = paths_by_side or {}
    texts_by_side = {k: v for k, v in (texts_by_side or {}).items() if (v or "").strip()}
    loaded = {s: load_quotes_one_side(p, rate_scale)
              for s, p in paths_by_side.items() if p and s not in texts_by_side}
    for s, t in texts_by_side.items():
        loaded[s] = parse_quotes_csv(t, rate_scale, label=f"CSV cargado ({s})")
    if "mid" not in loaded:
        raise QuoteError("Hace falta al menos el lado 'mid'. Recibido: "
                         f"{sorted(set(paths_by_side) | set(texts_by_side)) or 'nada'}")

    ref = loaded["mid"]
    for side, data in loaded.items():
        if side == "mid":
            continue
        if set(data) != set(ref):
            falta = sorted(set(ref) - set(data))
            sobra = sorted(set(data) - set(ref))
            raise QuoteError(
                f"El lado '{side}' no comparte grilla de tenores con 'mid'. "
                f"Falta: {falta or '—'} · Sobra: {sobra or '—'}. "
                f"Combinarlos armaría un smile con tenores distintos por lado."
            )
        for t in ref:
            if data[t]["expiry"] != ref[t]["expiry"]:
                raise QuoteError(
                    f"El tenor {t} tiene expiry {data[t]['expiry']} en '{side}' y "
                    f"{ref[t]['expiry']} en 'mid'. Los lados deben ser del mismo día."
                )
    return {s: loaded.get(s, ref) for s in SIDES}


# ---------------------------------------------------------------------------
def load_underlyings(path: str) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if _clean(r.get("Id")):
                rows.append({"id": _clean(r["Id"]), "type": _clean(r.get("Type")),
                             "description": _clean(r.get("Description"))})
    return rows


def validate_underlyings(underlyings: List[dict], quotes: Dict[str, dict],
                         pair: str) -> List[str]:
    """Comprueba que haya 5 instrumentos (ATM, BF25, RR25, BF10, RR10) por cada
    tenor de la hoja de quotes. El emparejamiento es por DESCRIPCIÓN, no por Id:
    los Ids de Calypso no son correlativos por tenor (hay bloques agregados en
    momentos distintos, p.ej. el 4M de USD/MXN)."""
    kinds = ("ATM", "Butterfly 25-delta", "Risk Reversal 25-delta",
             "Butterfly 10-delta", "Risk Reversal 10-delta")
    warns: List[str] = []
    seen: Dict[str, set] = {}
    for u in underlyings:
        d = u["description"]
        parts = d.split()
        if len(parts) < 2:
            continue
        tenor = dt.normalize_tenor(parts[1])
        for k in kinds:
            if d.endswith(k):
                seen.setdefault(tenor, set()).add(k)
                break
    for tenor in quotes:
        got = seen.get(tenor, set())
        missing = [k for k in kinds if k not in got]
        if missing:
            warns.append(f"[{pair}] el tenor {tenor} de la hoja de quotes no tiene "
                         f"underlying para: {', '.join(missing)}")
    extra = sorted(set(seen) - set(quotes))
    if extra:
        warns.append(f"[{pair}] hay underlyings de tenores sin quote: {', '.join(extra)}")
    return warns


# ---------------------------------------------------------------------------
def validate_quotes(quotes: Dict[str, dict], pair: str,
                    vol_day_count: str, valuation_date: _dt.date) -> List[str]:
    """Chequeos de sanidad de la entrada, antes de construir nada.

    No son cosméticos: cada uno corresponde a una forma concreta en que una hoja
    mal exportada produce una superficie sin sentido pero sin error visible.
    """
    warns: List[str] = []
    tenors = dt.sorted_tenors(list(quotes))

    prev_w = None
    for t in tenors:
        q = quotes[t]
        tau = dt.year_fraction(vol_day_count, valuation_date, q["expiry"])
        if tau <= 0:
            warns.append(f"[{pair}] {t}: expiry {q['expiry']} no es posterior a la "
                         f"fecha de valuación {valuation_date}.")
            continue
        # varianza total ATM creciente -> sin arbitraje de calendario
        w = q["atm"] ** 2 * tau
        if prev_w is not None and w <= prev_w:
            warns.append(f"[{pair}] {t}: la varianza total ATM no crece respecto del "
                         f"tenor anterior ({w:.8f} <= {prev_w:.8f}) — arbitraje de calendario.")
        prev_w = w
        # convexidad del smile
        if q["bf25"] <= 0:
            warns.append(f"[{pair}] {t}: BF25 = {q['bf25']*100:.4f} no es positivo.")
        if q["bf10"] <= q["bf25"]:
            warns.append(f"[{pair}] {t}: BF10 ({q['bf10']*100:.4f}) no supera a BF25 "
                         f"({q['bf25']*100:.4f}) — convexidad invertida.")
        if q["atm"] <= 0:
            warns.append(f"[{pair}] {t}: ATM = {q['atm']*100:.4f} no es positivo.")
        # coherencia de Cal Days con las fechas
        esperado = (q["expiry"] - valuation_date).days
        if abs(esperado - q["cal_days"]) > 1e-9:
            warns.append(f"[{pair}] {t}: 'Cal Days' = {q['cal_days']} no coincide con "
                         f"Exp − valuación = {esperado}. ¿Fecha de valuación equivocada?")
    return warns
