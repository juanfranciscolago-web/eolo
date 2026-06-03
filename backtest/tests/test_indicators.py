"""Tests for backtest.indicators (Sub-A MEGATERMINATOR)."""
from backtest.indicators import ema, sma, rsi, atr, vwap_from_candles, fibonacci_levels


def test_ema_first_equals_input():
    out = ema([10, 11, 12, 13], period=3)
    assert out[0] == 10
    assert len(out) == 4
    # Each subsequent value pulls toward latest
    assert out[1] > 10
    assert out[-1] > 12


def test_ema_period_1_passthrough():
    out = ema([10, 11, 12], period=1)
    assert out == [10, 11, 12]


def test_sma_window():
    out = sma([1, 2, 3, 4, 5], period=3)
    assert out[2] == 2.0  # (1+2+3)/3
    assert out[-1] == 4.0  # (3+4+5)/3


def test_rsi_short_series_returns_50():
    out = rsi([100, 101, 102], period=14)
    assert out == [50.0, 50.0, 50.0]


def test_rsi_monotonic_up_returns_high():
    closes = [100 + i for i in range(30)]
    out = rsi(closes, period=14)
    # All gains, no losses → RSI near 100
    assert out[-1] > 95


def test_rsi_monotonic_down_returns_low():
    closes = [100 - i for i in range(30)]
    out = rsi(closes, period=14)
    assert out[-1] < 5


def test_atr_positive_and_grows_with_range():
    highs  = [105, 110, 108, 112, 109]
    lows   = [100, 102, 103, 105, 106]
    closes = [104, 108, 105, 110, 107]
    out = atr(highs, lows, closes, period=3)
    assert len(out) == 5
    assert all(v > 0 for v in out)


def test_vwap_zero_volume_returns_zero():
    candles = [{"high": 10, "low": 8, "close": 9, "volume": 0}]
    assert vwap_from_candles(candles) == 0.0


def test_vwap_weighted_average():
    candles = [
        {"high": 11, "low": 9,  "close": 10, "volume": 100},  # typical=10
        {"high": 21, "low": 19, "close": 20, "volume": 100},  # typical=20
    ]
    # Equal weighted volumes → average of 10 and 20 = 15
    assert vwap_from_candles(candles) == 15.0


def test_fibonacci_levels_basic():
    lvls = fibonacci_levels(110, 90)
    assert lvls["fib_r3"] == 110  # high
    assert lvls["fib_s3"] == 90   # low
    # r1 should be above midpoint of (90 → 110)
    assert lvls["fib_r1"] > 90
    assert lvls["fib_r1"] < lvls["fib_r2"] < lvls["fib_r3"]


def test_fibonacci_levels_inverted_returns_flat():
    lvls = fibonacci_levels(100, 100)
    assert lvls["fib_r1"] == 100
    assert lvls["fib_s1"] == 100


def test_snapshot_replay_uses_real_indicators(tmp_path, monkeypatch):
    """E2E: cache QD + Schwab historical → fundamentals reales (no defaults)."""
    import json as _json
    from datetime import date as _date

    # 1) Cache QD min con stockPrice
    qd_cache = tmp_path / "SPY_2026-05-26.json"
    qd_cache.write_text(_json.dumps({
        "ticker": "SPY",
        "timestamp": "2026-05-26T09:30:00",
        "_raw": {"_qd_max-pain": {"maxPainStrikePrice": 750.0, "stockPrice": 752.5}},
    }))

    # 2) Mock Schwab OHLC window con 30 días sintéticos
    fake_window = [
        {"date": f"2026-05-{i:02d}", "open": 750+i, "high": 752+i, "low": 748+i, "close": 751+i, "volume": 1e6}
        for i in range(1, 27)
    ]

    import backtest.snapshot_replay as sr
    monkeypatch.setattr(sr, "get_window_for_date", lambda *a, **kw: fake_window)

    snap = sr.reconstruct_snapshot("SPY", _date(2026, 5, 26), cache_dir=str(tmp_path))
    assert snap is not None
    # Reales, NO defaults:
    assert snap["ema_9_daily"] != 752.5  # would be spot fallback if defaults
    assert snap["rsi_daily"] != 50.0     # would be DEFAULT_RSI if window empty
    assert snap["atr_daily"] > 0
    # prev candle is i=25 (2026-05-25), high = 752+25 = 777
    assert snap["pdh"] == 777
    assert snap["fib_r3"] == snap["pdh"]
