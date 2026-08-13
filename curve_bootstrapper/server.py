"""
server.py — Microservicio FastAPI que conecta la interfaz HTML con el motor.

Expone el pipeline de bootstrapping por HTTP para que el navegador pueda:
  1. cargar la configuración por defecto (GET /config)
  2. aplicar una hoja de quotes a una config (POST /apply-quotes)
  3. construir las 28 curvas bid/mid/ask y devolver las tablas (POST /build)
  4. descargar la tabla de una curva como CSV (POST /export-csv)

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

app = FastAPI(title="curvelib bootstrapping API", version="0.4.0")


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


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  curvelib — servidor de bootstrapping")
    print("  Abre:  http://127.0.0.1:8000")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
