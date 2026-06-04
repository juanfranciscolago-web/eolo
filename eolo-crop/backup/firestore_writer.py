"""Firestore backup writer — Master Plan v2.1 sec 18.

Backup automático de:
- Decisions (por día)
- Trades (lifecycle: open → manage → close)
- KB snapshots (daily)
- System events (mode switches, deploys, manual closes, kb updates)
- Daily journals (Sprint 9 future)
- Feedback sessions (Sprint 10 future)

Estructura colecciones:
firestore://eolo-backups/
├── decisions/{date}/items/{decision_id}
├── trades/{trade_id}
├── kb_snapshots/{date}
├── system_events/{event_id}
├── daily_journals/{date}
└── feedback_sessions/{date}/{session_id}
"""
import gzip
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.cloud import firestore

logger = logging.getLogger(__name__)

_PROJECT_ID = "eolo-schwab-agent"
# REGRESSION-FIX 2026-06-04: la database "eolo-backups" referenciada
# originalmente NUNCA fue creada en GCP. Causaba silent failures en TODOS
# los callers: feedback_sessions, nightly_journal, system_events, trades,
# kb_snapshots. Log evidence:
#   "404 The database eolo-backups does not exist for project ..."
# Fix: usar la database default que sí existe (mismo DB donde viven
# eolo-crop-theta-decisions, phase_checkpoints, etc).
_BACKUP_DB: Optional[str] = None  # None → default database

_client: Optional[firestore.Client] = None


def _get_client() -> firestore.Client:
    global _client
    if _client is None:
        if _BACKUP_DB:
            _client = firestore.Client(project=_PROJECT_ID, database=_BACKUP_DB)
        else:
            _client = firestore.Client(project=_PROJECT_ID)
    return _client


def backup_decision(decision_id: str, decision_data: dict) -> bool:
    """Persist decision to Firestore decisions/{date}/items/{decision_id}."""
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ref = (
            _get_client()
            .collection("decisions")
            .document(date_str)
            .collection("items")
            .document(decision_id)
        )
        ref.set({**decision_data, "_backed_up_at": firestore.SERVER_TIMESTAMP})
        return True
    except Exception as e:
        logger.error(f"[backup] decision {decision_id} failed: {e}")
        return False


def backup_trade(trade_id: str, trade_data: dict) -> bool:
    """Persist trade to Firestore trades/{trade_id} (merge mode)."""
    try:
        ref = _get_client().collection("trades").document(trade_id)
        ref.set(
            {**trade_data, "_backed_up_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
        return True
    except Exception as e:
        logger.error(f"[backup] trade {trade_id} failed: {e}")
        return False


def backup_kb_snapshot(kb_excel_path: Path) -> bool:
    """Persist daily KB snapshot (gzipped Excel) to Firestore kb_snapshots/{date}.

    Firestore tiene límite 1MB por doc — si > 1MB, usar Cloud Storage en lugar
    de field `content_gzip`. Por ahora, KB Excel típico < 200KB compressed.
    """
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(kb_excel_path, "rb") as f:
            data = gzip.compress(f.read())
        ref = _get_client().collection("kb_snapshots").document(date_str)
        ref.set({
            "filename": kb_excel_path.name,
            "size_bytes_compressed": len(data),
            "content_gzip": data,
            "_backed_up_at": firestore.SERVER_TIMESTAMP,
        })
        return True
    except Exception as e:
        logger.error(f"[backup] kb snapshot failed: {e}")
        return False


def log_system_event(event_type: str, details: dict) -> bool:
    """Log structured system event (manual close, deploy, mode switch, etc.)."""
    try:
        event_id = f"{event_type}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        ref = _get_client().collection("system_events").document(event_id)
        ref.set({
            "event_type": event_type,
            "details": details,
            "timestamp": firestore.SERVER_TIMESTAMP,
        })
        return True
    except Exception as e:
        logger.error(f"[backup] system_event {event_type} failed: {e}")
        return False
