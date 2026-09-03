"""vollib — construcción de superficies de volatilidad FX (Módulo 2).

Uso rápido:
    from vollib.orchestrator import build_from_file
    vs, avisos = build_from_file("config/surfaces.yaml")
    vs.sides["mid"]["USDMXN"].vol(date(2027, 9, 1), 18.50)
"""
from .curves import DiscountCurve, CurvelibAdapter, load_calypso_curve, load_fx_spots
from .deltas import DeltaConvention
from .orchestrator import (build_all, build_bid_mid_ask, build_from_file,
                           load_config, ConfigError)
from .quotes_loader import QuoteError, load_parameters, load_quotes, load_underlyings
from .smile import SmileSlice, SmilePoint, build_slice, wing_vols
from .surface import ForwardModel, VolSurface, VolSurfaceSet

__all__ = [
    "DiscountCurve", "CurvelibAdapter", "load_calypso_curve", "load_fx_spots",
    "DeltaConvention", "SmileSlice", "SmilePoint", "build_slice", "wing_vols",
    "ForwardModel", "VolSurface", "VolSurfaceSet",
    "build_all", "build_bid_mid_ask", "build_from_file", "load_config",
    "ConfigError", "QuoteError", "load_parameters", "load_quotes", "load_underlyings",
]
__version__ = "0.1.0"
