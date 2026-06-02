"""Disaster Recovery auto-close handler — Cloud Function INDEPENDIENTE del engine.

Master Plan v2.1 sec 19. Trigger: Cloud Run health check fail >90s sostenido.
5 min override window con alert por SMS + email + Slack antes de auto-close.

Deploy esta función como Cloud Function separada (NO en el container del bot).
"""
import os
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

OVERRIDE_WINDOW_SECONDS = 5 * 60  # 5 min
DISASTER_RECOVERY_TIMEOUT = 90  # 90s sustained health fail trigger


def health_check_engine(engine_url: str, token: str) -> bool:
    """Check if engine /health responds 200."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{engine_url}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning(f"[DR] engine health check failed: {e}")
        return False


def send_critical_alert(channels: list, message: str, details: dict) -> dict:
    """Send alert to operator via configured channels.

    Phase 1: stubbed. F-follow-up integration con Slack / SendGrid / Twilio.
    """
    results = {}
    for ch in channels:
        logger.warning(f"[DR][{ch}] ALERT: {message} — {details}")
        results[ch] = "logged_only_pending_integration"
    return results


def check_open_positions_via_broker() -> list:
    """Query broker for open positions (paper mode if PAPER_TRADING_ONLY).

    Phase 1: stub returns empty. Wire to Schwab API en follow-up.
    """
    if os.environ.get("PAPER_TRADING_ONLY") == "true":
        return []
    return []


def execute_close_all_via_broker(positions: list, reason: str) -> dict:
    """Close all positions directly via broker API (bypass engine).

    Phase 1: stub. F-follow-up integration Schwab.
    """
    if os.environ.get("PAPER_TRADING_ONLY") == "true":
        return {"closed": 0, "reason": "PAPER_MODE_NO_OP"}
    return {"closed": 0, "reason": "STUB_PENDING_F_FOLLOWUP"}


def disaster_recovery_handler(event: Any, context: Any = None) -> dict:
    """Cloud Function entry point.

    1. Send critical alert to operator
    2. Check positions in broker
    3. Wait 5 min override window (Phase 1 stub: assumes no override)
    4. If no override received → close all
    """
    started_at = datetime.now(timezone.utc).isoformat()

    send_critical_alert(
        channels=["sms", "email", "slack"],
        message="DISASTER RECOVERY TRIGGERED — Cloud Run engine health fail >90s",
        details={"started_at": started_at, "event": str(event)[:500]},
    )

    positions = check_open_positions_via_broker()
    if not positions:
        return {
            "status": "DR_TRIGGERED_NO_POSITIONS",
            "positions": 0,
            "started_at": started_at,
        }

    # F-follow-up: persist DR event to Firestore, watch for override flag
    override_received = False

    if override_received:
        return {
            "status": "DR_OVERRIDDEN_BY_OPERATOR",
            "positions": len(positions),
            "started_at": started_at,
        }

    result = execute_close_all_via_broker(positions, reason="DISASTER_RECOVERY_AUTO")
    return {
        "status": "DR_AUTO_CLOSED",
        "positions": len(positions),
        "close_result": result,
        "started_at": started_at,
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }
