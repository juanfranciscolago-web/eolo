"""VIX historical fetcher usando yfinance.

Cache local en backtest/data/vix_history.json.
"""
import json
import os
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Dict

CACHE_PATH = Path(__file__).parent / "data" / "vix_history.json"


def fetch_vix_range(start_date: str, end_date: str) -> Dict[str, float]:
    import yfinance as yf

    print(f"[vix_fetcher] Fetching ^VIX {start_date} -> {end_date}")
    end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
    df = yf.download(
        "^VIX",
        start=start_date,
        end=end_dt.strftime("%Y-%m-%d"),
        interval="1d",
        progress=False,
        auto_adjust=False,
    )

    result = {}
    if df.empty:
        return result

    close_col = df["Close"]
    if hasattr(close_col, "columns"):
        close_col = close_col.iloc[:, 0]

    for idx, vix_close in close_col.items():
        d_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        try:
            result[d_str] = float(vix_close)
        except (TypeError, ValueError):
            pass

    print(f"[vix_fetcher] Got {len(result)} VIX values")
    return result


def load_cache() -> Dict[str, float]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.load(open(CACHE_PATH))
    except Exception:
        return {}


def save_cache(cache: Dict[str, float]):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def get_vix_for_date(d: str, fallback: float = 16.0) -> Optional[float]:
    cache = load_cache()
    if d in cache:
        return cache[d]
    try:
        new_data = fetch_vix_range(d, d)
        if new_data:
            cache.update(new_data)
            save_cache(cache)
            return cache.get(d, fallback)
    except Exception as e:
        print(f"[vix_fetcher] WARN: fetch failed for {d}: {e}")
    return fallback


def ensure_range_cached(start_date: str, end_date: str) -> int:
    cache = load_cache()
    from datetime import date as date_cls
    start_d = date_cls.fromisoformat(start_date)
    end_d = date_cls.fromisoformat(end_date)

    missing = []
    current = start_d
    while current <= end_d:
        if current.weekday() < 5:
            d_str = current.isoformat()
            if d_str not in cache:
                missing.append(d_str)
        current += timedelta(days=1)

    if not missing:
        print(f"[vix_fetcher] Cache already covers {start_date} -> {end_date}")
        return 0

    print(f"[vix_fetcher] Missing {len(missing)} days, fetching range...")
    new_data = fetch_vix_range(start_date, end_date)
    cache.update(new_data)
    save_cache(cache)
    return len(new_data)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m backtest.vix_fetcher START_DATE END_DATE")
        sys.exit(1)
    n = ensure_range_cached(sys.argv[1], sys.argv[2])
    cache = load_cache()
    print(f"\nFetched {n} days. Cache total: {len(cache)} entries")
    print(f"Sample: {list(cache.items())[:5]}")
