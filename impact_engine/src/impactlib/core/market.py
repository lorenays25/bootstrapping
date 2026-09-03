"""
Los factores de riesgo con los que se valoriza UNA operación.

Es el contrato entre las dos capas del módulo: los `feeds` producen un
`Factors` y los `products` lo consumen. Ningún producto sabe de dónde salió cada
número, y ningún feed sabe qué se va a valorizar con él. Eso es lo que permite
correr la misma cartera con los factores de Calypso o con los propios sin tocar
una sola fórmula.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Optional

from .conventions import depo_df


@dataclass
class Factors:
    spot: float                 # moneda cotizada por unidad de base
    df_base: float              # descuento en divisa base, fecha spot -> entrega
    df_quote: float             # descuento en moneda cotizada, fecha spot -> entrega
    forward: float              # a la fecha de ENTREGA
    tau: float                  # años, ACT/365 valorización -> vencimiento
    days: int                   # días de la fecha spot a la entrega
    rate_base: float            # tasa simple implícita en df_base
    rate_quote: float           # tasa simple implícita en df_quote
    vol: Optional[float] = None # DECIMAL; None en productos lineales
    #: De dónde salió cada pieza: {"vol": "calypso", "curvas": "propias", ...}.
    fuente: Dict[str, str] = field(default_factory=dict)

    @property
    def basis(self) -> float:
        """Cuánto se aparta el forward de la paridad de depósitos.

        Vale 1.0 cuando el forward se construyó como spot·Df_base/Df_cotizada.
        Se aparta cuando el forward viene de la curva de forwards FX, que es lo
        que hace Calypso: medido contra el reporte, hasta 2.5 pb, creciente con
        el plazo. Al mover una tasa para calcular rho hay que CONSERVAR este
        factor, o el choque arrastra una diferencia que no es del choque.
        """
        par = self.spot * self.df_base / self.df_quote
        return self.forward / par if par else 1.0

    def bump(self, *, spot_mult: float = 1.0, dvol: float = 0.0,
             dtau: float = 0.0, ddays: int = 0,
             drate_base: float = 0.0, drate_quote: float = 0.0) -> "Factors":
        """Los mismos factores con uno movido. Rehace los descuentos desde las
        tasas y reconstruye el forward conservando la base."""
        b = self.basis
        d = self.days + ddays
        rb, rq = self.rate_base + drate_base, self.rate_quote + drate_quote
        dfb, dfq = depo_df(rb, d), depo_df(rq, d)
        S = self.spot * spot_mult
        return replace(self, spot=S, df_base=dfb, df_quote=dfq,
                       forward=S * dfb / dfq * b, days=d,
                       rate_base=rb, rate_quote=rq,
                       tau=max(self.tau + dtau, 1e-12),
                       vol=None if self.vol is None else self.vol + dvol)
