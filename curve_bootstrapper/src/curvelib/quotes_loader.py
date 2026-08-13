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
    Bond     -> sovereign_bond  (también BONO / GOVT)

BONOS — dos diferencias respecto de los demás instrumentos:

  1. Se identifican por VENCIMIENTO, no por tenor. El nombre puede traer la
     fecha en ISO o en formato Bloomberg, en cualquier posición:
         Bond.PEN.TIBO.2029-02-12,Price,105.857,105.954,106.051
         Bono.PERUGB.02/12/29,Price,105.857,105.954,106.051
     También se acepta una columna 'Maturity' explícita, o emparejar por el
     campo 'ticker' del instrumento en el YAML.

  2. Su quote es un PRECIO y NUNCA se multiplica por rate_scale. Si se
     escalara, 105.954 se volvería 1.05954 y la curva saldría absurda sin
     que nada falle a la vista. Se detecta por el prefijo del nombre o por
     la columna 'Type' (Price / Precio / Clean / Dirty).

El emparejamiento con el YAML se hace por (curva_destino, tipo, tenor).
La curva destino se deduce del mapa `curve_map` que le pasas (índice ->
nombre de curva en el YAML), o se puede inferir por convención de nombres.

Los valores de la hoja se interpretan en % (3.9393 -> 0.039393) salvo que
pases rate_scale=1.0.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import re as _re
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
    "BOND": "sovereign_bond",
    "BONO": "sovereign_bond",
    "GOVT": "sovereign_bond",
}

# Tipos cuyo quote es un PRECIO, no una tasa: nunca se les aplica rate_scale.
# Es el error clásico al mezclar bonos con swaps en la misma hoja: un precio
# de 105.954 escalado por 0.01 se convierte en 1.05954 y el bootstrap falla
# (o peor, converge a una curva absurda sin avisar).
_PRICE_TYPES = {"sovereign_bond"}

# Valores de la columna 'Type' de la hoja que indican precio.
_PRICE_MARKERS = {"PRICE", "PRECIO", "CLEAN", "DIRTY", "PX", "CLEAN_PRICE", "DIRTY_PRICE"}

_ISO_DATE = _re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_DATE = _re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


