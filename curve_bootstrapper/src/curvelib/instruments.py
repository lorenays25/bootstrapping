"""
instruments.py — Instrumentos de calibración y su lógica de repricing.

Cada instrumento sabe:
  1. Construir sus fechas (schedule) a partir de las convenciones.
  2. Repreciarse con las curvas actuales:  model_quote(ctx)
  3. Devolver su residual:                 residual = model_quote - market_quote

El motor de bootstrapping busca el DF del pilar que hace residual = 0.

CONTEXTO (CurveContext): diccionario de curvas ya construidas + spots FX.
Cada instrumento referencia curvas POR NOMBRE (inyectadas desde el YAML):
  - target      : la curva que se está construyendo (la incógnita)
  - discount    : curva de descuento (puede ser la misma target => 'self')
  - projection  : curva de proyección de forwards (si aplica)

SIMPLIFICACIONES DEL ESQUELETO (documentadas; ver docs/DOCUMENTACION.md):
  - OIS: pata flotante compuesta colapsa telescópicamente a DF(ini)-DF(fin).
  - XCCY: notional constante (sin resets MtM), sin convexidad.
  - Sin turn-of-year jumps ni fechas de reunión de bancos centrales.
  - TIIE 28d: frecuencia aproximada mensual (28d exactos = mejora futura).
  - UVR: pata fija real valorada a par contra pata IBR nominal (sin
    modelo de estacionalidad de inflación).
"""
from __future__ import annotations

import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import dates as dt
from .curve import Curve


# ============================================================================
# Contexto: acceso a curvas y FX spots por nombre
# ============================================================================
class CurveContext:
    def __init__(self, curves: Dict[str, Curve], fx_spots: Dict[str, float]):
        self.curves = curves
        self.fx_spots = fx_spots

    def curve(self, name: str) -> Curve:
        if name not in self.curves:
            raise KeyError(
                f"La curva '{name}' no está construida todavía. "
                f"Revisa 'depends_on' en el YAML. Disponibles: {list(self.curves)}"
            )
        return self.curves[name]

    def fx_spot(self, pair: str) -> float:
        if pair not in self.fx_spots:
            raise KeyError(f"Falta el spot FX '{pair}' en market_data.fx_spots del YAML.")
        return self.fx_spots[pair]


