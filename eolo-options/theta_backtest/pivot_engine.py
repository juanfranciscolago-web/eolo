# ============================================================
#  Theta Harvest Backtester — Pivot Engine (offline)
#
#  Versión sin-red: calcula pivots Standard, Camarilla y
#  Fibonacci usando datos históricos OHLC.
#
#  Retorna la misma estructura de "risk zone" que la versión
#  live (pivot_analysis.py) para garantizar coherencia.
# ============================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .config import (
    DIST_VERY_LOW_PCT,
    DIST_LOW_PCT,
    DIST_MID_PCT,
    DELTA_BY_RISK,
)


# ─────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────

@dataclass
class PivotLevels:
    """Niveles de pivot de un solo método."""
    method: str         # "standard" | "camarilla" | "fibonacci"
    pp:  float          # pivot point principal
    r1:  float
    r2:  float
    r3:  float
    s1:  float
    s2:  float
    s3:  float


@dataclass
class PivotZoneResult:
    """Resultado consolidado del análisis de pivots."""
    risk_zone:         str    # VERY_LOW / LOW / MID / NO_TRADE
    distance_pct:      float  # % distancia al soporte/resistencia más cercano
    nearest_level:     float  # precio del nivel más cercano
    spread_type:       str    # put_credit_spread | call_credit_spread
    delta_min:         float
    delta_max:         float
    support:           float
    resistance:        float
    avg_support:       float  # promedio de s1 entre los 3 métodos
    avg_resistance:    float  # promedio de r1 entre los 3 métodos
    levels:            list[PivotLevels]


# ─────────────────────────────────────────────────────────
#  Calculadores de pivots
# ─────────────────────────────────────────────────────────

def _standard_pivots(H: float, L: float, C: float) -> PivotLevels:
    pp = (H + L + C) / 3
    r1 = 2 * pp - L
    s1 = 2 * pp - H
    r2 = pp + (H - L)
    s2 = pp - (H - L)
    r3 = H + 2 * (pp - L)
    s3 = L - 2 * (H - pp)
    return PivotLevels("standard", pp, r1, r2, r3, s1, s2, s3)


def _camarilla_pivots(H: float, L: float, C: float) -> PivotLevels:
    range_ = H - L
    r1 = C + range_ * 1.1 / 12
    r2 = C + range_ * 1.1 / 6
    r3 = C + range_ * 1.1 / 4
    s1 = C - range_ * 1.1 / 12
    s2 = C - range_ * 1.1 / 6
    s3 = C - range_ * 1.1 / 4
    pp = (H + L + C) / 3
    return PivotLevels("camarilla", pp, r1, r2, r3, s1, s2, s3)


def _fibonacci_pivots(H: float, L: float, C: float) -> PivotLevels:
    pp   = (H + L + C) / 3
    diff = H - L
    r1 = pp + 0.382 * diff
    r2 = pp + 0.618 * diff
    r3 = pp + 1.000 * diff
    s1 = pp - 0.382 * diff
    s2 = pp - 0.618 * diff
    s3 = pp - 1.000 * diff
    return PivotLevels("fibonacci", pp, r1, r2, r3, s1, s2, s3)


# ─────────────────────────────────────────────────────────
#  Risk zone determination
# ─────────────────────────────────────────────────────────

def _determine_risk_zone(distance_pct: float) -> str:
    """
    Determina la zona de riesgo según la distancia al soporte/resistencia.

    distance_pct  : decimal (ej: 0.008 = 0.8% de distancia)
    DIST_*_PCT    : almacenados en porcentaje (ej: 0.80 = 0.80%)
                    → se dividen por 100 para comparar con distance_pct decimal.

    Lógica (cuanto MÁS lejos del pivot, MENOR el riesgo):
      distancia ≥ 0.80% → VERY_LOW  (muy alejado → delta bajo)
      distancia ≥ 0.51% → LOW
      distancia ≥ 0.25% → MID
      distancia <  0.25% → NO_TRADE (demasiado cerca del pivot)
    """
    very_low = DIST_VERY_LOW_PCT / 100  # 0.0080
    low      = DIST_LOW_PCT      / 100  # 0.0051
    mid      = DIST_MID_PCT      / 100  # 0.0025

    if distance_pct >= very_low:
        return "VERY_LOW"
    if distance_pct >= low:
        return "LOW"
    if distance_pct >= mid:
        return "MID"
    return "NO_TRADE"


# ─────────────────────────────────────────────────────────
#  Main function
# ─────────────────────────────────────────────────────────

def compute_pivots(
    df: pd.DataFrame,
    date: pd.Timestamp,
    current_price: float,
    spread_type: str,
) -> Optional[PivotZoneResult]:
    """
    Calcula Standard + Camarilla + Fibonacci usando la vela
    del día anterior a `date`.

    Parameters
    ----------
    df            : DataFrame OHLCV del subyacente
    date          : fecha de hoy (la sesión de entrada)
    current_price : precio de apertura de hoy
    spread_type   : 'put_credit_spread' | 'call_credit_spread'

    Returns
    -------
    PivotZoneResult o None si no hay datos suficientes
    """
    if date not in df.index:
        return None

    loc = df.index.get_loc(date)
    if loc < 1:
        return None

    prev = df.iloc[loc - 1]
    H = float(prev["High"])
    L = float(prev["Low"])
    C = float(prev["Close"])

    if H == L:   # sesión anómala
        return None

    # Calcular los 3 métodos
    std = _standard_pivots(H, L, C)
    cam = _camarilla_pivots(H, L, C)
    fib = _fibonacci_pivots(H, L, C)
    levels = [std, cam, fib]

    # Promediar s1 y r1 entre métodos
    avg_s1 = (std.s1 + cam.s1 + fib.s1) / 3
    avg_r1 = (std.r1 + cam.r1 + fib.r1) / 3

    # Para put spread → distancia al soporte más cercano
    # Para call spread → distancia a la resistencia más cercana
    is_put = "put" in spread_type

    if is_put:
        nearest = avg_s1
        distance_pct = abs(current_price - nearest) / current_price
    else:
        nearest = avg_r1
        distance_pct = abs(nearest - current_price) / current_price

    risk_zone = _determine_risk_zone(distance_pct)

    delta_range = DELTA_BY_RISK.get(risk_zone, (0.0, 0.0))

    return PivotZoneResult(
        risk_zone      = risk_zone,
        distance_pct   = round(distance_pct, 4),
        nearest_level  = round(nearest, 2),
        spread_type    = spread_type,
        delta_min      = delta_range[0],
        delta_max      = delta_range[1],
        support        = round(avg_s1, 2),
        resistance     = round(avg_r1, 2),
        avg_support    = round(avg_s1, 2),
        avg_resistance = round(avg_r1, 2),
        levels         = levels,
    )
