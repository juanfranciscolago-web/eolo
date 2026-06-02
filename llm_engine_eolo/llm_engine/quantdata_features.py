"""Quant Data compute layer — derived indicators (Master Plan v2.1 sec 6).

Transforma raw fields del snapshot en indicadores nombrados que el LLM puede
usar directamente en sus reglas.

Sprint T1.A (2026-06-02):
- classify_gamma_regime: long / negative / transition
- compute_vrp_score: rich / fair / cheap

Sprint T1.B (future):
- magnet_strength, cascade_risk, smart_money_bias
"""
from typing import Literal, Optional


def classify_gamma_regime(
    spot: Optional[float],
    gamma_zero_strike: Optional[float],
    flip_threshold_pct: float = 0.5,
) -> Optional[Literal["long", "negative", "transition"]]:
    """Classify gamma regime per Master Plan sec 6.1.

    long: spot > gamma_zero + threshold% → mean-reverting
    negative: spot < gamma_zero - threshold% → trending/explosive
    transition: within ±threshold% → high vol zone gris
    """
    if spot is None or gamma_zero_strike is None or spot <= 0:
        return None
    distance_pct = (spot - gamma_zero_strike) / spot * 100
    if distance_pct > flip_threshold_pct:
        return "long"
    elif distance_pct < -flip_threshold_pct:
        return "negative"
    else:
        return "transition"


def compute_vrp_score(
    vrp_percentile_252d: Optional[float],
) -> Optional[Literal["rich", "fair", "cheap"]]:
    """Compute VRP score per Master Plan sec 6.2.

    rich: percentile > 70 → vender es muy rentable
    fair: 30-70 → neutral
    cheap: < 30 → NO vender, esperar
    """
    if vrp_percentile_252d is None:
        return None
    if vrp_percentile_252d > 70:
        return "rich"
    elif vrp_percentile_252d < 30:
        return "cheap"
    else:
        return "fair"
