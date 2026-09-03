"""
Lectura de un export de valorización de Calypso.

CADA PRODUCTO TIENE SU PROPIO EXPORT Y SU PROPIO LECTOR. El reporte de opciones
y el de forwards no comparten columnas: un forward no tiene strike ni
volatilidad implícita, tiene tasa pactada y fecha de liquidación. Por eso el
mapeo de columnas vive en cada producto (`products/*.COLUMNAS`) y este módulo
solo se encarga de lo común: abrir el archivo, elegir el lector y leer las
columnas de RESULTADO, que sí son las mismas en los dos reportes porque son las
que Calypso publica para cualquier producto.

Se carga un archivo por producto. Cuando se pide un producto explícito, las
filas que no le corresponden se cuentan y se reportan, en vez de mezclarse: un
export de forwards cargado en la pestaña de opciones tiene que decirlo, no
devolver cero operaciones sin explicación.

CONVENCIONES DE SIGNO Y MONEDA, verificadas contra el reporte de opciones
------------------------------------------------------------------------
- `Quantity` viene FIRMADA: la venta es negativa. El PV hereda ese signo, así
  que el motor no vuelve a aplicar el lado.
- `PV` está en la moneda de la PATA COTIZADA del par, no en `Payout Ccy`: para
  USD/PEN está en PEN aunque `Payout Ccy` diga USD. Verificado con el cociente
  PV / `PV [USD]`, que vale 3.365 en las 820 filas de USD/PEN y 1 en el resto.
- `DELTA` y `GAMMA` van siempre en DIVISA BASE; PV, VEGA, THETA, RHO y RHO2 en
  la moneda del reporte.
- El PV NO incluye la prima pendiente de pago.
"""
from __future__ import annotations

import csv
import datetime as _dt
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .products import POR_CLAVE, REGISTRO, identificar
from .products.base import num, valor

#: Moneda cotizada de cada par soportado.
QUOTE_CCY = {"USD/PEN": "PEN", "USD/MXN": "MXN", "USD/BRL": "BRL",
             "USD/CLP": "CLP", "USD/COP": "COP", "EUR/USD": "USD"}

#: Columnas de RESULTADO. Son las mismas en todos los exports de Calypso.
RESULTADOS = {
    "pv":    ("PV", "NPV"),
    "delta": ("DELTA",),
    "gamma": ("GAMMA",),
    "vega":  ("VEGA",),
    "theta": ("THETA",),
    "rho":   ("RHO",),
    "rho2":  ("RHO2",),
}
#: Insumos del pricer de Calypso, que alimentan el escenario de comparación 1.
INSUMOS = {
    "vol":        ("IMPLIEDVOLATILITY", "Implied Volatility", "Vol"),
    "rate_base":  ("Pricer_PrimDepoRt", "Prim Depo Rate"),
    "rate_quote": ("Pricer_SecDepoRt", "Sec Depo Rate"),
    "fwd_delta":  ("FWD_DELTA_PCT", "Fwd Delta Pct"),
}


@dataclass
class Trade:
    """La operación, sin nada del reporte de resultados."""
    trade_id: str
    pair: str
    call: bool                # True = call sobre la divisa base; en un forward,
                              # compra de la base (el signo lo pone la cantidad)
    quantity: float           # FIRMADA, en divisa base
    strike: float             # moneda cotizada por unidad de base
    expiry: _dt.date
    delivery: _dt.date
    payout_ccy: str = ""
    bundle: str = ""
    bundle_type: str = ""


@dataclass
class Row:
    opt: Trade
    producto: object
    vol_calypso: Optional[float]
    rate_base: Optional[float]
    rate_quote: Optional[float]
    fwd_delta_pct: Optional[float]
    pv: Optional[float]
    pv_usd: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    vega: Optional[float]
    theta: Optional[float]
    rho: Optional[float]
    rho2: Optional[float]
    counterparty: str = ""
    book: str = ""
    crudo: dict = field(default_factory=dict, repr=False)


