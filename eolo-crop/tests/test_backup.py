"""Unit tests para backup.firestore_writer (mock cliente Firestore).

Run standalone (sin Firestore live):
    cd eolo-crop && python3 -m pytest tests/test_backup.py -v

Sprint T3 — Master Plan v2.1 sec 18.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Asegura que `backup/` sea importable como top-level ─────────────
_EOLO_CROP = str(Path(__file__).resolve().parent.parent)
if _EOLO_CROP not in sys.path:
    sys.path.insert(0, _EOLO_CROP)

from backup import firestore_writer  # noqa: E402


def test_backup_decision_success():
    mock_client = MagicMock()
    mock_doc = MagicMock()
    (
        mock_client.collection.return_value
        .document.return_value
        .collection.return_value
        .document.return_value
    ) = mock_doc

    with patch.object(firestore_writer, "_get_client", return_value=mock_client):
        result = firestore_writer.backup_decision("test_id", {"verdict": "WAIT"})

    assert result is True
    mock_doc.set.assert_called_once()


def test_backup_decision_handles_exception():
    with patch.object(firestore_writer, "_get_client", side_effect=Exception("boom")):
        result = firestore_writer.backup_decision("test_id", {})
    assert result is False


def test_log_system_event_success():
    mock_client = MagicMock()
    mock_doc = MagicMock()
    mock_client.collection.return_value.document.return_value = mock_doc

    with patch.object(firestore_writer, "_get_client", return_value=mock_client):
        result = firestore_writer.log_system_event(
            "MANUAL_CLOSE_ONE", {"position_id": "POS_001"}
        )

    assert result is True
    mock_doc.set.assert_called_once()
    call_kwargs = mock_doc.set.call_args[0][0]
    assert call_kwargs["event_type"] == "MANUAL_CLOSE_ONE"
    assert call_kwargs["details"]["position_id"] == "POS_001"


def test_backup_trade_uses_merge():
    mock_client = MagicMock()
    mock_doc = MagicMock()
    mock_client.collection.return_value.document.return_value = mock_doc

    with patch.object(firestore_writer, "_get_client", return_value=mock_client):
        result = firestore_writer.backup_trade("TRD_001", {"status": "open"})

    assert result is True
    args, kwargs = mock_doc.set.call_args
    assert kwargs.get("merge") is True
