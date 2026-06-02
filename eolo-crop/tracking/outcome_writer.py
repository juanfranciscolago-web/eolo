"""Auto-case generation — convierte trade outcome en KB case.

Master Plan v2.1 sec 11.5 + 12.9. Cap 8 cases/régimen/mes (diversificación).
"""
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

MAX_CASES_PER_REGIME_PER_MONTH = 8


def identify_regime(snapshot: dict) -> str:
    """Identify market regime for case categorization."""
    gamma = snapshot.get("gamma_regime_v2", "unknown")
    iv_rank = snapshot.get("iv_rank_call") or 0
    iv_tier = "high" if iv_rank > 50 else "low"
    return f"{gamma}_iv_{iv_tier}"


def is_exceptional_case(trade_outcome: dict) -> bool:
    """Exceptional = extreme P/L or manual override."""
    pnl = trade_outcome.get("pnl_dollars", 0)
    if abs(pnl) > 300:
        return True
    if trade_outcome.get("manual_override"):
        return True
    return False


def count_cases_this_month_by_regime(regime: str, kb_loader) -> int:
    """Count cases written this month for given regime."""
    if not kb_loader or not hasattr(kb_loader, "cases"):
        return 0
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    count = 0
    for case in kb_loader.cases:
        case_id = getattr(case, "case_id", "")
        case_regime = getattr(case, "regime", "")
        if case_id.startswith(current_month) and case_regime == regime:
            count += 1
    return count


def write_outcome_case(
    trade_outcome: dict,
    snapshot: dict,
    kb_loader,
    force: bool = False,
) -> Optional[dict]:
    """Generate case for KB from trade outcome.

    Respeta cap 8/régimen/mes salvo si is_exceptional.
    Returns case dict if written, None if skipped.
    """
    regime = identify_regime(snapshot)
    count = count_cases_this_month_by_regime(regime, kb_loader)

    if count >= MAX_CASES_PER_REGIME_PER_MONTH and not force:
        if not is_exceptional_case(trade_outcome):
            logger.info(f"[outcome] cap reached for {regime} ({count}); skipping non-exceptional case")
            return None

    case_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_{snapshot.get('ticker', 'X')}_{trade_outcome.get('trade_id', 'unknown')}"
    case = {
        "case_id": case_id,
        "regime": regime,
        "trade_id": trade_outcome.get("trade_id"),
        "ticker": snapshot.get("ticker"),
        "outcome": trade_outcome.get("outcome"),
        "pnl_dollars": trade_outcome.get("pnl_dollars"),
        "pct_capture": trade_outcome.get("pct_capture"),
        "snapshot_summary": {
            "vix": snapshot.get("vix_level"),
            "gamma_regime_v2": snapshot.get("gamma_regime_v2"),
            "vrp_score": snapshot.get("vrp_score"),
            "iv_rank_call": snapshot.get("iv_rank_call"),
        },
        "rules_applied": trade_outcome.get("rules_applied", []),
        "case_quality": "SILVER" if not is_exceptional_case(trade_outcome) else "GOLD",
        "auto_generated": True,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    return case
