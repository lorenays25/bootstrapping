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

from . import conventions as _conv
from . import dates as dt
from .curve import Curve


# ============================================================================
# Constantes de valores válidos para convenciones de enum fijo. ÚNICA fuente
# de verdad: tanto la validación en tiempo de build() como el CONVENTION_SCHEMA
# expuesto a la UI (ver server.py GET /schema) leen estas mismas constantes —
# nunca se repiten los valores a mano en un segundo lugar.
# ============================================================================
SHORT_END_STYLES = ("periodic", "bullet")
ACCRUAL_CONVENTIONS = ("excl_spread", "incl_spread")
RESET_POSITIONS = ("in_arrears", "in_advance")


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
        if declared is None:
            return
        if declared not in RESET_POSITIONS:
            raise ValueError(
                f"reset_position={declared!r} inválido. Usa uno de {RESET_POSITIONS}."
            )
        if declared != self._native_reset:
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
    def _pay_delay(self, leg: str) -> int:
        """Días hábiles de pay delay de UNA pata ('fixed' | 'float').

        Precedencia: la convención específica de la pata
        (fixed_pay_delay_days / float_pay_delay_days) gana sobre la genérica
        pay_delay_days. Si solo se define la genérica, ambas patas la usan
        -- que es el comportamiento histórico, por lo que configuraciones
        existentes no cambian en nada.

        Existe porque las dos patas de un swap pueden pagar con desfases
        distintos: es convención de mercado, no un caso de borde."""
        specific = self.conv.get(f"{leg}_pay_delay_days")
        if specific is not None:
            return int(specific)
        return int(self.conv.get("pay_delay_days", 0))

    def _annuity(self, curve: Curve, pay_dates: List[_dt.date],
                 start: _dt.date, day_count: str) -> float:
        """Σ τ_i · DF(pay_i) sobre el schedule de pagos de la PATA FIJA.
        El pay delay (fixed_pay_delay_days, o pay_delay_days) desplaza SOLO
        la fecha de descuento (pay_i); el devengo (τ) sigue usando las
        fechas de fin de periodo."""
        pay_delay = self._pay_delay("fixed")
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
        if style not in SHORT_END_STYLES:
            raise ValueError(
                f"short_end_payment_style={style!r} inválido. Usa uno de {SHORT_END_STYLES}."
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
        # El atajo telescópico aplica al NUMERADOR (pata flotante): solo
        # depende del delay de esa pata. El de la pata fija ya está dentro
        # de `ann`, así que no condiciona este camino.
        pay_delay = self._pay_delay("float")
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
        if mode not in ACCRUAL_CONVENTIONS:
            raise ValueError(
                f"accrual_convention={mode!r} inválido. Usa uno de {ACCRUAL_CONVENTIONS}."
            )
        pay_delay = self._pay_delay("float")
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
        if mode not in ACCRUAL_CONVENTIONS:
            raise ValueError(
                f"accrual_convention={mode!r} inválido. Usa uno de {ACCRUAL_CONVENTIONS}."
            )
        pay_delay = self._pay_delay("float")
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
        pay_delay = self._pay_delay("float")
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
# 8) Bono soberano de tasa fija — parte larga de curvas EM sin OIS líquido
#    (p.ej. Soberanos PEN en soles, Calc Type 1275 de Bloomberg).
#
#    Se diferencia de todos los anteriores en TRES cosas:
#      a) el pilar es la fecha de VENCIMIENTO, no un tenor desde spot;
#      b) calibra contra un PRECIO de mercado, no contra par/tasa;
#      c) tiene su propia fecha de liquidación (settlement_lag), que puede
#         diferir del spot_lag de los swaps de la MISMA curva (caso real:
#         OIS PEN liquida T+2, bono PEN liquida T+1).
#
#    Ecuación de calibración (self-discounting: se descuenta sobre la misma
#    curva que se construye), con todo expresado a fecha de liquidación:
#
#        precio_sucio_mkt = [ Σ c_i · DF(t_i) + R · DF(T) ] / DF(settle)
#
#    donde c_i = cupón/frecuencia por 100 de face (periodos regulares
#    30/360 => exactamente cupón/2 en semestral, sin stub), R = redención,
#    y precio_sucio_mkt = precio_limpio cotizado + interés corrido.
#
#    El residual es monótono en DF(T) => Brent 1D sirve en modo secuencial;
#    en tramos largos dominados por bonos conviene mode: global (LM), porque
#    los cupones de cada bono dependen de TODOS los pilares previos.
# ============================================================================
_FREQ_TO_MONTHS = {"annual": 12, "semiannual": 6, "quarterly": 3, "monthly": 1}
PRICE_TYPES = ("clean_price", "dirty_price")


@dataclass
class SovereignBond(Instrument):
    _native_reset = "in_advance"

    def build(self, valuation_date: _dt.date) -> None:
        self._validate_reset_position()
        cal = self.conv.get("calendar", "WEEKENDS")
        # settlement_lag es PROPIO del bono: no reusa spot_lag, que es la
        # convención de los swaps. Así conviven T+1 (bono) y T+2 (OIS) en
        # la misma curva.
        lag = int(self.conv.get("settlement_lag", 1))
        bdc = self.conv.get("business_day_convention", "F")

        mat = self.conv.get("maturity")
        if mat is None:
            raise ValueError("sovereign_bond requiere 'maturity' (fecha ISO YYYY-MM-DD).")
        if isinstance(mat, str):
            mat = _dt.date.fromisoformat(mat[:10])
        elif isinstance(mat, _dt.datetime):
            mat = mat.date()
        self.maturity = mat
        self.pillar_date = mat

        if self.conv.get("coupon") is None:
            raise ValueError("sovereign_bond requiere 'coupon' (tasa en %, ej. 5.94).")
        self.coupon = float(self.conv["coupon"])
        self.redemption = float(self.conv.get("redemption", 100.0))

        freq = str(self.conv.get("coupon_freq", "semiannual")).lower()
        if freq not in _FREQ_TO_MONTHS:
            raise ValueError(
                f"coupon_freq={freq!r} inválido. Usa uno de {tuple(_FREQ_TO_MONTHS)}."
            )
        self._period_months = _FREQ_TO_MONTHS[freq]

        ptype = self.conv.get("price_type", "clean_price")
        if ptype not in PRICE_TYPES:
            raise ValueError(f"price_type={ptype!r} inválido. Usa uno de {PRICE_TYPES}.")

        self.settle_date = dt.spot_date(valuation_date, lag, cal)
        if self.settle_date >= self.maturity:
            raise ValueError(
                f"sovereign_bond vencido o con vencimiento anterior a la liquidación: "
                f"maturity={self.maturity}, settle={self.settle_date}."
            )
        self.start_date = self.settle_date       # interfaz común de Instrument
        self._build_coupon_schedule(cal, bdc)

    def _build_coupon_schedule(self, cal, bdc) -> None:
        """Cupones FUTUROS (posteriores a la liquidación), generados hacia
        atrás desde el vencimiento en pasos de coupon_freq — así el ciclo
        queda anclado al vencimiento (12-Feb/12-Ago en los Soberanos PEN).

        Las fechas de DEVENGO van sin ajustar (definen el corrido y el monto
        del cupón); solo la fecha de PAGO se ajusta a día hábil, y es la que
        se usa para descontar."""
        future_unadj: List[_dt.date] = []
        k = 0
        while True:
            d = dt.add_months(self.maturity, -self._period_months * k)
            if d <= self.settle_date:
                prev_cpn = d                     # último cupón ya pagado
                break
            future_unadj.append(d)
            k += 1
        future_unadj.reverse()                   # ascendente; el último = maturity
        self.coupon_dates = future_unadj
        self.pay_dates = [dt.adjust(d, cal, bdc) for d in future_unadj]
        self.prev_cpn = prev_cpn
        self.next_cpn = future_unadj[0]

    def _coupon_amount(self) -> float:
        """Monto del cupón por 100 de face. En periodos regulares 30/360 cada
        cupón es exactamente cupón/frecuencia (no hay stub: los Soberanos PEN
        tienen cupones regulares y su primer cupón irregular ya pasó)."""
        return self.coupon / (12.0 / self._period_months)

    def _accrued(self) -> float:
        """Interés corrido a la fecha de liquidación.

        ACT/ACT (ISMA) es proporción del periodo de cupón: el corrido es
        cupón × (días transcurridos / días del periodo). Cualquier otro day
        count se aplica como devengo sobre la TASA: cupón_rate × τ(prev, settle).
        Son fórmulas distintas y no intercambiables."""
        dc = str(self.conv.get("daycount_accrued", "ACT/ACT_ISMA")).upper()
        if dc in ("ACT/ACT_ISMA", "ACT/ACT"):
            period_days = (self.next_cpn - self.prev_cpn).days
            if period_days <= 0:
                return 0.0
            frac = (self.settle_date - self.prev_cpn).days / period_days
            return self._coupon_amount() * frac
        return self.coupon * dt.year_fraction(dc, self.prev_cpn, self.settle_date)

    def model_quote(self, ctx: CurveContext) -> float:
        """Precio SUCIO modelo, expresado a fecha de liquidación."""
        c = ctx.curve(self.curve_names["discount"])   # self-discounting
        cpn = self._coupon_amount()
        pv = sum(cpn * c.df(d) for d in self.pay_dates)
        pv += self.redemption * c.df(self.pay_dates[-1])
        return pv / c.df(self.settle_date)

    def market_dirty_price(self) -> float:
        """Precio sucio de mercado: el quote más el corrido si cotiza limpio."""
        if self.conv.get("price_type", "clean_price") == "dirty_price":
            return self.quote
        return self.quote + self._accrued()

    def residual(self, ctx: CurveContext) -> float:
        return self.model_quote(ctx) - self.market_dirty_price()


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
    "sovereign_bond": SovereignBond,
}

