"""Tests para orchestrator components."""
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, "eolo-crop")

from orchestrator.daily_scheduler import DailyScheduler
from orchestrator.watchlist_builder import build_watchlist
from orchestrator.position_monitor import monitor_positions

ET = ZoneInfo("America/New_York")


def test_scheduler_current_phase_premarket():
    sched = DailyScheduler()
    # Tuesday 8:30 ET = pre_market
    test_dt = datetime(2026, 6, 9, 8, 30, tzinfo=ET)
    assert sched._current_phase(test_dt) == "pre_market"


def test_scheduler_current_phase_open():
    sched = DailyScheduler()
    test_dt = datetime(2026, 6, 9, 10, 0, tzinfo=ET)
    assert sched._current_phase(test_dt) == "open"


def test_scheduler_current_phase_weekend():
    sched = DailyScheduler()
    # Saturday
    test_dt = datetime(2026, 6, 13, 10, 0, tzinfo=ET)
    assert sched._current_phase(test_dt) is None


def test_scheduler_run_phase_no_callback():
    sched = DailyScheduler()
    result = sched.run_phase("pre_market")
    assert result["status"] == "skipped_no_callback"


def test_scheduler_run_phase_with_callback():
    called = []
    sched = DailyScheduler(callbacks={"pre_market": lambda: called.append(1) or "OK"})
    result = sched.run_phase("pre_market")
    assert result["status"] == "ok"
    assert called == [1]


def test_watchlist_builder_basic():
    iv_ranks = {"SPY": 50, "QQQ": 25, "IWM": 35}
    wl = build_watchlist(iv_rank_lookup=iv_ranks)
    assert "SPY" in wl["selected"]
    assert "IWM" in wl["selected"]
    assert "QQQ" in wl["rejected"]


def test_watchlist_uses_bot_cache_when_no_lookup():
    """T10.B: build_watchlist reads iv_rank from bot._last_snapshots."""
    class MockBot:
        _last_snapshots = {
            "SPY": {"iv_rank_call": 60},
            "QQQ": {"iv_rank_call": 25},  # below threshold
            "IWM": {"iv_rank_call": 45},
        }

    wl = build_watchlist(bot_instance=MockBot(), iv_rank_threshold=30)
    assert "SPY" in wl["selected"]
    assert "IWM" in wl["selected"]
    assert "QQQ" in wl["rejected"]


def test_watchlist_handles_missing_bot_cache():
    """T10.B: graceful when bot has no _last_snapshots."""
    class MockBot:
        pass  # no _last_snapshots attr

    wl = build_watchlist(bot_instance=MockBot(), iv_rank_threshold=30)
    assert len(wl["selected"]) == 0
    for ticker in wl["universe"]:
        assert "iv_rank_unavailable" in wl["rejected"].get(ticker, "")


def test_position_monitor_at_50_target():
    positions = [
        {"symbol": "SPY_240614_750P", "pct_capture": 55},
        {"symbol": "SPY_240614_770C", "pct_capture": 20},
    ]
    result = monitor_positions(positions)
    flags = result["flags"]
    assert len(flags) == 1
    assert flags[0]["flag"] == "AT_50_TARGET"