# ============================================================================
# Clase base
# ============================================================================
@dataclass
class Instrument(ABC):
    tenor: str
    quote: float                      # en unidades naturales: tasas y basis en DECIMAL, puntos fwd en puntos
    conv: dict                        # convenciones de la curva (day_count, calendar, ...)
    curve_names: dict                 # {'target':..., 'discount':..., 'projection':...}
    # calculados en build():
    pillar_date: _dt.date = field(default=None, init=False)
    start_date: _dt.date = field(default=None, init=False)

    # -------- convenciones genéricas parametrizables por curva vía YAML
    # (defaults preservan el comportamiento hardcodeado original de cada tipo;
    # cada subclase puede sobreescribir estos atributos de CLASE)
    _short_end_default = "periodic"   # short_end_payment_style: "periodic" | "bullet"
    _native_reset = "in_advance"      # reset_position nativo: "in_advance" | "in_arrears"

    def build(self, valuation_date: _dt.date) -> None:
        self._validate_reset_position()
        cal = self.conv.get("calendar", "WEEKENDS")
        lag = self.conv.get("spot_lag", 2)
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        self.start_date = dt.spot_date(valuation_date, lag, cal)
        self.pillar_date = dt.add_tenor(self.start_date, self.tenor, cal, bdc, eom)
        self._build_schedules(valuation_date)

    def _build_schedules(self, valuation_date: _dt.date) -> None:  # override si aplica
        pass

    def _validate_reset_position(self) -> None:
        """reset_position es solo de VALIDACIÓN: declara la intención en el YAML
        y el engine verifica que sea consistente con el 'type' del instrumento
        (no cambia la fórmula de pricing). ois_swap/xccy_basis/tenor_basis son
        nativamente 'in_arrears' (compounding telescópico); el resto (deposit,
        ibor_swap, xccy_fixed_float, fra) son nativamente 'in_advance'."""
        declared = self.conv.get("reset_position")
        if declared is not None and declared != self._native_reset:
            raise ValueError(
                f"reset_position={declared!r} declarado en el YAML no es compatible "
                f"con type={type(self).__name__!r} (asume reset_position="
                f"{self._native_reset!r}). Quita la key o corrige el 'type'."
            )

    @abstractmethod
    def model_quote(self, ctx: CurveContext) -> float: ...

    def residual(self, ctx: CurveContext) -> float:
        return self.model_quote(ctx) - self.quote

    # -------- helpers comunes
    def _annuity(self, curve: Curve, pay_dates: List[_dt.date],
                 start: _dt.date, day_count: str) -> float:
        """Σ τ_i · DF(pay_i) sobre el schedule de pagos.
        pay_delay_days (default 0) desplaza SOLO la fecha de descuento
        (pay_i); el devengo (τ) sigue usando las fechas de fin de periodo."""
        pay_delay = int(self.conv.get("pay_delay_days", 0))
        cal = self.conv.get("calendar", "WEEKENDS")
        a, prev = 0.0, start
        for d in pay_dates:
            pay_d = dt.advance_business_days(d, pay_delay, cal) if pay_delay else d
            a += dt.year_fraction(day_count, prev, d) * curve.df(pay_d)
            prev = d
        return a

    def _fixed_schedule(self) -> List[_dt.date]:
        """Genera el schedule de la pata fija.
        short_end_payment_style ('periodic' default de clase | 'bullet')
        controla si los tenores <=1Y pagan en su periodicidad nativa
        (fixed_freq) o en un único cupón cero (ZC) al final. Cada subclase
        fija su propio _short_end_default de clase para preservar el
        comportamiento histórico; se puede sobreescribir por curva vía YAML."""
        freq = self.conv.get("fixed_freq", "A")
        style = self.conv.get("short_end_payment_style", self._short_end_default)
        if style not in ("periodic", "bullet"):
            raise ValueError(
                f"short_end_payment_style={style!r} inválido. Usa 'periodic' o 'bullet'."
            )
        if style == "bullet" and dt.tenor_years(self.tenor) <= 1.0 + 1e-9:
            freq = "Z"
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        return dt.make_schedule(self.start_date, self.pillar_date, freq,
                                self.conv.get("calendar", "WEEKENDS"), bdc, eom)


# ============================================================================
# 0) Depósito / Money-Market (MM) — pilar corto de la curva.
#    Cubre el quote 'MM.USD.SOFR.ON' y depósitos 1M/2M/... si los usas.
#    Interés simple sobre el periodo:
#        DF(pillar) = DF(t_spot) / (1 + R · τ)
#    Para 'ON'/'TN' el start es la fecha de valuación / spot directamente.
# ============================================================================
@dataclass
class Deposit(Instrument):
    def build(self, valuation_date: _dt.date) -> None:
        self._validate_reset_position()
        cal = self.conv.get("calendar", "WEEKENDS")
        lag = self.conv.get("spot_lag", 2)
        t = self.tenor.upper().strip()
        # ON arranca en la fecha de valuación; el resto en spot.
        if t in ("ON", "O/N"):
            self.start_date = valuation_date
            self.pillar_date = dt.add_tenor(valuation_date, "1D", cal, "F")
        elif t in ("TN", "T/N"):
            self.start_date = dt.add_tenor(valuation_date, "1D", cal, "F")
            self.pillar_date = dt.add_tenor(self.start_date, "1D", cal, "F")
        else:
            bdc = self.conv.get("business_day_convention", "MF")
            eom = bool(self.conv.get("end_of_month", False))
            self.start_date = dt.spot_date(valuation_date, lag, cal)
            self.pillar_date = dt.add_tenor(self.start_date, self.tenor, cal, bdc, eom)

    def model_quote(self, ctx: CurveContext) -> float:
        c = ctx.curve(self.curve_names["target"])
        dc = self.conv.get("day_count", "ACT/360")
        tau = dt.year_fraction(dc, self.start_date, self.pillar_date)
        # tasa simple implícita en los DF de la curva
        return (c.df(self.start_date) / c.df(self.pillar_date) - 1.0) / tau