# Default de quote_convention según el tipo, si el usuario no lo especifica
# explícitamente en el YAML: los FRA cotizan en tasa, los futuros en precio.
_DEFAULT_QUOTE_CONVENTION = {"fra": "rate", "future": "price"}


# ============================================================================
# Catálogo de convenciones de curva, expuesto a la UI vía GET /schema
# (server.py). Cada campo "values"/"suggestions" deriva de la fuente de
# verdad real (dicts de dates.py o las constantes de arriba) -- NUNCA de
# valores tipeados a mano una segunda vez. Si el motor agrega un calendario,
# day count, o valor de enum nuevo, este catálogo lo refleja solo.
#
# type:
#   "enum"          -> valor debe ser uno de "values" (select estricto)
#   "bool"          -> true/false (select true/false)
#   "calendar"      -> texto libre; "suggestions" son códigos conocidos,
#                      pero admite listas tipo "US,PE" (calendario conjunto)
#   "day_count"     -> texto libre; "suggestions" son códigos conocidos
#   "tenor_pattern" -> texto libre; "suggestions" son presets (A/S/Q/M/Z),
#                      pero admite tenores explícitos (ej. 4W, 13W)
#   "int"           -> texto libre numérico
#   "string"        -> texto libre sin restricción
# ============================================================================
# ---------------------------------------------------------------------------
# Etiquetas legibles de los valores de convención.
#
# Viven ACÁ y no en el HTML a propósito: si el motor gana un valor nuevo (una
# frecuencia, una BDC), la UI lo muestra con su nombre sin tocar el frontend.
# La UI las consume vía /schema. Si un valor no tiene etiqueta, se muestra el
# código crudo -- así agregar un valor nunca rompe la interfaz.
# ---------------------------------------------------------------------------
FREQ_LABELS = {
    "A": "Anual", "S": "Semestral", "Q": "Trimestral", "M": "Mensual",
    "Z": "Cupón cero (un solo pago al vencimiento)",
}

