"""End-to-end backtest orchestrator (Sprint S5).

Loop:
  for ticker in tickers:
    for snap in iter_historical_snapshots(...):
      result = backtest_call_with_cost_control(snap)
      virtual_decisions.append(result)
      if cost_total > budget_cap: STOP
  metrics = aggregate(virtual_decisions, requested_count)
  write_report(metrics)
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
import json
import logging

from backtest.snapshot_replay import iter_historical_snapshots
from backtest.cost_optimizer import backtest_call_with_cost_control
from backtest.metrics_aggregator import aggregate

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_URL = "https://llm-engine-service-nmjz4iwcea-uc.a.run.app"


def run_backtest(
    tickers: list[str],
    start_date: date,
    end_date: date,
    auth_token: str,
    sample_hours: Optional[list[int]] = None,
    engine_url: str = DEFAULT_ENGINE_URL,
    use_prescreen: bool = True,
    budget_cap_usd: float = 10.0,
    output_dir: str = "/tmp/backtest_results",
    cache_dir: str = "backtest/data",
) -> dict:
    """Run backtest for given tickers/window.

    Returns summary metrics dict. Side effects:
      {output_dir}/decisions_{ticker}_{start}_{end}.jsonl
      {output_dir}/summary_{start}_{end}.json
      {output_dir}/report.md
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sample_hours = sample_hours or [10]

    all_decisions: list[dict] = []
    requested = 0
    cost_total = 0.0
    budget_hit = False
    started_at = datetime.now(timezone.utc).isoformat()

    for ticker in tickers:
        if budget_hit:
            break
        per_ticker: list[dict] = []
        for snap in iter_historical_snapshots(
            ticker, start_date, end_date, sample_hours=sample_hours, cache_dir=cache_dir,
        ):
            requested += 1
            if cost_total >= budget_cap_usd:
                logger.warning(
                    f"[runner] budget_cap ${budget_cap_usd} hit at {requested} requests. STOP."
                )
                budget_hit = True
                break

            result = backtest_call_with_cost_control(
                snapshot=snap,
                engine_url=engine_url,
                auth_token=auth_token,
                use_prescreen=use_prescreen,
            )
            result["snapshot"] = snap
            result["ticker"] = snap.get("ticker")
            result["timestamp"] = snap.get("timestamp")
            cost_total += float(result.get("cost_usd") or 0)
            all_decisions.append(result)
            per_ticker.append(result)

        jsonl_path = out / f"decisions_{ticker}_{start_date.isoformat()}_{end_date.isoformat()}.jsonl"
        with jsonl_path.open("w") as f:
            for d in per_ticker:
                f.write(json.dumps(d, default=str) + "\n")
        logger.info(f"[runner] wrote {jsonl_path} ({len(per_ticker)} decisions)")

    metrics = aggregate(all_decisions, requested)
    summary = {
        "started_at":      started_at,
        "ended_at":        datetime.now(timezone.utc).isoformat(),
        "tickers":         tickers,
        "start_date":      start_date.isoformat(),
        "end_date":        end_date.isoformat(),
        "sample_hours":    sample_hours,
        "use_prescreen":   use_prescreen,
        "budget_cap_usd":  budget_cap_usd,
        "budget_hit":      budget_hit,
        "engine_url":      engine_url,
        "metrics":         metrics,
    }
    summary_path = out / f"summary_{start_date.isoformat()}_{end_date.isoformat()}.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    write_report_markdown(summary, str(out / "report.md"))
    return summary


def write_report_markdown(summary: dict, output_path: str) -> None:
    """Human-readable summary report."""
    m = summary["metrics"]
    lines = [
        f"# Backtest Report",
        "",
        f"- Tickers:       {', '.join(summary['tickers'])}",
        f"- Window:        {summary['start_date']} → {summary['end_date']}",
        f"- Sample hours:  {summary['sample_hours']}",
        f"- Pre-screen:    {summary['use_prescreen']}",
        f"- Budget cap:    ${summary['budget_cap_usd']}",
        f"- Budget hit:    {summary['budget_hit']}",
        "",
        f"## Coverage",
        "",
        f"- Decisions produced: **{m['n_decisions']}** of {m['requested_count']} requested ({m['coverage_pct']}%)",
        f"- Total cost:         **${m['total_cost_usd']}**",
        "",
        f"## Verdict distribution",
        "",
    ]
    for verdict, count in sorted(m["verdicts"].items(), key=lambda x: -x[1]):
        lines.append(f"- {verdict}: {count}")
    lines += ["", "## Regime distribution", ""]
    for regime, count in sorted(m["regimes"].items(), key=lambda x: -x[1]):
        lines.append(f"- {regime}: {count}")
    lines += ["", "## Top rule citations", ""]
    for rid, count in list(m["rule_citations"].items())[:20]:
        lines.append(f"- {rid}: {count}")
    Path(output_path).write_text("\n".join(lines))
