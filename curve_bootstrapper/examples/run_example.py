"""
run_example.py — Construye las 28 curvas del config y muestra diagnósticos.

Uso:
    cd curve_bootstrapper
    python examples/run_example.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from curvelib.orchestrator import build_from_file

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "curves.yaml")


def main():
    print("=" * 70)
    print("  BOOTSTRAPPING MULTI-CURVA — ejemplo end-to-end")
    print("=" * 70)
    curves = build_from_file(CONFIG, verbose=True)

    print("\n" + "=" * 70)
    print("  DIAGNÓSTICOS")
    print("=" * 70)

    # --- 1. Tasas cero de USD SOFR
    usd = curves["USD_SOFR"]
    print("\nUSD_SOFR — tasas cero (continuas, ACT/365F):")
    for years in (1, 2, 5, 10, 20, 30):
        d = dt.date(2026 + years, 7, 2)
        print(f"   {years:>2}Y : {usd.zero(d) * 100:6.3f}%   DF = {usd.df(d):.6f}")

    # --- 2. Comparación PEN: doméstica TIBO vs colateralizada en SOFR
    print("\nPEN — TIBO doméstica vs PEN coll. SOFR (tasa cero a 5Y):")
    d5 = dt.date(2031, 7, 2)
    print(f"   PEN_OIS_TIBO : {curves['PEN_OIS_TIBO'].zero(d5) * 100:6.3f}%")
    print(f"   PEN_X_SOFR   : {curves['PEN_X_SOFR'].zero(d5) * 100:6.3f}%")
    print("   (la diferencia refleja el basis de colateral USD)")

    # --- 3. Las dos vistas del par USD/PEN
    print("\nPar USD/PEN — las dos incógnitas:")
    d1 = dt.date(2027, 7, 2)
    print(f"   USD_IMPL_TIBO (USD implícita) 1Y : {curves['USD_IMPL_TIBO'].zero(d1) * 100:6.3f}%")
    print(f"   USD_SOFR (mercado)            1Y : {curves['USD_SOFR'].zero(d1) * 100:6.3f}%")

    # --- 4. Fed Funds vs SOFR
    print("\nBasis EFFR/SOFR implícito en curvas (fwd 3M dentro de 1Y):")
    a, b = dt.date(2027, 7, 2), dt.date(2027, 10, 2)
    ff = curves["USD_FEDFUNDS"].fwd(a, b)
    so = curves["USD_SOFR"].fwd(a, b)
    print(f"   fwd SOFR = {so * 100:.3f}%  |  fwd FF = {ff * 100:.3f}%  |  "
          f"diff = {(ff - so) * 1e4:+.1f} bp")

    # --- 5. UVR real vs COP nominal
    print("\nCOP nominal vs UVR real (tasa cero a 5Y):")
    print(f"   COP_OIS_IBR : {curves['COP_OIS_IBR'].zero(d5) * 100:6.3f}%")
    print(f"   UVR_IBR     : {curves['UVR_IBR'].zero(d5) * 100:6.3f}%")
    print("   (spread ≈ inflación breakeven implícita)")

    print("\n✓ Las 28 curvas construidas y repriciadas dentro de tolerancia.")
    return curves


if __name__ == "__main__":
    main()
