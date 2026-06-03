"""CLI: python -m backtest.cli --ticker SPY --start 2026-04-01 --end 2026-04-05

Args:
  --ticker TICKER (repeatable, default SPY)
  --start  YYYY-MM-DD       (required)
  --end    YYYY-MM-DD       (required)
  --sample-hours 10,12,14   (default: 10)
  --no-prescreen            (disable Haiku pre-screen)
  --budget-cap USD          (default: 10.0)
  --output-dir PATH         (default: /tmp/backtest_results)
  --engine-url URL          (default: production engine)
  --dry-run                 (cap $0.50, sample_hours[:1])
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import date

from backtest.runner import run_backtest, DEFAULT_ENGINE_URL


def _resolve_auth_token(engine_url: str) -> str:
    """gcloud auth print-identity-token, with --audiences when possible.

    User accounts no soportan --audiences (fall back a sin audience).
    """
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-identity-token", "--audiences", engine_url],
            text=True, stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError:
        return subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"],
            text=True,
        ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="S5 backtest CLI")
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--sample-hours", default="10")
    parser.add_argument("--no-prescreen", action="store_true")
    parser.add_argument("--budget-cap", type=float, default=10.0)
    parser.add_argument("--output-dir", default="/tmp/backtest_results")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--cache-dir", default="backtest/data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tickers = args.ticker or ["SPY"]
    sample_hours = [int(h) for h in args.sample_hours.split(",")]
    budget_cap = args.budget_cap

    if args.dry_run:
        sample_hours = sample_hours[:1]
        budget_cap = min(budget_cap, 0.50)
        print(f"[dry-run] sample_hours={sample_hours} budget_cap=${budget_cap}", file=sys.stderr)

    auth_token = _resolve_auth_token(args.engine_url)

    summary = run_backtest(
        tickers=tickers,
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        sample_hours=sample_hours,
        auth_token=auth_token,
        engine_url=args.engine_url,
        use_prescreen=not args.no_prescreen,
        budget_cap_usd=budget_cap,
        output_dir=args.output_dir,
        cache_dir=args.cache_dir,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
