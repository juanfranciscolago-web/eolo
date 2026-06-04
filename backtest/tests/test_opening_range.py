"""Tests para Opening Range indicator (TR-Juan-077)."""
from datetime import datetime, timezone, timedelta

from backtest.opening_range import compute_opening_range, classify_or_state


def _ts_ms_at(et_h: int, et_m: int) -> int:
    """Generate UTC ts_ms for given ET hour/min (assume EDT UTC-4)."""
    dt_utc = datetime(2026, 6, 4, et_h + 4, et_m, 0, tzinfo=timezone.utc)
    return int(dt_utc.timestamp() * 1000)


def test_compute_or_basic():
    candles = [
        {"ts_ms": _ts_ms_at(9, 30), "open": 7580, "high": 7590, "low": 7575, "close": 7585},
        {"ts_ms": _ts_ms_at(9, 31), "open": 7585, "high": 7595, "low": 7580, "close": 7592},
        {"ts_ms": _ts_ms_at(9, 32), "open": 7592, "high": 7598, "low": 7588, "close": 7594},
        {"ts_ms": _ts_ms_at(9, 33), "open": 7594, "high": 7596, "low": 7585, "close": 7587},
        {"ts_ms": _ts_ms_at(9, 34), "open": 7587, "high": 7592, "low": 7583, "close": 7589},
        {"ts_ms": _ts_ms_at(9, 35), "open": 7589, "high": 7591, "low": 7582, "close": 7586},
        {"ts_ms": _ts_ms_at(9, 36), "open": 7586, "high": 7600, "low": 7586, "close": 7595},  # post-OR
    ]
    out = compute_opening_range(candles, or_duration_min=6)
    assert out is not None
    assert out["or_high"] == 7598
    assert out["or_low"] == 7575
    assert out["or_mid"] == 7586.5
    assert out["or_width"] == 23.0
    assert out["or_candles_used"] == 6
    assert out["or_fib_up_1618"] == round(7598 + 23 * 1.618, 2)


def test_classify_in_range():
    or_data = {
        "or_high": 7598, "or_low": 7575, "or_mid": 7586.5, "or_width": 23,
        "or_fib_up_1618": 7635.21, "or_fib_up_2236": 7649.43, "or_fib_up_2618": 7658.21,
        "or_fib_down_1618": 7537.79, "or_fib_down_2236": 7523.57, "or_fib_down_2618": 7514.79,
    }
    state = classify_or_state(or_data, 7585)
    assert state["state"] == "in_range"
    assert state["next_target_up"] == 7598
    assert state["next_target_down"] == 7575


def test_classify_breakout_up():
    or_data = {
        "or_high": 7598, "or_low": 7575, "or_mid": 7586.5, "or_width": 23,
        "or_fib_up_1618": 7635.21, "or_fib_up_2236": 7649.43, "or_fib_up_2618": 7658.21,
        "or_fib_down_1618": 7537.79, "or_fib_down_2236": 7523.57, "or_fib_down_2618": 7514.79,
    }
    state = classify_or_state(or_data, 7610)
    assert state["state"] == "breakout_up"
    assert state["next_target_up"] == 7635.21
    assert state["next_target_down"] == 7598


def test_classify_deep_above():
    or_data = {
        "or_high": 7598, "or_low": 7575, "or_mid": 7586.5, "or_width": 23,
        "or_fib_up_1618": 7635.21, "or_fib_up_2236": 7649.43, "or_fib_up_2618": 7658.21,
        "or_fib_down_1618": 7537.79, "or_fib_down_2236": 7523.57, "or_fib_down_2618": 7514.79,
    }
    state = classify_or_state(or_data, 7640)
    assert state["state"] == "deep_above"
    assert state["next_target_up"] == 7649.43


def test_classify_breakout_down():
    or_data = {
        "or_high": 7598, "or_low": 7575, "or_mid": 7586.5, "or_width": 23,
        "or_fib_up_1618": 7635.21, "or_fib_up_2236": 7649.43, "or_fib_up_2618": 7658.21,
        "or_fib_down_1618": 7537.79, "or_fib_down_2236": 7523.57, "or_fib_down_2618": 7514.79,
    }
    state = classify_or_state(or_data, 7560)
    assert state["state"] == "breakout_down"


def test_classify_deep_below():
    or_data = {
        "or_high": 7598, "or_low": 7575, "or_mid": 7586.5, "or_width": 23,
        "or_fib_up_1618": 7635.21, "or_fib_up_2236": 7649.43, "or_fib_up_2618": 7658.21,
        "or_fib_down_1618": 7537.79, "or_fib_down_2236": 7523.57, "or_fib_down_2618": 7514.79,
    }
    state = classify_or_state(or_data, 7530)
    assert state["state"] == "deep_below"


def test_compute_or_empty():
    assert compute_opening_range([]) is None


def test_compute_or_no_candles_in_window():
    candles = [
        {"ts_ms": _ts_ms_at(8, 0), "open": 7580, "high": 7585, "low": 7575, "close": 7580},
    ]
    assert compute_opening_range(candles, or_duration_min=6) is None


def test_classify_no_data():
    assert classify_or_state(None, 100)["state"] == "no_data"
    assert classify_or_state({"or_high": 100}, None)["state"] == "no_data"
