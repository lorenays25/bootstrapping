"""
Registro de productos.

Agregar un producto es escribir su módulo y sumarlo a REGISTRO. El orden importa:
`identificar` devuelve el PRIMERO que reconoce la fila, así que los productos
más específicos van antes que los más generales.
"""
from __future__ import annotations

from typing import Optional, Type

from .base import SHOCK_RATE, SHOCK_SPOT, SHOCK_VOL, Product, TODAS  # noqa: F401
from .fx_forward import FXForward
from .fx_option import FXOptionVanilla

#: El orden manda dos cosas: `identificar` devuelve el PRIMERO que reconoce la
#: fila, y la interfaz muestra las pestañas en este orden. Las opciones van
#: primero porque son el grueso de la cartera; no hay ambigüedad porque
#: FXOptionVanilla descarta explícitamente las descripciones de forward.
REGISTRO = (FXOptionVanilla, FXForward)

POR_CLAVE = {p.clave: p for p in REGISTRO}


def identificar(fila: dict) -> Optional[Type[Product]]:
    """El producto que corresponde a una fila cruda del export, o None."""
    for p in REGISTRO:
        if p.reconoce(fila):
            return p
    return None
