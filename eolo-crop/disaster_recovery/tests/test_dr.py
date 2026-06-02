"""Tests para disaster_recovery module.

Run standalone:
    cd eolo-crop && python3 -m pytest disaster_recovery/tests/test_dr.py -v

Sprint T8 — Master Plan v2.1 sec 19.
"""
import sys
from pathlib import Path
from unittest.mock import patch

_EOLO_CROP = str(Path(__file__).resolve().parent.parent.parent)
if _EOLO_CROP not in sys.path:
    sys.path.insert(0, _EOLO_CROP)


@patch("disaster_recovery.auto_close.execute_close_all_via_broker")
@patch("disaster_recovery.auto_close.check_open_positions_via_broker")
@patch("disaster_recovery.auto_close.send_critical_alert")
def test_dr_handler_no_positions(mock_alert, mock_check, mock_close):
    from disaster_recovery.auto_close import disaster_recovery_handler
    mock_alert.return_value = {"sms": "logged", "email": "logged", "slack": "logged"}
    mock_check.return_value = []
    result = disaster_recovery_handler(event="test")
    assert result["status"] == "DR_TRIGGERED_NO_POSITIONS"
    assert result["positions"] == 0
    mock_close.assert_not_called()


@patch("disaster_recovery.auto_close.execute_close_all_via_broker")
@patch("disaster_recovery.auto_close.check_open_positions_via_broker")
@patch("disaster_recovery.auto_close.send_critical_alert")
def test_dr_handler_with_positions_auto_close(mock_alert, mock_check, mock_close):
    from disaster_recovery.auto_close import disaster_recovery_handler
    mock_alert.return_value = {}
    mock_check.return_value = [{"symbol": "SPY_240614_750P"}]
    mock_close.return_value = {"closed": 1}
    result = disaster_recovery_handler(event="test")
    assert result["status"] == "DR_AUTO_CLOSED"
    assert result["positions"] == 1
    mock_close.assert_called_once()
