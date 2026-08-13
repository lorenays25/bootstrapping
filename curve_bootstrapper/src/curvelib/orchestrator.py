"""
orchestrator.py — Orquestador del DAG de curvas.

Lee el YAML de configuración, resuelve el orden topológico según
'depends_on' y construye cada curva con el BootstrapEngine.

Estructura esperada del YAML (ver config/curves.yaml):

  valuation_date: 2026-07-02
  market_data:
    fx_spots: { USDPEN: 3.75, EURUSD: 1.09, ... }
  curves:
    USD_SOFR:
      mode: sequential          # o global
      discount: self            # 'self' o nombre de otra curva
      projection: self
      depends_on: []            # curvas que deben existir antes
      conventions: { calendar: US, day_count: ACT/360, spot_lag: 2, fixed_freq: A }
      instruments:
        - { type: ois_swap, tenor: 1M, quote: 0.0430 }
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

import yaml

from .curve import Curve
from .engine import BootstrapEngine
from .instruments import CurveContext, make_instrument


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("valuation_date", "curves"):
        if key not in cfg:
            raise ConfigError(f"Falta la clave obligatoria '{key}' en {path}")
    return cfg


def topological_order(curves_cfg: dict) -> List[str]:
    """Ordena las curvas de modo que toda dependencia se construya antes."""
    order, state = [], {}  # state: 0 sin visitar, 1 en proceso, 2 listo

    def visit(name: str, chain=()):
        if name not in curves_cfg:
            raise ConfigError(
                f"'{name}' aparece en depends_on pero no está definida en curves. "
                f"Cadena: {' -> '.join(chain + (name,))}"
            )
        s = state.get(name, 0)
        if s == 2:
            return
        if s == 1:
            raise ConfigError(f"Dependencia circular: {' -> '.join(chain + (name,))}")
        state[name] = 1
        for dep in curves_cfg[name].get("depends_on", []) or []:
            visit(dep, chain + (name,))
        state[name] = 2
        order.append(name)

    for name in curves_cfg:
        visit(name)
    return order


def _collect_dependencies(curves_cfg: dict, wanted: List[str]) -> List[str]:
    """Dada una lista de curvas deseadas, devuelve el conjunto CERRADO que
    incluye todas sus dependencias (transitivas). Necesario para construir
    un subconjunto sin que falte ninguna curva referenciada."""
    needed = set()

    def add(name: str, chain=()):
        if name not in curves_cfg:
            raise ConfigError(
                f"'{name}' no está definida en el YAML. "
                f"{'Requerida por ' + ' -> '.join(chain) if chain else ''}"
            )
        if name in needed:
            return
        needed.add(name)
        for dep in curves_cfg[name].get("depends_on", []) or []:
            add(dep, chain + (name,))

    for w in wanted:
        add(w)
    return list(needed)


def select_curves(config: dict, wanted: List[str]) -> dict:
    """Devuelve una COPIA del config con solo las curvas `wanted` MÁS sus
    dependencias (cierre transitivo). Con esto puedes construir una, dos, o
    cualquier subconjunto de curvas sin editar el YAML a mano ni obtener
    errores de dependencias faltantes.

    Ejemplo:
        cfg = load_config("config/curves.yaml")
        sub = select_curves(cfg, ["PEN_X_SOFR"])   # incluye USD_SOFR sola
        curves = build_all(sub)
    """
    import copy
    if isinstance(wanted, str):
        wanted = [wanted]
    needed = _collect_dependencies(config["curves"], wanted)
    sub = copy.deepcopy(config)
    sub["curves"] = {n: config["curves"][n] for n in needed}
    return sub


# ---------------------------------------------------------------------------
def _resolve(ref: str | None, own_name: str) -> str | None:
    return own_name if ref in ("self", None) else ref


def build_all(config: dict, verbose: bool = True, side: str = "mid") -> Dict[str, Curve]:
    """Construye todas las curvas del YAML para un lado (bid/mid/ask) y
    devuelve {nombre: Curve}."""
    val_date = config["valuation_date"]
    if isinstance(val_date, str):
        val_date = _dt.date.fromisoformat(val_date[:10])

    md = config.get("market_data", {}) or {}
    ctx = CurveContext(curves={}, fx_spots=md.get("fx_spots", {}) or {})
    engine = BootstrapEngine(val_date, ctx)

    # Presets de convención con nombre, definidos en la raíz del YAML.
    # Un instrumento los referencia con `convention: <nombre>`; sirven para
    # no repetir la misma convención en, p.ej., los 8 bonos de una curva.
    presets = config.get("conventions", {}) or {}
    conv_warnings: List[str] = []

    curves_cfg = config["curves"]
    order = topological_order(curves_cfg)
    if verbose:
        print(f"Fecha de valuación: {val_date}   |   lado: {side}")
        print(f"Orden de construcción ({len(order)} curvas):")
        for i, n in enumerate(order, 1):
            print(f"  {i:>2}. {n}")
        print()

    for name in order:
        spec = curves_cfg[name]
        conv = dict(spec.get("conventions", {}) or {})
        curve_names = {
            "target": name,
            "discount": _resolve(spec.get("discount"), name),
            "projection": _resolve(spec.get("projection"), name),
            "other_leg": _resolve(spec.get("other_leg"), name),
        }
        warns: List[str] = []
        instruments = [make_instrument(ispec, conv, curve_names, side=side,
                                       presets=presets, warnings_out=warns)
                       for ispec in spec.get("instruments", [])]
        if not instruments:
            raise ConfigError(f"[{name}] no tiene instrumentos definidos.")
        conv_warnings.extend(f"[{name}] {w}" for w in warns)

        curve = engine.build_curve(
            name, instruments,
            mode=spec.get("mode", "sequential"),
            internal_day_count=spec.get("internal_day_count", "ACT/365F"),
        )
        if verbose:
            last_t = curve.times[-1]
            print(f"  ✓ {name:<22} {len(curve.times) - 1:>2} pilares | "
                  f"último nodo t={last_t:6.2f}y  DF={curve.df_t(last_t):.6f}")

    if verbose and conv_warnings:
        print(f"\n  Avisos de convención ({len(conv_warnings)}):")
        for w in dict.fromkeys(conv_warnings):     # dedup preservando orden
            print(f"    ! {w}")

    return ctx.curves


def convention_report(config: dict, side: str = "mid") -> dict:
    """Reporte de la convención EFECTIVA de cada instrumento de cada curva,
    con la procedencia de cada campo (curva / preset / instrumento / default).

    Es la vista de auditoría para reconciliar contra el sistema de primera
    línea: responde "¿qué settlement_lag se usó en este pilar y de dónde
    salió?" sin leer el YAML a mano. Usa exactamente el mismo resolutor que
    el cálculo, así que lo reportado es lo que se calculó.
    """
    from .conventions import effective_conventions
    from .instruments import (CONVENTION_SCHEMA, class_defaults_for_type,
                              resolve_instrument_conventions)

    presets = config.get("conventions", {}) or {}
    out = {"curves": {}, "warnings": []}
    for name, spec in config["curves"].items():
        conv = dict(spec.get("conventions", {}) or {})
        rows = []
        types_present = []
        for idx, ispec in enumerate(spec.get("instruments", []) or []):
            itype, resolved, prov, warns = resolve_instrument_conventions(
                ispec, conv, presets)
            out["warnings"].extend(f"[{name}] {w}" for w in warns)
            if itype not in types_present:
                types_present.append(itype)
            rows.append({
                "index": idx,
                "type": itype,
                "label": ispec.get("tenor") or ispec.get("maturity")
                         or ispec.get("ticker") or f"#{idx}",
                "preset": ispec.get("convention"),
                "conventions": effective_conventions(
                    itype, resolved, prov, CONVENTION_SCHEMA,
                    class_defaults_for_type(itype)),
            })

        # Una convención definida a nivel de curva que no aplica a NINGUNO de
        # sus instrumentos se hereda y nunca se lee: no rompe nada, pero no
        # hace nada. Es fácil de cometer (p.ej. poner settlement_lag, que solo
        # usan los bonos, en una curva que solo tiene swaps) y sin este aviso
        # pasa inadvertido.
        for field in conv:
            fspec = CONVENTION_SCHEMA.get(field)
            if fspec is None:
                out["warnings"].append(
                    f"[{name}] convención de curva desconocida '{field}' (¿typo?). Se ignora.")
                continue
            applies = fspec.get("applies_to")
            if applies and types_present and not (set(applies) & set(types_present)):
                out["warnings"].append(
                    f"[{name}] la convención de curva '{field}' no la usa ningún "
                    f"instrumento de esta curva (tipos presentes: "
                    f"{', '.join(sorted(types_present))}). No tiene efecto.")

        out["curves"][name] = {"types_present": sorted(types_present),
                               "instruments": rows}
    return out


class CurveSet:
    """Agrupa las tres curvas (bid/mid/ask) de cada nombre y expone la
    generación de tablas de output estilo pantalla."""
    def __init__(self, bid, mid, ask, valuation_date):
        self.sides = {"bid": bid, "mid": mid, "ask": ask}
        self.valuation_date = valuation_date

    def names(self):
        return list(self.sides["mid"].keys())

    def table(self, name: str, zero_day_count: str = "ACT/360"):
        """Devuelve la tabla de una curva, replicando la pantalla:
        [{date, offset, zero_bid, zero_mid, zero_ask, df_bid, df_mid, df_ask}]
        Las fechas de pilar se toman de la curva MID (los tres lados comparten
        exactamente los mismos pilares porque nacen del mismo YAML)."""
        mid = self.sides["mid"][name]
        rows = []
        for d in mid.pillar_dates:
            row = {"date": d, "offset": (d - self.valuation_date).days}
            for side in ("bid", "mid", "ask"):
                c = self.sides[side][name]
                row[f"zero_{side}"] = c.zero_rate_annual(d, zero_day_count)
                row[f"df_{side}"] = c.df(d)
            rows.append(row)
        return rows

    def to_csv(self, name: str, path: str, zero_day_count: str = "ACT/360"):
        import csv
        rows = self.table(name, zero_day_count)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Offset", "Zero Bid", "Zero Mid", "Zero Ask",
                        "Df Bid", "Df Mid", "Df Ask"])
            for r in rows:
                w.writerow([r["date"].isoformat(), r["offset"],
                            f"{r['zero_bid']*100:.5f}", f"{r['zero_mid']*100:.5f}",
                            f"{r['zero_ask']*100:.5f}", f"{r['df_bid']:.8f}",
                            f"{r['df_mid']:.8f}", f"{r['df_ask']:.8f}"])
        return path


def build_bid_mid_ask(config: dict, verbose: bool = False) -> CurveSet:
    """Ejecuta el pipeline COMPLETO tres veces (bid, mid, ask) — enfoque A —
    y agrupa el resultado en un CurveSet. Aplica a las 28 curvas."""
    val_date = config["valuation_date"]
    if isinstance(val_date, str):
        val_date = _dt.date.fromisoformat(val_date[:10])
    results = {}
    for side in ("bid", "mid", "ask"):
        results[side] = build_all(config, verbose=verbose, side=side)
    return CurveSet(results["bid"], results["mid"], results["ask"], val_date)


def build_from_file(path: str, verbose: bool = True) -> Dict[str, Curve]:
    return build_all(load_config(path), verbose=verbose)
