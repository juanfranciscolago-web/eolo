"""End-to-end backtest orchestrator (Sprint S5).

Loop:
  for ticker in tickers:
    for snap in iter_historical_snapshots(...):
      result = backtest_call_with_cost_control(snap)
      virtual_decisions.append(result)
      if cost_total > budget_cap: STOP
  metrics = aggregate(virtual_decisions, requested_count)
  write_report(metrics)

DUAL MODE (shadow-bundle-9): run_backtest_dual calls A+B engines in parallel
per snapshot, records both verdicts side by side for delta analysis.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional
import json
import logging
import subprocess
import time

from backtest.snapshot_replay import iter_historical_snapshots
from backtest.cost_optimizer import backtest_call_with_cost_control
from backtest.metrics_aggregator import aggregate

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_URL = "https://llm-engine-service-nmjz4iwcea-uc.a.run.app"

# gcloud identity tokens expire after ~1h. Refresh every 45min to be safe.
TOKEN_REFRESH_SECONDS = 45 * 60


def make_token_refresher(initial_token: str | None = None):
    """Returns a closure that returns a valid auth token, refreshing every 45min."""
    state = {"token": initial_token, "fetched_at": 0 if not initial_token else time.time()}

    def get_token() -> str:
        if not state["token"] or (time.time() - state["fetched_at"]) > TOKEN_REFRESH_SECONDS:
            try:
                tok = subprocess.check_output(
                    ["gcloud", "auth", "print-identity-token"],
                    text=True, stderr=subprocess.PIPE,
                ).strip()
                state["token"] = tok
                state["fetched_at"] = time.time()
                logger.info("[token] refreshed gcloud identity token")
            except Exception as e:
                logger.warning(f"[token] refresh failed: {e}; reusing cached")
        return state["token"]

    return get_token


def _call_one(snapshot, engine_url, auth_token, use_prescreen):
    return backtest_call_with_cost_control(
        snapshot=snapshot,
        engine_url=engine_url,
        auth_token=auth_token,
        use_prescreen=use_prescreen,
    )


def call_both_engines(snapshot, url_a, url_b, auth_token, use_prescreen=True):
    """Run A+B engines in parallel via threads. Returns (result_a, result_b)."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_call_one, snapshot, url_a, auth_token, use_prescreen)
        fb = ex.submit(_call_one, snapshot, url_b, auth_token, use_prescreen)
        return fa.result(), fb.result()


def run_backtest_dual(
    tickers: list[str],
    start_date: date,
    end_date: date,
    auth_token: str,
    engine_a_url: str,
    engine_b_url: str,
    sample_hours: Optional[list[float]] = None,
    use_prescreen: bool = True,
    budget_cap_usd: float = 150.0,
    output_dir: str = "/tmp/backtest_dual",
    cache_dir: str = "backtest/data",
) -> dict:
    """Dual-engine backtest: call A and B per snapshot, store both verdicts."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sample_hours = sample_hours or [10]
    get_token = make_token_refresher(auth_token)

    all_decisions: list[dict] = []
    requested = 0
    cost_total = 0.0
    cost_a_total = 0.0
    cost_b_total = 0.0
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
                    f"[runner-dual] budget_cap ${budget_cap_usd} hit at {requested}. STOP."
                )
                budget_hit = True
                break

            current_token = get_token()
            res_a, res_b = call_both_engines(
                snap, engine_a_url, engine_b_url, current_token, use_prescreen,
            )
            ca = float(res_a.get("cost_usd") or 0)
            cb = float(res_b.get("cost_usd") or 0)
            cost_a_total += ca
            cost_b_total += cb
            cost_total += ca + cb

            decision_a = res_a.get("decision") or {}
            decision_b = res_b.get("decision") or {}
            row = {
                "snapshot": snap,
                "ticker": snap.get("ticker"),
                "timestamp": snap.get("timestamp"),
                "verdict_a": res_a.get("verdict"),
                "verdict_b": res_b.get("verdict"),
                "tier_a": res_a.get("tier"),
                "tier_b": res_b.get("tier"),
                "skipped_a": res_a.get("skipped"),
                "skipped_b": res_b.get("skipped"),
                "haiku_meta_a": res_a.get("haiku_meta"),
                "haiku_meta_b": res_b.get("haiku_meta"),
                "confidence_a": decision_a.get("confidence"),
                "confidence_b": decision_b.get("confidence"),
                "rules_a": decision_a.get("rules_applied") or decision_a.get("tacit_rules_applied") or [],
                "rules_b": decision_b.get("rules_applied") or decision_b.get("tacit_rules_applied") or [],
                "cost_a": ca,
                "cost_b": cb,
                "cost_usd": ca + cb,
            }
            all_decisions.append(row)
            per_ticker.append(row)

            # Append-mode flush every sample to survive interrupts/token issues.
            jsonl_path = out / f"decisions_{ticker}_{start_date.isoformat()}_{end_date.isoformat()}.jsonl"
            with jsonl_path.open("a") as f:
                f.write(json.dumps(row, default=str) + "\n")

        logger.info(f"[runner-dual] {ticker} done ({len(per_ticker)} decisions)")

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
        "engine_a_url":    engine_a_url,
        "engine_b_url":    engine_b_url,
        "cost_a_total":    cost_a_total,
        "cost_b_total":    cost_b_total,
        "total_samples":   len(all_decisions),
    }
    summary_path = out / f"summary_dual_{start_date.isoformat()}_{end_date.isoformat()}.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    return summary


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
    get_token = make_token_refresher(auth_token)

    all_decisions: list[dict] = []
    requested = 0
    cost_total = 0.0
    budget_hit = False
    started_at = datetime.now(timezone.utc).isoformat()

    for ticker in tickers:
        if budget_hit:
            break
        per_ticker: list[dict] = []
        jsonl_path = out / f"decisions_{ticker}_{start_date.isoformat()}_{end_date.isoformat()}.jsonl"
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
                auth_token=get_token(),
                use_prescreen=use_prescreen,
            )
            result["snapshot"] = snap
            result["ticker"] = snap.get("ticker")
            result["timestamp"] = snap.get("timestamp")
            cost_total += float(result.get("cost_usd") or 0)
            all_decisions.append(result)
            per_ticker.append(result)

            with jsonl_path.open("a") as f:
                f.write(json.dumps(result, default=str) + "\n")

        logger.info(f"[runner] {ticker} done ({len(per_ticker)} decisions)")

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
