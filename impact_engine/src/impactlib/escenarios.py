"""
Los tres escenarios de comparación, armados a partir de los feeds.

Es el único sitio del módulo que sabe de la existencia del Módulo 1 y del
Módulo 2. Los importa de forma TOLERANTE A FALLO: si falta cualquiera de los
dos, el escenario que lo necesita se marca como no disponible con el motivo, y
los demás siguen corriendo. Una cartera se tiene que poder valorizar aunque hoy
no haya cotizaciones de curva.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import io
import os
from typing import Dict, Optional


@contextlib.contextmanager
def _callado():
    """Silencia lo que imprimen los orquestadores de los otros dos módulos.

    Los dos escriben su resumen de construcción en stdout. Está bien cuando se
    corren a mano, pero aquí se llaman desde el servidor y desde la interfaz, y
    ahí ese texto no lo lee nadie: solo ensucia el log y esconde los avisos que
    sí importan.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf

from .feeds import (CalypsoFeed, CurvasPropiasFeed, FeedCompuesto,
                    SuperficiePropiaFeed)

CLAVES = ("calypso", "curvas", "completo")

DESCRIPCION = {
    "calypso":  ("Pricer aislado — factores de Calypso",
                 "Volatilidad, forward y tasas del propio reporte. Si esto no "
                 "cuadra, el problema es la fórmula y no los insumos."),
    "curvas":   ("Curvas propias — vol de Calypso",
                 "Descuento y forward del Módulo 1. La diferencia contra el "
                 "escenario anterior es el aporte de las curvas."),
    "completo": ("Cadena completa — curvas y superficie propias",
                 "Módulo 1 y Módulo 2. La diferencia contra el escenario "
                 "anterior es el aporte de la superficie."),
}


def _raiz(sub: str) -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__),
                                        "..", "..", "..", sub))


def construir_curvas(valuation_date: _dt.date, ruta_yaml: Optional[str] = None):
    """Curvas del Módulo 1 para la fecha pedida. Devuelve (curvas, error)."""
    try:
        import sys
        raiz = _raiz("curve_bootstrapper")
        src = os.path.join(raiz, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from curvelib.orchestrator import build_bid_mid_ask, load_config
        cfg = load_config(ruta_yaml or os.path.join(raiz, "config", "curves.yaml"))
        fecha_yaml = cfg.get("valuation_date")
        cfg["valuation_date"] = valuation_date
        with _callado():
            cs = build_bid_mid_ask(cfg, verbose=False)
        aviso = None
        if str(fecha_yaml)[:10] != valuation_date.isoformat():
            aviso = (f"Las curvas se construyeron con las cotizaciones del "
                     f"{str(fecha_yaml)[:10]}, no del {valuation_date}. Los "
                     f"factores de descuento son de esa fecha: la comparación "
                     f"mide el motor, no el mercado del día.")
        return cs.sides["mid"], aviso
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def construir_superficie(valuation_date: _dt.date, spots_vol: Optional[dict] = None):
    """Superficie del Módulo 2 para la fecha pedida. Devuelve (vs, error)."""
    try:
        import sys
        raiz = _raiz("vol_surface_builder")
        src = os.path.join(raiz, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from vollib.orchestrator import build_bid_mid_ask, load_config
        cfg = load_config(os.path.join(raiz, "config", "surfaces.yaml"))
        cfg["_root"] = raiz
        cfg["valuation_date"] = valuation_date
        with _callado():
            vs, _ = build_bid_mid_ask(cfg)
        for par, sp in (spots_vol or {}).items():
            k = par.replace("/", "")
            if k in vs.sides["mid"] and sp:
                vs.sides["mid"][k].fwd.spot = float(sp)
        return vs, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def armar(clave: str, filas, valuation_date: _dt.date, spot_date: _dt.date,
          spots: Dict[str, float], spots_vol: Optional[dict] = None,
          curvas=None, superficie=None, curva_por_moneda: Optional[dict] = None):
    """Devuelve (feed, avisos). Si el escenario no se puede armar, el feed cae
    al de Calypso para la pieza que falta y el aviso lo dice."""
    cal = CalypsoFeed(filas, valuation_date, spot_date, spots)
    avisos = []
    if clave == "calypso":
        return cal, avisos

    if curvas is None:
        curvas, err = construir_curvas(valuation_date)
        if err and curvas is None:
            avisos.append(f"No se pudieron construir las curvas propias ({err}). "
                          f"Se usan las tasas de Calypso.")
            return cal, avisos
        if err:
            avisos.append(err)
    cf = CurvasPropiasFeed(curvas, valuation_date, spot_date, spots,
                           curva_por_moneda)

    if clave == "curvas":
        feed = FeedCompuesto(cf, cal, "curvas propias + vol de Calypso",
                             DESCRIPCION["curvas"][1])
        return feed, avisos + cf.avisos()

    if superficie is None:
        superficie, err = construir_superficie(valuation_date, spots_vol)
        if superficie is None:
            avisos.append(f"No se pudo construir la superficie propia ({err}). "
                          f"Se usa la volatilidad de Calypso.")
            feed = FeedCompuesto(cf, cal, "curvas propias + vol de Calypso",
                                 DESCRIPCION["curvas"][1])
            return feed, avisos + cf.avisos()
    sf = SuperficiePropiaFeed(superficie)

    class _VolConRespaldo:
        """La superficie propia, y donde no llegue, la de Calypso. Sin esto un
        par sin superficie tumbaría toda la fila en vez de degradarse."""
        nombre = "superficie propia"

        def factores(self, fila):
            f = sf.factores(fila)
            return f if f is not None else cal.factores(fila)

        def avisos(self):
            return sf.avisos()

    feed = FeedCompuesto(cf, _VolConRespaldo(), "curvas y superficie propias",
                         DESCRIPCION["completo"][1])
    return feed, avisos + cf.avisos() + sf.avisos()
