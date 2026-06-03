"""Reconstruct full MarketSnapshot for historical replay (Sprint S5).

Builds on existing T4/T6 modules:
- backtest.historical_fetcher.fetch_one        (online QD fetch, optional)
- backtest.snapshot_builder.build_snapshot_dict_from_cache (parse cached → fields)

Sub-A MEGATERMINATOR adds quality from Schwab pricehistory:
- schwab_historical.get_window_for_date: real daily OHLC.
- indicators: ema/rsi/atr/vwap_from_candles/fibonacci_levels pure-Python.

Sub-A.5 hallazgo (TERMINATOR): sessionDate param soportado por los 30
endpoints QD → backtest 365d UNBLOCKED.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Iterator
import json

from backtest.snapshot_builder import build_snapshot_dict_from_cache
from backtest.indicators import ema, rsi, atr, vwap_from_candles, fibonacci_levels
from backtest.schwab_historical import get_window_for_date


# Sane VIX defaults para regímenes históricos. Para replay fino, override por
# (date → vix_level) lookup table o fetch CBOE históricos (futuro).
DEFAULT_VIX_LEVEL = 16.0
DEFAULT_RSI = 50.0
DEFAULT_ATR_PCT = 0.5  # 0.5% of spot


def reconstruct_snapshot(
    ticker: str,
    target_date: date,
    target_hour: int = 10,
    target_minute: int = 0,
    cache_dir: str = "backtest/data",
) -> Optional[dict]:
    """Reconstruct one historical MarketSnapshot dict.

    Returns dict matching MarketSnapshot Pydantic schema, or None if cached
    data missing for the (ticker, date) tuple.

    Strategy:
      - Load cached QD raw responses via snapshot_builder.
      - Fill QD-derived fields (max_pain, iv_rank, gex_*, net_*_drift, etc).
      - Fill fundamental fields with sensible defaults (sea below). For richer
        replay con OHLC/VIX reales, integrar Schwab historical fetch (T+).
    """
    cache_path = Path(cache_dir) / f"{ticker}_{target_date.isoformat()}.json"
    if not cache_path.exists():
        return None

    qd_snap = build_snapshot_dict_from_cache(cache_path)
    if qd_snap is None:
        return None

    # Extract spot from cached QD if available (max_pain returns stockPrice).
    raw = json.loads(cache_path.read_text()).get("_raw", {})
    mp_raw = raw.get("_qd_max-pain", {})
    spot = mp_raw.get("stockPrice") if isinstance(mp_raw, dict) else None
    if spot is None:
        # Fallback to a literal that lets the snapshot validate; backtest is
        # signaling test, not realistic strike/level scoring.
        spot = qd_snap.get("max_pain_strike") or 100.0
    spot = float(spot)

    timestamp_iso = f"{target_date.isoformat()}T{target_hour:02d}:{target_minute:02d}:00"

    # Sub-A: real indicators from Schwab pricehistory cuando disponible.
    window = get_window_for_date(ticker, target_date, lookback_days=50, cache_dir=cache_dir)
    fund = _fundamentals_from_window(window, spot)

    full = {
        "ticker":             ticker,
        "timestamp":          timestamp_iso,
        "session_phase":      "regular",
        "price":              spot,
        "open_price":         fund["open_price"],
        "high":               fund["high"],
        "low":                fund["low"],
        "prev_close":         fund["prev_close"],
        "vix_level":          DEFAULT_VIX_LEVEL,
        "pdh":                fund["pdh"],
        "pdl":                fund["pdl"],
        "pdc":                fund["pdc"],
        "rsi_2m":             fund["rsi_2m"],
        "rsi_15m":            fund["rsi_15m"],
        "rsi_daily":          fund["rsi_daily"],
        "atr_2m":             fund["atr_2m"],
        "atr_15m":            fund["atr_15m"],
        "atr_daily":          fund["atr_daily"],
        "ema_9_daily":        fund["ema_9_daily"],
        "ema_21_daily":       fund["ema_21_daily"],
        "ema_50_daily":       fund["ema_50_daily"],
        "ema_200_daily":      fund["ema_200_daily"],
        "vwap":               fund["vwap"],
        "fib_r1":             fund["fib_r1"],
        "fib_r2":             fund["fib_r2"],
        "fib_r3":             fund["fib_r3"],
        "fib_s1":             fund["fib_s1"],
        "fib_s2":             fund["fib_s2"],
        "fib_s3":             fund["fib_s3"],
    }
    full.update(qd_snap)  # QD-derived fields override defaults
    return full


def _fundamentals_from_window(window: list[dict], fallback_spot: float) -> dict:
    """Compute fundamentals dict from list of daily OHLC candles.

    Window debe estar ordenado asc por fecha. Empty → all defaults.
    """
    if not window:
        atr_default = max(0.01, fallback_spot * DEFAULT_ATR_PCT / 100)
        rng = max(0.01, fallback_spot * 0.01)
        return {
            "open_price":   fallback_spot,
            "high":         fallback_spot * 1.005,
            "low":          fallback_spot * 0.995,
            "prev_close":   fallback_spot,
            "pdh":          fallback_spot * 1.01,
            "pdl":          fallback_spot * 0.99,
            "pdc":          fallback_spot,
            "rsi_2m":       DEFAULT_RSI,
            "rsi_15m":      DEFAULT_RSI,
            "rsi_daily":    DEFAULT_RSI,
            "atr_2m":       atr_default,
            "atr_15m":      atr_default,
            "atr_daily":    atr_default,
            "ema_9_daily":  fallback_spot,
            "ema_21_daily": fallback_spot,
            "ema_50_daily": fallback_spot,
            "ema_200_daily": fallback_spot,
            "vwap":         fallback_spot,
            **fibonacci_levels(fallback_spot + rng, fallback_spot - rng),
        }

    closes = [c["close"] for c in window]
    highs  = [c["high"]  for c in window]
    lows   = [c["low"]   for c in window]
    last   = window[-1]
    prev   = window[-2] if len(window) >= 2 else last

    rsis = rsi(closes, period=14)
    atrs = atr(highs, lows, closes, period=14)
    ema9   = ema(closes, 9)
    ema21  = ema(closes, 21)
    ema50  = ema(closes, 50)
    ema200 = ema(closes, 200)

    return {
        "open_price":   last["open"],
        "high":         last["high"],
        "low":          last["low"],
        "prev_close":   prev["close"],
        "pdh":          prev["high"],
        "pdl":          prev["low"],
        "pdc":          prev["close"],
        "rsi_2m":       rsis[-1],
        "rsi_15m":      rsis[-1],
        "rsi_daily":    rsis[-1],
        "atr_2m":       max(0.01, atrs[-1] * 0.1),  # intraday proxy
        "atr_15m":      max(0.01, atrs[-1] * 0.3),
        "atr_daily":    max(0.01, atrs[-1]),
        "ema_9_daily":  ema9[-1],
        "ema_21_daily": ema21[-1],
        "ema_50_daily": ema50[-1],
        "ema_200_daily": ema200[-1],
        "vwap":         vwap_from_candles(window[-1:]),  # single-day VWAP proxy
        **fibonacci_levels(prev["high"], prev["low"]),
    }


def iter_historical_snapshots(
    ticker: str,
    start_date: date,
    end_date: date,
    sample_hours: Optional[list[int]] = None,
    cache_dir: str = "backtest/data",
) -> Iterator[dict]:
    """Iterate historical snapshots day by day, hour by hour.

    sample_hours=[10, 12, 14] → 3 snapshots per trading day.
    Default sample_hours=[10] → 1 snapshot/day (cost optimization).
    Weekends skipped.
    """
    sample_hours = sample_hours or [10]
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Mon-Fri
            for hour in sample_hours:
                snap = reconstruct_snapshot(ticker, current, hour, cache_dir=cache_dir)
                if snap is not None:
                    yield snap
        current += timedelta(days=1)
