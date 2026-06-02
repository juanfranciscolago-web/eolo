"""Unit tests para tracking module."""
from unittest.mock import patch
import sys
sys.path.insert(0, "eolo-crop")


def test_identify_regime():
    from tracking.outcome_writer import identify_regime
    snap = {"gamma_regime_v2": "long", "iv_rank_call": 60}
    assert identify_regime(snap) == "long_iv_high"
    snap2 = {"gamma_regime_v2": "negative", "iv_rank_call": 30}
    assert identify_regime(snap2) == "negative_iv_low"


def test_is_exceptional_case_extreme_pnl():
    from tracking.outcome_writer import is_exceptional_case
    assert is_exceptional_case({"pnl_dollars": 500}) is True
    assert is_exceptional_case({"pnl_dollars": -350}) is True
    assert is_exceptional_case({"pnl_dollars": 100}) is False


def test_is_exceptional_manual_override():
    from tracking.outcome_writer import is_exceptional_case
    assert is_exceptional_case({"pnl_dollars": 50, "manual_override": True}) is True


@patch("tracking.trade_lifecycle.backup_trade")
def test_record_trade_opened(mock_backup):
    from tracking.trade_lifecycle import record_trade_opened
    mock_backup.return_value = True
    snap = {"ticker": "SPY", "vix_level": 16.0}
    dec = {"verdict": "SELL_PUT", "confidence": 8}
    result = record_trade_opened("T_001", snap, dec)
    assert result is True
    mock_backup.assert_called_once()
    args = mock_backup.call_args[0]
    assert args[0] == "T_001"
    assert args[1]["status"] == "OPEN"


def test_compute_rule_accuracy_basic():
    from tracking.accuracy_report import compute_rule_accuracy
    trades = [
        {"rules_applied": ["TR-A"], "outcome": "win", "pnl_dollars": 80},
        {"rules_applied": ["TR-A"], "outcome": "win", "pnl_dollars": 100},
        {"rules_applied": ["TR-A"], "outcome": "loss", "pnl_dollars": -50},
        {"rules_applied": ["TR-B"], "outcome": "loss", "pnl_dollars": -30},
    ]
    acc = compute_rule_accuracy(trades)
    assert acc["TR-A"]["n_trades"] == 3
    assert acc["TR-A"]["wins"] == 2
    assert acc["TR-A"]["win_rate"] == 2/3
    assert acc["TR-B"]["n_trades"] == 1
