"""
server.py — Microservicio FastAPI que conecta la interfaz HTML con el motor.

Expone el pipeline de bootstrapping por HTTP para que el navegador pueda:
  1. cargar la configuración por defecto (GET /config)
  2. aplicar una hoja de quotes a una config (POST /apply-quotes)
  3. construir las 28 curvas bid/mid/ask y devolver las tablas (POST /build)
  4. descargar la tabla de una curva como CSV (POST /export-csv)
  5. superficies de volatilidad (Modulo 2): /vol/config, /vol/validate-quotes,
     /vol/build, /vol/query, /vol/pillars, /vol/fx-spots,
     /vol/sensitivity, /vol/export-csv

Uso:
    cd curve_bootstrapper
    python server.py
    # abre http://127.0.0.1:8000  (sirve la interfaz y la API juntas)

El servidor sirve el HTML de ui/parametrizador.html en la raíz, de modo que
no hay problemas de CORS ni de archivos locales: todo corre en el mismo
origen http://127.0.0.1:8000.
"""
from __future__ import annotations

import copy
import datetime as _dt
import io
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import yaml
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from curvelib.orchestrator import (load_config, build_bid_mid_ask, convention_report,
                                   topological_order)
from curvelib.quotes_loader import apply_quotes_sheet
from curvelib.instruments import (CONVENTION_SCHEMA, INSTRUMENT_TYPES,
                                  REQUIRED_BY_TYPE, conventions_for_type)

HERE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(HERE, "config", "curves.yaml")
UI_PATH = os.path.join(HERE, "ui", "parametrizador.html")

# ---------------------------------------------------------------------------
# MÓDULO 2 — superficies de volatilidad (vollib)
#
# Import TOLERANTE A FALLO a propósito: si `vol_surface_builder` no está
# disponible, la pestaña de curvas tiene que seguir funcionando igual. El error
# se reporta en /vol/config para que la interfaz lo muestre en su pestaña en vez
# de romper el arranque del servidor.
# ---------------------------------------------------------------------------
VOL_ROOT = os.path.abspath(os.path.join(HERE, "..", "vol_surface_builder"))
VOL_CONFIG_PATH = os.path.join(VOL_ROOT, "config", "surfaces.yaml")
_VOL_ERR = None
try:
    sys.path.insert(0, os.path.join(VOL_ROOT, "src"))
    from vollib import orchestrator as volorch
    from vollib import quotes_loader as volql
    from vollib import curves as volcurves
except Exception as _e:                                    # pragma: no cover
    volorch = volql = volcurves = None
    _VOL_ERR = f"{type(_e).__name__}: {_e}"


def _vol_config():
    """Carga surfaces.yaml y fija la raíz para resolver las rutas relativas."""
    cfg = volorch.load_config(VOL_CONFIG_PATH)
    cfg["_root"] = VOL_ROOT
    return cfg


def _vol_val_date(cfg):
    """Fecha de valuación como `date`.

    El YAML la entrega como `date` pero la interfaz la manda como cadena, y
    `load_fx_spots` compara contra objetos `date`: sin normalizar, ninguna fila
    del archivo de TC casa y el spot "desaparece".
    """
    v = cfg["valuation_date"]
    return _dt.date.fromisoformat(str(v)[:10]) if not isinstance(v, _dt.date) else v


def _vol_unavailable():
    return {"ok": False, "error":
            f"El Módulo 2 no está disponible: {_VOL_ERR}. "
            f"Se esperaba encontrarlo en {VOL_ROOT}."}


app = FastAPI(title="curvelib + vollib API", version="0.5.0")


# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    """Sirve la interfaz. Inyecta una marca para que el HTML sepa que está
    corriendo contra el servidor (y habilite el botón 'Construir')."""
    with open(UI_PATH, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<!--SERVER_MODE-->", "<script>window.SERVER_MODE=true;</script>")
    return HTMLResponse(html)


def _jsonable(obj):
    """Convierte fechas a ISO recursivamente.

    El YAML parsea como `date` cualquier valor con forma de fecha, no solo
    `valuation_date`: también el `maturity` de cada bono. JSONResponse no
    serializa `date`, así que sin esto /config devuelve 500 y la UI cae al
    respaldo embebido."""
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


@app.get("/config")
def get_config():
    """Devuelve la configuración por defecto (YAML -> JSON)."""
    cfg = load_config(CONFIG_PATH)
    return JSONResponse(_jsonable(cfg))


@app.get("/schema")
def get_schema():
    """Catálogo de convenciones válidas (nombre, tipo, valores permitidos,
    y a qué tipos de instrumento aplica cada una) -- consumido por el
    selector "+ Convención" de la UI para no hardcodear nombres/valores en
    el HTML. Mismo principio que /config: si el motor agrega una convención
    nueva, este endpoint la refleja solo. Cada campo declara además
    `applies_to`: los tipos de instrumento a los que aplica (ver
    /schema/types para el mapa inverso)."""
    return JSONResponse(CONVENTION_SCHEMA)


@app.get("/schema/types")
def get_schema_types():
    """Qué convenciones aplican a cada tipo de instrumento, y cuáles son
    obligatorias. Va en un endpoint aparte de /schema para no cambiar la
    forma (dict plano) que ya consume el parametrizador.

    Con esto la UI ofrece, en CADA instrumento, solo las convenciones que
    ese instrumento usa: un bono no debe mostrar rate_cutoff_days, ni un
    OIS mostrar coupon_freq."""
    return JSONResponse({
        "instrument_types": sorted(INSTRUMENT_TYPES),
        "by_type": {t: sorted(conventions_for_type(t)) for t in INSTRUMENT_TYPES},
        "required_by_type": {t: list(v) for t, v in REQUIRED_BY_TYPE.items()},
    })


@app.post("/conventions")
def get_conventions(payload: dict = Body(...)):
    """Convención EFECTIVA de cada instrumento de cada curva, con la
    procedencia de cada campo (curve / preset / instrument / default).

    Es la vista de auditoría para reconciliar contra el sistema de primera
    línea: permite responder "¿qué settlement_lag se aplicó a este bono y
    de dónde salió?" sin leer el YAML a mano. Usa el mismo resolutor que el
    cálculo, así que lo reportado es exactamente lo que se calculó.

    payload = {config: {...}}"""
    config = copy.deepcopy(payload["config"])
    try:
        return {"ok": True, **convention_report(config)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/apply-quotes")
def apply_quotes(payload: dict = Body(...)):
    """Aplica una hoja de quotes CSV a la config recibida.
    payload = {config: {...}, quotes_csv: "...", rate_scale: 0.01,
               curve_map: {...}}"""
    config = copy.deepcopy(payload["config"])
    quotes_csv = payload.get("quotes_csv", "")
    rate_scale = payload.get("rate_scale", 0.01)
    curve_map = payload.get("curve_map") or None
    try:
        config, warnings = apply_quotes_sheet(
            config, quotes_csv, curve_map=curve_map, rate_scale=rate_scale)
        return {"ok": True, "config": config, "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/build")
def build(payload: dict = Body(...)):
    """Construye bid/mid/ask y devuelve las tablas de todas las curvas.
    payload = {config: {...}, zero_day_count: "ACT/360"}
    Cada curva se intenta por separado; si una falla, se reporta su error
    sin abortar el resto (útil cuando editas quotes y algo queda inconsistente)."""
    config = copy.deepcopy(payload["config"])
    zdc = payload.get("zero_day_count", "ACT/360")

    val_date = config["valuation_date"]
    if isinstance(val_date, str):
        val_date = _dt.date.fromisoformat(val_date[:10])

    # Construcción global; si TODO falla, devolvemos el traceback.
    try:
        cs = build_bid_mid_ask(config, verbose=False)
    except Exception as e:
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc().splitlines()[-6:]}

    order = topological_order(config["curves"])
    tables, errors = {}, {}
    for name in order:
        try:
            rows = cs.table(name, zero_day_count=zdc)
            tables[name] = [
                {"date": r["date"].isoformat(), "offset": r["offset"],
                 "zero_bid": r["zero_bid"] * 100, "zero_mid": r["zero_mid"] * 100,
                 "zero_ask": r["zero_ask"] * 100, "df_bid": r["df_bid"],
                 "df_mid": r["df_mid"], "df_ask": r["df_ask"]}
                for r in rows
            ]
        except Exception as e:
            errors[name] = str(e)

    return {"ok": True, "order": order, "tables": tables, "errors": errors,
            "valuation_date": val_date.isoformat()}


@app.post("/export-csv", response_class=PlainTextResponse)
def export_csv(payload: dict = Body(...)):
    """Devuelve la tabla de una curva como texto CSV descargable.
    payload = {config, name, zero_day_count}"""
    config = copy.deepcopy(payload["config"])
    name = payload["name"]
    zdc = payload.get("zero_day_count", "ACT/360")
    cs = build_bid_mid_ask(config, verbose=False)
    rows = cs.table(name, zero_day_count=zdc)
    buf = io.StringIO()
    buf.write("Date,Offset,Zero Bid,Zero Mid,Zero Ask,Df Bid,Df Mid,Df Ask\n")
    for r in rows:
        buf.write(f"{r['date'].isoformat()},{r['offset']},"
                  f"{r['zero_bid']*100:.5f},{r['zero_mid']*100:.5f},"
                  f"{r['zero_ask']*100:.5f},{r['df_bid']:.8f},"
                  f"{r['df_mid']:.8f},{r['df_ask']:.8f}\n")
    return PlainTextResponse(buf.getvalue(),
                             headers={"Content-Disposition":
                                      f'attachment; filename="{name}.csv"'})




# ===========================================================================
# MÓDULO 2 — SUPERFICIES DE VOLATILIDAD
# ===========================================================================
# Comparadas contra el manual Calypso "FX Volatility Surfaces" v16.1, para que
# la interfaz pueda marcar en qué se aparta cada superficie de lo documentado.
# Ver COMPARACION_SUPERFICIES_6_PARES.md en la raíz del repo.
VOL_RECOMMENDED = {
    "Strangle/Fly Quotes": ("1vol (Broker)",
        "el manual: 'this will almost always be 1vol (Broker)'"),
    "Up Extrap 1.0 Delta": ("2.0",
        "Calypso recomienda 2.0. NOTA: este parámetro describe la extrapolación "
        "del propio Calypso; el motor ya no lo usa — el ala se validó contra la "
        "malla de 5 en 5 deltas y quedó como extensión lineal tangente."),
    "Down Extrap 1.0 Delta": ("0.0",
        "Calypso recomienda 0.0. Misma nota que Up Extrap: es una observación "
        "sobre la configuración de Calypso, no un parámetro del motor."),
    "Interpolate Outright Variance": ("true", "valor recomendado"),
    "Roll Method": ("Forward Volatility", "recomendación explícita del manual"),
    "Volatility Day Count": ("ACT/365", "'almost always ACT/365'"),
}


# Divisas G10. Un par con alguna pata fuera de esta lista es "emerging market"
# para efectos de las recomendaciones del manual.
G10 = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK"}


def _vol_flags(params, spec):
    """Desviaciones de una superficie respecto del manual Calypso.

    Dos de las reglas dependen del PAR y no se pueden expresar como un valor
    recomendado fijo; son justamente los dos hallazgos principales de la
    validación (ver COMPARACION_SUPERFICIES_6_PARES.md):

      - Spot Delta / ATM Zero Straddle Last Tenor: el manual recomienda 1Y/10Y
        para pares G10 y 0D/0D para emergentes.
      - Quotes are Delta with Premium: true cuando la prima se paga en la PRIMERA
        divisa del par, false cuando se paga en la segunda.
    """
    base = spec["base_ccy"].upper()
    quote = spec["quote_ccy"].upper()
    is_em = (base not in G10) or (quote not in G10)
    flags = []

    def add(field, got, rec, why):
        got = (got or "").strip()
        if got != rec:
            flags.append({"field": field, "value": got or "(vacío)",
                          "recommended": rec, "why": why})

    for k, (rec, why) in VOL_RECOMMENDED.items():
        add(k, params.get(k), rec, why)

    seg = "emergente" if is_em else "G10"
    add("Spot Delta Last Tenor", params.get("Spot Delta Last Tenor"),
        "0D" if is_em else "1Y",
        f"el manual: 1Y para pares G10 y 0D para emergentes — {base}/{quote} es {seg}")
    add("ATM Zero Straddle Last Tenor", params.get("ATM Zero Straddle Last Tenor"),
        "0D" if is_em else "10Y",
        f"el manual: 10Y para pares G10 y 0D para emergentes — {base}/{quote} es {seg}")

    # la prima se paga en USD en los 6 pares: es la PRIMERA divisa en USD/XXX y
    # la SEGUNDA en EUR/USD, y el manual nombra a EURUSD explícitamente
    if base == "USD" or quote == "USD":
        premium_first = (base == "USD")
        add("Quotes are Delta with Premium", params.get("Quotes are Delta with Premium"),
            "true" if premium_first else "false",
            "la prima se paga en USD, que en este par es la "
            + ("primera" if premium_first else "segunda")
            + " divisa" + ("" if premium_first else
                           " — el manual nombra a EURUSD como caso de 'false'"))
    return flags


@app.get("/vol/config")
def vol_get_config():
    """Configuración de las superficies: por par, los parámetros leídos del panel
    de Calypso y sus cotizaciones. Es lo que alimenta la pestaña de la interfaz."""
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        cfg = _vol_config()
        val = cfg["valuation_date"]
        val = val.isoformat() if hasattr(val, "isoformat") else str(val)[:10]
        out = {}
        for pair, spec in cfg["surfaces"].items():
            # Solo CONVENCIONES. Los datos de mercado (cotizaciones y tipo de
            # cambio) NO se precargan: cambian todos los días y mostrarlos desde
            # el repositorio hace creer que la superficie está construida con los
            # datos del día cuando no lo está. La interfaz arranca vacía y se
            # llena únicamente con lo que la mesa carga.
            params = volql.load_parameters(volorch._path(cfg, spec["parameters"]))
            flags = _vol_flags(params, spec)
            out[pair] = {"base_ccy": spec["base_ccy"], "quote_ccy": spec["quote_ccy"],
                         "delivery_lag": spec.get("delivery_lag", 2),
                         "holidays": spec.get("holidays"),
                         "parameters": params, "quotes": [], "flags": flags,
                         "repo_sides": sorted((spec.get("quotes") or {}).keys())}
        return JSONResponse({"ok": True, "valuation_date": val, "surfaces": out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


@app.get("/vol/repo-data")
def vol_repo_data():
    """Devuelve los archivos de mercado del repositorio COMO TEXTO.

    Existe para que "usar los datos del repositorio" pase por el mismo camino que
    una carga manual: el contenido viaja a la interfaz, la interfaz lo guarda en
    su estado y lo reenvía en cada llamada. Así no queda ningún camino en el que
    el motor use datos que el usuario no cargó explícitamente.
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        cfg = _vol_config()
        quotes, spots = {}, {}
        for pair, spec in cfg["surfaces"].items():
            sides = {}
            for side, rel in (spec.get("quotes") or {}).items():
                try:
                    with open(volorch._path(cfg, rel), encoding="utf-8-sig") as f:
                        sides[side] = f.read()
                except Exception:
                    pass
            if sides:
                quotes[pair] = sides
        try:
            cur = volcurves.load_fx_spots(
                volorch._path(cfg, cfg["market_data"]["fx_spots_file"]),
                _vol_val_date(cfg))
            spots = {k: {"bid": v.get("bid"), "ask": v.get("ask")} for k, v in cur.items()}
        except Exception:
            spots = {}
        val = cfg["valuation_date"]
        return JSONResponse({"ok": True, "quotes_csv": quotes, "fx_spots": spots,
                             "valuation_date": (val.isoformat()
                                                if hasattr(val, "isoformat") else str(val)[:10])})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/vol/build")
