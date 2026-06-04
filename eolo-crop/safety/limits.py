"""Risk limits + circuit breakers (Wave 3 OMNI-SPRINT).

Enforcear en cada path de entry. Si breach:
- Trade execution ABORTED.
- alert_critical() via disaster_recovery.alerting.
- Bot keeps running para monitoring/exits, sin abrir nuevos trades.

Limits configurables via Firestore `eolo-config/risk_limits` con defaults seguros.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskLimits:
    max_position_size_usd:          float = 1000.0
    max_daily_loss_usd:             float = 500.0
    max_open_positions_per_ticker:  int   = 2
    max_open_positions_total:       int   = 8
    max_consecutive_losses:         int   = 3
    kill_switch_active:             bool  = False

    @classmethod
    def from_firestore(cls) -> "RiskLimits":
        """Read limits from Firestore `eolo-config/risk_limits`. Safe default if absent."""
        try:
            from google.cloud import firestore
            db = firestore.Client()
            doc = db.collection("eolo-config").document("risk_limits").get()
            if not doc.exists:
                return cls()
            raw = doc.to_dict() or {}
            valid = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            return cls(**valid)
        except Exception:
            return cls()


def check_pre_entry_limits(
    proposed_position_value_usd: float,
    current_positions:           list,
    ticker:                      str,
    daily_pnl_usd:               float,
    consecutive_losses_today:    int,
    limits:                      Optional[RiskLimits] = None,
) -> tuple[bool, list[str]]:
    """Returns (allowed, breach_reasons). reasons empty si allowed=True."""
    limits = limits or RiskLimits.from_firestore()
    reasons: list[str] = []

    if limits.kill_switch_active:
        reasons.append("KILL_SWITCH_ACTIVE")

    if proposed_position_value_usd > limits.max_position_size_usd:
        reasons.append(f"POSITION_SIZE_LIMIT (${proposed_position_value_usd:.0f} > ${limits.max_position_size_usd:.0f})")

    if daily_pnl_usd < -limits.max_daily_loss_usd:
        reasons.append(f"DAILY_LOSS_LIMIT (${daily_pnl_usd:.0f} < -${limits.max_daily_loss_usd:.0f})")

    same_ticker_count = sum(1 for p in current_positions if p.get("ticker") == ticker)
    if same_ticker_count >= limits.max_open_positions_per_ticker:
        reasons.append(f"PER_TICKER_LIMIT ({same_ticker_count} >= {limits.max_open_positions_per_ticker})")

    if len(current_positions) >= limits.max_open_positions_total:
        reasons.append(f"TOTAL_POSITIONS_LIMIT ({len(current_positions)} >= {limits.max_open_positions_total})")

    if consecutive_losses_today >= limits.max_consecutive_losses:
        reasons.append(f"CONSECUTIVE_LOSSES_LIMIT ({consecutive_losses_today} >= {limits.max_consecutive_losses})")

    return (len(reasons) == 0, reasons)
