"""Chat feedback session manager — apertura automática 16:30 ET.

Master Plan v2.1 sec 11.4. Sigue accesible 24hs, no presiona si Juan no responde.
"""
from datetime import datetime, timezone
from typing import Optional
import logging
import sys
sys.path.insert(0, "eolo-crop")
from backup.firestore_writer import _get_client, log_system_event

logger = logging.getLogger(__name__)


def open_feedback_session(date_str: Optional[str] = None) -> str:
    """Create new feedback session in Firestore. Returns session_id."""
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_id = f"FB_{date_str}"
    try:
        client = _get_client()
        ref = client.collection("feedback_sessions").document(date_str)
        ref.set({
            "session_id": session_id,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "status": "OPEN",
            "artifacts": [],
        }, merge=True)
        log_system_event("FEEDBACK_SESSION_OPENED", {"session_id": session_id})
        return session_id
    except Exception as e:
        logger.error(f"[feedback_chat] open session failed: {e}")
        return session_id


def add_message_to_session(date_str: str, role: str, content: str) -> bool:
    """Append message turn to session subcollection."""
    try:
        client = _get_client()
        msg_ref = (
            client.collection("feedback_sessions")
            .document(date_str)
            .collection("messages")
            .document()
        )
        msg_ref.set({
            "role": role,
            "content": content,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception as e:
        logger.error(f"[feedback_chat] add_message failed: {e}")
        return False


def add_artifact_to_session(date_str: str, artifact: dict) -> bool:
    """Append generated artifact (rule proposal, case upgrade, lesson) to session."""
    try:
        client = _get_client()
        art_ref = (
            client.collection("feedback_sessions")
            .document(date_str)
            .collection("artifacts")
            .document()
        )
        art_ref.set({
            **artifact,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception as e:
        logger.error(f"[feedback_chat] add_artifact failed: {e}")
        return False


def close_session(date_str: str) -> dict:
    """Mark session closed. Returns summary."""
    try:
        client = _get_client()
        ref = client.collection("feedback_sessions").document(date_str)
        ref.set({
            "status": "CLOSED",
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }, merge=True)
        log_system_event("FEEDBACK_SESSION_CLOSED", {"date": date_str})
        return {"date": date_str, "status": "CLOSED"}
    except Exception as e:
        logger.error(f"[feedback_chat] close session failed: {e}")
        return {"date": date_str, "status": "ERROR", "error": str(e)[:200]}