def vol_build(payload: dict = Body(default={})):
    """Construye las superficies bid/mid/ask y devuelve la tabla de cada una.

    payload opcional: {overrides: {PAR: {"Spot Delta Last Tenor": "0D", ...}}}
    Los overrides son el mecanismo de análisis de sensibilidad: permiten
    construir el mismo par con otra convención sin tocar el YAML.
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        cfg = _vol_config()
        # La fecha puede venir de la interfaz. Si no cuadra con los expiries de
        # las hojas de quotes, `validate_quotes` lo reporta como aviso en vez de
        # construir en silencio una superficie con los plazos corridos.
        if payload.get("valuation_date"):
            cfg["valuation_date"] = str(payload["valuation_date"])[:10]
        cfg = _vol_apply_uploads(cfg, payload)
        for pair, ov in (payload.get("overrides") or {}).items():
            if pair in cfg["surfaces"]:
                cfg["surfaces"][pair]["overrides"] = ov
        vs, warns = volorch.build_bid_mid_ask(cfg, verbose=False)
        warns = list(cfg.get("_ui_notes") or []) + list(warns)
        tables, errors = {}, {}
        for pair in vs.pairs():
            try:
                tables[pair] = [
                    {"tenor": r["tenor"], "expiry": r["expiry"].isoformat(),
                     "point": r["point"],
                     "delta": r["delta_mid"],
                     "strike_bid": r["strike_bid"], "strike_mid": r["strike_mid"],
                     "strike_ask": r["strike_ask"],
                     "vol_bid": r["vol_bid"], "vol_mid": r["vol_mid"],
                     "vol_ask": r["vol_ask"]}
                    for r in vs.table(pair)]
            except Exception as e:
                errors[pair] = str(e)
        meta = {}
        for pair, s in vs.sides["mid"].items():
            conv = next(iter(s.conv_by_tenor.values()))
            meta[pair] = {"spot": s.fwd.spot, "n_tenors": len(s.slices),
                          "delta_convention": conv.label(),
                          "vol_day_count": s.vol_day_count,
                          "forwards": {sl.tenor: sl.forward for sl in s.slices}}
        return JSONResponse({"ok": True, "pairs": list(vs.pairs()), "tables": tables,
                             "errors": errors, "warnings": warns, "meta": meta,
                             "valuation_date": vs.valuation_date.isoformat()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


# ---------------------------------------------------------------------------
# Pilares de la superficie — la vista equivalente a la pestaña `Surface` de Calypso
# ---------------------------------------------------------------------------
_CP_ORDER = ["P10", "P25", "ATM", "C25", "C10"]


def _parse_calypso_pillars(text: str) -> dict:
    """Lee un export de Calypso de pilares y devuelve {expiry_iso: {punto: vol}}.

    Reconoce los tres formatos que exporta la pantalla, por su encabezado:
      - Surface C/P     : Expiry;Term / Delta;P10;P25;ATM;C25;C10
      - Surface RR/BF   : Expiry;Term / Delta;ATM;RR25;RR10;BF25;BF10
      - Points BID/ASK  : Expiry/Delta;10;25;C (ATM) P;25;10   (10C 25C ATM 25P 10P)
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        raise ValueError("El CSV está vacío.")
    delim = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    head = [h.strip() for h in lines[0].split(delim)]
    up = [h.upper() for h in head]
    if "RR25" in up:
        kind = "rrbf"
    elif "P10" in up and "C10" in up:
        kind = "cp"
    elif len(head) >= 6 and "DELTA" in up[0].replace(" ", ""):
        kind = "points"
    else:
        raise ValueError(
            "No reconozco el encabezado. Se esperaba un export de Surface "
            "(C/P o RR/BF) o de Points BID/ASK. Recibido: " + "; ".join(head[:7]))

    out = {}
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(delim)]
        if len(cells) < 6 or not cells[0]:
            continue
        try:
            d, m, y = cells[0].split("/")
            iso = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except Exception:
            continue

        def f(i):
            return float(cells[i].replace(",", ""))

        try:
            if kind == "cp":
                vals = {"P10": f(2), "P25": f(3), "ATM": f(4), "C25": f(5), "C10": f(6)}
            elif kind == "points":
                vals = {"C10": f(1), "C25": f(2), "ATM": f(3), "P25": f(4), "P10": f(5)}
            else:
                atm, rr25, rr10, bf25, bf10 = f(2), f(3), f(4), f(5), f(6)
                vals = {"ATM": atm,
                        "C25": atm + bf25 + rr25 / 2, "P25": atm + bf25 - rr25 / 2,
                        "C10": atm + bf10 + rr10 / 2, "P10": atm + bf10 - rr10 / 2}
        except (ValueError, IndexError):
            continue
        out[iso] = vals
    if not out:
        raise ValueError("El CSV no tiene filas con fecha y valores legibles.")
    return out


