"""
run_bid_mid_ask.py — Demuestra el pipeline completo bid/mid/ask.

1. Carga el YAML de convenciones.
2. Carga la hoja de quotes (CSV formato pantalla) e inyecta bid/mid/ask.
3. Construye las 28 curvas TRES veces (bid, mid, ask) — enfoque A.
4. Genera la tabla de output estilo pantalla para USD_SOFR y la exporta a CSV.

Uso:
    cd curve_bootstrapper
    python examples/run_bid_mid_ask.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from curvelib.orchestrator import load_config, build_bid_mid_ask
from curvelib.quotes_loader import apply_quotes_sheet

HERE = os.path.dirname(__file__)
CONFIG = os.path.join(HERE, "..", "config", "curves.yaml")
QUOTES = os.path.join(HERE, "quotes_usd_sofr.csv")


def main():
    print("=" * 74)
    print("  PIPELINE BID / MID / ASK  (enfoque A: tres bootstraps completos)")
    print("=" * 74)

    config = load_config(CONFIG)

    # --- inyecta la hoja de quotes en la curva USD_SOFR ---------------------
    with open(QUOTES, encoding="utf-8") as f:
        quotes_text = f.read()
    config, warnings = apply_quotes_sheet(
        config, quotes_text,
        curve_map={"SOFR": "USD_SOFR"},   # índice -> curva del YAML
        rate_scale=0.01,                  # la hoja viene en %
    )
    print("\nCarga de la hoja de quotes:")
    for w in warnings:
        print("   -", w)

    # --- construye las tres curvas -----------------------------------------
    print("\nConstruyendo bid / mid / ask ...")
    cs = build_bid_mid_ask(config, verbose=False)
    print("   ✓ 28 curvas × 3 lados construidas")

    # --- tabla de output estilo pantalla para USD_SOFR ---------------------
    print("\n" + "=" * 74)
    print("  TABLA USD_SOFR  (Zero en ACT/360 anual compuesto, como la pantalla)")
    print("=" * 74)
    rows = cs.table("USD_SOFR", zero_day_count="ACT/360")
    hdr = f"{'Date':<12}{'Offset':>7}  {'Zero Bid':>9}{'Zero Mid':>10}{'Zero Ask':>10}" \
          f"  {'Df Bid':>10}{'Df Mid':>10}{'Df Ask':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['date'].isoformat():<12}{r['offset']:>7}  "
              f"{r['zero_bid']*100:>9.5f}{r['zero_mid']*100:>10.5f}{r['zero_ask']*100:>10.5f}  "
              f"{r['df_bid']:>10.6f}{r['df_mid']:>10.6f}{r['df_ask']:>10.6f}")

    out = os.path.join(HERE, "output_usd_sofr.csv")
    cs.to_csv("USD_SOFR", out)
    print(f"\n✓ Tabla exportada a {os.path.relpath(out)}")

    # --- verifica que bid < mid < ask en la zona líquida -------------------
    mid_10y = [r for r in rows if r["offset"] > 3600][:1]
    if mid_10y:
        r = mid_10y[0]
        assert r["df_ask"] <= r["df_mid"] <= r["df_bid"] + 1e-9, \
            "esperaba Df bid >= mid >= ask (tasa ask mayor => DF menor)"
        print("✓ Orden de DF coherente (ask ≤ mid ≤ bid) en el 10Y")


if __name__ == "__main__":
    main()
