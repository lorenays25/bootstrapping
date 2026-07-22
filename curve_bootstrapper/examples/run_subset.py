"""
run_subset.py — Construir SOLO algunas curvas (una, dos, o las que quieras).

`select_curves` toma tu lista deseada y agrega automáticamente todas las
dependencias necesarias (cierre transitivo del DAG), de modo que nunca te
falte una curva referenciada.

Uso:
    cd curve_bootstrapper
    python examples/run_subset.py
    python examples/run_subset.py USD_SOFR EUR_ESTR
    python examples/run_subset.py PEN_X_SOFR          # incluirá USD_SOFR sola
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from curvelib.orchestrator import load_config, select_curves, build_all

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "curves.yaml")


def main():
    # curvas pedidas por línea de comandos, o un ejemplo por defecto
    wanted = sys.argv[1:] or ["USD_SOFR", "PEN_X_SOFR"]

    cfg = load_config(CONFIG)
    sub = select_curves(cfg, wanted)

    print(f"Curvas pedidas: {wanted}")
    print(f"Sub-config (con dependencias): {sorted(sub['curves'].keys())}\n")

    curves = build_all(sub, verbose=True)

    print("\nTasas cero a 5Y (continuas):")
    d5 = dt.date(2031, 7, 2)
    for name in sorted(curves):
        try:
            print(f"   {name:<16} {curves[name].zero(d5) * 100:6.3f}%")
        except Exception:
            print(f"   {name:<16} (sin nodo a 5Y)")


if __name__ == "__main__":
    main()