def sniff_delimiter(path: str) -> str:
    with open(path, encoding="utf-8-sig") as fh:
        head = fh.readline()
    return ";" if head.count(";") > head.count(",") else ","


def load(path: str, producto: Optional[str] = None,
         pairs: Optional[List[str]] = None):
    """Lee el export con el lector del producto pedido.

    `producto` es la clave del registro ("FXOPT_VANILLA", "FXFWD"). Si es None,
    cada fila se despacha al producto que la reconoce — útil desde la línea de
    comandos, pero la interfaz siempre pide uno explícito, porque un archivo es
    un producto.

    Devuelve (filas, diagnostico). El diagnóstico explica CADA fila que quedó
    fuera y por qué; sin eso un total que no cuadra no se puede investigar.
    """
    prod = POR_CLAVE.get(producto) if producto else None
    filas: List[Row] = []
    otros = Counter()          # filas de otro producto
    faltantes = Counter()      # columnas obligatorias ausentes
    ilegibles = 0
    total = 0
    cabeceras: List[str] = []

    with open(path, encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh, delimiter=sniff_delimiter(path))
        cabeceras = list(rd.fieldnames or [])
        for r in rd:
            total += 1
            p = prod
            if p is None:
                p = identificar(r)
                if p is None:
                    ilegibles += 1
                    continue
            elif not p.reconoce(r):
                otro = identificar(r)
                otros[otro.etiqueta if otro else "producto no reconocido"] += 1
                continue
            if pairs and (valor(r, ("Ccy Pair", "Currency Pair")) or "") not in pairs:
                continue
            trade, faltan = p.leer(r)
            if trade is None:
                for c in faltan:
                    faltantes[c] += 1
                continue
            v = num(r, INSUMOS["vol"])
            rb = num(r, INSUMOS["rate_base"])
            rq = num(r, INSUMOS["rate_quote"])
            filas.append(Row(
                opt=trade, producto=p,
                vol_calypso=None if v is None else v / 100.0,
                rate_base=None if rb is None else rb / 100.0,
                rate_quote=None if rq is None else rq / 100.0,
                fwd_delta_pct=num(r, INSUMOS["fwd_delta"]),
                pv=num(r, RESULTADOS["pv"]),
                pv_usd=num(r, ("PV [USD]", "NPV [USD]")),
                delta=num(r, RESULTADOS["delta"]), gamma=num(r, RESULTADOS["gamma"]),
                vega=num(r, RESULTADOS["vega"]), theta=num(r, RESULTADOS["theta"]),
                rho=num(r, RESULTADOS["rho"]), rho2=num(r, RESULTADOS["rho2"]),
                counterparty=valor(r, ("CounterParty_Full Name", "Counterparty")) or "",
                book=valor(r, ("Book",)) or "", crudo=r))

    diag = {"total": total, "leidas": len(filas), "cabeceras": cabeceras,
            "otros_productos": dict(otros), "columnas_faltantes": dict(faltantes),
            "no_reconocidas": ilegibles, "mensajes": []}
    if prod is not None and otros:
        det = ", ".join(f"{v} de {k}" for k, v in otros.items())
        diag["mensajes"].append(
            f"El archivo trae {sum(otros.values())} filas que no son "
            f"{prod.etiqueta.lower()} ({det}). Cárgalas en su propia pestaña: "
            f"cada producto tiene su export y su lector.")
    if faltantes:
        det = ", ".join(f"{k} ({v})" for k, v in faltantes.items())
        p = prod or (REGISTRO[0] if REGISTRO else None)
        diag["mensajes"].append(
            f"Faltan columnas obligatorias: {det}. "
            + (f"Este producto espera {', '.join(p.columnas_visibles())}. "
               f"Si tu export las llama distinto, dime cómo y las agrego como "
               f"alias." if p else ""))
    if ilegibles:
        diag["mensajes"].append(
            f"{ilegibles} filas no corresponden a ningún producto soportado.")
    return filas, diag
