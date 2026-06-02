"""Nightly journal — corre 16:15 ET cada día.

Master Plan v2.1 sec 11.1.

Genera journal estructurado con:
- Trades del día + P/L
- Decisiones tomadas vs resultados
- Accuracy por regla
- Reglas que NO se chequearon en 30+ días
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import sys
sys.path.insert(0, "eolo-crop")
from backup.firestore_writer import _get_client, log_system_event
from tracking.accuracy_report import compute_rule_accuracy

logger = logging.getLogger(__name__)


def fetch_today_trades_from_firestore() -> list[dict]:
    """Read trades closed today from Firestore."""
    try:
        client = _get_client()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Trades collection direct query
        trades = []
        for doc in client.collection("trades").stream():
            data = doc.to_dict()
            closed_at = data.get("closed_at", "")
            if closed_at and closed_at.startswith(today_str):
                trades.append(data)
        return trades
    except Exception as e:
        logger.error(f"[journal] fetch trades failed: {e}")
        return []


def fetch_recent_decisions_from_firestore(days: int = 30) -> list[dict]:
    """Read recent decisions across last N days."""
    try:
        client = _get_client()
        results = []
        for n in range(days):
            date_str = (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")
            items_ref = client.collection("decisions").document(date_str).collection("items")
            for doc in items_ref.stream():
                results.append(doc.to_dict())
        return results
    except Exception as e:
        logger.error(f"[journal] fetch decisions failed: {e}")
        return []


def identify_unused_rules(decisions: list[dict], all_rule_ids: list[str], threshold_days: int = 30) -> list[str]:
    """Find rules not triggered in last N days."""
    triggered_ids = set()
    for d in decisions:
        for rid in d.get("tacit_rules_applied", []):
            triggered_ids.add(rid)
    return sorted(set(all_rule_ids) - triggered_ids)


def generate_daily_journal(
    trades_today: list[dict],
    recent_decisions: list[dict],
    all_rule_ids: list[str],
) -> dict:
    """Generate structured journal for the day."""
    wins = [t for t in trades_today if t.get("outcome") == "win"]
    losses = [t for t in trades_today if t.get("outcome") == "loss"]
    total_pnl = sum(t.get("pnl_dollars", 0) for t in trades_today)

    # Per-rule accuracy
    rule_acc = compute_rule_accuracy([t for t in trades_today if t.get("outcome")])

    # Rules cited today
    rules_cited_today = set()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for d in recent_decisions:
        d_date = (d.get("opened_at") or d.get("timestamp", ""))[:10]
        if d_date == today_str:
            for rid in d.get("tacit_rules_applied", []):
                rules_cited_today.add(rid)

    # Rules unused in 30 days
    unused_30d = identify_unused_rules(recent_decisions, all_rule_ids, threshold_days=30)

    return {
        "date": today_str,
        "trades_count": len(trades_today),
        "wins_count": len(wins),
        "losses_count": len(losses),
        "total_pnl_dollars": total_pnl,
        "win_rate": len(wins) / len(trades_today) if trades_today else 0,
        "accuracy_per_rule": rule_acc,
        "rules_cited_today": sorted(rules_cited_today),
        "rules_unused_30d": unused_30d,
        "summary": f"{len(trades_today)} trades · {len(wins)}W/{len(losses)}L · P/L ${total_pnl:.0f}",
    }


def format_journal_markdown(journal: dict) -> str:
    """Render journal as Markdown for Slack/email."""
    lines = [
        f"# 🌙 Eolo daily journal — {journal['date']}",
        "",
        f"**Trades hoy:** {journal['trades_count']} ({journal['wins_count']} wins, {journal['losses_count']} losses)",
        f"**P/L:** ${journal['total_pnl_dollars']:.0f}",
        f"**Win rate:** {journal['win_rate']:.0%}",
        "",
        "## Reglas citadas hoy",
        ", ".join(journal["rules_cited_today"]) if journal["rules_cited_today"] else "(ninguna)",
        "",
        "## Reglas que NO se chequearon en 30+ días",
    ]
    unused = journal["rules_unused_30d"]
    if unused:
        lines.append("¿Siguen relevantes? " + ", ".join(unused[:10]))
        if len(unused) > 10:
            lines.append(f"(+{len(unused) - 10} más)")
    else:
        lines.append("(todas las reglas se chequearon en últimos 30d)")
    return "\n".join(lines)


def run_nightly_journal(all_rule_ids: list[str]) -> dict:
    """Entry point: build journal + persist + return."""
    trades = fetch_today_trades_from_firestore()
    decisions = fetch_recent_decisions_from_firestore(days=30)
    journal = generate_daily_journal(trades, decisions, all_rule_ids)
    journal["markdown"] = format_journal_markdown(journal)
    log_system_event("NIGHTLY_JOURNAL", {"date": journal["date"], "trades": journal["trades_count"]})
    return journal
