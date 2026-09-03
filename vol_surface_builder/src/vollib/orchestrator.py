"""
orchestrator.py — YAML → superficies.

Espejo de `curvelib.orchestrator`: el YAML declara una entrada por superficie con
sus archivos y sus convenciones, y el orquestador arma las tres superficies
(bid/mid/ask) de cada par.

    valuation_date: 2026-09-01
    market_data:
      fx_spots_file: data/curves/tc.csv
      curves:
        USD: data/curves/usd_sofr.csv
        MXN: data/curves/mxn_coll_usd_sofr.csv
    surfaces:
      USDMXN:
        base_ccy: USD
        quote_ccy: MXN
        delivery_lag: 2
        quotes: {mid: data/vol_quotes/quotes_USDMXN.csv}
        parameters: data/vol_quotes/par_USDMXN.csv
        underlyings: data/vol_quotes/und_USDMXN.csv

Las convenciones NO se escriben a mano en el YAML: se leen del panel de
parámetros exportado de Calypso (`parameters:`), que es la fuente de verdad. El
YAML solo puede sobreescribirlas explícitamente vía `overrides:`, que es el
mecanismo para correr análisis de sensibilidad — por ejemplo construir USD/MXN
con `spot_delta_last_tenor: 0D` para medir el impacto del Hallazgo 1.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Dict, List, Tuple

import yaml

from . import curves as cv
from . import dates as dt
from . import quotes_loader as ql
from . import smile as sm
from .deltas import DeltaConvention
from .surface import ForwardModel, VolSurface, VolSurfaceSet

SIDES = ("bid", "mid", "ask")


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for k in ("valuation_date", "surfaces", "market_data"):
        if k not in cfg:
            raise ConfigError(f"Falta la clave obligatoria '{k}' en {path}")
    cfg["_base_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg


def _path(cfg: dict, p: str) -> str:
    if os.path.isabs(p):
        return p
    root = cfg.get("_root") or os.path.dirname(cfg["_base_dir"])
    return os.path.join(root, p)


def _resolve_cut_date(tenor_label: str, quotes: Dict[str, dict],
                      valuation_date: _dt.date) -> _dt.date:
    """Convierte un 'Last Tenor' (p.ej. `1Y`, `10Y`, `0D`) en la fecha hasta la
    cual aplica la convención.

    `0D` significa "ninguna expiración" → se devuelve la propia fecha de
    valuación, con lo que ningún expiry queda `<= cut`. Si el tenor existe en la
    hoja de quotes se usa SU expiry real; si no (p.ej. `10Y` en una superficie
    que llega a 2Y), se aproxima sumando los años calendario, que para un corte
    fuera del rango da igual porque ningún expiry lo alcanza.
    """
    lbl = dt.normalize_tenor(tenor_label)
    if lbl in ("0D", "0"):
        return valuation_date
    if lbl in quotes:
        return quotes[lbl]["expiry"]
    years = dt.tenor_sort_key(lbl)
    return valuation_date + _dt.timedelta(days=round(years * 365))


def _surface_settings(params: dict, overrides: dict) -> dict:
    """Convenciones efectivas: lo del panel de Calypso, con `overrides` encima."""
    p = dict(params)
    p.update({k: v for k, v in (overrides or {}).items()})
    vdc = ql._clean(p.get("Volatility Day Count")) or None
    return {
        "vol_day_count": vdc,
        "premium_adjusted": ql.param_bool(p, "Quotes are Delta with Premium"),
        "spot_delta_last_tenor": ql._clean(p.get("Spot Delta Last Tenor")) or "0D",
        "atm_zero_straddle_last_tenor": ql._clean(p.get("ATM Zero Straddle Last Tenor")) or "0D",
        "strangle_fly": ql._clean(p.get("Strangle/Fly Quotes")),
        "up_extrap": ql.param_float(p, "Up Extrap 1.0 Delta", 1.0),
        "down_extrap": ql.param_float(p, "Down Extrap 1.0 Delta", 1.0),
        "interpolate_outright_variance": ql.param_bool(p, "Interpolate Outright Variance", True),
        "interpolate_on_trading_time": ql.param_bool(p, "Interpolate on Trading Time", False),
        "roll_method": ql._clean(p.get("Roll Method")),
    }


# ---------------------------------------------------------------------------
def build_all(config: dict, side: str = "mid", verbose: bool = True
              ) -> Tuple[Dict[str, VolSurface], List[str]]:
    """Construye todas las superficies del YAML para UN lado."""
    val = config["valuation_date"]
    if isinstance(val, str):
        val = _dt.date.fromisoformat(val[:10])

    md = config["market_data"]
    spots_all = cv.load_fx_spots(_path(config, md["fx_spots_file"]), val)
    warnings: List[str] = list(cv.report_spot_precision(spots_all))

    curves_by_ccy: Dict[str, dict] = {}
    for ccy, p in md["curves"].items():
        curves_by_ccy[ccy.upper()] = cv.load_calypso_curve(_path(config, p), val, name=ccy)

    out: Dict[str, VolSurface] = {}
    for pair, spec in config["surfaces"].items():
        base, quote = spec["base_ccy"].upper(), spec["quote_ccy"].upper()
        for ccy in (base, quote):
            if ccy not in curves_by_ccy:
                raise ConfigError(f"[{pair}] falta la curva de {ccy} en market_data.curves")

        params = ql.load_parameters(_path(config, spec["parameters"]))
        st = _surface_settings(params, spec.get("overrides"))
        if st["vol_day_count"] is None:
            st["vol_day_count"] = spec.get("vol_day_count_fallback", "ACT/365")
            warnings.append(
                f"[{pair}] 'Volatility Day Count' viene VACÍO en el panel de Calypso; "
                f"se usa {st['vol_day_count']} como supuesto. Confirmar con la mesa.")
        if st["strangle_fly"] and st["strangle_fly"] != "2vol (CP Avg)":
            raise ConfigError(
                f"[{pair}] Strangle/Fly Quotes = '{st['strangle_fly']}'. Este motor solo "
                f"implementa '2vol (CP Avg)' (álgebra directa). La convención "
                f"'1vol (Broker)' requiere calibración iterativa — ver DOCUMENTACION.")
        if st["interpolate_on_trading_time"]:
            raise ConfigError(
                f"[{pair}] 'Interpolate on Trading Time = true' no está implementado: "
                f"requiere el cálculo ponderado de trading time (feriados, eventos y "
                f"multiplicadores de cut, manual §1.1). Las 6 superficies actuales están "
                f"en false.")
        if not st["interpolate_outright_variance"]:
            warnings.append(
                f"[{pair}] 'Interpolate Outright Variance = false': Calypso interpolaría "
                f"sobre VOLATILIDAD y este motor interpola sobre varianza total. "
                f"Los tenores cotizados coinciden; las fechas intermedias no.")

        # `quotes_text` (CSV en memoria, p.ej. subido desde la interfaz) manda
        # sobre `quotes` (rutas del YAML). Permite construir con cotizaciones
        # nuevas sin escribir archivos ni tocar la configuración.
        paths = {s: _path(config, p) for s, p in (spec.get("quotes") or {}).items()}
        quotes_sides = ql.load_quotes(paths, texts_by_side=spec.get("quotes_text"))
        quotes = quotes_sides[side]

        warnings += ql.validate_quotes(quotes, pair, st["vol_day_count"], val)
        if spec.get("underlyings"):
            unds = ql.load_underlyings(_path(config, spec["underlyings"]))
            warnings += ql.validate_underlyings(unds, quotes, pair)

        # ---- spot del par, en la dirección en que lo cotiza el export de TC
        spot_key = f"{base}{quote}"
        if spot_key not in spots_all:
            raise ConfigError(f"[{pair}] falta el spot '{spot_key}' en el archivo de TC "
                              f"(disponibles: {sorted(spots_all)})")
        spot = spots_all[spot_key][side]

        fwd = ForwardModel(pair=pair, spot=spot,
                           base_curve=curves_by_ccy[base][side],
                           quote_curve=curves_by_ccy[quote][side])

        cut_spot = _resolve_cut_date(st["spot_delta_last_tenor"], quotes, val)
        cut_atm = _resolve_cut_date(st["atm_zero_straddle_last_tenor"], quotes, val)
        lag = int(spec.get("delivery_lag", 2))

        # Calendario de las plazas del par (campo `Holidays` del panel de Calypso,
        # o `holidays:` del YAML). Validado contra la grilla DAILY de Calypso:
        # NYC,MEX y NYC,BRA reproducen exactamente los dias habiles excluidos.
        venues = spec.get("holidays") or params.get("Holidays") or ""
        try:
            cal = dt.Calendar(venues)
        except ValueError as e:
            warnings.append(f"[{pair}] {e} Se usa solo fines de semana.")
            cal = dt.Calendar()
        if not cal.venues:
            warnings.append(
                f"[{pair}] sin plazas de feriados configuradas: las fechas de entrega "
                f"se calculan solo con fines de semana. Agregar `holidays:` al YAML.")
        # Factor de pendiente del ala (extension lineal mas alla de los nodos de
        # 10 delta). 1.0 = pendiente tangente pura, que es el valor validado.
        wing_k = float(spec.get("wing_slope_factor",
                                config.get("wing_slope_factor", 1.0)))

        slices, conv_by_tenor = [], {}
        for tenor in dt.sorted_tenors(list(quotes)):
            q = quotes[tenor]
            expiry = q["expiry"]
            delivery = dt.advance_business_days(expiry, lag, cal)
            tau = dt.year_fraction(st["vol_day_count"], val, expiry)
            conv = DeltaConvention(premium_adjusted=st["premium_adjusted"],
                                   spot_delta=(expiry <= cut_spot))
            conv_by_tenor[tenor] = conv
            slices.append(sm.build_slice(
                tenor=tenor, expiry=expiry, delivery=delivery, tau=tau,
                forward=fwd.forward(delivery), df_for=fwd.df_base(delivery), conv=conv,
                atm=q["atm"], rr25=q["rr25"], bf25=q["bf25"],
                rr10=q["rr10"], bf10=q["bf10"],
                zero_delta_straddle=(expiry <= cut_atm),
                wing_slope_factor=wing_k))

        surf = VolSurface(pair=pair, side=side, valuation_date=val,
                          vol_day_count=st["vol_day_count"], conv_by_tenor=conv_by_tenor,
                          fwd=fwd, delivery_lag=lag, slices=slices,
                          calendar=cal, wing_slope_factor=wing_k,
                          zero_delta_straddle=(slices[0].expiry <= cut_atm))
        surf._spot_delta_cut_date = cut_spot
        surf.settings = st
        out[pair] = surf

        if verbose:
            conv_lbl = next(iter(conv_by_tenor.values())).label()
            print(f"  ✓ {pair:<8} {len(slices):>2} tenores | spot {spot:>10.4f} | "
                  f"delta {conv_lbl:<20} | ATM "
                  f"{'zero-straddle' if slices[0].expiry <= cut_atm else 'forward'} | "
                  f"cal {'+'.join(cal.venues) or 'fines de semana'}")

    return out, warnings


def build_bid_mid_ask(config: dict, verbose: bool = True
                      ) -> Tuple[VolSurfaceSet, List[str]]:
    """Pipeline completo tres veces (enfoque A del Módulo 1)."""
    val = config["valuation_date"]
    if isinstance(val, str):
        val = _dt.date.fromisoformat(val[:10])
    res, warns = {}, []
    for side in SIDES:
        if verbose:
            print(f"\nLado {side}:")
        res[side], w = build_all(config, side=side, verbose=verbose)
        warns += w
    # dedup preservando orden
    warns = list(dict.fromkeys(warns))
    return VolSurfaceSet(res["bid"], res["mid"], res["ask"], val), warns


def build_from_file(path: str, verbose: bool = True):
    return build_bid_mid_ask(load_config(path), verbose=verbose)
