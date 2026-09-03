"""Suite del Módulo 2. Ejecutar: python3 tests/test_vollib.py"""
import datetime as _dt, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scipy.stats import norm
from vollib import deltas as dl, dates as dt, smile as sm
from vollib.deltas import DeltaConvention
from vollib.orchestrator import load_config, build_all, build_bid_mid_ask

FAIL = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FALLA'} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond: FAIL.append(name)

print("\n1. Convenciones de delta: ida y vuelta strike -> delta -> strike")
F, tau, dff = 17.0, 1.0, 0.96
for pa in (False, True):
    for sd in (False, True):
        c = DeltaConvention(premium_adjusted=pa, spot_delta=sd)
        for tgt in (0.10, 0.25, 0.40):
            for sig in (0.05, 0.10, 0.25):
                K = dl.strike_from_call_delta(tgt, F, sig, tau, dff, c)
                got = dl.call_delta(F, K, sig, tau, dff, c)
                check(f"call Δ={tgt} σ={sig} {c.label()}", abs(got - tgt) < 1e-10,
                      f"{got} vs {tgt}")
                K = dl.strike_from_put_delta(tgt, F, sig, tau, dff, c)
                got = -dl.put_delta(F, K, sig, tau, dff, c)
                check(f"put  Δ={tgt} σ={sig} {c.label()}", abs(got - tgt) < 1e-10,
                      f"{got} vs {tgt}")

print("\n2. ATM zero-delta straddle: el straddle es efectivamente delta-neutral")
for pa in (False, True):
    for sd in (False, True):
        c = DeltaConvention(premium_adjusted=pa, spot_delta=sd)
        for sig in (0.05, 0.15, 0.30):
            K = dl.atm_strike(F, sig, tau, c, zero_delta_straddle=True)
            net = dl.call_delta(F, K, sig, tau, dff, c) + dl.put_delta(F, K, sig, tau, dff, c)
            check(f"straddle neutral σ={sig} {c.label()}", abs(net) < 1e-12, f"net={net}")

print("\n3. ATM ajustado por prima cae POR DEBAJO del forward (y sin ajuste, por encima)")
c_pa = DeltaConvention(True, False); c_pl = DeltaConvention(False, False)
k_pa = dl.atm_strike(F, 0.20, 1.0, c_pa, True); k_pl = dl.atm_strike(F, 0.20, 1.0, c_pl, True)
check("K_ATM(premium-adj) < F", k_pa < F, f"{k_pa}")
check("K_ATM(plain) > F", k_pl > F, f"{k_pl}")

print("\n4. Álgebra del smile (2vol CP Avg) reproduce RR y BF de entrada")
atm, rr, bf = 0.0972, 0.02288, 0.00425
sc, sp = sm.wing_vols(atm, rr, bf)
check("RR = call - put", abs((sc - sp) - rr) < 1e-15)
check("BF = (call+put)/2 - ATM", abs((sc + sp) / 2 - atm - bf) < 1e-15)

print("\n5. Ala: extension lineal con la pendiente tangente del spline")
import datetime as _d5
_sl = sm.build_slice("T", _d5.date(2027,9,1), _d5.date(2027,9,3), 1.0, 17.5, 0.959,
                     DeltaConvention(True, True),
                     0.0972, 0.022875, 0.00425, 0.043075, 0.01265,
                     zero_delta_straddle=True, wing_slope_factor=1.0)
_lo, _hi = _sl.axis_range()
_h = 1e-4
_slope_in = (_sl.vol_at_call_delta(_lo + _h) - _sl.vol_at_call_delta(_lo)) / _h
_slope_out = (_sl.vol_at_call_delta(_lo) - _sl.vol_at_call_delta(_lo - _h)) / _h
check("pendiente continua en el nodo de 10 delta call", abs(_slope_in - _slope_out) < 1e-6,
      f"{_slope_in:.8f} vs {_slope_out:.8f}")
_x = _lo - 5.0
check("el ala es RECTA (no plana, no curva)",
      abs(_sl.vol_at_call_delta(_x) - (_sl.vol_at_call_delta(_lo) + _slope_out * (_x - _lo))) < 1e-12)
_sl0 = sm.build_slice("T", _d5.date(2027,9,1), _d5.date(2027,9,3), 1.0, 17.5, 0.959,
                      DeltaConvention(True, True),
                      0.0972, 0.022875, 0.00425, 0.043075, 0.01265,
                      zero_delta_straddle=True, wing_slope_factor=0.0)
check("wing_slope_factor=0 -> ala PLANA",
      abs(_sl0.vol_at_call_delta(_x) - _sl0.vol_at_call_delta(_lo)) < 1e-15)
check("el eje del spline es MONOTONO en los 5 nodos",
      all(b > a for a, b in zip([p.call_delta for p in _sl.points],
                                [p.call_delta for p in _sl.points][1:])))

print("\n6. Interpolación de curva log-lineal reproduce los nodos")
cfg = load_config(os.path.join(ROOT, "config", "surfaces.yaml")); cfg["_root"] = ROOT
from vollib.curves import load_calypso_curve
cs = load_calypso_curve(os.path.join(ROOT, "data/curves/usd_sofr.csv"), _dt.date(2026,9,1))
import csv as _csv
with open(os.path.join(ROOT, "data/curves/usd_sofr.csv"), encoding="utf-8-sig") as f:
    for r in _csv.DictReader(f, delimiter=";"):
        off = float(r["Offset"].replace(",","")); want = float(r["Df Mid"].replace(",",""))
        got = cs["mid"].df_offset(off)
        check(f"USD_SOFR nodo offset {off:.0f}", abs(got-want) < 1e-12, f"{got} vs {want}")

