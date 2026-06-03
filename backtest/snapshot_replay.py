"""Reconstruct full MarketSnapshot for historical replay (Sprint S5).

Builds on existing T4/T6 modules:
- backtest.historical_fetcher.fetch_one        (online QD fetch, optional)
- backtest.snapshot_builder.build_snapshot_dict_from_cache (parse cached → fields)

This module extends with:
- Fundamentals fallbacks: price/OHLC/VIX/RSI/ATR sane defaults so /decide accepts.
- iter_historical_snapshots: weekend-aware day iterator with sampling.

Sub-A.5 hallazgo: sessionDate param soportado por los 30 endpoints QD → backtest
365d UNBLOCKED.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Iterator
import json

from backtest.snapshot_builder import build_snapshot_dict_from_cache


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

    atr = max(0.01, spot * DEFAULT_ATR_PCT / 100)

    full = {
        "ticker":             ticker,
        "timestamp":          timestamp_iso,
        "session_phase":      "regular",
        "price":              spot,
        "open_price":         spot,
        "high":               spot * 1.005,
        "low":                spot * 0.995,
        "prev_close":         spot,
        "vix_level":          DEFAULT_VIX_LEVEL,
        "pdh":                spot * 1.01,
        "pdl":                spot * 0.99,
        "pdc":                spot,
        "rsi_2m":             DEFAULT_RSI,
        "rsi_15m":            DEFAULT_RSI,
        "rsi_daily":          DEFAULT_RSI,
        "atr_2m":             atr,
        "atr_15m":            atr,
        "atr_daily":          atr,
    }
    full.update(qd_snap)  # QD-derived fields override defaults
    return full


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
