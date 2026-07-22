"""curvelib — bootstrapping multi-curva / multi-colateral.

Uso rápido:
    from curvelib.orchestrator import build_from_file
    curves = build_from_file("config/curves.yaml")
    curves["USD_SOFR"].df(datetime.date(2030, 7, 2))
"""
from .curve import Curve
from .engine import BootstrapEngine, BootstrapError
from .instruments import CurveContext, INSTRUMENT_TYPES
from .orchestrator import (build_all, build_bid_mid_ask, build_from_file,
                           CurveSet, load_config, select_curves)
from .quotes_loader import apply_quotes_sheet, parse_quotes_csv

__all__ = [
    "Curve", "BootstrapEngine", "BootstrapError",
    "CurveContext", "INSTRUMENT_TYPES",
    "build_all", "build_from_file", "build_bid_mid_ask", "CurveSet",
    "load_config", "select_curves", "apply_quotes_sheet", "parse_quotes_csv",
]
__version__ = "0.1.0"
