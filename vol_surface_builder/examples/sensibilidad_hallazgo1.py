"""
Cuantifica el Hallazgo 1: USD/MXN está configurado con convención G10
(Spot Delta Last Tenor = 1Y, ATM Zero Straddle = 10Y) mientras el manual de
Calypso recomienda 0D/0D para pares emergentes — y USD/PEN, el único par local,
sí está en 0D/0D.

Corre la MISMA superficie con las dos convenciones y compara strikes y vols.
"""
import copy, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from vollib.orchestrator import load_config, build_all

def build(pair, overrides=None):
    cfg = load_config(os.path.join(ROOT, "config", "surfaces.yaml")); cfg["_root"] = ROOT
    cfg["surfaces"] = {pair: copy.deepcopy(cfg["surfaces"][pair])}
    if overrides:
        cfg["surfaces"][pair]["overrides"] = overrides
    s, _ = build_all(cfg, side="mid", verbose=False)
    return s[pair]

EM = {"Spot Delta Last Tenor": "0D", "ATM Zero Straddle Last Tenor": "0D"}

for pair in ("USDMXN", "USDBRL", "USDCLP", "USDCOP"):
    a = build(pair)                # configuración actual (G10: 1Y / 10Y)
    b = build(pair, EM)            # convención de emergentes (0D / 0D)
    print("=" * 104)
    print(f"{pair} — configuración actual (1Y/10Y) vs. convención de emergentes (0D/0D)")
    print("=" * 104)
    print(f"{'Tenor':>5} | {'K 10C actual':>13} {'K 10C 0D/0D':>13} {'dif %':>8} | "
          f"{'K ATM actual':>13} {'K ATM 0D/0D':>13} {'dif %':>8} | "
          f"{'K 10P actual':>13} {'K 10P 0D/0D':>13} {'dif %':>8}")
    worst = 0.0
    for sa, sb in zip(a.slices, b.slices):
        row = f"{sa.tenor:>5} | "
        for lbl in ("10C", "ATM", "10P"):
            ka = next(p for p in sa.points if p.label == lbl).strike
            kb = next(p for p in sb.points if p.label == lbl).strike
            d = (kb / ka - 1) * 100
            worst = max(worst, abs(d))
            row += f"{ka:13.4f} {kb:13.4f} {d:+7.3f}% | "
        print(row)
    print(f"  -> desplazamiento máximo de strike: {worst:.3f}%\n")

# Efecto sobre la VOL que se le asigna a una opción de strike fijo
print("=" * 104)
print("USD/MXN — vol asignada a strikes FIJOS con una y otra convención")
print("=" * 104)
a, b = build("USDMXN"), build("USDMXN", EM)
print(f"{'Tenor':>5} {'Expiry':>11} | " + " ".join(f"{k:>21}" for k in
      ("K=16.00", "K=17.00", "K=19.00", "K=21.00")))
for sa in a.slices:
    row = f"{sa.tenor:>5} {sa.expiry.isoformat():>11} | "
    for K in (16.0, 17.0, 19.0, 21.0):
        va = a.vol(sa.expiry, K) * 100
        vb = b.vol(sa.expiry, K) * 100
        row += f"{va:6.3f}/{vb:6.3f} ({vb-va:+5.3f}) ".rjust(22)
    print(row)
print("\nformato: vol_actual / vol_0D0D (diferencia en puntos de vol)")
