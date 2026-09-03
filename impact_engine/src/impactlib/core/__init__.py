"""Núcleo compartido: convenciones de fecha, descuento y numéricas."""
from .conventions import LAG_SPOT, depo_df, depo_rate, spot_date, tau_vol  # noqa: F401
from .market import Factors  # noqa: F401
from .numerics import N, n, norm_ppf  # noqa: F401