# ============================================================================
# 1) OIS Swap  (SOFR, ESTR, SONIA, SARON, TONAR, CORRA, TIBO, IBR, Cámara)
#    Pata fija anual vs pata flotante o/n compuesta. Self-discounting.
#    Par:  R · Σ τ_i DF(t_i)  =  DF(t_spot) − DF(t_N)
# ============================================================================
@dataclass
class OISSwap(Instrument):
    _short_end_default = "bullet"   # preserva force_zc_short_end=True actual
    _native_reset = "in_arrears"    # compounding telescópico = reset in arrears

    def _build_schedules(self, valuation_date):
        self.fixed_dates = self._fixed_schedule()

    def model_quote(self, ctx: CurveContext) -> float:
        c = ctx.curve(self.curve_names["discount"])   # = target (self-discounting)
        dc = self.conv.get("day_count", "ACT/360")
        ann = self._annuity(c, self.fixed_dates, self.start_date, dc)
        cutoff = int(self.conv.get("rate_cutoff_days", 0))
        pay_delay = int(self.conv.get("pay_delay_days", 0))
        if cutoff == 0 and pay_delay == 0:
            # atajo telescópico exacto (comportamiento actual, sin cambios)
            return (c.df(self.start_date) - c.df(self.pillar_date)) / ann
        # descomposición por periodo: requerida si hay cutoff y/o pay delay.
        # Por cada cupón, factor = compounding real hasta la fecha de corte
        # (c_end) × (1 + tasa congelada en c_end · τ de la cola congelada).
        # Con cutoff=0 esto colapsa a factor=DF(prev)/DF(accrual_end), y con
        # pay_delay=0 el descuento cae en accrual_end — ambos casos reducen
        # exactamente a la identidad telescópica de arriba (ver tests).
        cal = self.conv.get("calendar", "WEEKENDS")
        num, prev = 0.0, self.start_date
        for accrual_end in self.fixed_dates:
            pay_date = (dt.advance_business_days(accrual_end, pay_delay, cal)
                        if pay_delay else accrual_end)
            if cutoff:
                c_end = dt.advance_business_days(accrual_end, -cutoff, cal)
                c_end = max(c_end, prev)   # guarda contra cutoff >= periodo
                one_bd = dt.advance_business_days(c_end, 1, cal)
                fwd_cutoff = c.fwd(c_end, one_bd, dc)          # tasa que se congela
                tau_tail = dt.year_fraction(dc, c_end, accrual_end)
                factor = (c.df(prev) / c.df(c_end)) * (1.0 + fwd_cutoff * tau_tail)
            else:
                factor = c.df(prev) / c.df(accrual_end)
            num += (factor - 1.0) * c.df(pay_date)
            prev = accrual_end
        return num / ann


# ============================================================================
# 2) FX Forward (puntos forward)
#    Cotización:  F = spot + puntos / points_factor      (outright si se indica)
#    Paridad cubierta (medida colateral):  F = S · DF_base / DF_quote
#      pair 'USDPEN' => base=USD, quote=PEN, F en PEN por USD.
#    solve_for = 'quote_ccy' : la incógnita es la curva de la moneda cotizada
#                              (PEN coll SOFR, CNH offshore, ...)
#    solve_for = 'base_ccy'  : la incógnita es la curva base
#                              (USD implícita TIBO).
# ============================================================================
@dataclass
class FXForward(Instrument):
    def _build_schedules(self, valuation_date):
        pass

    def _market_outright(self, ctx: CurveContext) -> float:
        pair = self.conv["fx_pair"]
        if self.conv.get("quote_type", "points") == "outright":
            return self.quote
        factor = self.conv.get("points_factor", 10000.0)
        return ctx.fx_spot(pair) + self.quote / factor

    def model_quote(self, ctx: CurveContext) -> float:
        pair = self.conv["fx_pair"]
        s = ctx.fx_spot(pair)
        solve_for = self.conv.get("solve_for", "quote_ccy")
        target = ctx.curve(self.curve_names["target"])
        other = ctx.curve(self.curve_names["other_leg"])
        d = self.pillar_date
        if solve_for == "quote_ccy":     # F = S · DF_base(otra) / DF_quote(target)
            f_model = s * other.df(d) / target.df(d)
        else:                            # F = S · DF_base(target) / DF_quote(otra)
            f_model = s * target.df(d) / other.df(d)
        return f_model

    def residual(self, ctx: CurveContext) -> float:
        return self.model_quote(ctx) - self._market_outright(ctx)


