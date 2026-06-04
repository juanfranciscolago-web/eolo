"""Opening Range Breakout indicator + Fibonacci extensions (TR-Juan-077).

Basado en código ThinkOrSwim de Juan, 2026-06-04.

Opening Range = high/low de primeros N minutos del session open (default 6 min, 9:30-9:36 ET).

Niveles output:
- or_high, or_low, or_mid, or_width
- Fibonacci extensions arriba: or_fib_up_{1618|2236|2618}
- Fibonacci extensions abajo: or_fib_down_{1618|2236|2618}
- State: in_range | breakout_up | breakout_down | deep_above | deep_below
"""
from __future__ import annotations
from datetime import datetime, time, timezone, timedelta
from typing import Optional


def compute_opening_range(
    candles_1min: list[dict],
    session_open_et: time = time(9, 30),
    or_duration_min: int = 6,
    et_utc_offset_hours: int = -4,  # EDT default, EST = -5
) -> Optional[dict]:
    """Compute Opening Range from 1-min candles.

    candles_1min: list of dicts con keys {"ts_ms", "open", "high", "low", "close"}.
    Returns dict con or_high/or_low/or_mid/or_width + Fibonacci extensions,
    o None si no hay candles dentro del OR window.
    """
    if not candles_1min:
        return None

    or_high = -float("inf")
    or_low = float("inf")
    or_candles_count = 0
    et_offset = timedelta(hours=et_utc_offset_hours)

    for c in candles_1min:
        ts_ms = c.get("ts_ms")
        if ts_ms is None:
            continue
        dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        # Convert UTC → ET (EDT default UTC-4)
        et_dt = dt_utc + et_offset
        et_time = et_dt.time()
        if et_time < session_open_et:
            continue
        seconds_in = (et_time.hour - session_open_et.hour) * 3600 + (et_time.minute - session_open_et.minute) * 60
        if seconds_in >= or_duration_min * 60:
            break
        h = c.get("high")
        l = c.get("low")
        if h is None or l is None:
            continue
        or_high = max(or_high, h)
        or_low = min(or_low, l)
        or_candles_count += 1

    if or_candles_count == 0 or or_high == -float("inf"):
        return None

    or_width = or_high - or_low
    or_mid = (or_high + or_low) / 2.0

    return {
        "or_high":          round(or_high, 2),
        "or_low":           round(or_low, 2),
        "or_mid":           round(or_mid, 2),
        "or_width":         round(or_width, 2),
        "or_candles_used":  or_candles_count,
        "or_fib_up_1618":   round(or_high + or_width * 1.618, 2),
        "or_fib_up_2236":   round(or_high + or_width * 2.236, 2),
        "or_fib_up_2618":   round(or_high + or_width * 2.618, 2),
        "or_fib_down_1618": round(or_low - or_width * 1.618, 2),
        "or_fib_down_2236": round(or_low - or_width * 2.236, 2),
        "or_fib_down_2618": round(or_low - or_width * 2.618, 2),
    }


def classify_or_state(or_data: Optional[dict], current_price: Optional[float]) -> dict:
    """Classify current price relative to Opening Range.

    Returns dict con:
      state: in_range | breakout_up | breakout_down | deep_above | deep_below | no_data
      distance_from_mid_pct: float | None
      next_target_up / next_target_down: float | None
    """
    if or_data is None or current_price is None:
        return {"state": "no_data", "distance_from_mid_pct": None,
                "next_target_up": None, "next_target_down": None}

    orh = or_data["or_high"]
    orl = or_data["or_low"]
    orm = or_data["or_mid"]
    dist_pct = ((current_price - orm) / orm) * 100 if orm else 0.0

    if orl <= current_price <= orh:
        state = "in_range"
        nt_up = orh
        nt_dn = orl
    elif current_price > or_data["or_fib_up_1618"]:
        state = "deep_above"
        nt_up = or_data["or_fib_up_2236"]
        nt_dn = or_data["or_fib_up_1618"]
    elif current_price > orh:
        state = "breakout_up"
        nt_up = or_data["or_fib_up_1618"]
        nt_dn = orh
    elif current_price < or_data["or_fib_down_1618"]:
        state = "deep_below"
        nt_up = or_data["or_fib_down_1618"]
        nt_dn = or_data["or_fib_down_2236"]
    else:
        state = "breakout_down"
        nt_up = orl
        nt_dn = or_data["or_fib_down_1618"]

    return {
        "state":                  state,
        "distance_from_mid_pct":  round(dist_pct, 3),
        "next_target_up":         nt_up,
        "next_target_down":       nt_dn,
    }