print("\n7. REPRICING CHECK: la superficie devuelve la vol de entrada en cada strike calibrado")
surfs, warns = build_all(cfg, side="mid", verbose=False)
worst = 0.0; worst_id = ""
for pair, s in surfs.items():
    for sl in s.slices:
        for p in sl.points:
            if p.strike is None: continue
            got = sl.vol_at_strike(p.strike)
            d = abs(got - p.vol)
            if d > worst: worst, worst_id = d, f"{pair} {sl.tenor} {p.label}"
check("residual maximo < 1e-10 (slice)", worst < 1e-10, f"{worst:.3e} en {worst_id}")

print("\n8. REPRICING a nivel SUPERFICIE (via interpolacion en plazo)")
worst = 0.0; worst_id = ""
for pair, s in surfs.items():
    for sl in s.slices:
        for p in sl.points:
            if p.strike is None: continue
            got = s.vol(sl.expiry, p.strike)
            d = abs(got - p.vol)
            if d > worst: worst, worst_id = d, f"{pair} {sl.tenor} {p.label}"
check("residual maximo < 1e-9 (superficie)", worst < 1e-9, f"{worst:.3e} en {worst_id}")

print("\n9. Sin arbitraje de calendario: varianza total creciente a delta fijo")
bad = []
for pair, s in surfs.items():
    for x in (10.0, 25.0, 50.0, 75.0, 90.0):
        prev = None
        for sl in s.slices:
            w = sl.vol_at_call_delta(x) ** 2 * sl.tau
            if prev is not None and w <= prev: bad.append(f"{pair}@{x}Δ {sl.tenor}")
            prev = w
check("varianza total creciente en los 6 pares", not bad, f"{bad[:5]}")

print("\n9-bis. CONSULTA POR (VENCIMIENTO, STRIKE): round-trip vol -> strike -> vol")
worst, worst_id = 0.0, ""
for pair, s_ in surfs.items():
    for sl in s_.slices:
        for p_ in sl.points:
            got = s_.vol(sl.expiry, p_.strike)          # <- la consulta del Modulo 3
            d = abs(got - p_.vol)
            if d > worst: worst, worst_id = d, f"{pair} {sl.tenor} {p_.label}"
check("vol(expiry, strike) reproduce la vol del nodo, < 1e-10", worst < 1e-10,
      f"{worst:.3e} en {worst_id}")

print("\n9-ter. Fechas INTERMEDIAS: vol(expiry, strike) es continua y consistente")
import datetime as _d9
worst = 0.0
for pair, s_ in surfs.items():
    sl0 = s_.slices[len(s_.slices)//2]
    for off in (1, 5, 20):
        e1 = sl0.expiry + _d9.timedelta(days=off)
        sl1 = s_.slice_at(e1)
        K, v = sl1.strike_at_delta(0.25, "C")
        back = s_.vol(e1, K)
        worst = max(worst, abs(back - v))
check("strike_at_delta y vol(expiry,strike) coinciden fuera de pilar, < 1e-10", worst < 1e-10,
      f"{worst:.3e}")

print("\n9-quater. Calendario: la grilla habil reproduce los feriados de Calypso")
from vollib.dates import Calendar
_cal = Calendar("NYC,MEX")
for _d, _n in [( _d9.date(2026,9,7), "Labor Day"), (_d9.date(2026,9,16), "Independencia MEX"),
               (_d9.date(2027,3,25), "Jueves Santo"), (_d9.date(2028,5,1), "Dia del Trabajo")]:
    check(f"NYC+MEX: {_d} es feriado ({_n})", not _cal.is_business_day(_d))
check("NYC+MEX: 2026-09-08 es habil", _cal.is_business_day(_d9.date(2026,9,8)))
_calb = Calendar("NYC,BRA")
for _d, _n in [(_d9.date(2027,2,8), "Carnaval"), (_d9.date(2027,5,27), "Corpus Christi"),
               (_d9.date(2027,10,12), "Aparecida")]:
    check(f"NYC+BRA: {_d} es feriado ({_n})", not _calb.is_business_day(_d))

print("\n10. bid <= mid <= ask en la vol ATM de cada tenor")
vs, _ = build_bid_mid_ask(cfg, verbose=False)
bad = []
for pair in vs.pairs():
    for sl in vs.sides["mid"][pair].slices:
        v = [vs.sides[s][pair].slice_by_tenor(sl.tenor).atm_vol for s in ("bid","mid","ask")]
        if not (v[0] <= v[1] <= v[2] + 1e-15): bad.append(f"{pair} {sl.tenor} {v}")
check("orden bid<=mid<=ask", not bad, f"{bad[:3]}")

print("\n" + "="*70)
print(f"RESULTADO: {'TODO OK' if not FAIL else f'{len(FAIL)} FALLAS'}")
if FAIL:
    for f_ in FAIL[:15]: print("  -", f_)
sys.exit(1 if FAIL else 0)