# ============================================================================
# 3) XCCY Fixed-Float  (NDS LatAm: PEN/COP/CLP/BRL fija local vs USD SOFR)
#    Bajo colateral USD, la pata local fija debe valer par en la curva
#    local colateralizada DF_x:
#        R · Σ τ_i DF_x(t_i) + DF_x(T) = DF_x(t_spot)
#    =>  R_model = (DF_x(t_spot) − DF_x(T)) / annuity
#    (misma forma que un bono a la par; notional constante)
# ============================================================================
@dataclass
class XCCYFixedFloat(Instrument):
    def _build_schedules(self, valuation_date):
        self.fixed_dates = self._fixed_schedule()

    def model_quote(self, ctx: CurveContext) -> float:
        c = ctx.curve(self.curve_names["target"])     # curva local coll. USD
        dc = self.conv.get("day_count", "ACT/360")
        ann = self._annuity(c, self.fixed_dates, self.start_date, dc)
        return (c.df(self.start_date) - c.df(self.pillar_date)) / ann


# ============================================================================
# 4) XCCY Basis (G10: flotante local RFR + basis vs USD SOFR, notional cte.)
#    Pata local (con intercambio de notional) a valor par en medida colateral:
#      DF_x(t0) = Σ (fwd_i + b) τ_i DF_x(t_i) + DF_x(T)
#    fwd_i proyectados de la curva RFR local (projection, ya construida).
#    =>  b_model = [DF_x(t0) − DF_x(T) − Σ fwd_i τ_i DF_x(t_i)] / Σ τ_i DF_x(t_i)
# ============================================================================
@dataclass
class XCCYBasis(Instrument):
    _native_reset = "in_arrears"

    def _build_schedules(self, valuation_date):
        freq = self.conv.get("float_freq", "Q")
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        self.float_dates = dt.make_schedule(self.start_date, self.pillar_date,
                                            freq, self.conv.get("calendar", "WEEKENDS"), bdc, eom)

    def model_quote(self, ctx: CurveContext) -> float:
        cx = ctx.curve(self.curve_names["target"])        # curva coll. (incógnita)
        cp = ctx.curve(self.curve_names["projection"])    # RFR local (conocida)
        dc = self.conv.get("day_count", "ACT/360")
        mode = self.conv.get("accrual_convention", "excl_spread")
        if mode not in ("excl_spread", "incl_spread"):
            raise ValueError(
                f"accrual_convention={mode!r} inválido. Usa 'excl_spread' o 'incl_spread'."
            )
        pay_delay = int(self.conv.get("pay_delay_days", 0))
        cal = self.conv.get("calendar", "WEEKENDS")
        num, den, prev = 0.0, 0.0, self.start_date
        for d in self.float_dates:
            tau = dt.year_fraction(dc, prev, d)
            fwd = cp.fwd(prev, d, dc)
            pay_d = dt.advance_business_days(d, pay_delay, cal) if pay_delay else d
            w = cx.df(pay_d)
            num += fwd * tau * w
            den += (tau + fwd * tau * tau) * w if mode == "incl_spread" else tau * w
            prev = d
        b_model = (cx.df(self.start_date) - cx.df(self.pillar_date) - num) / den
        return b_model


# ============================================================================
# 5) Tenor Basis Swap  (Fed Funds vs SOFR;  spread sobre la pata FF)
#    Σ (fwdFF_i + s) τ_i DF_d = Σ fwdSOFR_i τ_i DF_d
#    =>  s_model = Σ (fwdSOFR_i − fwdFF_i) τ_i DF_d / Σ τ_i DF_d
#    Descuento en la curva base (SOFR); incógnita: curva de proyección FF.
# ============================================================================
@dataclass
class TenorBasisSwap(Instrument):
    _native_reset = "in_arrears"

    def _build_schedules(self, valuation_date):
        freq = self.conv.get("float_freq", "Q")
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        self.float_dates = dt.make_schedule(self.start_date, self.pillar_date,
                                            freq, self.conv.get("calendar", "WEEKENDS"), bdc, eom)

    def model_quote(self, ctx: CurveContext) -> float:
        ct = ctx.curve(self.curve_names["target"])       # FF (incógnita, proyección)
        cb = ctx.curve(self.curve_names["projection"])   # SOFR (base conocida)
        cd = ctx.curve(self.curve_names["discount"])     # descuento (SOFR)
        dc = self.conv.get("day_count", "ACT/360")
        mode = self.conv.get("accrual_convention", "excl_spread")
        if mode not in ("excl_spread", "incl_spread"):
            raise ValueError(
                f"accrual_convention={mode!r} inválido. Usa 'excl_spread' o 'incl_spread'."
            )
        pay_delay = int(self.conv.get("pay_delay_days", 0))
        cal = self.conv.get("calendar", "WEEKENDS")
        num, den, prev = 0.0, 0.0, self.start_date
        for d in self.float_dates:
            tau = dt.year_fraction(dc, prev, d)
            fwd_t = ct.fwd(prev, d, dc)
            pay_d = dt.advance_business_days(d, pay_delay, cal) if pay_delay else d
            w = cd.df(pay_d)
            num += (cb.fwd(prev, d, dc) - fwd_t) * tau * w
            den += (tau + fwd_t * tau * tau) * w if mode == "incl_spread" else tau * w
            prev = d
        return num / den


