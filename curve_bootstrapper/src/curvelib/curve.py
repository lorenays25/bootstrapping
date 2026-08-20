"""
curve.py — Objeto Curve: factores de descuento + interpolación.

Parametrización elegida: nodos (t_i, DF_i) con interpolación LINEAL EN
log(DF).  Equivale a tasas forward instantáneas constantes por tramo
(piecewise-flat forwards). Ventajas:
  - Garantiza DF > 0 siempre.
  - Estable en el bootstrapping (cada pilar afecta localmente).
  - Es el estándar de facto para curvas de descuento OIS.

La extrapolación más allá del último nodo mantiene la última forward
instantánea constante (extrapolación log-lineal).

El tiempo t se mide en fracción de año con un day count "interno" de
la curva (por defecto ACT/365F), independiente del day count de los
instrumentos. Esto es solo la coordenada del eje x de la curva.
"""
from __future__ import annotations

import datetime as _dt
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import List

import numpy as np

from . import dates as dt


@dataclass
class Curve:
    name: str
    valuation_date: _dt.date
    internal_day_count: str = "ACT/365F"
    # nodos: tiempos en años y log(DF). Siempre incluye el nodo (0, log 1 = 0)
    times: List[float] = field(default_factory=lambda: [0.0])
    log_dfs: List[float] = field(default_factory=lambda: [0.0])
    pillar_dates: List[_dt.date] = field(default_factory=list)  # fecha real de cada pilar (sin el nodo 0)

    # ----------------------------------------------------------- coordenada t
    def t(self, d: _dt.date) -> float:
        return dt.year_fraction(self.internal_day_count, self.valuation_date, d)

    # ----------------------------------------------------------- nodos
    def add_node(self, d: _dt.date, df: float | None = None) -> int:
        """Agrega un nodo en la fecha d. Devuelve su índice.
        Si df es None, inicializa extrapolando la curva actual (buen guess)."""
        t = self.t(d)
        if t <= self.times[-1] + 1e-12:
            raise ValueError(
                f"[{self.name}] Nodo en t={t:.4f} no es posterior al último "
                f"({self.times[-1]:.4f}). Los pilares deben ir en orden."
            )
        guess = np.log(self.df_t(t)) if df is None else float(np.log(df))
        self.times.append(t)
        self.log_dfs.append(guess)
        self.pillar_dates.append(d)
        return len(self.times) - 1

    def set_node(self, idx: int, df: float) -> None:
        self.log_dfs[idx] = float(np.log(df))

    def set_node_log(self, idx: int, log_df: float) -> None:
        self.log_dfs[idx] = float(log_df)

    def insert_spot_node(self, d: _dt.date, df: float | None = None) -> int:
        """Inserta un nodo DERIVADO (no una incógnita del solve) justo antes
        del primer pilar real, típicamente la fecha SPOT de una curva cross
        que solo cotiza `fx_forward`/`xccy_basis` a partir de esa fecha.

        Si `df` es None, se calcula interpolando la curva YA RESUELTA en `d`
        (`df_t`). Como ese punto cae exactamente sobre la interpolación
        log-lineal que ya existe entre el nodo (0, DF=1) y el primer pilar
        real, insertarlo NO cambia el valor de la curva en ningún otro punto
        -- es una materialización de un punto que ya estaba implícito, para
        que aparezca como fila propia en la tabla/CSV de salida (el "primer
        tenor" que Calypso sí muestra, ver manual "Yield Curves Generation"
        §4.4: extrapola una tasa continua desde el primer instrumento de
        mercado hasta la fecha spot y la deja como nodo explícito).

        No usar para insertar un nodo real del solve: `add_node` es lo
        correcto para eso (y no permite esta inserción fuera de orden a
        propósito, para no confundir un nodo derivado con una incógnita).
        """
        t = self.t(d)
        if t <= 0.0:
            raise ValueError(f"[{self.name}] insert_spot_node: t={t:.6f} no es posterior a la valuación.")
        if self.pillar_dates and d >= self.pillar_dates[0]:
            raise ValueError(
                f"[{self.name}] insert_spot_node: {d} debe ser anterior al primer "
                f"pilar real ({self.pillar_dates[0]})."
            )
        value = self.df_t(t) if df is None else float(df)
        self.times.insert(1, t)
        self.log_dfs.insert(1, float(np.log(value)))
        self.pillar_dates.insert(0, d)
        return 1

    # ----------------------------------------------------------- evaluación
    def df_t(self, t: float) -> float:
        """DF interpolado log-linealmente; extrapola con última fwd constante."""
        if t <= 0.0:
            return 1.0
        ts, ys = self.times, self.log_dfs
        n = len(ts)
        if n == 1:  # curva sin pilares aún: DF = 1 (plano en 0%)
            return 1.0
        if t >= ts[-1]:  # extrapolación: pendiente del último tramo
            slope = (ys[-1] - ys[-2]) / (ts[-1] - ts[-2])
            return float(np.exp(ys[-1] + slope * (t - ts[-1])))
        i = bisect_left(ts, t)
        w = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
        return float(np.exp(ys[i - 1] + w * (ys[i] - ys[i - 1])))

    def df(self, d: _dt.date) -> float:
        return self.df_t(self.t(d))

    def zero(self, d: _dt.date) -> float:
        """Tasa cero continua (anualizada, base interna de la curva)."""
        t = self.t(d)
        if t <= 0:
            return 0.0
        return -np.log(self.df_t(t)) / t

    def zero_rate_annual(self, d: _dt.date, day_count: str = "ACT/360") -> float:
        """Tasa cero ANUALMENTE COMPUESTA bajo el day count indicado —
        la convención que muestra la pantalla (columnas Zero Bid/Mid/Ask,
        con selectores ACT/360 + PA). Se define por:
            DF = 1 / (1 + R)^τ      con τ = year_fraction(day_count)
        =>  R = DF^(-1/τ) − 1
        NO altera el DF calibrado; es solo una representación de la tasa.
        (Distinta de zero(), que devuelve la tasa CONTINUA de uso interno.)"""
        tau = dt.year_fraction(day_count, self.valuation_date, d)
        if tau <= 0:
            return 0.0
        return self.df(d) ** (-1.0 / tau) - 1.0

    def fwd(self, d1: _dt.date, d2: _dt.date, day_count: str = "ACT/360") -> float:
        """Forward simple entre d1 y d2 con el day count indicado."""
        tau = dt.year_fraction(day_count, d1, d2)
        if tau <= 0:
            return 0.0
        return (self.df(d1) / self.df(d2) - 1.0) / tau

    # ----------------------------------------------------------- utilidades
    def nodes(self):
        return list(zip(self.times, np.exp(self.log_dfs)))

    def __repr__(self) -> str:
        return f"Curve({self.name}, {len(self.times) - 1} pilares)"
