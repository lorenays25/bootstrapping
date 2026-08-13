"""
test_conventions_and_bonds.py — Verifica el cambio de convenciones por
instrumento y el instrumento sovereign_bond.

Correr:  python3 tests/test_conventions_and_bonds.py
(desde el directorio curve_bootstrapper)
"""
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from curvelib import conventions as conv
from curvelib.instruments import (CONVENTION_SCHEMA, REQUIRED_BY_TYPE,
                                  CurveContext, conventions_for_type,
                                  make_instrument,
                                  resolve_instrument_conventions)
from curvelib.orchestrator import (build_all, build_bid_mid_ask, load_config,
                                   convention_report)
from curvelib.quotes_loader import apply_quotes_sheet, parse_quotes_csv

OK, FAIL = [], []


def check(cond, label, detail=""):
    (OK if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FALLA'}  {label}{('  -> ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
print("\n1) JERARQUÍA DE RESOLUCIÓN (curva < preset < instrumento)")
presets = {"bono_pe": {"type": "sovereign_bond", "settlement_lag": 1,
                       "business_day_convention": "F"}}
curve_conv = {"calendar": "PE", "spot_lag": 2, "business_day_convention": "MF"}

# a) instrumento que hereda todo de la curva
r, p = conv.resolve({"type": "ois_swap", "tenor": "2Y", "quote": 0.04}, curve_conv, presets)
check(r["spot_lag"] == 2 and p["spot_lag"] == "curve",
      "instrumento sin overrides hereda de la curva", f"spot_lag={r['spot_lag']} ({p['spot_lag']})")

# b) preset gana sobre la curva
r, p = conv.resolve({"convention": "bono_pe", "maturity": "2029-02-12", "coupon": 5.94,
                     "quote": 100.0}, curve_conv, presets)
check(r["business_day_convention"] == "F" and p["business_day_convention"] == "preset",
      "preset gana sobre la convención de la curva", f"bdc={r['business_day_convention']} ({p['business_day_convention']})")
check(r["calendar"] == "PE" and p["calendar"] == "curve",
      "lo no fijado por el preset se sigue heredando de la curva")

# c) instrumento gana sobre el preset
r, p = conv.resolve({"convention": "bono_pe", "maturity": "2029-02-12", "coupon": 5.94,
                     "settlement_lag": 3, "quote": 100.0}, curve_conv, presets)
check(r["settlement_lag"] == 3 and p["settlement_lag"] == "instrument-flat",
      "campo suelto del instrumento gana sobre el preset", f"settlement_lag={r['settlement_lag']}")

# d) sub-bloque conventions: del instrumento
r, p = conv.resolve({"type": "ois_swap", "tenor": "2Y", "quote": 0.04,
                     "conventions": {"spot_lag": 0}}, curve_conv, presets)
check(r["spot_lag"] == 0 and p["spot_lag"] == "instrument",
      "sub-bloque `conventions:` del instrumento gana sobre la curva")

# e) tipo heredado del preset
check(conv.instrument_type({"convention": "bono_pe"}, presets) == "sovereign_bond",
      "el tipo se hereda del preset si el instrumento no lo declara")

# f) preset inexistente -> error claro
try:
    conv.resolve({"convention": "no_existe", "quote": 1}, curve_conv, presets)
    check(False, "preset inexistente lanza error")
except conv.ConventionError as e:
    check("no está definida" in str(e), "preset inexistente lanza error claro")


# ---------------------------------------------------------------------------
print("\n2) CONVENCIONES DISTINTAS PARA INSTRUMENTOS DE LA MISMA CURVA")
cfg_mix = {
    "valuation_date": "2026-07-02",
    "market_data": {"fx_spots": {}},
    "conventions": {"bono_soberano_pen": {"type": "sovereign_bond", "settlement_lag": 1,
                                      "coupon_freq": "semiannual",
                                      "price_type": "clean_price"}},
    "curves": {"PEN": {
        "mode": "global", "discount": "self", "projection": "self", "depends_on": [],
        "conventions": {"calendar": "PE", "day_count": "ACT/360", "spot_lag": 2,
                        "fixed_freq": "A"},
        "instruments": [
            {"type": "ois_swap", "tenor": "1Y", "quote": 0.0435},
            {"type": "ois_swap", "tenor": "2Y", "quote": 0.0445},
            {"convention": "bono_soberano_pen", "maturity": "2029-02-12",
             "coupon": 5.94, "quote": 105.954},
        ]}},
}
val = _dt.date(2026, 7, 2)
names = {k: "PEN" for k in ("target", "discount", "projection", "other_leg")}
ins_swap = make_instrument(cfg_mix["curves"]["PEN"]["instruments"][0],
                           cfg_mix["curves"]["PEN"]["conventions"], names,
                           presets=cfg_mix["conventions"])
ins_bond = make_instrument(cfg_mix["curves"]["PEN"]["instruments"][2],
                           cfg_mix["curves"]["PEN"]["conventions"], names,
                           presets=cfg_mix["conventions"])
ins_swap.build(val)
ins_bond.build(val)
check(ins_swap.start_date != ins_bond.settle_date,
      "en la MISMA curva, el swap usa spot T+2 y el bono liquidación T+1",
      f"swap spot={ins_swap.start_date} vs bono settle={ins_bond.settle_date}")
check((ins_bond.settle_date - val).days < (ins_swap.start_date - val).days,
      "la liquidación del bono es anterior a la del swap")


# ---------------------------------------------------------------------------
print("\n3) BONO: schedule, corrido y repricing")
check(ins_bond.pillar_date == _dt.date(2029, 2, 12),
      "el pilar del bono es su vencimiento")
check(all(d.month in (2, 8) and d.day == 12 for d in ins_bond.coupon_dates),
      "el ciclo de cupones queda anclado al vencimiento (12-Feb / 12-Ago)",
      f"{len(ins_bond.coupon_dates)} cupones futuros")
check(abs(ins_bond._coupon_amount() - 5.94 / 2) < 1e-12,
      "cupón semestral regular = cupón/2 exacto (30/360 sin stub)")
check(0.0 <= ins_bond._accrued() <= 5.94 / 2,
      "corrido dentro del rango de un cupón", f"{ins_bond._accrued():.6f}")
check(ins_bond.market_dirty_price() > ins_bond.quote,
      "precio sucio = limpio + corrido",
      f"{ins_bond.quote} -> {ins_bond.market_dirty_price():.6f}")

curves = build_all(cfg_mix, verbose=False)
ctx = CurveContext(curves=curves, fx_spots={})
worst = 0.0
for spec in cfg_mix["curves"]["PEN"]["instruments"]:
    i = make_instrument(spec, cfg_mix["curves"]["PEN"]["conventions"], names,
                        presets=cfg_mix["conventions"])
    i.build(val)
    worst = max(worst, abs(i.residual(ctx)))
check(worst < 1e-10, "la curva reprecia todos sus quotes (bonos incluidos)",
      f"residual máximo {worst:.2e}")

# price_type dirty: el quote ya es sucio, no se le suma corrido
spec_dirty = dict(cfg_mix["curves"]["PEN"]["instruments"][2], price_type="dirty_price")
ins_d = make_instrument(spec_dirty, cfg_mix["curves"]["PEN"]["conventions"], names,
                        presets=cfg_mix["conventions"])
ins_d.build(val)
check(abs(ins_d.market_dirty_price() - ins_d.quote) < 1e-12,
      "price_type=dirty_price NO suma corrido")


# ---------------------------------------------------------------------------
print("\n4) VALIDACIÓN")
try:
    resolve_instrument_conventions({"type": "sovereign_bond", "quote": 100.0},
                                   {"calendar": "PE"}, {})
    check(False, "bono sin maturity lanza error")
except conv.ConventionError as e:
    check("maturity" in str(e), "bono sin 'maturity' falla con mensaje claro")

_, _, _, warns = resolve_instrument_conventions(
    {"type": "ois_swap", "tenor": "2Y", "quote": 0.04, "coupon_freq": "semiannual"},
    {"calendar": "PE"}, {})
check(any("no aplica" in w for w in warns),
      "convención de bono puesta en un swap genera aviso")

_, _, _, warns = resolve_instrument_conventions(
    {"type": "ois_swap", "tenor": "2Y", "quote": 0.04, "spot_lagg": 2},
    {"calendar": "PE"}, {})
check(any("desconocida" in w for w in warns), "typo en el nombre de la convención genera aviso")

# campo heredado de la curva que no aplica a un tipo -> SIN aviso (curva mixta)
_, _, _, warns = resolve_instrument_conventions(
    {"type": "xccy_fixed_float", "tenor": "2Y", "quote": 0.04},
    {"calendar": "PE", "solve_for": "quote_ccy", "points_factor": 10000}, {})
check(not warns, "campos heredados de una curva mixta no generan ruido", f"{warns}")

try:
    resolve_instrument_conventions(
        {"convention": "b", "maturity": "2029-02-12", "coupon": 5.0, "quote": 100.0},
        {}, {"b": {"type": "sovereign_bond", "price_type": "inventado"}})
    check(False, "valor de enum inválido lanza error")
except conv.ConventionError:
    check(True, "valor de enum inválido lanza error")

check("coupon_freq" not in conventions_for_type("ois_swap")
      and "rate_cutoff_days" not in conventions_for_type("sovereign_bond"),
      "el catálogo filtra convenciones por tipo de instrumento")


# ---------------------------------------------------------------------------
print("\n5) CARGA DE QUOTES DESDE HOJA (precios de bonos)")
csv_text = """Quote Name,Type,BID,MID,ASK
Swap.1Y.PEN.TIBO,Yield,4.30,4.35,4.40
Bond.PEN.TIBO.2029-02-12,Price,105.857,105.954,106.051
Bono.PERUGB.02/12/55,Price,101.752,102.810,103.869
"""
recs = parse_quotes_csv(csv_text, rate_scale=0.01)
by_name = {r["raw"]: r for r in recs}
check(abs(by_name["Swap.1Y.PEN.TIBO"]["mid"] - 0.0435) < 1e-12,
      "las tasas SÍ se escalan por rate_scale", "4.35 -> 0.0435")
check(abs(by_name["Bond.PEN.TIBO.2029-02-12"]["mid"] - 105.954) < 1e-12,
      "los precios de bonos NO se escalan", "105.954 se mantiene")
check(by_name["Bond.PEN.TIBO.2029-02-12"]["maturity"] == _dt.date(2029, 2, 12),
      "vencimiento en ISO reconocido en el nombre del quote")
check(by_name["Bono.PERUGB.02/12/55"]["maturity"] == _dt.date(2055, 2, 12),
      "vencimiento en formato Bloomberg (02/12/55) reconocido")

cfg_load = {
    "curves": {"PEN_OIS_TIBO": {"instruments": [
        {"type": "ois_swap", "tenor": "1Y", "quote": 0.0},
        {"convention": "bono_soberano_pen", "maturity": _dt.date(2029, 2, 12),
         "coupon": 5.94, "quote": 0.0},
    ]}}
}
cfg_load, warnings = apply_quotes_sheet(
    cfg_load, "Quote Name,Type,BID,MID,ASK\nBond.X.2029-02-12,Price,105.857,105.954,106.051\n",
    curve_map={None: "PEN_OIS_TIBO"})
bond_spec = cfg_load["curves"]["PEN_OIS_TIBO"]["instruments"][1]
check(isinstance(bond_spec["quote"], dict) and abs(bond_spec["quote"]["mid"] - 105.954) < 1e-12,
      "el quote del bono se empareja por VENCIMIENTO y llega sin escalar",
      f"{bond_spec['quote']}")


# ---------------------------------------------------------------------------
print("\n6) YAML REAL: build, bid/mid/ask y reporte de convenciones")
cfg = load_config("config/curves.yaml")
curves = build_all(cfg, verbose=False)
check(len(curves) >= 29, f"construyen todas las curvas del YAML ({len(curves)})")

pen = curves["PEN_OIS_TIBO"]
check(_dt.date(2055, 2, 12) in pen.pillar_dates,
      "la curva PEN llega a 2055 gracias a los bonos")

cs = build_bid_mid_ask(cfg, verbose=False)
rows = cs.table("PEN_OIS_TIBO")
last = rows[-1]
check(last["zero_bid"] > last["zero_ask"],
      "bid/ask del bono más largo se propaga a la curva (precio alto = tasa baja)",
      f"zero bid={last['zero_bid']*100:.3f}% ask={last['zero_ask']*100:.3f}%")

rep = convention_report(cfg)
pen_rows = rep["curves"]["PEN_OIS_TIBO"]["instruments"]
bond_row = next(r for r in pen_rows if r["type"] == "sovereign_bond")
swap_row = next(r for r in pen_rows if r["type"] == "ois_swap")
b_settle = next(c for c in bond_row["conventions"] if c["field"] == "settlement_lag")
s_spot = next(c for c in swap_row["conventions"] if c["field"] == "spot_lag")
check(b_settle["value"] == 1 and b_settle["source"] == "preset",
      "el reporte muestra settlement_lag=1 del bono y su procedencia (preset)")
check(s_spot["value"] == 2 and s_spot["source"] == "curve",
      "el reporte muestra spot_lag=2 del swap heredado de la curva")
check(not any("no aplica" in w or "desconocida" in w for w in rep["warnings"]),
      "el YAML del repo no genera avisos de convención", f"{rep['warnings'][:2]}")
check(rep["curves"]["PEN_OIS_TIBO"]["types_present"] == ["mm", "ois_swap", "sovereign_bond"],
      "el reporte lista los tipos presentes en la curva (para filtrar la UI)",
      f"{rep['curves']['PEN_OIS_TIBO']['types_present']}")

# convención de curva que no la usa ningún instrumento -> aviso
cfg_inutil = {"curves": {"SOLO_SWAPS": {
    "conventions": {"calendar": "US", "spot_lag": 2, "settlement_lag": 1},
    "instruments": [{"type": "ois_swap", "tenor": "1Y", "quote": 0.04}]}}}
w = convention_report(cfg_inutil)["warnings"]
check(any("settlement_lag" in x and "No tiene efecto" in x for x in w),
      "avisa si una convención de curva no la usa ningún instrumento (settlement_lag en curva sin bonos)")
check(not any("spot_lag" in x for x in w),
      "no avisa de las que sí se usan")


# ---------------------------------------------------------------------------
print("\n7) PAY DELAY POR PATA (fixed vs float)")
names_pen = {k: "PEN" for k in ("target", "discount", "projection", "other_leg")}
base_conv = {"calendar": "PE", "day_count": "ACT/360", "spot_lag": 2, "fixed_freq": "A"}

def mk(spec, conv=None):
    i = make_instrument(spec, conv or base_conv, names_pen, presets={})
    i.build(_dt.date(2026, 7, 2))
    return i

# compatibilidad: si solo se define la genérica, ambas patas la usan
i = mk({"type": "ois_swap", "tenor": "2Y", "quote": 0.04, "pay_delay_days": 3})
check(i._pay_delay("fixed") == 3 and i._pay_delay("float") == 3,
      "pay_delay_days genérico se aplica a ambas patas (comportamiento histórico)")

# diferenciación por pata
i = mk({"type": "ois_swap", "tenor": "2Y", "quote": 0.04,
        "fixed_pay_delay_days": 0, "float_pay_delay_days": 2})
check(i._pay_delay("fixed") == 0 and i._pay_delay("float") == 2,
      "cada pata puede tener su propio pay delay", "fija=0, flotante=2")

# la específica gana sobre la genérica, y solo en su pata
i = mk({"type": "ois_swap", "tenor": "2Y", "quote": 0.04,
        "pay_delay_days": 5, "float_pay_delay_days": 1})
check(i._pay_delay("float") == 1 and i._pay_delay("fixed") == 5,
      "la específica de una pata gana; la otra sigue con la genérica")

# default 0 si no se define nada
i = mk({"type": "ois_swap", "tenor": "2Y", "quote": 0.04})
check(i._pay_delay("fixed") == 0 and i._pay_delay("float") == 0,
      "sin configuración, ambas patas tienen pay delay 0")

# efecto numérico real: un delay solo en la fija debe mover el precio
cfg_pd = {"valuation_date": "2026-07-02", "market_data": {"fx_spots": {}},
          "curves": {"PEN": {"mode": "sequential", "discount": "self",
                             "projection": "self", "depends_on": [],
                             "conventions": base_conv,
                             "instruments": [
                                 {"type": "ois_swap", "tenor": "1Y", "quote": 0.0435},
                                 {"type": "ois_swap", "tenor": "5Y", "quote": 0.0470}]}}}
c0 = build_all(cfg_pd, verbose=False)["PEN"]
import copy as _copy
cfg_pd2 = _copy.deepcopy(cfg_pd)
cfg_pd2["curves"]["PEN"]["instruments"][1]["fixed_pay_delay_days"] = 10
c1 = build_all(cfg_pd2, verbose=False)["PEN"]
d = abs(c0.df(c0.pillar_dates[-1]) - c1.df(c1.pillar_dates[-1]))
check(d > 0, "un pay delay solo en la pata fija cambia la curva", f"ΔDF={d:.2e}")


# ---------------------------------------------------------------------------
print("\n8) TRAMO CORTO PEN: ON, At Maturity y SemiAnnual")
cfg_r = load_config("config/curves.yaml")
pen_spec = cfg_r["curves"]["PEN_OIS_TIBO"]
pconv = pen_spec["conventions"]
pnames = {k: "PEN_OIS_TIBO" for k in ("target", "discount", "projection", "other_leg")}

def build_pen(spec):
    i = make_instrument(spec, pconv, pnames, presets=cfg_r.get("conventions"))
    i.build(cfg_r["valuation_date"])
    return i

by_tenor = {sp.get("tenor"): sp for sp in pen_spec["instruments"] if sp.get("tenor")}
check(by_tenor["ON"]["type"] == "mm", "el ON (TIBO) entra como depósito, no como swap")
on = build_pen(by_tenor["ON"])
check(on.start_date == cfg_r["valuation_date"],
      "el ON arranca en la fecha de valuación (T+0), sin usar spot_lag")

for t in ("3M", "6M", "9M", "12M"):
    n = len(build_pen(by_tenor[t]).fixed_dates)
    check(n == 1, f"{t} At Maturity: un solo pago", f"{n} pago(s)")

i18 = build_pen(by_tenor["18M"])
check(len(i18.fixed_dates) == 1,
      "18M At Maturity: un solo pago (requiere fixed_freq: Z porque supera 1Y)",
      f"{len(i18.fixed_dates)} pago(s)")
check(by_tenor["18M"].get("fixed_freq") == "Z",
      "el 18M lleva fixed_freq: Z explícito; sin él pagaría dos veces")

i24 = build_pen(by_tenor["24M"])
check(by_tenor["24M"].get("fixed_freq") == "S" and len(i24.fixed_dates) == 4,
      "24M SemiAnnual: cuatro pagos semestrales", f"{len(i24.fixed_dates)} pagos")
check(pconv.get("fixed_freq") == "A",
      "la curva sigue con fixed_freq: A; 18M y 24M lo pisan solo en su instrumento")

curves_r = build_all(cfg_r, verbose=False)
pen_c = curves_r["PEN_OIS_TIBO"]
ctx_r = CurveContext(curves=curves_r,
                     fx_spots=cfg_r.get("market_data", {}).get("fx_spots", {}))
worst_r = 0.0
for sp in pen_spec["instruments"]:
    ins = build_pen(sp)
    worst_r = max(worst_r, abs(ins.residual(ctx_r)))
check(worst_r < 1e-10, "la curva PEN completa reprecia todos sus quotes",
      f"{len(pen_c.pillar_dates)} pilares, residual máx {worst_r:.2e}")


# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print(f"RESULTADO: {len(OK)} pasaron, {len(FAIL)} fallaron")
if FAIL:
    for f in FAIL:
        print(f"  FALLA: {f}")
    sys.exit(1)
print("Todo OK")