# ============================================================================
# 6) IBOR Swap  (Euribor 3M/6M, STIBOR 3M, TIIE 28d)
#    Fija (freq fixed_freq) vs flotante IBOR (freq float_freq).
#    R_model = Σ fwd_j τ_j DF_d(t_j)  /  Σ τ_i DF_d(t_i)
#    Descuento: curva 'discount' (ESTR para Euribor, self para TIIE/STIBOR).
#    Proyección: la curva target (incógnita).
# ============================================================================
@dataclass
class IBORSwap(Instrument):
    def _build_schedules(self, valuation_date):
        cal = self.conv.get("calendar", "WEEKENDS")
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        self.fixed_dates = self._fixed_schedule()
        self.float_dates = dt.make_schedule(self.start_date, self.pillar_date,
                                            self.conv.get("float_freq", "Q"), cal, bdc, eom)

    def model_quote(self, ctx: CurveContext) -> float:
        cp = ctx.curve(self.curve_names["target"])       # proyección (incógnita)
        cd = ctx.curve(self.curve_names["discount"])
        dc_fix = self.conv.get("day_count", "ACT/360")
        dc_flt = self.conv.get("float_day_count", dc_fix)
        pay_delay = int(self.conv.get("pay_delay_days", 0))
        cal = self.conv.get("calendar", "WEEKENDS")
        pv_float, prev = 0.0, self.start_date
        for d in self.float_dates:
            tau = dt.year_fraction(dc_flt, prev, d)
            pay_d = dt.advance_business_days(d, pay_delay, cal) if pay_delay else d
            pv_float += cp.fwd(prev, d, dc_flt) * tau * cd.df(pay_d)
            prev = d
        ann = self._annuity(cd, self.fixed_dates, self.start_date, dc_fix)
        return pv_float / ann


# ============================================================================
# 7) FRA / Futuro (parte corta de curvas IBOR: Euribor, TIIE, STIBOR, etc.)
#
#    Cubre las dos formas de mercado observadas en pantalla:
#      - FRA:    tenor 'start x end' (p.ej. '1Mx7M'), quote = tasa FRA directa
#      - Futuro: mismo esquema de fechas, quote en PRECIO (100 − tasa),
#                con ajuste de convexidad opcional (ver limitación abajo).
#
#    Ecuación de calibración (misma para ambos, solo cambia la unidad del
#    quote): la tasa forward implícita de la curva de proyección entre
#    start y end debe igualar la tasa de mercado (ajustada por convexidad
#    en el caso de futuros):
#
#        fwd_model(start, end) = tasa_mercado − convexidad
#
#    LIMITACIÓN DOCUMENTADA: el ajuste de convexidad de futuros (relevante
#    en tenores largos, >2Y) no se calcula analíticamente aquí — se expone
#    como override manual `convexity_bp` (default 0) para que lo alimentes
#    desde tu propio modelo (Hull-White, etc.) si lo necesitas.
# ============================================================================
@dataclass
class FRA(Instrument):
    def build(self, valuation_date: _dt.date) -> None:
        self._validate_reset_position()
        # Override completo de build(): un FRA/futuro define su propio
        # start/end como offsets desde spot, no un único 'tenor' desde spot.
        cal = self.conv.get("calendar", "WEEKENDS")
        lag = self.conv.get("spot_lag", 2)
        bdc = self.conv.get("business_day_convention", "MF")
        eom = bool(self.conv.get("end_of_month", False))
        base = dt.spot_date(valuation_date, lag, cal)
        t = self.tenor.upper().replace(" ", "")
        if "X" in t:
            start_t, end_t = t.split("X")
        else:
            start_t, end_t = "0D", t          # sin 'x': offset 0 -> tenor
        self.start_date = base if start_t in ("0D", "0") else \
            dt.add_tenor(base, start_t, cal, bdc, eom)
        self.pillar_date = dt.add_tenor(self.start_date, end_t, cal, bdc, eom)

    def model_quote(self, ctx: CurveContext) -> float:
        c = ctx.curve(self.curve_names["target"])
        dc = self.conv.get("day_count", "ACT/360")
        return c.fwd(self.start_date, self.pillar_date, dc)

    def residual(self, ctx: CurveContext) -> float:
        model_rate = self.model_quote(ctx)
        conv = self.conv.get("convexity_bp", 0.0) / 1e4
        if self.conv.get("quote_convention", "rate") == "price":
            mkt_rate = (100.0 - self.quote) / 100.0
        else:
            mkt_rate = self.quote
        return model_rate - (mkt_rate - conv)


