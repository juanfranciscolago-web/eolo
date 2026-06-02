"""Unit tests for backtest engine."""
import sys
from pathlib import Path

sys.path.insert(0, "llm_engine_eolo")
sys.path.insert(0, ".")

from backtest.metrics import (
    win_rate, avg_pnl, max_drawdown, sharpe_ratio, compute_metrics_per_rule,
)


def test_win_rate_empty():
    assert win_rate([]) == 0.0


def test_win_rate_basic():
    outs = [{"outcome": "win"}] * 7 + [{"outcome": "loss"}] * 3
    assert win_rate(outs) == 0.7


def test_avg_pnl():
    outs = [{"pnl_dollars": 100}, {"pnl_dollars": -50}, {"pnl_dollars": 80}]
    assert avg_pnl(outs) == (100 - 50 + 80) / 3


def test_max_drawdown():
    outs = [{"pnl_dollars": 100}, {"pnl_dollars": -200}, {"pnl_dollars": 50}]
    # cumulative: 100, -100, -50 → peak=100, low=-100 → DD=200
    assert max_drawdown(outs) == 200


def test_sharpe_zero_stdev():
    outs = [{"pnl_dollars": 50}] * 5
    assert sharpe_ratio(outs) == 0.0


def test_compute_metrics_per_rule():
    rule_outs = {
        "TR-A": [{"pnl_dollars": 100, "outcome": "win"}] * 5,
        "TR-B": [{"pnl_dollars": -50, "outcome": "loss"}] * 3,
    }
    metrics = compute_metrics_per_rule(rule_outs)
    assert metrics["TR-A"]["n_trades"] == 5
    assert metrics["TR-A"]["win_rate"] == 1.0
    assert metrics["TR-B"]["win_rate"] == 0.0
