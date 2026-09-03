"""
Interfaz de una fuente de factores de riesgo.

Un feed contesta una sola pregunta: dada una operación, ¿cuál es el spot, el
forward, los dos factores de descuento y la volatilidad? De dónde los saque es
asunto suyo. Esa frontera es lo que permite valorizar la misma cartera con los
factores de Calypso o con los del propio motor sin tocar una fórmula, y es
también lo que hace que las tres comparaciones sean comparables entre sí:
cambia el feed, no el pricer.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from typing import Optional

from ..core.market import Factors


class FactorFeed:
    nombre = ""
    #: Texto para la interfaz: qué mide esta combinación de factores.
    descripcion = ""

    def factores(self, fila) -> Optional[Factors]:
        """Los factores de una fila del portafolio, o None si no se pueden armar."""
        raise NotImplementedError

    def avisos(self) -> list:
        """Lo que el usuario tiene que saber antes de leer los números."""
        return []


class FeedCompuesto(FactorFeed):
    """Toma cada pieza del primer feed que sepa darla.

    Es lo que hace posible la comparación 2: curvas propias con la volatilidad
    de Calypso aísla el aporte de las curvas, igual que la 3 aísla el de la
    superficie. Sin esto habría que elegir entre todo propio o todo ajeno, y una
    diferencia en el PV no se podría atribuir a nadie.
    """

    def __init__(self, curvas: FactorFeed, vol: FactorFeed, nombre: str,
                 descripcion: str = ""):
        self.curvas, self.vol = curvas, vol
        self.nombre, self.descripcion = nombre, descripcion

    def factores(self, fila) -> Optional[Factors]:
        base = self.curvas.factores(fila)
        if base is None:
            return None
        otro = self.vol.factores(fila)
        v = None if otro is None else otro.vol
        fuente = dict(base.fuente)
        fuente["vol"] = self.vol.nombre if v is not None else "no disponible"
        return replace(base, vol=v, fuente=fuente)

    def avisos(self) -> list:
        return self.curvas.avisos() + self.vol.avisos()
