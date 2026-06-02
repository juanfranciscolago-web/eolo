"""Weekly review — corre domingos, re-backtest sobre últimos 60 días.

Master Plan v2.1 sec 11.2.

Produce delta de performance por regla + sugerencias.
"""
from typing import Optional
import logging
from datetime import datetime, timedelta, timezone
import sys
sys.path.insert(0, "eolo-crop")
from backup.firestore_writer import log_system_event
from tracking.accuracy_report import compute_rule_accuracy

logger = logging.getLogger(__name__)


def compare_periods(
    trades_recent: list[dict],
    trades_baseline: list[dict],
) -> dict:
    """Compare metrics between recent (last 30d) vs baseline (30-60d ago)."""
    acc_recent = compute_rule_accuracy(trades_recent)
    acc_baseline = compute_rule_accuracy(trades_baseline)

    deltas = {}
    all_rules = set(acc_recent.keys()) | set(acc_baseline.keys())
    for rid in all_rules:
        r = acc_recent.get(rid, {"n_trades": 0, "win_rate": 0, "avg_pnl": 0})
        b = acc_baseline.get(rid, {"n_trades": 0, "win_rate": 0, "avg_pnl": 0})
        deltas[rid] = {
            "n_recent": r["n_trades"],
            "n_baseline": b["n_trades"],
            "win_rate_delta": r["win_rate"] - b["win_rate"],
            "pnl_delta": r["avg_pnl"] - b["avg_pnl"],
        }
    return deltas


def categorize_rules(deltas: dict) -> dict:
    """Classify rules: improving, degrading, stable, new, gone."""
    improving = []
    degrading = []
    new = []
    gone = []
    for rid, d in deltas.items():
        if d["n_baseline"] == 0 and d["n_recent"] > 0:
            new.append(rid)
        elif d["n_recent"] == 0 and d["n_baseline"] > 0:
            gone.append(rid)
        elif d["pnl_delta"] > 20 or d["win_rate_delta"] > 0.1:
            improving.append(rid)
        elif d["pnl_delta"] < -20 or d["win_rate_delta"] < -0.1:
            degrading.append(rid)
    return {"improving": improving, "degrading": degrading, "new": new, "gone": gone}


def run_weekly_review(
    trades_recent: list[dict],
    trades_baseline: list[dict],
) -> dict:
    """Build weekly review report."""
    deltas = compare_periods(trades_recent, trades_baseline)
    categories = categorize_rules(deltas)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_window_trades": len(trades_recent),
        "baseline_window_trades": len(trades_baseline),
        "categories": categories,
        "deltas_per_rule": deltas,
    }
    log_system_event("WEEKLY_REVIEW", {
        "improving": len(categories["improving"]),
        "degrading": len(categories["degrading"]),
        "new": len(categories["new"]),
        "gone": len(categories["gone"]),
    })
    return report