def _rrbf(v: dict) -> dict:
    """De las 5 vols C/P a la vista RR/BF (álgebra 2vol CP Avg)."""
    return {"ATM": v["ATM"],
            "RR25": v["C25"] - v["P25"], "RR10": v["C10"] - v["P10"],
            "BF25": (v["C25"] + v["P25"]) / 2 - v["ATM"],
            "BF10": (v["C10"] + v["P10"]) / 2 - v["ATM"]}


@app.post("/vol/pillars")
def vol_pillars(payload: dict = Body(default={})):
    """Pilares de UNA superficie, en las dos vistas de Calypso y los tres lados.

    payload: {pair, valuation_date?, uploads?, calypso_csv?}

    `calypso_csv` es el contenido de un export de Calypso (Surface C/P, Surface
    RR/BF o Points BID/ASK). Si viene, cada fila trae además la columna de
    Calypso y la diferencia, que es el entregable de validación.
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        pair = payload.get("pair")
        cfg = _vol_config()
        if payload.get("valuation_date"):
            cfg["valuation_date"] = str(payload["valuation_date"])[:10]
        cfg = _vol_apply_uploads(cfg, payload)
        if pair not in cfg["surfaces"]:
            return JSONResponse({"ok": False,
                                 "error": f"Par '{pair}' no está en la configuración."})
        vs, warns = volorch.build_bid_mid_ask(cfg, verbose=False)
        ui_notes = list(cfg.get("_ui_notes") or [])
        warns = ui_notes + list(warns)

        cly = None
        cly_error = None
        if (payload.get("calypso_csv") or "").strip():
            try:
                cly = _parse_calypso_pillars(payload["calypso_csv"])
            except Exception as e:
                cly_error = str(e)

        mid = vs.sides["mid"][pair]
        rows = []
        for sl in mid.slices:
            iso = sl.expiry.isoformat()
            row = {"tenor": sl.tenor, "expiry": iso,
                   "delivery": sl.delivery.isoformat(),
                   "forward": sl.forward,
                   "delta_convention": sl.conv.label(),
                   "cp": {}, "rrbf": {}, "strike": {}}
            for side in ("bid", "mid", "ask"):
                s = vs.sides[side][pair]
                ssl = s.slice_by_tenor(sl.tenor)
                v = {p.label.replace("10C", "C10").replace("25C", "C25")
                      .replace("25P", "P25").replace("10P", "P10"): p.vol * 100.0
                     for p in ssl.points}
                row["cp"][side] = {k: v[k] for k in _CP_ORDER}
                row["rrbf"][side] = _rrbf(v)
                if side == "mid":
                    row["strike"] = {p.label.replace("10C", "C10").replace("25C", "C25")
                                      .replace("25P", "P25").replace("10P", "P10"): p.strike
                                     for p in ssl.points}
            if cly and iso in cly:
                row["cp"]["calypso"] = {k: cly[iso][k] for k in _CP_ORDER}
                row["rrbf"]["calypso"] = _rrbf(cly[iso])
            rows.append(row)

        matched = sum(1 for r in rows if "calypso" in r["cp"])
        return JSONResponse({
            "ok": True, "pair": pair, "rows": rows,
            "valuation_date": vs.valuation_date.isoformat(),
            "spot": mid.fwd.spot,
            "calendar": ("+".join(mid.calendar.venues) if getattr(mid, "calendar", None)
                         and mid.calendar.venues else None),
            "calypso": {"loaded": cly is not None, "matched": matched,
                        "total": len(rows), "error": cly_error},
            "sides_note": next((n for n in ui_notes if n.startswith(f"[{pair}]")), None),
            "warnings": warns})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


@app.post("/vol/sensitivity")
def vol_sensitivity(payload: dict = Body(...)):
    """Corre un par con dos juegos de convenciones y compara strike a strike.

    Es la herramienta que convierte un hallazgo de configuración ("esta
    superficie usa convención G10 y el manual recomienda la de emergentes")
    en un número ("desplaza los strikes hasta X%").

    payload = {pair, overrides: {...}}
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        pair = payload["pair"]
        overrides = payload.get("overrides") or {}

        def build(ov):
            cfg = _vol_apply_uploads(_vol_config(), payload)
            cfg["surfaces"] = {pair: dict(cfg["surfaces"][pair])}
            if ov:
                cfg["surfaces"][pair]["overrides"] = ov
            return volorch.build_all(cfg, side="mid", verbose=False)[0][pair]

        a, b = build(None), build(overrides)
        rows, worst = [], 0.0
        for sa, sb in zip(a.slices, b.slices):
            row = {"tenor": sa.tenor, "expiry": sa.expiry.isoformat()}
            for lbl in ("10C", "25C", "ATM", "25P", "10P"):
                ka = next(p for p in sa.points if p.label == lbl).strike
                kb = next(p for p in sb.points if p.label == lbl).strike
                d = (kb / ka - 1.0) * 100.0
                worst = max(worst, abs(d))
                row[lbl] = {"base": ka, "alt": kb, "diff_pct": d}
            rows.append(row)
        return JSONResponse({"ok": True, "pair": pair, "rows": rows,
                             "worst_pct": worst, "overrides": overrides})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


_TC_HEADER = ("Date;Quote Name;Quote Type;Bid ;Ask ;Open ;Close ;High ;Low ;Last ;"
              "Entered Date;Entered User;EstimatedB;Known Date;Source Name")


def _fx_pair_to_quote_name(pair: str) -> str:
    """USDMXN -> FX.USD.MXN (el nombre con que Calypso cotiza el spot)."""
    return f"FX.{pair[:3].upper()}.{pair[3:].upper()}"


def _write_tc_csv(rows, valuation_date) -> str:
    """Escribe un CSV en el formato del export de TC de Calypso y devuelve la ruta.

    `rows` = [(quote_name, bid, ask)]. Se usa tanto para el archivo que sube el
    usuario (que se guarda tal cual) como para los spots tecleados a mano.
    """
    import tempfile
    d = str(valuation_date)[:10]
    dd = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"      # el export de Calypso usa dd/mm/yyyy
    lines = [_TC_HEADER]
    for name, bid, ask in rows:
        lines.append(f"{dd};{name};Price;{bid};{ask};NaN;{bid};NaN;NaN;{bid};"
                     f"{dd} 00:00:00;interfaz;false;{dd};MANUAL")
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write("\n".join(lines) + "\n")
    fh.close()
    return fh.name


def _parse_fx_upload(text: str, filename: str = "") -> dict:
    """Lee un archivo de tipos de cambio y devuelve {PAR: {"bid":x, "ask":y}}.

    Acepta el export de Calypso (`Date;Quote Name;...;Bid;Ask;...` con nombres
    tipo `FX.USD.MXN`) y también un CSV simple de dos o tres columnas:
    `par;spot` o `par;bid;ask`. Es a propósito tolerante: la mesa manda el
    archivo en distintas formas y lo que importa es no fallar en silencio.
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        raise ValueError("El archivo está vacío.")
    delim = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    head = [h.strip().lower() for h in lines[0].split(delim)]
    out = {}

    def as_pair(name):
        n = (name or "").strip().upper().replace("FX.", "").replace(".", "").replace("/", "")
        return n if len(n) == 6 and n.isalpha() else None

    if "quote name" in head:                       # export de Calypso
        iname = head.index("quote name")
        ibid = next((i for i, h in enumerate(head) if h.startswith("bid")), None)
        iask = next((i for i, h in enumerate(head) if h.startswith("ask")), None)
        ilast = next((i for i, h in enumerate(head) if h.startswith("last")), None)
        for line in lines[1:]:
            c = [x.strip() for x in line.split(delim)]
            if len(c) <= iname:
                continue
            pr = as_pair(c[iname])
            if not pr:
                continue
            def num(i):
                try:
                    v = float(c[i].replace(",", ""))
                    return v if v == v else None      # descarta NaN
                except (ValueError, TypeError, IndexError):
                    return None
            b = num(ibid) if ibid is not None else None
            a = num(iask) if iask is not None else None
            if b is None and a is None and ilast is not None:
                b = a = num(ilast)
            if b is None and a is None:
                continue
            out[pr] = {"bid": b if b is not None else a, "ask": a if a is not None else b}
    else:                                          # CSV simple par;spot[;ask]
        start = 0 if as_pair(lines[0].split(delim)[0]) else 1
        for line in lines[start:]:
            c = [x.strip() for x in line.split(delim)]
            pr = as_pair(c[0] if c else "")
            if not pr or len(c) < 2:
                continue
            try:
                b = float(c[1].replace(",", ""))
                a = float(c[2].replace(",", "")) if len(c) > 2 and c[2] else b
            except ValueError:
                continue
            out[pr] = {"bid": b, "ask": a}
    if not out:
        raise ValueError(
            "No encontré ningún par. Se esperaba el export de tipos de cambio de "
            "Calypso (columna `Quote Name` con nombres tipo FX.USD.MXN) o un CSV "
            "simple `par;spot`. " + (f"Archivo: {filename}" if filename else ""))
    return out


def _vol_apply_uploads(cfg, payload):
    """Inyecta en la config lo que se sube desde la interfaz.

    payload["quotes_csv"]  = {PAR: {"bid": "...", "mid": "...", "ask": "..."}}
    payload["fx_spots"]    = {PAR: {"bid": x, "ask": y}}  — tipo de cambio del día

    El texto manda sobre las rutas del YAML, así se puede construir con datos
    nuevos sin escribir nada en el repositorio.
    """
    notes = []
    cargados = {p for p, sides in (payload.get("quotes_csv") or {}).items()
                if any((v or "").strip() for v in (sides or {}).values())}
    if payload.get("strict_uploads"):
        # Sin cotizaciones cargadas no hay superficie. Se quitan los pares sin
        # carga en vez de construirlos con los archivos del repositorio.
        sin = [p for p in list(cfg["surfaces"]) if p not in cargados]
        for p in sin:
            del cfg["surfaces"][p]
        if not cfg["surfaces"]:
            raise ValueError(
                "No hay cotizaciones cargadas. Usa «Cargar cotizaciones (CSV)» para "
                "subir las hojas del día, o «Usar los datos del repositorio» si "
                "quieres trabajar con las del 01/09/2026 que están versionadas.")
    for pair, sides in (payload.get("quotes_csv") or {}).items():
        if pair not in cfg["surfaces"] or not sides:
            continue
        given = {k: v for k, v in sides.items() if (v or "").strip()}
        if not given:
            continue
        cfg["surfaces"][pair] = dict(cfg["surfaces"][pair])
        cfg["surfaces"][pair]["quotes_text"] = given
        # Lo que sube la mesa DEFINE los lados de ese par. Si solo carga el mid,
        # los lados que faltan NO se pueden tomar de los archivos del YAML: serían
        # de otra fecha y de otra fuente, y el bid/ask resultante no tendría nada
        # que ver con el mid recién cargado. Se anulan las rutas y `load_quotes`
        # copia el mid, que es el comportamiento honesto: sin spread cargado, no
        # hay spread.
        cfg["surfaces"][pair]["quotes"] = {}
        falt = [s for s in ("bid", "ask") if s not in given]
        if falt:
            notes.append(
                f"[{pair}] se cargó solo el lado {'/'.join(sorted(given))}: "
                f"{' y '.join(falt)} se igualan al mid (spread cero). Los archivos "
                f"del YAML no se mezclan con una carga manual porque serían de otra "
                f"fecha. Carga las tres hojas si necesitas bid/ask reales.")

    spots = payload.get("fx_spots") or {}
    if spots:
        # Se parte de los spots del archivo del YAML y solo se PISAN los pares
        # que vengan en el payload: así se puede corregir un spot sin perder los
        # otros cinco.
        base, base_err = {}, None
        try:
            base = volcurves.load_fx_spots(
                volorch._path(cfg, cfg["market_data"]["fx_spots_file"]),
                _vol_val_date(cfg))
        except Exception as e:
            base_err = str(e)
        faltan = [p for p in cfg["surfaces"]
                  if p not in base and p.upper() not in {k.upper() for k in spots}]
        if faltan:
            raise ValueError(
                "No tengo spot para " + ", ".join(sorted(faltan)) +
                ". Carga el archivo de tipo de cambio del día o teclea esos spots "
                "antes de construir." + (f" (archivo base: {base_err})" if base_err else ""))
        rows = []
        for pr, v in base.items():
            rows.append((_fx_pair_to_quote_name(pr), v.get("bid"), v.get("ask")))
        for pr, v in spots.items():
            pr = pr.upper()
            if v is None:
                continue
            b = v.get("bid") if isinstance(v, dict) else v
            a = (v.get("ask") if isinstance(v, dict) else v)
            if b is None and a is None:
                continue
            b = float(b if b is not None else a)
            a = float(a if a is not None else b)
            name = _fx_pair_to_quote_name(pr)
            rows = [r for r in rows if r[0] != name]
            rows.append((name, b, a))
        cfg["market_data"] = dict(cfg["market_data"])
        cfg["market_data"]["fx_spots_file"] = _write_tc_csv(rows, _vol_val_date(cfg))
    cfg["_ui_notes"] = notes
    return cfg


@app.post("/vol/fx-spots")
def vol_fx_spots(payload: dict = Body(default={})):
    """Spots vigentes, y parseo del archivo de tipo de cambio que suba la mesa.

    payload: {csv?: "...", filename?: "..."}  — sin `csv` devuelve los del YAML.
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        cfg = _vol_config()
        if payload.get("valuation_date"):
            cfg["valuation_date"] = str(payload["valuation_date"])[:10]
        current, parsed, err = {}, None, None
        try:
            cur = volcurves.load_fx_spots(
                volorch._path(cfg, cfg["market_data"]["fx_spots_file"]),
                _vol_val_date(cfg))
            current = {k: {"bid": v.get("bid"), "mid": v.get("mid"), "ask": v.get("ask")}
                       for k, v in cur.items()}
        except Exception as e:
            err = f"No pude leer el archivo de TC del YAML: {e}"
        if (payload.get("csv") or "").strip():
            try:
                parsed = _parse_fx_upload(payload["csv"], payload.get("filename", ""))
            except Exception as e:
                err = str(e)
        pairs = list(cfg["surfaces"].keys())
        needed = {p: {"base": cfg["surfaces"][p]["base_ccy"],
                      "quote": cfg["surfaces"][p]["quote_ccy"]} for p in pairs}
        return JSONResponse({"ok": True, "pairs": pairs, "needed": needed,
                             "current": current, "parsed": parsed, "error": err,
                             "source": str(cfg["market_data"]["fx_spots_file"])})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


@app.post("/vol/validate-quotes")
def vol_validate_quotes(payload: dict = Body(...)):
    """Parsea y valida un CSV de cotizaciones ANTES de construir.

    Devuelve el reporte que la interfaz muestra: cuántos tenores se leyeron por
    lado, si los lados son combinables, y los chequeos de sanidad (varianza
    total creciente, convexidad, coherencia de Cal Days con las fechas). Es el
    equivalente al reporte de "N/M quotes emparejados" de la pestaña de curvas.

    payload = {pair, quotes: {bid?, mid, ask?}}
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        import datetime as _d
        pair = payload["pair"]
        cfg = _vol_config()
        spec = cfg["surfaces"][pair]
        params = volql.load_parameters(volorch._path(cfg, spec["parameters"]))
        vdc = (params.get("Volatility Day Count") or "").strip() or               spec.get("vol_day_count_fallback", "ACT/365")
        val = cfg["valuation_date"]
        if isinstance(val, str):
            val = _d.date.fromisoformat(val[:10])
        if payload.get("valuation_date"):
            val = _d.date.fromisoformat(str(payload["valuation_date"])[:10])

        texts = {k: v for k, v in (payload.get("quotes") or {}).items()
                 if (v or "").strip()}
        sides = volql.load_quotes(texts_by_side=texts)          # ya valida grillas
        quotes = sides["mid"]
        warns = volql.validate_quotes(quotes, pair, vdc, val)
        if spec.get("underlyings"):
            unds = volql.load_underlyings(volorch._path(cfg, spec["underlyings"]))
            warns += volql.validate_underlyings(unds, quotes, pair)

        rows = [{"tenor": q["tenor"], "term_raw": q["term_raw"],
                 "expiry": q["expiry"].isoformat(), "cal_days": q["cal_days"],
                 "atm": q["atm"] * 100, "rr25": q["rr25"] * 100,
                 "rr10": q["rr10"] * 100, "bf25": q["bf25"] * 100,
                 "bf10": q["bf10"] * 100, "trade_days": q.get("trade_days")}
                for t in volorch.dt.sorted_tenors(list(quotes)) for q in [quotes[t]]]
        return JSONResponse({
            "ok": True, "pair": pair,
            "sides_loaded": sorted(texts),
            "n_tenors": {k: len(v) for k, v in
                         {s: sides[s] for s in ("bid", "mid", "ask")}.items()},
            "valuation_date": val.isoformat(), "quotes": rows, "warnings": warns})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/vol/query")
def vol_query(payload: dict = Body(...)):
    """LA consulta que justifica la superficie: dada una EXPIRACIÓN y un STRIKE,
    devolver la volatilidad implícita que le corresponde.

    Es lo que necesita el Módulo 3 para valorizar una opción: sin maturity y
    strike no hay vol que buscar. Devuelve además el forward, el delta y los
    tenores que enmarcan la consulta, para que se vea de dónde sale el número.

    payload = {pair, expiry: 'YYYY-MM-DD', strike: float}
              o {pair, expiry, call_delta: 0..100}
    """
    if volorch is None:
        return JSONResponse(_vol_unavailable())
    try:
        import datetime as _d
        pair = payload["pair"]
        expiry = _d.date.fromisoformat(str(payload["expiry"])[:10])
        cfg = _vol_config()
        if payload.get("valuation_date"):
            cfg["valuation_date"] = str(payload["valuation_date"])[:10]
        cfg = _vol_apply_uploads(cfg, payload)
        cfg["surfaces"] = {pair: cfg["surfaces"][pair]}
        vs, _w = volorch.build_bid_mid_ask(cfg, verbose=False)

        mid = vs.sides["mid"][pair]
        if expiry <= mid.valuation_date:
            return JSONResponse({"ok": False, "error":
                f"La expiración {expiry} no es posterior a la fecha de "
                f"valuación {mid.valuation_date}."})

        from vollib import dates as voldates, deltas as voldl
        # La fecha de entrega la resuelve la superficie, que ya conoce el
        # calendario de las plazas del par (campo `Holidays` de Calypso).
        delivery = mid.delivery_for(expiry)
        tau = voldates.year_fraction(mid.vol_day_count, mid.valuation_date, expiry)

        out = {}
        for side in ("bid", "mid", "ask"):
            s = vs.sides[side][pair]
            sl = s.slice_at(expiry)            # smile reconstruido a esa fecha
            F, dff, conv = sl.forward, sl.df_for, sl.conv
            if payload.get("strike") is not None:
                K = float(payload["strike"])
                vol = sl.vol_at_strike(K)
            else:
                # El delta que pide el usuario es el COTIZADO (ajustado por prima).
                # `strike_at_delta` lo resuelve contra el smile; no se puede pasar
                # ese numero directo al eje del interpolador, que es delta PLANO.
                d = float(payload["call_delta"])
                K, vol = sl.strike_at_delta(min(abs(d), 100.0 - abs(d)) / 100.0,
                                            "C" if d <= 50.0 else "P")
            out[side] = {"vol_pct": vol * 100.0, "strike": K, "forward": F,
                         "call_delta": 100.0 * voldl.call_delta(F, K, vol, tau, dff, conv),
                         "axis_delta": 100.0 * voldl.call_delta(F, K, vol, tau, dff,
                                                                sl.axis_conv),
                         "moneyness": K / F}

        # tenores que enmarcan la consulta: de ahí sale la interpolación en plazo
        lo = hi = None
        for sl in mid.slices:
            if sl.expiry <= expiry:
                lo = sl
            if sl.expiry >= expiry and hi is None:
                hi = sl
        return JSONResponse({
            "ok": True, "pair": pair, "expiry": expiry.isoformat(),
            "delivery": delivery.isoformat(), "tau": tau,
            "valuation_date": mid.valuation_date.isoformat(),
            "delta_convention": mid.conv_for(expiry).label(),
            "vol_day_count": mid.vol_day_count,
            "bracket": {"lower": (lo.tenor if lo else None),
                        "lower_expiry": (lo.expiry.isoformat() if lo else None),
                        "upper": (hi.tenor if hi else None),
                        "upper_expiry": (hi.expiry.isoformat() if hi else None),
                        "extrapolated": (lo is None or hi is None)},
            "sides": out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e),
                             "trace": traceback.format_exc().splitlines()[-6:]})


@app.post("/vol/export-csv", response_class=PlainTextResponse)
def vol_export_csv(payload: dict = Body(...)):
    """Tabla de una superficie como CSV descargable. payload = {pair, overrides?}"""
    if volorch is None:
        return PlainTextResponse("Módulo 2 no disponible: " + str(_VOL_ERR), status_code=503)
    pair = payload["pair"]
    cfg = _vol_apply_uploads(_vol_config(), payload)
    for p, ov in (payload.get("overrides") or {}).items():
        if p in cfg["surfaces"]:
            cfg["surfaces"][p]["overrides"] = ov
    vs, _ = volorch.build_bid_mid_ask(cfg, verbose=False)
    buf = io.StringIO()
    buf.write("Tenor,Expiry,Point,Delta,Strike Bid,Strike Mid,Strike Ask,"
              "Vol Bid,Vol Mid,Vol Ask\n")
    for r in vs.table(pair):
        def f(v, n=8):
            return "" if v is None else f"{v:.{n}f}"
        buf.write(f"{r['tenor']},{r['expiry'].isoformat()},{r['point']},"
                  f"{f(r['delta_mid'],4)},{f(r['strike_bid'])},{f(r['strike_mid'])},"
                  f"{f(r['strike_ask'])},{f(r['vol_bid'],5)},{f(r['vol_mid'],5)},"
                  f"{f(r['vol_ask'],5)}\n")
    return PlainTextResponse(buf.getvalue(),
                             headers={"Content-Disposition":
                                      f'attachment; filename="{pair}_surface.csv"'})


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  curvelib + vollib — curvas y superficies de volatilidad")
    print("  Abre:  http://127.0.0.1:8000")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
