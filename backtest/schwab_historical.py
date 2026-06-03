"""Schwab pricehistory fetcher for backtest snapshot quality.

Reusa el endpoint /marketdata/v1/pricehistory ya usado por
eolo-crop/stream/rest_polling.py:300 (daily history fetch).

Cache local: backtest/data/schwab_ohlc_{ticker}.json para evitar
re-fetch en cada run.

Auth: Schwab access_token desde Firestore (helpers.get_access_token).
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional
import json
import logging
import time

logger = logging.getLogger(__name__)

URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"


def _get_schwab_token() -> Optional[str]:
    """Try to fetch Schwab access_token via existing helpers.

    Sub-1 BACKTEST-COMPLETO fix: helpers exporta `get_access_token`, no
    `get_schwab_access_token`. El nombre erróneo del primer scaffold S5
    causaba que todo el backtest local usara indicators defaults.
    """
    try:
        import sys
        sys.path.insert(0, "eolo-crop")
        from helpers import get_access_token  # type: ignore
        return get_access_token()
    except Exception as e:
        logger.warning(f"[schwab_historical] token fetch failed: {e}")
        return None


def fetch_daily_ohlc(
    ticker: str,
    period_years: int = 1,
    cache_dir: str = "backtest/data",
    refresh: bool = False,
) -> list[dict]:
    """Fetch last `period_years` of daily OHLC candles for `ticker`.

    Returns list[{date_iso, open, high, low, close, volume}].
    Disk cache: {cache_dir}/schwab_ohlc_{ticker}.json. Skip fetch if cache fresh.
    """
    cache = Path(cache_dir) / f"schwab_ohlc_{ticker}.json"
    if cache.exists() and not refresh:
        try:
            data = json.loads(cache.read_text())
            return data.get("candles", [])
        except Exception:
            pass

    token = _get_schwab_token()
    if not token:
        logger.warning(f"[schwab_historical] no token, returning empty for {ticker}")
        return []

    try:
        import requests
        resp = requests.get(
            URL,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "symbol":          ticker,
                "periodType":      "year",
                "period":          period_years,
                "frequencyType":   "daily",
                "frequency":       1,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[schwab_historical] HTTP {resp.status_code} for {ticker}: {resp.text[:200]}")
            return []
        raw = (resp.json() or {}).get("candles", [])
    except Exception as e:
        logger.warning(f"[schwab_historical] fetch failed {ticker}: {e}")
        return []

    candles = []
    for c in raw:
        ts_ms = c.get("datetime")
        if ts_ms is None:
            continue
        d = date.fromtimestamp(int(ts_ms) / 1000).isoformat()
        candles.append({
            "date":    d,
            "open":    float(c.get("open") or 0),
            "high":    float(c.get("high") or 0),
            "low":     float(c.get("low") or 0),
            "close":   float(c.get("close") or 0),
            "volume":  float(c.get("volume") or 0),
        })
    candles.sort(key=lambda x: x["date"])

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ticker": ticker, "fetched_ts": time.time(), "candles": candles}))
    return candles


def get_window_for_date(
    ticker: str,
    target_date: date,
    lookback_days: int = 50,
    cache_dir: str = "backtest/data",
) -> list[dict]:
    """Return list of OHLC candles ending at target_date (inclusive), max `lookback_days` long.

    Returns empty list if data unavailable. Useful for indicator computation.
    """
    all_candles = fetch_daily_ohlc(ticker, cache_dir=cache_dir)
    if not all_candles:
        return []
    target_iso = target_date.isoformat()
    window = [c for c in all_candles if c["date"] <= target_iso]
    return window[-lookback_days:]