def _parse_date_part(part: str):
    """Reconoce una fecha de vencimiento dentro del Quote Name.
    Acepta ISO (2029-02-12) y formato Bloomberg (02/12/29 = 12-feb-2029)."""
    part = part.strip()
    m = _ISO_DATE.match(part)
    if m:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _US_DATE.match(part)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yy < 100:
            yy += 2000 if yy < 70 else 1900
        try:
            return _dt.date(yy, mm, dd)
        except ValueError:
            return None
    return None


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

    # Un bono no se identifica por tenor sino por VENCIMIENTO: se busca una
    # fecha en cualquier posición del nombre (Bond.PEN.TIBO.2029-02-12 o
    # Bond.PERUGB.02/12/29). Si aparece, manda sobre el tenor.
    maturity = None
    for part in parts[1:]:
        d = _parse_date_part(part)
        if d is not None:
            maturity = d
            break
    if maturity is not None and itype is None:
        itype = "sovereign_bond"

    return {"prefix": prefix, "type": itype, "tenor": tenor, "maturity": maturity,
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

        # ¿Este quote es un PRECIO? Dos señales independientes: el tipo
        # deducido del prefijo, y la columna 'Type' de la hoja. Cualquiera
        # de las dos basta para NO aplicar rate_scale.
        col_type = str(row.get("type", "") or "").strip().upper()
        is_price = (parsed.get("type") in _PRICE_TYPES) or (col_type in _PRICE_MARKERS)
        if is_price and parsed.get("type") is None:
            parsed["type"] = "sovereign_bond"
        scale = 1.0 if is_price else rate_scale
        parsed["is_price"] = is_price

        # Vencimiento explícito en columna, si la hoja lo trae
        if parsed.get("maturity") is None:
            for key in ("maturity", "vencimiento", "maturity_date"):
                raw_m = row.get(key)
                if raw_m:
                    parsed["maturity"] = _parse_date_part(str(raw_m))
                    break

        def num(key, _scale=scale):
            v = row.get(key, "")
            if v is None or str(v).strip() == "":
                return None
            return float(v) * _scale

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
        """Encuentra la curva destino de un quote.

        Precedencia:
          1. curve_map explícito por índice o ccy
          2. Heurística nombre de curva: requiere ccy E index
             (o solo ccy para bonos donde index es la fecha)
          3. Comodín None en curve_map
        """
        # 1a) curve_map por índice
        if curve_map:
            if rec["index"] in curve_map:
                return curve_map[rec["index"]]
            # 1b) curve_map por ccy
            if rec["ccy"] and rec["ccy"] in curve_map:
                return curve_map[rec["ccy"]]

        # 2) heurística sobre el nombre de la curva.
        #    Requiere coincidencia EXACTA de ambos tokens en el nombre de la
        #    curva (p.ej. "PEN" Y "TIBO" en "PEN_OIS_TIBO"). Evita falsos
        #    positivos como que "TIBO" matchee "STIBOR".
        ccy = (rec["ccy"] or "").upper()
        idx = (rec["index"] or "").upper()
        has_mat = rec.get("maturity") is not None

        def tokens(cname):
            # tokeniza el nombre de curva: PEN_OIS_TIBO -> {PEN, OIS, TIBO}
            return set(cname.upper().replace("-", "_").split("_"))

        if ccy and idx and not has_mat:
            # swap / MM: ambos tokens deben aparecer
            for cname in config["curves"]:
                t = tokens(cname)
                if ccy in t and idx in t:
                    return cname

        if ccy and (has_mat or not idx):
            # bono o nombre sin índice: basta con la ccy como token exacto
            for cname in config["curves"]:
                if ccy in tokens(cname):
                    return cname

        if idx and not has_mat:
            # fallback: solo índice como token exacto
            for cname in config["curves"]:
                if idx in tokens(cname):
                    return cname

        # 3) comodín None
        if curve_map and None in curve_map:
            return curve_map[None]
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
        def norm_maturity(v):
            """El YAML puede entregar la fecha ya parseada (date) o como str."""
            if v is None:
                return None
            if isinstance(v, _dt.datetime):
                return v.date()
            if isinstance(v, _dt.date):
                return v
            return _parse_date_part(str(v)[:10])

        target = None
        if rec.get("maturity") is not None:
            # BONOS: el identificador es el VENCIMIENTO, no el tenor.
            for ins in insts:
                if norm_maturity(ins.get("maturity")) == rec["maturity"]:
                    target = ins
                    break
            # respaldo: emparejar por ticker si la hoja lo trae en el nombre
            if target is None:
                raw_up = rec["raw"].upper()
                for ins in insts:
                    tk = str(ins.get("ticker", "") or "").upper()
                    if tk and tk.replace(" ", "") in raw_up.replace(" ", ""):
                        target = ins
                        break
        else:
            for ins in insts:
                if norm_tenor(ins.get("tenor", "")) == norm_tenor(rec["tenor"]):
                    target = ins
                    break
        if target is None:
            ident = (f"vencimiento {rec['maturity']}" if rec.get("maturity")
                     else f"tenor {rec['tenor']}")
            msg = (f"'{rec['raw']}' ({ident}) no coincide con "
                   f"ningún instrumento de {cname}")
            if strict:
                raise KeyError(msg)
            warnings.append(msg)
            continue
        target["quote"] = {"bid": rec["bid"], "mid": rec["mid"], "ask": rec["ask"]}
        matched += 1

    warnings.insert(0, f"{matched}/{len(records)} quotes emparejados con éxito")
    return config, warnings
