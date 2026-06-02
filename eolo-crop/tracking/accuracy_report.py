"""Weekly accuracy report per rule."""
from collections import defaultdict


def compute_rule_accuracy(closed_trades: list[dict]) -> dict[str, dict]:
    """Aggregate trade outcomes by rule_id. Returns {rule_id: {n, wins, win_rate, avg_pnl}}."""
    by_rule = defaultdict(list)
    for trade in closed_trades:
        rules = trade.get("rules_applied", [])
        for rid in rules:
            by_rule[rid].append(trade)

    out = {}
    for rid, trades in by_rule.items():
        wins = sum(1 for t in trades if t.get("outcome") == "win")
        avg = sum(t.get("pnl_dollars", 0) for t in trades) / len(trades) if trades else 0
        out[rid] = {
            "n_trades": len(trades),
            "wins": wins,
            "win_rate": wins / len(trades) if trades else 0,
            "avg_pnl": avg,
        }
    return out
