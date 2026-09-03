"""Construye las 6 superficies (bid/mid/ask), muestra diagnósticos y exporta CSV.

Ejecutar desde la raíz del repo o desde cualquier sitio:

    python vol_surface_builder/examples/run_all.py
"""
import os, sys, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from vollib.orchestrator import load_config, build_bid_mid_ask

cfg = load_config(os.path.join(ROOT, "config", "surfaces.yaml"))
cfg["_root"] = ROOT
vs, avisos = build_bid_mid_ask(cfg, verbose=True)

PUNTOS = ("10C", "25C", "ATM", "25P", "10P")

print("\n" + "=" * 100)
print("SMILE CALIBRADO — USDMXN, lado mid   (eje = call delta PLANO)")
print("=" * 100)
s = vs.sides["mid"]["USDMXN"]
print(f"{'Tenor':>5} {'Expiry':>11} {'Entrega':>11} {'Fwd':>10} | " +
      " ".join(f"{lbl:>16}" for lbl in PUNTOS))
for sl in s.slices:
    row = f"{sl.tenor:>5} {sl.expiry.isoformat():>11} {sl.delivery.isoformat():>11} {sl.forward:10.4f} | "
    for lbl in PUNTOS:
        p = next(p for p in sl.points if p.label == lbl)
        row += f"{p.call_delta:5.1f}Δ {p.vol*100:6.3f} ".rjust(17)
    print(row)

print("\nStrikes (mid):")
print(f"{'Tenor':>5} | " + " ".join(f"{l:>10}" for l in PUNTOS))
for sl in s.slices:
    row = f"{sl.tenor:>5} | "
    for lbl in PUNTOS:
        p = next(p for p in sl.points if p.label == lbl)
        row += f"{p.strike:10.4f} "
    print(row)

# ---------------------------------------------------------------- consulta
print("\n" + "=" * 100)
print("CONSULTA POR (VENCIMIENTO, STRIKE) — lo que consume el Módulo 3")
print("=" * 100)
exp = dt.date(2027, 3, 15)                     # una fecha cualquiera, no es pilar
F = s.forward(exp)
lo, hi = s.slice_at(exp).axis_range()
print(f"USDMXN  vencimiento {exp:%d/%m/%Y}  forward {F:.6f}  entrega {s.delivery_for(exp):%d/%m/%Y}")
print(f"{'strike':>12} {'K/F':>8} {'vol bid %':>11} {'vol mid %':>11} {'vol ask %':>11}")
for m in (0.95, 1.00, 1.05, 1.10, 1.20):
    K = F * m
    v = vs.vol("USDMXN", exp, K)
    print(f"{K:12.5f} {m:8.4f} {100*v['bid']:11.5f} {100*v['mid']:11.5f} {100*v['ask']:11.5f}")
print(f"(el spline cubre call delta plano {lo:.2f}–{hi:.2f}; fuera de ahí actúa el ala lineal)")

if avisos:
    print(f"\nAVISOS ({len(avisos)}):")
    for a in dict.fromkeys(avisos):
        print(f"  ! {a}")

outdir = os.path.join(ROOT, "examples", "output")
os.makedirs(outdir, exist_ok=True)
for pair in vs.pairs():
    vs.to_csv(pair, os.path.join(outdir, f"{pair.lower()}_surface.csv"))
print(f"\nCSV exportados a examples/output/ ({len(vs.pairs())} superficies)")
