"""Compute backtest metrics: win rate, avg P/L, max DD, Sharpe per rule × régimen.

Master Plan v2.1 sec 10.1.
"""
import math


def win_rate(outcomes: list[dict]) -> float:
    if not outcomes:
        return 0.0
    wins = sum(1 for o in outcomes if o.get("outcome") == "win")
    return wins / len(outcomes)


def avg_pnl(outcomes: list[dict]) -> float:
    if not outcomes:
        return 0.0
    return sum(o.get("pnl_dollars", 0) for o in outcomes) / len(outcomes)


def max_drawdown(outcomes: list[dict]) -> float:
    if not outcomes:
        return 0.0
    cumulative = []
    total = 0
    for o in outcomes:
        total += o.get("pnl_dollars", 0)
        cumulative.append(total)
    peak = 0
    max_dd = 0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(outcomes: list[dict]) -> float:
    """Simple Sharpe: mean / stdev * sqrt(252)."""
    if len(outcomes) < 2:
        return 0.0
    pnls = [o.get("pnl_dollars", 0) for o in outcomes]
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return 0.0
    return (mean / stdev) * math.sqrt(252)


def compute_metrics_per_rule(rule_outcomes: dict[str, list[dict]]) -> dict[str, dict]:
    """Compute aggregate metrics per rule."""
    out = {}
    for rule_id, outs in rule_outcomes.items():
        out[rule_id] = {
            "n_trades": len(outs),
            "win_rate": win_rate(outs),
            "avg_pnl": avg_pnl(outs),
            "max_dd": max_drawdown(outs),
            "sharpe": sharpe_ratio(outs),
        }
    return out
