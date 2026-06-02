"""Trade lifecycle tracker — sigue trade desde fill hasta close.

Master Plan v2.1 sec 12.9. Persiste lifecycle events para outcome analysis.
"""
from datetime import datetime, timezone
import logging

from backup.firestore_writer import backup_trade

logger = logging.getLogger(__name__)


def record_trade_opened(trade_id: str, snapshot: dict, decision: dict) -> bool:
    """Record trade open event."""
    data = {
        "trade_id": trade_id,
        "status": "OPEN",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "ticker": snapshot.get("ticker"),
        "snapshot_at_open": {
            "vix_level": snapshot.get("vix_level"),
            "gamma_regime_v2": snapshot.get("gamma_regime_v2"),
            "vrp_score": snapshot.get("vrp_score"),
            "iv_rank_call": snapshot.get("iv_rank_call"),
            "max_pain_strike": snapshot.get("max_pain_strike"),
        },
        "decision_at_open": {
            "verdict": decision.get("verdict"),
            "confidence": decision.get("confidence"),
            "tacit_rules_applied": decision.get("tacit_rules_applied", []),
            "main_reason": (decision.get("main_reason") or "")[:500],
        },
    }
    return backup_trade(trade_id, data)


def record_trade_managed(trade_id: str, event: str, details: dict) -> bool:
    """Record mid-life event (partial close, adjustment, etc.)."""
    data = {
        "trade_id": trade_id,
        "management_events": [{
            "event": event,
            "at": datetime.now(timezone.utc).isoformat(),
            "details": details,
        }],
    }
    return backup_trade(trade_id, data)


def record_trade_closed(trade_id: str, exit_data: dict) -> bool:
    """Record trade close + final P/L."""
    data = {
        "trade_id": trade_id,
        "status": "CLOSED",
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "exit": exit_data,
        "pnl_dollars": exit_data.get("pnl_dollars"),
        "pct_capture": exit_data.get("pct_capture"),
        "outcome": "win" if (exit_data.get("pnl_dollars") or 0) > 0 else "loss",
    }
    return backup_trade(trade_id, data)
