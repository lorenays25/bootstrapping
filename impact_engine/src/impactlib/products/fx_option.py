"""
Opción FX europea (vanilla). Garman-Kohlhagen sobre el forward.

EXPORT QUE ESPERA: el reporte de valorización de opciones de Calypso. Las
columnas están verificadas contra el archivo del 01/09/2026 —973 filas, 70
columnas— así que los alias primeros son los nombres reales de ese export.
"""
from __future__ import annotations

import math

from ..core.market import Factors
from ..core.numerics import N
from .base import Product, fecha, num, valor


def premium(F: float, K: float, tau: float, vol: float, df_q: float,
            call: bool) -> float:
    """Prima por unidad de nocional, en MONEDA COTIZADA."""
    if tau <= 0.0 or vol is None or vol <= 0.0 or F <= 0.0 or K <= 0.0:
        return df_q * (max(F - K, 0.0) if call else max(K - F, 0.0))
    st = vol * math.sqrt(tau)
    d1 = (math.log(F / K) + 0.5 * st * st) / st
    d2 = d1 - st
    if call:
        return df_q * (F * N(d1) - K * N(d2))
    return df_q * (K * N(-d2) - F * N(-d1))


class FXOptionVanilla(Product):
    clave = "FXOPT_VANILLA"
    etiqueta = "Opciones FX"
    espera = ("Reporte de valorización de opciones. Necesita el strike, el "
              "vencimiento, la entrega y el lado call/put de cada operación.")
    griegas = ("pv", "delta", "gamma", "vega", "theta", "rho", "rho2")

    COLUMNAS = {
        "trade_id":  ("Trade Id", "TradeId", "Trade"),
        "pair":      ("Ccy Pair", "Currency Pair", "Par"),
        "call_put":  ("Put/Call", "Call/Put", "CallPut", "Tipo"),
        "quantity":  ("Quantity", "Principal Amount", "Nominal", "Cantidad"),
        "strike":    ("Strike", "Strike Price"),
        "expiry":    ("Expiry Date", "Expiry", "Maturity Date", "Vencimiento"),
        "delivery":  ("Delivery Date", "Settlement Date", "Value Date", "Entrega"),
        "payout":    ("Payout Ccy", "Settle Ccy"),
        "bundle":    ("Bundle Name",),
        "bundle_ty": ("Bundle Type",),
    }
    OBLIGATORIAS = ("pair", "quantity", "strike", "expiry", "delivery")

    @classmethod
    def reconoce(cls, fila: dict) -> bool:
        desc = (valor(fila, ("Product Description", "Product", "Product Type")) or "").upper()
        if any(m in desc for m in ("FXOPTION", "FX OPTION", "VANILLA")):
            return True
        if any(m in desc for m in ("FORWARD", "NDF", "SWAP")):
            return False
        # Sin descripción: un strike más un lado call/put ya es una opción.
        return (num(fila, cls.COLUMNAS["strike"]) is not None
                and bool(valor(fila, cls.COLUMNAS["call_put"])))

    @classmethod
    def leer(cls, fila: dict):
        from ..portfolio import Trade
        C = cls.COLUMNAS
        vals = {"pair": valor(fila, C["pair"]),
                "quantity": num(fila, C["quantity"]),
                "strike": num(fila, C["strike"]),
                "expiry": fecha(fila, C["expiry"]),
                "delivery": fecha(fila, C["delivery"])}
        faltan = [C[k][0] for k in cls.OBLIGATORIAS if vals[k] is None]
        if faltan:
            return None, faltan
        cp = (valor(fila, C["call_put"]) or "").upper()
        return Trade(trade_id=valor(fila, C["trade_id"]) or "",
                     pair=vals["pair"], call=("CALL" in cp or cp.startswith("C")),
                     quantity=vals["quantity"], strike=vals["strike"],
                     expiry=vals["expiry"], delivery=vals["delivery"],
                     payout_ccy=valor(fila, C["payout"]) or "",
                     bundle=valor(fila, C["bundle"]) or "",
                     bundle_type=valor(fila, C["bundle_ty"]) or ""), []

    @staticmethod
    def pv(op, f: Factors) -> float:
        return op.quantity * premium(f.forward, op.strike, f.tau, f.vol,
                                     f.df_quote, op.call)
