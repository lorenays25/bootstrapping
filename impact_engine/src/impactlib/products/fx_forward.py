"""
Forward FX y NDF.

El pago a la entrega es (S_T − K) por unidad de nocional en moneda cotizada, así
que el valor presente no depende de la volatilidad: es el forward descontado. Un
NDF liquida la diferencia en una sola moneda, pero el valor presente es el
mismo; lo que cambia es la moneda del flujo, no su monto en valor presente.

Por eso este producto reporta delta, theta y las dos rho, y NO reporta gamma ni
vega: son exactamente cero, y publicarlas como "0.0000" al lado de las de una
opción invita a leer un cero calculado donde solo hay un cero estructural.

EXPORT QUE ESPERA: el reporte de forwards de Calypso, que NO es el de opciones.
Un forward no tiene strike ni volatilidad implícita: tiene tasa pactada y fecha
de liquidación. Los alias de abajo cubren los nombres habituales, pero están
escritos sobre supuestos: NO se han verificado contra un export real, porque el
archivo del 01/09/2026 solo trae opciones. Cuando llegue uno de forwards, el
lector dirá por su nombre qué columna no encontró en vez de descartar las filas
en silencio, y ajustar los alias es cuestión de una línea.
"""
from __future__ import annotations

from ..core.market import Factors
from .base import Product, fecha, num, valor


class FXForward(Product):
    clave = "FXFWD"
    etiqueta = "Forwards FX / NDF"
    espera = ("Reporte de forwards. Necesita la tasa pactada, la fecha de "
              "liquidación y el sentido de la operación. Los nombres de columna "
              "aún no se han verificado contra un export real de forwards.")
    griegas = ("pv", "delta", "theta", "rho", "rho2")

    COLUMNAS = {
        "trade_id": ("Trade Id", "TradeId", "Trade"),
        "pair":     ("Ccy Pair", "Currency Pair", "Par"),
        "quantity": ("Quantity", "Principal Amount", "Nominal", "Notional",
                     "Nominal Amount", "Cantidad"),
        "strike":   ("Strike", "Fwd Rate", "Forward Rate", "Agreed Rate",
                     "Contract Rate", "Rate", "Tipo Pactado"),
        "delivery": ("Delivery Date", "Settlement Date", "Value Date",
                     "Maturity Date", "Entrega"),
        "expiry":   ("Fixing Date", "Expiry Date", "Maturity Date",
                     "Delivery Date", "Value Date"),
        "side":     ("Buy/Sell", "Direction", "Sentido"),
        "payout":   ("Payout Ccy", "Settle Ccy"),
    }
    OBLIGATORIAS = ("pair", "quantity", "strike", "delivery")

    @classmethod
    def reconoce(cls, fila: dict) -> bool:
        desc = (valor(fila, ("Product Description", "Product", "Product Type",
                             "Product Subtype")) or "").upper()
        return any(m in desc for m in ("FXFORWARD", "FX FORWARD", "FXNDF",
                                       "NDF", "FORWARD", "OUTRIGHT"))

    @classmethod
    def leer(cls, fila: dict):
        from ..portfolio import Trade
        C = cls.COLUMNAS
        vals = {"pair": valor(fila, C["pair"]),
                "quantity": num(fila, C["quantity"]),
                "strike": num(fila, C["strike"]),
                "delivery": fecha(fila, C["delivery"])}
        faltan = [C[k][0] for k in cls.OBLIGATORIAS if vals[k] is None]
        if faltan:
            return None, faltan
        # El fixing es la fecha de referencia para el plazo; si el export no la
        # trae, la entrega sirve: en un forward entregable coinciden salvo el
        # rezago de liquidación, y el plazo solo entra por el descuento.
        exp = fecha(fila, C["expiry"]) or vals["delivery"]
        # El sentido puede venir en el signo de la cantidad o en Buy/Sell. Si
        # viene en los dos, manda el signo, que es el que usa el PV de Calypso.
        q = vals["quantity"]
        side = (valor(fila, C["side"]) or "").upper()
        if q > 0 and side.startswith("S"):
            q = -q
        return Trade(trade_id=valor(fila, C["trade_id"]) or "",
                     pair=vals["pair"], call=True, quantity=q,
                     strike=vals["strike"], expiry=exp, delivery=vals["delivery"],
                     payout_ccy=valor(fila, C["payout"]) or ""), []

    @classmethod
    def tipo_texto(cls, op) -> str:
        return "Outright"

    @staticmethod
    def pv(op, f: Factors) -> float:
        """PV en MONEDA COTIZADA. La cantidad ya viene firmada: comprar la
        divisa base paga F − K, venderla paga K − F, y el signo lo pone ella."""
        return op.quantity * f.df_quote * (f.forward - op.strike)
