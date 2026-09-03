"""
Interfaz de un producto.

Un producto sabe cuatro cosas: qué columnas necesita del export, cómo leer una
fila, cómo calcular su PV a partir de un `Factors`, y qué griegas tienen sentido
para él. Las griegas se calculan siempre por DIFERENCIAS FINITAS sobre el mismo
`pv`, para que la sensibilidad no pueda contradecir a la valorización.

CADA PRODUCTO LEE SU PROPIO EXPORT. Calypso no exporta las opciones y los
forwards con las mismas columnas —un forward no tiene strike ni volatilidad
implícita, tiene tasa pactada y fecha de liquidación— así que el mapeo de
columnas vive con el producto, no en un lector único que intente adivinar. Cada
producto declara sus columnas con ALIAS, y cuando una falta lo dice por su
nombre en vez de descartar la fila en silencio.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional, Tuple

from ..core.market import Factors

#: Choque de spot, en relativo. Se aplica como ±SHOCK_SPOT/2 — ver `greeks`.
SHOCK_SPOT = 0.01
#: Choque de volatilidad, en decimal (0.01 = 1 punto de vol). Central.
SHOCK_VOL = 0.01
#: Choque de tasa, en decimal (0.01 = 100 pb). Central.
SHOCK_RATE = 0.01

TODAS = ("pv", "delta", "gamma", "vega", "theta", "rho", "rho2")


# ---------------------------------------------------------------------------
# Lectura de columnas con alias
# ---------------------------------------------------------------------------
def valor(fila: dict, alias) -> Optional[str]:
    """El primer alias que aparece con contenido en la fila."""
    for a in alias:
        v = fila.get(a)
        if v is not None and str(v).strip() not in ("", "-"):
            return str(v).strip()
    return None


def num(fila: dict, alias) -> Optional[float]:
    v = valor(fila, alias)
    if v is None:
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def fecha(fila: dict, alias) -> Optional[_dt.date]:
    v = valor(fila, alias)
    if v is None:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return _dt.datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


class Product:
    """Clase base. Los productos son de clase, no de instancia: no guardan
    estado, solo saben leer y calcular."""

    clave: str = ""
    etiqueta: str = ""
    #: Texto para la interfaz: qué export espera este producto.
    espera: str = ""
    #: Griegas que este producto reporta. Un forward no tiene gamma ni vega.
    griegas: tuple = TODAS
    #: Campo lógico -> alias de columna aceptados, en orden de preferencia.
    COLUMNAS: Dict[str, tuple] = {}
    #: Campos sin los cuales la fila no se puede valorizar.
    OBLIGATORIAS: tuple = ()

    # ------------------------------------------------------------- lectura
    @classmethod
    def reconoce(cls, fila: dict) -> bool:
        raise NotImplementedError

    @classmethod
    def leer(cls, fila: dict):
        """(Trade, faltantes). Si faltan campos obligatorios devuelve
        (None, [nombres]) para que el llamador diga cuáles."""
        raise NotImplementedError

    @classmethod
    def columnas_visibles(cls) -> List[str]:
        """El primer alias de cada campo: lo que se le muestra al usuario."""
        return [v[0] for v in cls.COLUMNAS.values()]

    @classmethod
    def tipo_texto(cls, op) -> str:
        """Cómo se nombra el tipo de operación en el detalle. Un forward no es
        call ni put; escribir "CALL" en su fila es información falsa."""
        return "CALL" if op.call else "PUT"

    # ------------------------------------------------------------ cálculo
    @staticmethod
    def pv(op, f: Factors) -> float:
        """PV en MONEDA COTIZADA, con el signo de la cantidad."""
        raise NotImplementedError

    @classmethod
    def greeks(cls, op, f: Factors) -> Dict[str, float]:
        """Todas las griegas del producto, por diferencias finitas.

        EL CHOQUE PARTIDO EN DOS IMPORTA. Aplicar el 1 % hacia adelante en vez
        de ±0.5 % lleva el error de gamma contra Calypso de 0.37 % a 8.65 %, y
        la diferencia adelantada en vega lo lleva de 0.05 % a 2.20 %. Es el
        mismo efecto de segundo orden en los dos casos.
        """
        pv = cls.pv

        def delta_en(mult: float) -> float:
            h = f.spot * SHOCK_SPOT / 2.0
            up = pv(op, f.bump(spot_mult=mult * (1 + SHOCK_SPOT / 2)))
            dn = pv(op, f.bump(spot_mult=mult * (1 - SHOCK_SPOT / 2)))
            return (up - dn) / (2 * h * mult)

        base = pv(op, f)
        out = {"pv": base, "delta": 0.0, "gamma": 0.0, "vega": 0.0,
               "theta": 0.0, "rho": 0.0, "rho2": 0.0}
        g = cls.griegas
        if "delta" in g:
            out["delta"] = delta_en(1.0)
        if "gamma" in g:
            out["gamma"] = delta_en(1 + SHOCK_SPOT / 2) - delta_en(1 - SHOCK_SPOT / 2)
        if "vega" in g and f.vol is not None:
            out["vega"] = (pv(op, f.bump(dvol=+SHOCK_VOL / 2))
                           - pv(op, f.bump(dvol=-SHOCK_VOL / 2)))
        if "theta" in g:
            out["theta"] = pv(op, f.bump(dtau=-1.0 / 365.0, ddays=-1)) - base
        if "rho" in g:
            out["rho"] = (pv(op, f.bump(drate_base=+SHOCK_RATE / 2))
                          - pv(op, f.bump(drate_base=-SHOCK_RATE / 2)))
        if "rho2" in g:
            out["rho2"] = (pv(op, f.bump(drate_quote=+SHOCK_RATE / 2))
                           - pv(op, f.bump(drate_quote=-SHOCK_RATE / 2)))
        return out
