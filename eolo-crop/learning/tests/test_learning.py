"""Unit tests para learning module."""
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, "eolo-crop")


def test_generate_daily_journal_no_trades():
    from learning.nightly_journal import generate_daily_journal
    journal = generate_daily_journal([], [], ["TR-Juan-001"])
    assert journal["trades_count"] == 0
    assert journal["win_rate"] == 0
    assert "TR-Juan-001" in journal["rules_unused_30d"]


def test_generate_daily_journal_with_trades():
    from learning.nightly_journal import generate_daily_journal
    trades = [
        {"outcome": "win", "pnl_dollars": 100, "rules_applied": ["TR-Juan-001"]},
        {"outcome": "loss", "pnl_dollars": -30, "rules_applied": ["TR-Juan-002"]},
    ]
    journal = generate_daily_journal(trades, [], ["TR-Juan-001", "TR-Juan-002", "TR-Juan-003"])
    assert journal["trades_count"] == 2
    assert journal["wins_count"] == 1
    assert journal["total_pnl_dollars"] == 70
    assert journal["win_rate"] == 0.5


def test_identify_unused_rules():
    from learning.nightly_journal import identify_unused_rules
    decisions = [
        {"tacit_rules_applied": ["TR-A", "TR-B"]},
        {"tacit_rules_applied": ["TR-A"]},
    ]
    all_ids = ["TR-A", "TR-B", "TR-C", "TR-D"]
    unused = identify_unused_rules(decisions, all_ids)
    assert "TR-C" in unused
    assert "TR-D" in unused
    assert "TR-A" not in unused


def test_format_journal_markdown():
    from learning.nightly_journal import format_journal_markdown
    journal = {
        "date": "2026-06-02",
        "trades_count": 3,
        "wins_count": 2,
        "losses_count": 1,
        "total_pnl_dollars": 150.0,
        "win_rate": 0.67,
        "rules_cited_today": ["TR-Juan-012"],
        "rules_unused_30d": ["TR-Juan-099"],
        "summary": "3 trades · 2W/1L · P/L $150",
    }
    md = format_journal_markdown(journal)
    assert "2026-06-02" in md
    assert "$150" in md
    assert "TR-Juan-012" in md
    assert "TR-Juan-099" in md


def test_compare_periods_baseline():
    from learning.weekly_review import compare_periods, categorize_rules
    recent = [{"rules_applied": ["TR-A"], "outcome": "win", "pnl_dollars": 100}] * 5
    baseline = [{"rules_applied": ["TR-A"], "outcome": "loss", "pnl_dollars": -50}] * 5
    deltas = compare_periods(recent, baseline)
    assert "TR-A" in deltas
    assert deltas["TR-A"]["win_rate_delta"] == 1.0  # 100% - 0%
    cats = categorize_rules(deltas)
    assert "TR-A" in cats["improving"]


@patch("learning.feedback_chat.session_manager._get_client")
def test_open_feedback_session(mock_client):
    from learning.feedback_chat.session_manager import open_feedback_session
    mock_doc = MagicMock()
    mock_client.return_value.collection.return_value.document.return_value = mock_doc
    sid = open_feedback_session("2026-06-02")
    assert sid == "FB_2026-06-02"
    mock_doc.set.assert_called_once()


def test_build_feedback_prompt_no_trades():
    from learning.feedback_chat.prompt_builder import build_feedback_user_prompt
    journal = {"date": "2026-06-02", "summary": "0 trades", "rules_unused_30d": ["TR-A", "TR-B"]}
    prompt = build_feedback_user_prompt([], journal)
    assert "2026-06-02" in prompt
    assert "30+ días" in prompt or "30+ d" in prompt


def test_build_feedback_prompt_with_trades():
    from learning.feedback_chat.prompt_builder import build_feedback_user_prompt
    trades = [
        {"trade_id": "T1", "ticker": "SPY", "pnl_dollars": 150, "manual_override": True, "rules_applied": ["TR-Juan-012"]},
        {"trade_id": "T2", "ticker": "QQQ", "pnl_dollars": -30, "rules_applied": ["TR-Juan-005"]},
    ]
    journal = {"date": "2026-06-02", "total_pnl_dollars": 120, "win_rate": 0.5}
    prompt = build_feedback_user_prompt(trades, journal)
    assert "T1" in prompt
    assert "MANUAL_OVERRIDE" in prompt