VALUE_LABELS = {
    "business_day_convention": {
        "MF": "Modified Following — siguiente hábil, salvo cambio de mes",
        "F": "Following — siguiente día hábil",
        "P": "Preceding — día hábil anterior",
        "NONE": "Sin ajuste",
    },
    "short_end_payment_style": {
        "bullet": "Bullet — un solo pago al vencimiento",
        "periodic": "Periódica — según la frecuencia fija",
    },
    "reset_position": {
        "in_arrears": "In arrears — fixing al final del periodo",
        "in_advance": "In advance — fixing al inicio del periodo",
    },
    "accrual_convention": {
        "excl_spread": "Spread excluido del compounding",
        "incl_spread": "Spread incluido en el compounding",
    },
    "price_type": {
        "clean_price": "Precio limpio (se le suma el corrido)",
        "dirty_price": "Precio sucio (ya incluye el corrido)",
    },
    "coupon_freq": {
        "annual": "Anual", "semiannual": "Semestral",
        "quarterly": "Trimestral", "monthly": "Mensual",
    },
    "solve_for": {
        "quote_ccy": "Moneda cotizada (term)",
        "base_ccy": "Moneda base",
    },
    "quote_type": {
        "points": "Puntos forward", "outright": "Outright",
    },
    "quote_convention": {
        "rate": "Tasa (FRA)", "price": "Precio 100 - tasa (futuro)",
    },
    "end_of_month": {"true": "Sí — regla fin de mes", "false": "No"},
    "fixed_freq": FREQ_LABELS,
    "float_freq": FREQ_LABELS,
}


