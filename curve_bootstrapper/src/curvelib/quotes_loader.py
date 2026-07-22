"""
quotes_loader.py — Carga la hoja de cotizaciones (formato pantalla, captura 1).

Separa CONVENCIÓN (YAML, estable) de PRECIO (hoja de quotes, diario):
la hoja solo trae los valores bid/mid/ask por instrumento; el loader los
inyecta en los instrumentos ya definidos en el YAML, emparejando por
(tipo, tenor, curva).

Formato de la hoja (CSV con encabezados):

    Quote Name,Type,BID,MID,ASK
    MM.USD.SOFR.ON.LIBOR01,Yield,3.66,3.66,3.66
    Swap.1M.USD.SOFR.1D/1Y.LIBOR01,Yield,3.6549,3.6595,3.6641
    Swap.5Y.USD.SOFR.1D/1Y.LIBOR01,Yield,3.9358,3.9393,3.9427
    ...

Reglas de parseo del 'Quote Name' (prefijo -> tipo de instrumento):

    MM       -> mm            (depósito / money-market, incl. tenor ON)
    Swap     -> ois_swap / ibor_swap   (según el índice de la curva destino)
    FRA      -> fra
    Fut      -> future
    FX/Fwd   -> fx_forward
    XCCY     -> xccy_basis / xccy_fixed_float

El emparejamiento con el YAML se hace por (curva_destino, tipo, tenor).
La curva destino se deduce del mapa `curve_map` que le pasas (índice ->
nombre de curva en el YAML), o se puede inferir por convención de nombres.

Los valores de la hoja se interpretan en % (3.9393 -> 0.039393) salvo que
pases rate_scale=1.0.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional, Tuple

# prefijo del Quote Name -> tipo de instrumento del YAML
_PREFIX_TO_TYPE = {
    "MM": "mm",
    "DEPO": "mm",
    "SWAP": "ois_swap",     # se ajusta a ibor_swap si la curva es IBOR
    "IRS": "ois_swap",
    "FRA": "fra",
    "FUT": "future",
    "FUTURE": "future",
    "FX": "fx_forward",
    "FWD": "fx_forward",
    "XCCY": "xccy_basis",
    "BASIS": "xccy_basis",
}


def parse_quote_name(name: str) -> Dict[str, Optional[str]]:
    """Descompone 'Swap.5Y.USD.SOFR.1D/1Y.LIBOR01' en sus partes.
    Devuelve {prefix, type, tenor, ccy, index, raw}."""
    parts = name.strip().split(".")
    prefix = parts[0].upper() if parts else ""
    itype = _PREFIX_TO_TYPE.get(prefix)
    tenor = ccy = index = None
    if prefix in ("MM", "DEPO"):
        # MM.USD.SOFR.ON.LIBOR01  -> ccy=USD, index=SOFR, tenor=ON
        ccy = parts[1] if len(parts) > 1 else None
        index = parts[2] if len(parts) > 2 else None
        tenor = parts[3] if len(parts) > 3 else "ON"
    else:
        # Swap.5Y.USD.SOFR...  -> tenor=5Y, ccy=USD, index=SOFR
        tenor = parts[1] if len(parts) > 1 else None
        ccy = parts[2] if len(parts) > 2 else None
        index = parts[3] if len(parts) > 3 else None
    return {"prefix": prefix, "type": itype, "tenor": tenor,
            "ccy": ccy, "index": index, "raw": name.strip()}


def parse_quotes_csv(text: str, rate_scale: float = 0.01) -> List[dict]:
    """Lee el CSV de quotes y devuelve una lista de registros:
    [{name, prefix, type, tenor, ccy, index, bid, mid, ask}]."""
    rows: List[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    # normaliza encabezados a minúsculas sin espacios
    field_map = {h: h.strip().lower() for h in (reader.fieldnames or [])}
    for raw in reader:
        row = {field_map.get(k, k): v for k, v in raw.items()}
        name = row.get("quote name") or row.get("quote_name") or row.get("name")
        if not name:
            continue
        parsed = parse_quote_name(name)

        def num(key):
            v = row.get(key, "")
            if v is None or str(v).strip() == "":
                return None
            return float(v) * rate_scale

        bid, mid, ask = num("bid"), num("mid"), num("ask")
        if mid is None:
            mid = bid if bid is not None else ask
        parsed.update({"bid": bid, "mid": mid, "ask": ask})
        rows.append(parsed)
    return rows


def apply_quotes_sheet(
    config: dict,
    quotes_text: str,
    curve_map: Optional[Dict[str, str]] = None,
    rate_scale: float = 0.01,
    strict: bool = False,
) -> Tuple[dict, List[str]]:
    """Inyecta los bid/mid/ask de la hoja en el `config` (YAML ya cargado).

    curve_map: {index -> nombre_de_curva_YAML}. Ej: {'SOFR': 'USD_SOFR'}.
               Si es None, intenta emparejar por el índice contra los nombres
               de curva (heurística).
    strict:    si True, lanza error cuando un quote de la hoja no encuentra
               instrumento destino. Si False, lo acumula en la lista de avisos.

    Devuelve (config_modificado, avisos).
    Modifica los quotes de los instrumentos a la forma dict:
        quote: {bid: .., mid: .., ask: ..}
    """
    records = parse_quotes_csv(quotes_text, rate_scale=rate_scale)
    warnings: List[str] = []

    def find_curve(rec) -> Optional[str]:
        if curve_map and rec["index"] in curve_map:
            return curve_map[rec["index"]]
        # heurística: busca una curva cuyo nombre contenga ccy e index
        for cname in config["curves"]:
            up = cname.upper()
            if rec["ccy"] and rec["index"] and \
               rec["ccy"] in up and rec["index"] in up:
                return cname
        # fallback: solo por índice
        for cname in config["curves"]:
            if rec["index"] and rec["index"] in cname.upper():
                return cname
        return None

    matched = 0
    for rec in records:
        cname = find_curve(rec)
        if not cname:
            msg = f"Sin curva destino para quote '{rec['raw']}'"
            if strict:
                raise KeyError(msg)
            warnings.append(msg)
            continue
        insts = config["curves"][cname].get("instruments", [])
        # empareja por tenor; el tipo puede diferir (swap->ois/ibor), así que
        # priorizamos coincidencia de tenor y, si hay varias, también de tipo.
        def norm_tenor(v):
            # YAML puede convertir 'ON'/'TN' en booleanos si van sin comillas
            if v is True:
                return "ON"
            if v is False:
                return "OFF"
            return str(v).upper().strip()
        target = None
        for ins in insts:
            if norm_tenor(ins.get("tenor", "")) == norm_tenor(rec["tenor"]):
                target = ins
                break
        if target is None:
            msg = (f"'{rec['raw']}' (tenor {rec['tenor']}) no coincide con "
                   f"ningún instrumento de {cname}")
            if strict:
                raise KeyError(msg)
            warnings.append(msg)
            continue
        target["quote"] = {"bid": rec["bid"], "mid": rec["mid"], "ask": rec["ask"]}
        matched += 1

    warnings.insert(0, f"{matched}/{len(records)} quotes emparejados con éxito")
    return config, warnings
