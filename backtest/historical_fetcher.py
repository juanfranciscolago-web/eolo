"""Fetch historical snapshots Quant Data → cache local JSON.

Usage:
    python3 -m backtest.historical_fetcher --start 2025-06-01 --end 2026-05-31 --tickers SPY,QQQ,IWM

Pulls QD endpoints con sessionDate param para cada (ticker, date).
Cache en backtest/data/{ticker}_{date}.json.
"""
import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path("backtest/data")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_one(ticker: str, date: str, api_key: str) -> dict:
    """Fetch all QD endpoints para (ticker, date). Returns snapshot dict."""
    snap = {
        "ticker": ticker,
        "timestamp": f"{date}T09:30:00",
        "_session_date": date,
    }

    endpoints = [
        ("max-pain", {"sessionDate": date, "filter": {"ticker": ticker, "expirationDate": date}}),
        ("iv-rank", {"sessionDate": date, "filter": {"ticker": ticker, "lookBackPeriod": 252}}),
        ("exposure-by-strike", {"sessionDate": date, "filter": {"ticker": ticker}, "greekMode": "gamma", "representationMode": "total"}),
        ("net-drift", {"sessionDate": date, "filter": {"ticker": ticker}}),
        ("volatility-drift", {"sessionDate": date, "filter": {"ticker": ticker}}),
        ("volatility-skew", {"sessionDate": date, "filter": {"ticker": ticker, "expirationDate": date}}),
        ("term-structure", {"sessionDate": date, "filter": {"ticker": ticker}}),
        ("open-interest-by-strike", {"sessionDate": date, "filter": {"ticker": ticker, "expirationDate": date}}),
    ]

    for endpoint, body in endpoints:
        url = f"https://api.quantdata.us/v1/options/tool/{endpoint}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.load(resp)
            snap[f"_qd_{endpoint}"] = raw
        except Exception as e:
            snap[f"_qd_{endpoint}_error"] = str(e)[:200]

    return snap


def parse_to_snapshot(raw_snap: dict) -> dict:
    """Transform raw QD responses into MarketSnapshot dict fields."""
    out = {
        "ticker": raw_snap["ticker"],
        "timestamp": raw_snap["timestamp"],
    }
    # Parseo mínimo per endpoint — extender en T4 follow-up para mapping completo.
    # Por ahora preserva raw data para análisis posterior.
    out["_raw"] = {k: v for k, v in raw_snap.items() if k.startswith("_qd_")}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="ISO YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="ISO YYYY-MM-DD")
    parser.add_argument("--tickers", default="SPY,QQQ,IWM")
    args = parser.parse_args()

    api_key = subprocess.check_output([
        "gcloud", "secrets", "versions", "access", "latest",
        "--secret=quantdata-api-key", "--project=eolo-schwab-agent"
    ]).decode().strip()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    tickers = args.tickers.split(",")

    fetched = 0
    skipped = 0
    cur = start
    while cur <= end:
        if cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue
        date_str = cur.strftime("%Y-%m-%d")
        for ticker in tickers:
            out_path = OUT_DIR / f"{ticker}_{date_str}.json"
            if out_path.exists():
                skipped += 1
                continue
            snap = fetch_one(ticker, date_str, api_key)
            parsed = parse_to_snapshot(snap)
            out_path.write_text(json.dumps(parsed))
            fetched += 1
            time.sleep(0.25)
        cur += timedelta(days=1)

    print(f"Done: fetched={fetched} skipped_cached={skipped}")


if __name__ == "__main__":
    main()
