"""Rule-based backtest simulator — replay día por día.

Master Plan v2.1 sec 10.1.

Sin LLM calls (reproducible + cost zero). Evalúa cada regla del KB v1.3
deterministicamente contra cada snapshot histórico.

Output JSON con outcomes por trade simulado + agregados por regla × régimen.
"""
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import logging
import sys

sys.path.insert(0, "llm_engine_eolo")
from llm_engine.kb_loader import KBLoader

logger = logging.getLogger(__name__)


def evaluate_rules_against_snapshot(kb: KBLoader, snap_dict: dict) -> list[dict]:
    """Evaluate every KB rule deterministically against a snapshot.

    Returns list of {rule_id, tier, triggered, action, confidence}.
    Simplified: matches trigger text keywords against snapshot dict.
    Real implementation extiende esto con rule-specific predicates (T4 follow-up).
    """
    evaluations = []
    for rule in kb.tacit_rules:
        trigger = (rule.trigger or "").lower()
        triggered = False
        confidence = 0

        if "vix" in trigger and snap_dict.get("vix_velocity_30m_pct") is not None:
            if abs(snap_dict["vix_velocity_30m_pct"]) > 5:
                triggered = True
                confidence = 8
        if "gex_regime" in trigger or "gamma_regime" in trigger:
            if snap_dict.get("gamma_regime_v2"):
                triggered = True
                confidence = 7
        if "vrp" in trigger and snap_dict.get("vrp_score"):
            triggered = True
            confidence = 7
        if "iv_rank" in trigger and snap_dict.get("iv_rank_call") is not None:
            triggered = True
            confidence = 6

        evaluations.append({
            "rule_id": rule.rule_id,
            "tier": rule.tier,
            "triggered": triggered,
            "action": rule.action if triggered else None,
            "confidence": confidence,
        })
    return evaluations


def simulate_trade_outcome(snap_dict: dict, rule: dict, exit_horizon_days: int = 1) -> dict:
    """Mock trade outcome — devuelve P/L estimado.

    Simplificación T4 inicial: outcome deterministic per (rule_id, date) seed.
    T4 follow-up: simulación real con theta decay + spot move.
    """
    seed = f"{rule['rule_id']}_{snap_dict.get('timestamp', '')}"
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    pnl = ((h % 200) - 50)
    return {
        "pnl_dollars": pnl,
        "credit_received": 80,
        "exit_horizon_days": exit_horizon_days,
        "outcome": "win" if pnl > 0 else "loss",
    }


def run_backtest(
    data_dir: Path,
    kb_path: Path,
    start_date: str,
    end_date: str,
    tickers: list[str],
) -> dict:
    """Run backtest over date range. Returns aggregated metrics."""
    kb = KBLoader(str(kb_path))

    all_outcomes = []
    rule_outcomes: dict[str, list[dict]] = {}

    cur = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    while cur <= end:
        if cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue
        date_str = cur.strftime("%Y-%m-%d")
        for ticker in tickers:
            snap_path = data_dir / f"{ticker}_{date_str}.json"
            if not snap_path.exists():
                continue
            try:
                snap_dict = json.loads(snap_path.read_text())
            except Exception:
                continue

            evals = evaluate_rules_against_snapshot(kb, snap_dict)
            for ev in evals:
                if not ev["triggered"]:
                    continue
                outcome = simulate_trade_outcome(snap_dict, ev)
                outcome_record = {
                    "date": date_str,
                    "ticker": ticker,
                    "rule_id": ev["rule_id"],
                    "tier": ev["tier"],
                    **outcome,
                }
                all_outcomes.append(outcome_record)
                rule_outcomes.setdefault(ev["rule_id"], []).append(outcome_record)
        cur += timedelta(days=1)

    return {
        "total_outcomes": len(all_outcomes),
        "rules_with_outcomes": len(rule_outcomes),
        "outcomes_per_rule": {rid: len(outs) for rid, outs in rule_outcomes.items()},
        "all_outcomes": all_outcomes,
        "rule_outcomes": rule_outcomes,
    }
