"""
Módulo 3 — Impactos: valorización y griegas de la cartera de derivados.

Tres capas, y la frontera entre ellas es lo que hace que el motor sea
comparable consigo mismo:

    core/      convenciones de fecha y descuento, y el contrato `Factors`
    feeds/     de dónde salen los factores: Calypso, curvas propias, superficie
    products/  qué se valoriza: opción FX vanilla, forward FX / NDF

Un producto no sabe de dónde vinieron sus factores y un feed no sabe qué se va a
valorizar con ellos. Cambiar de feed cambia la comparación sin tocar una fórmula.
"""
from . import core, escenarios, feeds, portfolio, products, report  # noqa: F401
from .products import REGISTRO, identificar  # noqa: F401
