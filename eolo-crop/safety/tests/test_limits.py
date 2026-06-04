"""Wave 3 OMNI-SPRINT: circuit breaker tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from safety.limits import RiskLimits, check_pre_entry_limits


def test_default_limits_allow_small_trade():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=500,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=0,
        limits=RiskLimits(),
    )
    assert allowed is True
    assert reasons == []


def test_position_size_limit_blocks():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=2000,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=0,
        limits=RiskLimits(max_position_size_usd=1000),
    )
    assert allowed is False
    assert any("POSITION_SIZE_LIMIT" in r for r in reasons)


def test_daily_loss_limit_blocks():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=100,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=-600,
        consecutive_losses_today=0,
        limits=RiskLimits(max_daily_loss_usd=500),
    )
    assert allowed is False
    assert any("DAILY_LOSS_LIMIT" in r for r in reasons)


def test_per_ticker_limit():
    positions = [{"ticker": "SPY"}, {"ticker": "SPY"}]
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=100,
        current_positions=positions,
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=0,
        limits=RiskLimits(max_open_positions_per_ticker=2),
    )
    assert allowed is False
    assert any("PER_TICKER_LIMIT" in r for r in reasons)


def test_total_positions_limit():
    positions = [{"ticker": f"T{i}"} for i in range(8)]
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=100,
        current_positions=positions,
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=0,
        limits=RiskLimits(max_open_positions_total=8),
    )
    assert allowed is False
    assert any("TOTAL_POSITIONS_LIMIT" in r for r in reasons)


def test_consecutive_losses_blocks():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=100,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=5,
        limits=RiskLimits(max_consecutive_losses=3),
    )
    assert allowed is False
    assert any("CONSECUTIVE_LOSSES_LIMIT" in r for r in reasons)


def test_kill_switch_blocks_everything():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=100,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=0,
        consecutive_losses_today=0,
        limits=RiskLimits(kill_switch_active=True),
    )
    assert allowed is False
    assert "KILL_SWITCH_ACTIVE" in reasons


def test_multiple_breaches_reported():
    allowed, reasons = check_pre_entry_limits(
        proposed_position_value_usd=5000,
        current_positions=[],
        ticker="SPY",
        daily_pnl_usd=-9999,
        consecutive_losses_today=0,
        limits=RiskLimits(max_position_size_usd=1000, max_daily_loss_usd=500),
    )
    assert allowed is False
    # Should report BOTH position size and daily loss
    assert any("POSITION_SIZE_LIMIT" in r for r in reasons)
    assert any("DAILY_LOSS_LIMIT" in r for r in reasons)
