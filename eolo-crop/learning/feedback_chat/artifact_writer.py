"""Artifact writer — persiste outputs estructurados de sesiones de feedback.

Master Plan v2.1 sec 11.4. Cada sesión genera ≥ 1 artifact que feeda monthly review.
"""
from datetime import datetime, timezone
import sys
sys.path.insert(0, "eolo-crop")
from learning.feedback_chat.session_manager import add_artifact_to_session


def write_rule_proposal(date_str: str, rule_id: str, change: str, justification: str) -> bool:
    """Persist proposed KB rule change (new rule or edit existing)."""
    return add_artifact_to_session(date_str, {
        "type": "rule_proposal",
        "rule_id": rule_id,
        "change": change,
        "justification": justification[:1000],
    })


def write_case_upgrade(date_str: str, case_id: str, from_quality: str, to_quality: str, reason: str) -> bool:
    """Persist case quality upgrade (SILVER → GOLD typically)."""
    return add_artifact_to_session(date_str, {
        "type": "case_upgrade",
        "case_id": case_id,
        "from_quality": from_quality,
        "to_quality": to_quality,
        "reason": reason[:1000],
    })


def write_lesson_learned(date_str: str, lesson: str, supporting_trade_ids: list) -> bool:
    """Persist lesson learned for KB."""
    return add_artifact_to_session(date_str, {
        "type": "lesson_learned",
        "lesson": lesson[:1000],
        "supporting_trade_ids": supporting_trade_ids,
    })


def write_qa_ticket(date_str: str, bug_summary: str, severity: str) -> bool:
    """Persist QA ticket for follow-up."""
    return add_artifact_to_session(date_str, {
        "type": "qa_ticket",
        "summary": bug_summary[:500],
        "severity": severity,
    })
