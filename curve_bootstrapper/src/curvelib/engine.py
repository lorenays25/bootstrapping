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
    @staticmethod
    def _order_group_for_seeding(specs: List[dict]) -> List[dict]:
        """Ordena los miembros del grupo para sembrarlos en secuencia
        Gauss-Seidel: primero las curvas que otros miembros usan como
        'projection' (referencia estable, ya conocida), después las que
        dependen de ellas. Se detecta inspeccionando ins.curve_names de
        cada instrumento (dict con roles 'target'/'discount'/'projection'/
        'other_leg'), sin asumir nombres de curva específicos.

        Si no hay dependencias 'projection' claras entre miembros (o si
        se forma un ciclo, lo cual no debería ocurrir para el rol
        'projection'), se conserva el orden original como respaldo.
        """
        names = [s["name"] for s in specs]
        name_set = set(names)
        spec_by_name = {s["name"]: s for s in specs}
        deps: Dict[str, set] = {n: set() for n in names}
        for spec in specs:
            name = spec["name"]
            for ins in spec["instruments"]:
                cn = getattr(ins, "curve_names", None)
                if not cn:
                    continue
                proj = cn.get("projection")
                if proj and proj in name_set and proj != name:
                    deps[name].add(proj)

        ordered: List[str] = []
        remaining = set(names)
        while remaining:
            ready = [n for n in remaining if deps[n] <= set(ordered)]
            if not ready:
                # Ciclo inesperado en dependencias 'projection': no debería
                # pasar, pero para no romper el build se conserva el resto
                # del orden original.
                ready = [n for n in names if n in remaining]
            ready.sort(key=lambda n: names.index(n))
            for n in ready:
                ordered.append(n)
                remaining.discard(n)
        return [spec_by_name[n] for n in ordered]

    def _seed_group(self, specs: List[dict]) -> Dict[str, List[float]]:
        """Semilla inicial para el solve conjunto: un bootstrap RÁPIDO de
        cada curva del grupo, en secuencia tipo Gauss-Seidel. Es el paso 1
        del algoritmo de Calypso para curvas simultáneas (manual "Yield
        Curves Generation" §9.2 Simultaneous Curve Algorithm): *"Make a
        trial guess for each curve by solving a fast bootstrap assuming
        the discount and forward curves are all the same."*, generalizado
        para que cada miembro use, cuando ya están disponibles, las
        semillas REALES (no auto-referenciadas) de los miembros del grupo
        sembrados previamente -- en vez de un alias en bloque de todo el
        grupo a la propia curva en construcción.

        Por qué: el alias en bloque (todo el grupo == esta curva) es
        correcto para la primera curva que se siembra (coincide con la
        aproximación de Calypso discount=projection=self), pero rompe la
        estructura matemática de fórmulas como XCCYBasis para las curvas
        sembradas DESPUÉS, cuyo 'projection' está pensado como una curva
        ya conocida y estable -- auto-referenciarla produce raíces de
        brentq sin sentido (log DF positivos y crecientes en vez de
        negativos). Sembrando en el orden dado por
        _order_group_for_seeding, cada miembro usa la semilla real de sus
        'projection' ya calculadas, y solo se auto-alias a sí mismo (y a
        los miembros del grupo aún no sembrados, como aproximación).

        Sin esto, el least_squares del solve conjunto arranca desde DF=1
        plano (0% flat) en TODOS los pilares -- para curvas a 10-32 años
        eso está lejísimos de la solución real y Levenberg-Marquardt tarda
        decenas de segundos en converger. Con esta semilla arranca a un
        paso o dos de la solución final.

        Referencias a curvas FUERA del grupo (p.ej. other_leg: USD_SOFR)
        SÍ usan el contexto real, porque esas ya están construidas.
        Best-effort: si el bootstrap aislado de una curva falla (porque su
        fórmula realmente necesita la otra curva, p.ej. si tuviera cero
        instrumentos independientes), se usa el guess por defecto
        (extrapolación plana) para esa curva sin abortar el resto.
        """
        group_names = {s["name"] for s in specs}
        ordered_specs = self._order_group_for_seeding(specs)
        x0_map: Dict[str, List[float]] = {}
        seed_curves: Dict[str, Curve] = {}  # miembros ya sembrados (curva real)
        for spec in ordered_specs:
            name = spec["name"]
            seed_curve = Curve(name=f"{name}__seed", valuation_date=self.valuation_date)
            seed_ctx = CurveContext(curves=dict(self.ctx.curves), fx_spots=self.ctx.fx_spots)
            for gn in group_names:
                # Miembros ya sembrados: usar su semilla REAL (Gauss-Seidel).
                # Miembros aún no sembrados: alias a esta curva en
                # construcción (misma aproximación que el paso 1 de Calypso).
                seed_ctx.curves[gn] = seed_curves.get(gn, seed_curve)
            seed_ctx.curves[name] = seed_curve  # el propio SIEMPRE apunta a sí mismo
            seed_engine = BootstrapEngine(self.valuation_date, seed_ctx)

            for ins in spec["instruments"]:
                if ins.pillar_date is None:
                    ins.build(self.valuation_date)
            instruments = sorted(spec["instruments"], key=lambda i: i.pillar_date)

            for ins in instruments:
                idx = seed_curve.add_node(ins.pillar_date)
                guess = seed_curve.log_dfs[idx]
                try:
                    def f(log_df, _idx=idx, _ins=ins):
                        seed_curve.set_node_log(_idx, log_df)
                        return _ins.residual(seed_ctx)
                    lo, hi = seed_engine._bracket(f, guess, ins, name)
                    root = brentq(f, lo, hi, xtol=1e-10, rtol=1e-10, maxiter=200)
                    seed_curve.set_node_log(idx, root)
                except BootstrapError:
                    pass  # no se pudo acotar la raíz aislada: se deja el guess (extrapolación)
            x0_map[name] = list(seed_curve.log_dfs[1:])  # excluye el nodo sintético (0, log 1)
            seed_curves[name] = seed_curve  # disponible para el siguiente miembro del grupo
        return x0_map

    # ------------------------------------------------------------------
    def build_group(
        self,
        specs: List[dict],
        internal_day_count: str = "ACT/365F",
    ) -> Dict[str, Curve]:
        """Resuelve un GRUPO de curvas con dependencia mutua en un solo
        sistema simultáneo -- equivalente al 'Multicurve Package' /
        DoubleGlobalM de Calypso, o al concepto de 'construcción simultánea'
        (manual BCP §6.4): cuando la Curva A necesita la Curva B para
        descontar/proyectar y B necesita A, ninguna puede construirse antes
        que la otra, así que se resuelven juntas.

        specs: lista de {"name": str, "instruments": List[Instrument]}.
        Todas las curvas del grupo se registran vacías en el contexto ANTES
        de construir instrumentos y ANTES del solve, de modo que las
        referencias cruzadas (discount/projection entre miembros) resuelvan
        vía ctx.curve(). Se concatenan los nodos (log DF) de TODAS las
        curvas del grupo en un solo vector de incógnitas y se resuelve con
        UN least_squares sobre el vector de residuales de TODOS los
        instrumentos de TODAS las curvas del grupo -- el mismo algoritmo
        que _solve_global, generalizado a más de una curva.
        """
        # 1) Construye y ordena instrumentos de cada curva (una sola vez;
        #    _seed_group más abajo reutiliza estos mismos objetos, ya build()).
        for spec in specs:
            for ins in spec["instruments"]:
                ins.build(self.valuation_date)
            instruments = sorted(spec["instruments"], key=lambda i: i.pillar_date)
            seen = set()
            for ins in instruments:
                if ins.pillar_date in seen:
                    raise BootstrapError(
                        f"[{spec['name']}] Dos instrumentos con el mismo pilar "
                        f"{ins.pillar_date} (tenor {ins.tenor})."
                    )
                seen.add(ins.pillar_date)
            spec["instruments"] = instruments

        all_instruments: List[Instrument] = [ins for spec in specs for ins in spec["instruments"]]
        if not all_instruments:
            raise BootstrapError(
                f"[grupo {', '.join(s['name'] for s in specs)}] no tiene instrumentos."
            )

        # 2) Semilla: bootstrap rápido aislado por curva (Calypso §9.2 paso 1,
        #    ver _seed_group) -- mucho mejor punto de partida que DF=1 plano.
        seed = self._seed_group(specs)

        # 3) Curvas reales del grupo, registradas en el contexto ANTES del
        #    solve para que las referencias cruzadas (discount/projection
        #    entre miembros) resuelvan vía ctx.curve().
        curves: Dict[str, Curve] = {}
        for spec in specs:
            c = Curve(name=spec["name"], valuation_date=self.valuation_date,
                      internal_day_count=spec.get("internal_day_count", internal_day_count))
            curves[spec["name"]] = c
            self.ctx.curves[spec["name"]] = c

        idx_map: List[tuple] = []  # (curve_name, node_idx), alineado con x0
        for spec in specs:
            name = spec["name"]
            for i, ins in enumerate(spec["instruments"]):
                idx = curves[name].add_node(ins.pillar_date)
                curves[name].set_node_log(idx, seed[name][i])
                idx_map.append((name, idx))

        # 4) Solve conjunto: UN least_squares sobre el vector concatenado de
        #    log(DF) de TODAS las curvas del grupo, arrancando de la semilla.
        x0 = np.array([curves[cname].log_dfs[idx] for cname, idx in idx_map])

        def residuals(x):
            for (cname, idx), v in zip(idx_map, x):
                curves[cname].set_node_log(idx, v)
            return np.array([ins.residual(self.ctx) for ins in all_instruments])

        sol = least_squares(residuals, x0, method="lm", xtol=1e-14, ftol=1e-14)
        if not sol.success:
            names = ", ".join(spec["name"] for spec in specs)
            raise BootstrapError(f"[grupo {names}] solve simultáneo no convergió: {sol.message}")
        for (cname, idx), v in zip(idx_map, sol.x):
            curves[cname].set_node_log(idx, v)

        for spec in specs:
            self._check(curves[spec["name"]], spec["instruments"])

        return curves

    # ------------------------------------------------------------------
    def _check(self, curve: Curve, instruments: List[Instrument], tol: float = 1e-8):
        worst = max(abs(ins.residual(self.ctx)) for ins in instruments)
        if worst > tol:
            raise BootstrapError(
                f"[{curve.name}] Repricing check falló: residual máximo "
                f"{worst:.2e} > {tol:.0e}. La curva no reprecia sus propios quotes."
            )