def value_labels(field: str) -> dict:
    """Etiquetas legibles de los valores de una convención ({} si no tiene)."""
    return VALUE_LABELS.get(field, {})


# Grupos de tipos, para declarar a qué instrumentos aplica cada convención.
_ALL_TYPES = ("mm", "deposit", "ois_swap", "fx_forward", "xccy_fixed_float",
              "xccy_basis", "tenor_basis", "ibor_swap", "fra", "future",
              "uvr_swap", "sovereign_bond")
_NON_BOND = tuple(t for t in _ALL_TYPES if t != "sovereign_bond")
_FIXED_LEG = ("ois_swap", "ibor_swap", "xccy_fixed_float", "uvr_swap")
_FLOAT_LEG = ("ibor_swap", "xccy_basis", "tenor_basis")
_PAY_DELAY = ("ois_swap", "ibor_swap", "xccy_basis", "tenor_basis",
              "xccy_fixed_float", "uvr_swap")
_FRA_LIKE = ("fra", "future")
_BOND = ("sovereign_bond",)


CONVENTION_SCHEMA = {
    "calendar": {
        "type": "calendar", "suggestions": dt.calendar_codes(), "applies_to": _ALL_TYPES,
        "description": "Código de calendario, o lista tipo [US,PE] para calendario conjunto.",
    },
    "day_count": {
        "type": "day_count", "suggestions": dt.day_count_codes(), "applies_to": _NON_BOND,
        "description": "Day count de la pata fija.",
    },
    "float_day_count": {
        "type": "day_count", "suggestions": dt.day_count_codes(), "applies_to": ("ibor_swap",),
        "description": "Day count de la pata flotante (default: igual a day_count). Solo ibor_swap.",
    },
    "spot_lag": {
        "type": "int", "default": 2, "applies_to": _NON_BOND,
        "description": "Días hábiles de fecha de valuación a fecha spot. "
                       "Los bonos NO lo usan: tienen settlement_lag propio.",
    },
    "fixed_freq": {
        "type": "tenor_pattern", "suggestions": ["A", "S", "Q", "M", "Z"], "applies_to": _FIXED_LEG,
        "description": "Frecuencia de pago fija: preset o tenor libre (ej. 4W para TIIE 28D).",
    },
    "float_freq": {
        "type": "tenor_pattern", "suggestions": ["A", "S", "Q", "M"], "applies_to": _FLOAT_LEG,
        "description": "Frecuencia de pago flotante: preset o tenor libre. No aplica a ois_swap.",
    },
    "business_day_convention": {
        "type": "enum", "values": dt.business_day_convention_codes(), "default": "MF",
        "applies_to": _ALL_TYPES,
        "description": "Ajuste de fecha hábil (Modified Following, Following, Preceding, sin ajuste).",
    },
    "end_of_month": {
        "type": "bool", "default": False, "applies_to": _NON_BOND,
        "description": "Regla EOM en la generación de schedules.",
    },
    "short_end_payment_style": {
        "type": "enum", "values": list(SHORT_END_STYLES), "applies_to": _FIXED_LEG,
        "description": "Pago bullet o periódico en tenores <=1Y "
                       "(default por type: bullet en ois_swap, periodic en el resto).",
    },
    "rate_cutoff_days": {
        "type": "int", "default": 0, "applies_to": ("ois_swap",),
        "description": "Días hábiles de rate cutoff (compounding congelado). Solo ois_swap.",
    },
    "pay_delay_days": {
        "type": "int", "default": 0, "applies_to": _PAY_DELAY,
        "description": "Días hábiles entre fin de devengo y pago. Aplica a AMBAS patas, "
                       "salvo que se defina el valor específico de una de ellas.",
    },
    "fixed_pay_delay_days": {
        "type": "int", "applies_to": _PAY_DELAY,
        "description": "Pay delay SOLO de la pata fija. Si se omite, usa pay_delay_days.",
    },
    "float_pay_delay_days": {
        "type": "int", "applies_to": _PAY_DELAY,
        "description": "Pay delay SOLO de la pata flotante. Si se omite, usa pay_delay_days.",
    },
    "reset_position": {
        "type": "enum", "values": list(RESET_POSITIONS), "applies_to": _ALL_TYPES,
        "description": "Solo VALIDA contra el 'type' del instrumento; no cambia el pricing.",
    },
    "accrual_convention": {
        "type": "enum", "values": list(ACCRUAL_CONVENTIONS), "default": "excl_spread",
        "applies_to": ("xccy_basis", "tenor_basis"),
        "description": "Cómo compone el spread sobre el índice. Solo xccy_basis/tenor_basis.",
    },
    "fx_pair": {
        "type": "string", "applies_to": ("fx_forward", "xccy_fixed_float", "xccy_basis"),
        "description": "Par FX, ej. USDPEN.",
    },
    "solve_for": {
        "type": "enum", "values": ["quote_ccy", "base_ccy"], "applies_to": ("fx_forward",),
        "description": "Qué lado del par FX es la incógnita. Solo fx_forward.",
    },
    "points_factor": {
        "type": "int", "applies_to": ("fx_forward",),
        "description": "Divisor de los puntos forward para llegar al outright. Solo fx_forward.",
    },
    "quote_type": {
        "type": "enum", "values": ["points", "outright"], "default": "points",
        "applies_to": ("fx_forward",),
        "description": "El quote FX son puntos forward o un outright. Solo fx_forward.",
    },
    "quote_convention": {
        "type": "enum", "values": ["rate", "price"], "applies_to": _FRA_LIKE,
        "description": "El quote es tasa (FRA) o precio 100-tasa (futuro). Solo fra/future.",
    },
    "convexity_bp": {
        "type": "int", "default": 0, "applies_to": _FRA_LIKE,
        "description": "Ajuste de convexidad en bp, alimentado desde tu modelo. Solo fra/future.",
    },

    # ---------------- convenciones propias de sovereign_bond ----------------
    "maturity": {
        "type": "string", "applies_to": _BOND,
        "description": "Fecha de vencimiento YYYY-MM-DD. Es el pilar del bono. OBLIGATORIA.",
    },
    "coupon": {
        "type": "string", "applies_to": _BOND,
        "description": "Tasa de cupón anual en % (ej. 5.94). OBLIGATORIA.",
    },
    "coupon_freq": {
        "type": "enum", "values": list(_FREQ_TO_MONTHS), "default": "semiannual",
        "applies_to": _BOND,
        "description": "Frecuencia de cupón del bono.",
    },
    "redemption": {
        "type": "int", "default": 100, "applies_to": _BOND,
        "description": "Nominal de redención al vencimiento.",
    },
    "price_type": {
        "type": "enum", "values": list(PRICE_TYPES), "default": "clean_price",
        "applies_to": _BOND,
        "description": "El quote del bono es precio limpio (se le suma el corrido) o sucio.",
    },
    "settlement_lag": {
        "type": "int", "default": 1, "applies_to": _BOND,
        "description": "Días hábiles a la liquidación del bono. Independiente de spot_lag: "
                       "permite que el bono liquide T+1 y el OIS T+2 en la misma curva.",
    },
    "daycount_accrued": {
        "type": "day_count", "suggestions": dt.day_count_codes(), "default": "ACT/ACT_ISMA",
        "applies_to": _BOND,
        "description": "Day count del interés corrido. ACT/ACT_ISMA = proporción del periodo.",
    },
}


