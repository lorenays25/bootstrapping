"""
Las tres comparaciones del Módulo 3, con detalle por operación.

Cada comparación es la MISMA valorización con un feed distinto, y esa es toda la
diferencia entre ellas. Por eso la resta entre dos comparaciones atribuye:

  1. PRICER AISLADO — factores de Calypso. Mide solo la fórmula.
  2. CURVAS         — curvas propias, volatilidad de Calypso.
                      La diferencia contra la 1 es el aporte de las curvas.
  3. CADENA COMPLETA— curvas y superficie propias.
                      La diferencia contra la 2 es el aporte de la superficie.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Dict, List, Optional

from .feeds import FactorFeed
from .products import TODAS

CAMPOS = TODAS
#: Griegas que Calypso reporta en la DIVISA BASE; el resto en la moneda del reporte.
EN_BASE = ("delta", "gamma")

#: Umbrales de materialidad. No son cosmética: una opción muy fuera del dinero
#: tiene delta de tres unidades sobre un nocional de diez millones, y una
#: diferencia en el sexto decimal da un error relativo de miles por ciento que
#: domina cualquier promedio sin representar riesgo alguno.
MIN_PV = 1000.0
MIN_DELTA_PCT = 0.02      # como fracción del nocional
MIN_GRIEGA = 10.0


def reporting_factor(rows) -> Dict[str, float]:
    """PV / PV [USD] por par. Vale 1 cuando el reporte ya está en USD y vale el
    spot cuando está en la moneda cotizada — el caso de USD/PEN."""
    acc = defaultdict(list)
    for r in rows:
        if r.pv and r.pv_usd and abs(r.pv_usd) > 1e-6:
            acc[r.opt.pair].append(r.pv / r.pv_usd)
    out = {}
    for p, v in acc.items():
        v = sorted(v)
        out[p] = v[len(v)//2] if len(v) % 2 else 0.5*(v[len(v)//2-1]+v[len(v)//2])
    return out


def run(rows, feed: FactorFeed) -> List[dict]:
    """Valoriza el portafolio con un feed y devuelve una fila por operación."""
    rep = reporting_factor(rows)
    out = []
    for r in rows:
        f = feed.factores(r)
        if f is None:
            continue
        prod = r.producto
        g = prod.greeks(r.opt, f)
        o = r.opt
        conv = f.spot / rep.get(o.pair, 1.0)      # cotizada -> moneda del reporte
        fila = {"trade_id": o.trade_id, "pair": o.pair,
                "producto": prod.etiqueta, "clave_producto": prod.clave,
                "tipo": prod.tipo_texto(o),
                "lado": "Compra" if o.quantity > 0 else "Venta",
                "cantidad": o.quantity, "strike": o.strike,
                "expiry": o.expiry.isoformat(), "delivery": o.delivery.isoformat(),
                "dias": int(round(f.tau * 365)),
                "vol_propia": None if f.vol is None else f.vol * 100.0,
                "vol_calypso": None if r.vol_calypso is None else r.vol_calypso * 100.0,
                "forward": f.forward, "spot": f.spot,
                "fuente_vol": f.fuente.get("vol", ""),
                "fuente_curvas": f.fuente.get("curvas", ""),
                "bundle": o.bundle, "bundle_type": o.bundle_type,
                "contraparte": r.counterparty, "book": r.book}
        for k in CAMPOS:
            aplica = k in prod.griegas
            mio = (g[k] if k in EN_BASE else g[k] / conv) if aplica else None
            cal = getattr(r, k)
            fila[k] = mio
            fila[k + "_calypso"] = cal
            fila[k + "_dif"] = None if (cal is None or mio is None) else mio - cal
            fila[k + "_dif_pct"] = (None if (not cal or mio is None)
                                    else (mio - cal) / abs(cal) * 100.0)
        out.append(fila)
    return out


def _material(fila: dict, k: str) -> bool:
    cal = fila.get(k + "_calypso")
    if cal is None or cal == 0 or fila.get(k) is None:
        return False
    if k in EN_BASE:
        return abs(cal) / (abs(fila["cantidad"]) or 1.0) >= MIN_DELTA_PCT
    if k == "pv":
        return abs(cal) >= MIN_PV
    return abs(cal) >= MIN_GRIEGA


def resumen(filas: List[dict]) -> List[dict]:
    """Estadística de error por par y griega, sobre operaciones materiales."""
    acc = defaultdict(lambda: defaultdict(list))
    for f in filas:
        for k in CAMPOS:
            if _material(f, k):
                acc[f["pair"]][k].append(abs(f[k + "_dif"]) / abs(f[k + "_calypso"]) * 100.0)
    out = []
    for pair in sorted(acc):
        fila = {"pair": pair}
        for k in CAMPOS:
            v = sorted(acc[pair][k])
            if not v:
                fila[k] = None
                continue
            nn = len(v)
            fila[k] = {"n": nn, "mediana": v[nn // 2],
                       "p90": v[min(nn - 1, int(0.90 * nn))], "max": v[-1]}
        out.append(fila)
    return out