# ============================================================================
# 7b) UVR Swap (UVR vs IBR) — curva de DESCUENTO de flujos UVR.
#    Simplificación de esqueleto: pata fija real (en UVR) a valor par contra
#    la pata flotante IBR nominal (que vale par sobre la curva COP IBR).
#        R_uvr · Σ τ_i DF_uvr(t_i) + DF_uvr(T) = DF_uvr(t_spot)
#    Matemáticamente igual al par-bond; la semántica (real vs nominal) y las
#    mejoras (estacionalidad CPI) se documentan en DOCUMENTACION.md.
# ============================================================================
@dataclass
class UVRSwap(XCCYFixedFloat):
    pass


# ============================================================================
# Registro de tipos para el orquestador (nombre en YAML -> clase)
# ============================================================================
INSTRUMENT_TYPES = {
    "mm": Deposit,
    "deposit": Deposit,
    "ois_swap": OISSwap,
    "fx_forward": FXForward,
    "xccy_fixed_float": XCCYFixedFloat,
    "xccy_basis": XCCYBasis,
    "tenor_basis": TenorBasisSwap,
    "ibor_swap": IBORSwap,
    "fra": FRA,
    "future": FRA,          # misma clase; difiere en quote_convention (ver abajo)
    "uvr_swap": UVRSwap,
}

# Default de quote_convention según el tipo, si el usuario no lo especifica
# explícitamente en el YAML: los FRA cotizan en tasa, los futuros en precio.
_DEFAULT_QUOTE_CONVENTION = {"fra": "rate", "future": "price"}


def _select_quote(spec: dict, side: str) -> float:
    """Elige el quote a usar según el lado de la corrida (bid/mid/ask).

    Un instrumento puede especificar el quote de dos formas:
      - escalar:   quote: 0.0393                (mismo valor para los 3 lados)
      - por lado:  quote: {bid: 0.0392, mid: 0.0393, ask: 0.0394}
    Si falta el lado pedido en la forma dict, cae a 'mid' y, si tampoco está,
    al primer valor disponible. Así los instrumentos sin bid/ask (p.ej. un
    basis con un único valor) funcionan en las tres corridas."""
    q = spec["quote"]
    if isinstance(q, dict):
        if side in q:
            return float(q[side])
        if "mid" in q:
            return float(q["mid"])
        return float(next(iter(q.values())))
    return float(q)


def make_instrument(spec: dict, conv: dict, curve_names: dict,
                    side: str = "mid") -> Instrument:
    itype = spec["type"]
    if itype not in INSTRUMENT_TYPES:
        raise KeyError(f"Tipo de instrumento desconocido: '{itype}'. "
                       f"Disponibles: {list(INSTRUMENT_TYPES)}")
    merged_conv = {**conv, **{k: v for k, v in spec.items()
                              if k not in ("type", "tenor", "quote")}}
    if itype in _DEFAULT_QUOTE_CONVENTION:
        merged_conv.setdefault("quote_convention", _DEFAULT_QUOTE_CONVENTION[itype])
    return INSTRUMENT_TYPES[itype](
        tenor=spec["tenor"], quote=_select_quote(spec, side),
        conv=merged_conv, curve_names=curve_names,
    )