# Convenciones OBLIGATORIAS por tipo: si faltan tras resolver todas las
# capas, la construcción falla con un mensaje claro en vez de un error
# oscuro más adentro del motor.
REQUIRED_BY_TYPE = {
    "sovereign_bond": ("maturity", "coupon"),
    "fx_forward": ("fx_pair",),
}


# Adjunta las etiquetas legibles a cada campo del catálogo, para que /schema
# las entregue junto al resto y la UI no tenga que conocerlas.
for _f, _spec in CONVENTION_SCHEMA.items():
    _lbl = VALUE_LABELS.get(_f)
    if _lbl:
        _spec["labels"] = _lbl
del _f, _spec, _lbl


def conventions_for_type(itype: str) -> dict:
    """Subconjunto del catálogo aplicable a un tipo de instrumento.
    Lo consume la UI para ofrecer, en cada instrumento, solo las
    convenciones que ese instrumento realmente usa."""
    return {k: v for k, v in CONVENTION_SCHEMA.items()
            if itype in (v.get("applies_to") or _ALL_TYPES)}


def class_defaults_for_type(itype: str) -> dict:
    """Defaults que dependen de la CLASE (no del catálogo), para reporte.
    Hoy: short_end_payment_style, que es 'bullet' en ois_swap y 'periodic'
    en el resto."""
    cls = INSTRUMENT_TYPES.get(itype)
    if cls is None:
        return {}
    return {"short_end_payment_style": getattr(cls, "_short_end_default", None)}



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


