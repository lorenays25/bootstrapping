"""
engine.py — Motor de bootstrapping.

Dos modos de resolución:

  SECUENCIAL  (mode: sequential)
    Los instrumentos se ordenan por madurez. Para cada uno se agrega un
    nodo en su pilar y se resuelve el DF de ese nodo con root-finding 1D
    (Brent) tal que residual(instrumento) = 0. Es el modo estándar cuando
    la estructura es "triangular": cada quote agrega información más allá
    del pilar anterior.

  GLOBAL  (mode: global)
    Se agregan todos los nodos y se resuelve el sistema completo
    simultáneamente con scipy.optimize.least_squares (Levenberg-Marquardt /
    Trust Region), minimizando el vector de residuales. Necesario cuando
    hay dependencia cruzada entre pilares (instrumentos que "miran" nodos
    posteriores) o solapamiento de tenores.

La variable de optimización es log(DF) del nodo => DF > 0 garantizado.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

import numpy as np
from scipy.optimize import brentq, least_squares

from . import dates as dt
from .curve import Curve
from .instruments import CurveContext, Instrument


class BootstrapError(RuntimeError):
    pass


class BootstrapEngine:
    def __init__(self, valuation_date: _dt.date, ctx: CurveContext):
        self.valuation_date = valuation_date
        self.ctx = ctx

    # ------------------------------------------------------------------
    def build_curve(
        self,
        name: str,
        instruments: List[Instrument],
        mode: str = "sequential",
        internal_day_count: str = "ACT/365F",
    ) -> Curve:
        curve = Curve(name=name, valuation_date=self.valuation_date,
                      internal_day_count=internal_day_count)
        # La curva en construcción entra al contexto para que los
        # instrumentos puedan referenciarla como 'target' / 'self'.
        self.ctx.curves[name] = curve

        for ins in instruments:
            ins.build(self.valuation_date)
        instruments = sorted(instruments, key=lambda i: i.pillar_date)

        # valida pilares duplicados
        seen = set()
        for ins in instruments:
            if ins.pillar_date in seen:
                raise BootstrapError(
                    f"[{name}] Dos instrumentos con el mismo pilar "
                    f"{ins.pillar_date} (tenor {ins.tenor}). Elimina uno o usa mode: global."
                )
            seen.add(ins.pillar_date)

        if mode == "sequential":
            self._solve_sequential(curve, instruments)
        elif mode == "global":
            self._solve_global(curve, instruments)
        else:
            raise BootstrapError(f"[{name}] mode desconocido: {mode}")

        self._check(curve, instruments)
        return curve

    # ------------------------------------------------------------------
    def _solve_sequential(self, curve: Curve, instruments: List[Instrument]):
        for ins in instruments:
            idx = curve.add_node(ins.pillar_date)   # guess: extrapolación
            guess = curve.log_dfs[idx]

            def f(log_df, _idx=idx, _ins=ins):
                curve.set_node_log(_idx, log_df)
                return _ins.residual(self.ctx)

            lo, hi = self._bracket(f, guess, ins, curve.name)
            root = brentq(f, lo, hi, xtol=1e-14, rtol=1e-13, maxiter=200)
            curve.set_node_log(idx, root)

    @staticmethod
    def _bracket(f, guess: float, ins: Instrument, cname: str):
        """Expande simétricamente alrededor del guess hasta encerrar la raíz."""
        for width in (0.05, 0.2, 0.8, 2.0, 5.0):
            lo, hi = guess - width, guess + width
            try:
                if f(lo) * f(hi) < 0:
                    return lo, hi
            except Exception:
                continue
        raise BootstrapError(
            f"[{cname}] No se pudo encerrar la raíz para {type(ins).__name__} "
            f"tenor {ins.tenor} (quote={ins.quote}). Revisa el quote y las "
            f"convenciones: probablemente el dato está en unidades equivocadas "
            f"(¿porcentaje en vez de decimal? ¿puntos con factor incorrecto?)."
        )

    # ------------------------------------------------------------------
    def _solve_global(self, curve: Curve, instruments: List[Instrument]):
        idxs = [curve.add_node(ins.pillar_date) for ins in instruments]
        x0 = np.array([curve.log_dfs[i] for i in idxs])

        def residuals(x):
            for i, v in zip(idxs, x):
                curve.set_node_log(i, v)
            return np.array([ins.residual(self.ctx) for ins in instruments])

        sol = least_squares(residuals, x0, method="lm", xtol=1e-14, ftol=1e-14)
        if not sol.success:
            raise BootstrapError(f"[{curve.name}] global solve no convergió: {sol.message}")
        for i, v in zip(idxs, sol.x):
            curve.set_node_log(i, v)

    # ------------------------------------------------------------------
    def _check(self, curve: Curve, instruments: List[Instrument], tol: float = 1e-8):
        worst = max(abs(ins.residual(self.ctx)) for ins in instruments)
        if worst > tol:
            raise BootstrapError(
                f"[{curve.name}] Repricing check falló: residual máximo "
                f"{worst:.2e} > {tol:.0e}. La curva no reprecia sus propios quotes."
            )
