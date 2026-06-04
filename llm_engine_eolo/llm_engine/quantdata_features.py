"""Quant Data compute layer — derived indicators (Master Plan v2.1 sec 6).

Transforma raw fields del snapshot en indicadores nombrados que el LLM puede
usar directamente en sus reglas.

Sprint T1.A (2026-06-02):
- classify_gamma_regime: long / negative / transition
- compute_vrp_score: rich / fair / cheap

Sprint T1.B (TERMINATOR Sub-B, 2026-06-03):
- compute_magnet_strength: max_pain pin probability + confluence (TR-Juan-067)
- compute_cascade_risk: gamma cascade defense zone (TR-Juan-064)
- compute_smart_money_bias: institutional flow bias (TR-Juan-066/068)
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

    Sprint BUNDLE-v1.5: validación defensiva — gamma_zero<=0 o non-finite
    también return None (evita 'transition' espurea ante missing/garbage data,
    causa raíz de 160 WAIT/8d via TR-Juan-070).
    """
    if spot is None or gamma_zero_strike is None:
        return None
    try:
        s = float(spot)
        gz = float(gamma_zero_strike)
    except (TypeError, ValueError):
        return None
    if s <= 0 or gz <= 0:
        return None
    if s != s or gz != gz:  # NaN
        return None
    distance_pct = (s - gz) / s * 100
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

    Sprint BUNDLE-v1.5: tratar 0 / valores no-finitos / fuera de [0,100]
    como sentinels de missing data → return None en vez de 'cheap'. Esto
    evita disparar TR-Juan-063 (NO_NEW_SELLING) ante data ausente,
    causa raíz de 119 WAIT/8d en análisis live.
    """
    if vrp_percentile_252d is None:
        return None
    try:
        v = float(vrp_percentile_252d)
    except (TypeError, ValueError):
        return None
    # NaN / Inf / fuera de rango → tratar como missing
    if v != v or v < 0 or v > 100:  # NaN check via self-compare
        return None
    # Sentinel exacto 0 = no data (percentiles legítimos casi nunca caen exactamente en 0)
    if v == 0:
        return None
    if v > 70:
        return "rich"
    elif v < 30:
        return "cheap"
    else:
        return "fair"


def compute_magnet_strength(
    spot: Optional[float],
    max_pain_strike: Optional[float],
    gamma_zero_strike: Optional[float] = None,
    oi_max_call_strike: Optional[float] = None,
    oi_max_put_strike: Optional[float] = None,
) -> Optional[dict]:
    """Magnet/pin probability per TR-Juan-067 (Master Plan sec 7.5).

    Returns {"score": 0-100, "magnet_strike": float, "components": {...}} or None.

    Base score from max_pain proximity to spot (TR-Juan-067 threshold ±0.3%):
      - within 0.3%  → 70 base
      - within 0.6%  → 50 base
      - within 1.0%  → 30 base
      - >1.0%        → 10 base
    Bonus +10 each for: gex_zero, oi_max_call, oi_max_put within 0.5% of magnet.
    Capped 0-100.
    """
    if spot is None or spot <= 0 or max_pain_strike is None:
        return None
    distance_pct = abs(spot - max_pain_strike) / spot * 100
    if distance_pct <= 0.3:
        base = 70
    elif distance_pct <= 0.6:
        base = 50
    elif distance_pct <= 1.0:
        base = 30
    else:
        base = 10
    components = {"max_pain_distance_pct": round(distance_pct, 3)}
    bonus = 0
    proximity_threshold = max_pain_strike * 0.005  # 0.5% of magnet
    for label, strike in (
        ("gamma_zero", gamma_zero_strike),
        ("oi_max_call", oi_max_call_strike),
        ("oi_max_put", oi_max_put_strike),
    ):
        if strike is not None and abs(strike - max_pain_strike) <= proximity_threshold:
            bonus += 10
            components[label] = "confluent"
        elif strike is not None:
            components[label] = f"diverges_${strike:.2f}"
    score = max(0, min(100, base + bonus))
    return {"score": score, "magnet_strike": max_pain_strike, "components": components}


def compute_cascade_risk(
    spot: Optional[float],
    oi_max_put_strike: Optional[float],
    atr_daily: Optional[float],
    gex_total: Optional[float] = None,
    iv_rank_call: Optional[float] = None,
    put_skew_25d: Optional[float] = None,
    vix_level: Optional[float] = None,
) -> Optional[dict]:
    """Cascade defense risk per TR-Juan-064 (Master Plan sec 6.4).

    Returns {"risk_level": "low|medium|high|extreme", "score": 0-100, "drivers": [...]}.

    Primary trigger TR-Juan-064: spot < oi_max_put_strike + 1 ATR → cascade_zone.
    Amplifiers (additive): negative GEX magnitude, IVR call < 30 (no cushion),
    steep put skew (>5%), VIX > 22.
    """
    if spot is None or spot <= 0:
        return None
    drivers: list[str] = []
    score = 0

    if oi_max_put_strike is not None and atr_daily is not None:
        cascade_zone_top = oi_max_put_strike + atr_daily
        if spot < cascade_zone_top:
            distance = (cascade_zone_top - spot) / spot * 100
            score += 50
            drivers.append(
                f"spot ${spot:.2f} < cascade_zone_top ${cascade_zone_top:.2f} "
                f"(oi_max_put + 1 ATR; +{distance:.2f}% inside zone)"
            )

    if gex_total is not None and gex_total < 0:
        magnitude = abs(gex_total) / 1e9
        if magnitude > 2:
            score += 20
            drivers.append(f"negative GEX magnitude {magnitude:.2f}B (>2B)")
        elif magnitude > 1:
            score += 10
            drivers.append(f"negative GEX magnitude {magnitude:.2f}B (>1B)")

    if iv_rank_call is not None and iv_rank_call < 30:
        score += 10
        drivers.append(f"IVR call {iv_rank_call:.0f} <30 (no premium cushion)")

    if put_skew_25d is not None and put_skew_25d > 5:
        score += 10
        drivers.append(f"steep put skew 25Δ {put_skew_25d:.1f}% (>5%)")

    if vix_level is not None and vix_level > 22:
        score += 10
        drivers.append(f"VIX {vix_level:.1f} (>22)")

    score = max(0, min(100, score))
    if score >= 70:
        risk_level = "extreme"
    elif score >= 50:
        risk_level = "high"
    elif score >= 25:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {"risk_level": risk_level, "score": score, "drivers": drivers}


def compute_smart_money_bias(
    net_call_premium_drift: Optional[float],
    net_put_premium_drift: Optional[float],
    put_skew_25d: Optional[float] = None,
    call_skew_25d: Optional[float] = None,
    drift_threshold_usd: float = 5_000_000,
) -> Optional[dict]:
    """Smart money flow bias per TR-Juan-066 + TR-Juan-068 (Master Plan sec 7.2).

    Returns {"bias": "bullish|bearish|neutral", "conviction": 0-100, "evidence": {...}}.

    Primary: net premium drift differential.
        net_call - net_put > +threshold  → bullish
        net_call - net_put < -threshold  → bearish
        within ±threshold                 → neutral
    Secondary (TR-Juan-068): put_skew > call_skew + 2% reinforces SELL_PUT side.
    """
    if net_call_premium_drift is None and net_put_premium_drift is None:
        return None
    nc = net_call_premium_drift or 0
    np_ = net_put_premium_drift or 0
    diff = nc - np_

    if diff > drift_threshold_usd:
        bias = "bullish"
        magnitude = diff / drift_threshold_usd
    elif diff < -drift_threshold_usd:
        bias = "bearish"
        magnitude = -diff / drift_threshold_usd
    else:
        bias = "neutral"
        magnitude = abs(diff) / drift_threshold_usd

    conviction = int(max(0, min(100, magnitude * 30)))

    evidence: dict = {
        "net_call_drift_usd":       round(nc, 0),
        "net_put_drift_usd":        round(np_, 0),
        "diff_usd":                 round(diff, 0),
        "drift_threshold_usd":      drift_threshold_usd,
    }

    if put_skew_25d is not None and call_skew_25d is not None:
        skew_diff = put_skew_25d - call_skew_25d
        evidence["skew_put_minus_call_pct"] = round(skew_diff, 2)
        if skew_diff > 2 and bias != "bearish":
            conviction = min(100, conviction + 15)
            evidence["skew_amplifier"] = "TR-Juan-068 SELL_PUT bias (+15 conviction)"

    return {"bias": bias, "conviction": conviction, "evidence": evidence}