def resolve_instrument_conventions(spec: dict, conv: dict,
                                   presets: dict | None = None,
                                   strict: bool = False):
    """Resuelve la convención efectiva de UN instrumento y la valida.

    Devuelve (itype, resuelto, procedencia, avisos). Es el punto ÚNICO
    donde se decide qué convención rige cada instrumento; tanto el
    constructor de instrumentos como el reporte /conventions lo usan, así
    que lo que se reporta es exactamente lo que se calcula.
    """
    itype = _conv.instrument_type(spec, presets)
    if itype is None:
        raise KeyError(
            f"El instrumento no declara 'type' ni hereda uno de un preset: {spec!r}"
        )
    if itype not in INSTRUMENT_TYPES:
        raise KeyError(f"Tipo de instrumento desconocido: '{itype}'. "
                       f"Disponibles: {list(INSTRUMENT_TYPES)}")

    resolved, provenance = _conv.resolve(spec, conv, presets)
    warnings = _conv.validate(itype, resolved, CONVENTION_SCHEMA,
                              REQUIRED_BY_TYPE, provenance, strict=strict)

    if itype in _DEFAULT_QUOTE_CONVENTION:
        if resolved.get("quote_convention") is None:
            resolved["quote_convention"] = _DEFAULT_QUOTE_CONVENTION[itype]
            provenance["quote_convention"] = "default"

    return itype, resolved, provenance, warnings


def make_instrument(spec: dict, conv: dict, curve_names: dict,
                    side: str = "mid", presets: dict | None = None,
                    strict: bool = False,
                    warnings_out: list | None = None) -> Instrument:
    """Construye el instrumento con su convención propia ya resuelta.

    La convención de cada instrumento se resuelve por capas
    (curva -> preset -> instrumento), de modo que dos instrumentos de la
    MISMA curva pueden tener convenciones distintas: p.ej. un ois_swap con
    spot_lag 2 y un sovereign_bond con settlement_lag 1.
    """
    itype, resolved, _prov, warns = resolve_instrument_conventions(
        spec, conv, presets, strict=strict)
    if warnings_out is not None:
        warnings_out.extend(warns)
    return INSTRUMENT_TYPES[itype](
        tenor=spec.get("tenor"),          # None en bonos: su pilar es 'maturity'
        quote=_select_quote(spec, side),
        conv=resolved, curve_names=curve_names,
    )
