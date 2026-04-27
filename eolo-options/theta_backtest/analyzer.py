# ============================================================
#  Theta Harvest Backtester — Analyzer
#
#  Calcula métricas de performance a partir de los trades
#  cerrados generados por el simulator.
# ============================================================
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

import pandas as pd

from .simulator import SpreadPosition, DailyResult


# ─────────────────────────────────────────────────────────
#  Core metrics
# ─────────────────────────────────────────────────────────

def compute_metrics(
    positions: list[SpreadPosition],
    daily_results: Optional[list[DailyResult]] = None,
    ticker: str = "",
) -> dict:
    """
    Calcula métricas completas de un conjunto de trades.

    Returns
    -------
    dict con: win_rate, profit_factor, avg_credit, avg_pnl,
              total_pnl, max_drawdown, sharpe, trades_by_dte,
              trades_by_zone, trades_by_exit, avg_dte_at_close
    """
    if not positions:
        return {"error": "no trades", "total_pnl": 0.0, "win_rate": 0.0}

    pnls        = [p.pnl for p in positions if p.pnl is not None]
    credits     = [p.entry_credit for p in positions]
    total_trades = len(pnls)

    wins        = [p for p in pnls if p > 0]
    losses      = [p for p in pnls if p <= 0]
    win_rate    = len(wins) / total_trades if total_trades else 0

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win   = sum(wins) / len(wins)   if wins   else 0.0
    avg_loss  = sum(losses) / len(losses) if losses else 0.0
    avg_pnl   = sum(pnls) / total_trades
    total_pnl = sum(pnls)

    # ── DTE at close ─────────────────────────────────
    dtes_at_close = []
    for p in positions:
        if p.exit_date and p.entry_date:
            days = (p.exit_date - p.entry_date).days
            dtes_at_close.append(days)
    avg_dte_close = sum(dtes_at_close) / len(dtes_at_close) if dtes_at_close else 0

    # ── Max drawdown ─────────────────────────────────
    cumulative = 0.0
    peak       = 0.0
    max_dd     = 0.0
    for p in pnls:
        cumulative += p
        peak        = max(peak, cumulative)
        dd          = peak - cumulative
        max_dd      = max(max_dd, dd)

    # ── Sharpe (diario, asumiendo 252 días/año) ───────
    if daily_results:
        daily_pnls = [d.daily_pnl for d in daily_results]
        mean_d = sum(daily_pnls) / len(daily_pnls)
        if len(daily_pnls) > 1:
            var_d = sum((x - mean_d) ** 2 for x in daily_pnls) / (len(daily_pnls) - 1)
            std_d = math.sqrt(var_d)
            sharpe = (mean_d / std_d * math.sqrt(252)) if std_d > 0 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # ── Breakdown por DTE ────────────────────────────
    by_dte: dict[int, dict] = {}
    for dte in sorted({p.dte_at_entry for p in positions}):
        subset = [p for p in positions if p.dte_at_entry == dte]
        sub_pnls = [p.pnl for p in subset if p.pnl is not None]
        by_dte[dte] = {
            "count":         len(subset),
            "win_rate":      len([x for x in sub_pnls if x > 0]) / len(sub_pnls) if sub_pnls else 0,
            "total_pnl":     sum(sub_pnls),
            "avg_credit":    sum(p.entry_credit for p in subset) / len(subset),
            "profit_factor": _pf(sub_pnls),
        }

    # ── Breakdown por risk zone ──────────────────────
    by_zone: dict[str, dict] = {}
    for zone in sorted({p.risk_zone for p in positions}):
        subset   = [p for p in positions if p.risk_zone == zone]
        sub_pnls = [p.pnl for p in subset if p.pnl is not None]
        by_zone[zone] = {
            "count":         len(subset),
            "win_rate":      len([x for x in sub_pnls if x > 0]) / len(sub_pnls) if sub_pnls else 0,
            "total_pnl":     sum(sub_pnls),
            "profit_factor": _pf(sub_pnls),
        }

    # ── Breakdown por exit reason ────────────────────
    by_exit: dict[str, dict] = {}
    for reason in sorted({p.exit_reason for p in positions}):
        subset   = [p for p in positions if p.exit_reason == reason]
        sub_pnls = [p.pnl for p in subset if p.pnl is not None]
        by_exit[reason] = {
            "count":     len(subset),
            "total_pnl": sum(sub_pnls),
            "avg_pnl":   sum(sub_pnls) / len(sub_pnls) if sub_pnls else 0,
        }

    # ── Spread type breakdown ────────────────────────
    by_type: dict[str, dict] = {}
    for st in sorted({p.spread_type for p in positions}):
        subset   = [p for p in positions if p.spread_type == st]
        sub_pnls = [p.pnl for p in subset if p.pnl is not None]
        by_type[st] = {
            "count":         len(subset),
            "win_rate":      len([x for x in sub_pnls if x > 0]) / len(sub_pnls) if sub_pnls else 0,
            "total_pnl":     sum(sub_pnls),
            "profit_factor": _pf(sub_pnls),
        }

    # ── Breakdown por tranche ────────────────────────
    # Muestra el performance de cada estrategia de salida:
    #   T0 = exit rápido 35%  |  T1 = estándar 65%  |  T2 = hold to expiry
    by_tranche: dict[int, dict] = {}
    for tid in sorted({p.tranche_id for p in positions}):
        subset   = [p for p in positions if p.tranche_id == tid]
        sub_pnls = [p.pnl for p in subset if p.pnl is not None]
        sample   = subset[0] if subset else None
        target_label = (
            f"{sample.tranche_target*100:.0f}%" if sample and sample.tranche_target is not None
            else "EXPIRY"
        )
        by_tranche[tid] = {
            "count":         len(subset),
            "win_rate":      len([x for x in sub_pnls if x > 0]) / len(sub_pnls) if sub_pnls else 0,
            "total_pnl":     sum(sub_pnls),
            "avg_pnl":       sum(sub_pnls) / len(sub_pnls) if sub_pnls else 0,
            "profit_factor": _pf(sub_pnls),
            "target_label":  target_label,
        }

    # ── Cumulative P&L series ────────────────────────
    sorted_positions = sorted(positions, key=lambda p: p.exit_date or pd.Timestamp.min)
    cumulative_pnl = []
    cum = 0.0
    for p in sorted_positions:
        if p.pnl is not None:
            cum += p.pnl
            cumulative_pnl.append({
                "date":      p.exit_date.isoformat() if p.exit_date else "",
                "pnl":       round(p.pnl, 2),
                "cum":       round(cum, 2),
                "dte":       p.dte_at_entry,
                "zone":      p.risk_zone,
                "exit":      p.exit_reason,
                "type":      p.spread_type,
                "credit":    round(p.entry_credit, 2),
                "tranche_id": p.tranche_id,
                "tranche_target": p.tranche_target,
            })

    return {
        "ticker":         ticker,
        "total_trades":   total_trades,
        "win_rate":       round(win_rate, 4),
        "profit_factor":  round(profit_factor, 3) if math.isfinite(profit_factor) else 999.0,
        "total_pnl":      round(total_pnl, 2),
        "avg_pnl":        round(avg_pnl, 2),
        "avg_credit":     round(sum(credits) / len(credits), 4) if credits else 0,
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "max_drawdown":   round(max_dd, 2),
        "sharpe":         round(sharpe, 3),
        "avg_dte_close":  round(avg_dte_close, 2),
        "by_dte":         by_dte,
        "by_zone":        by_zone,
        "by_exit":        by_exit,
        "by_type":        by_type,
        "by_tranche":     by_tranche,
        "cumulative_pnl": cumulative_pnl,
    }


def _pf(pnls: list[float]) -> float:
    gp = sum(x for x in pnls if x > 0)
    gl = abs(sum(x for x in pnls if x <= 0))
    if gl == 0:
        return 999.0
    return round(gp / gl, 3)


def print_summary(metrics: dict) -> None:
    """Imprime resumen de métricas en consola."""
    t = metrics.get("ticker", "")
    print(f"\n{'─'*55}")
    print(f"  {t} — Backtest Summary")
    print(f"{'─'*55}")

    if "error" in metrics:
        print(f"  ⚠️  Sin trades: {metrics['error']}")
        print(f"  Total P&L: ${metrics.get('total_pnl', 0):.2f}")
        return

    print(f"  Total trades    : {metrics['total_trades']}")
    print(f"  Win rate        : {metrics['win_rate']*100:.1f}%")
    print(f"  Profit factor   : {metrics['profit_factor']:.2f}")
    print(f"  Total P&L       : ${metrics['total_pnl']:,.0f}")
    print(f"  Avg P&L / trade : ${metrics['avg_pnl']:,.2f}")
    print(f"  Avg credit      : ${metrics['avg_credit']:.4f} ({metrics['avg_credit']*100:.2f}¢)")
    print(f"  Max drawdown    : ${metrics['max_drawdown']:,.0f}")
    print(f"  Sharpe          : {metrics['sharpe']:.2f}")
    print(f"  Avg DTE at close: {metrics['avg_dte_close']:.1f}")

    print(f"\n  By DTE:")
    for dte, d in sorted(metrics["by_dte"].items()):
        print(f"    DTE {dte}: {d['count']:4d} trades  WR {d['win_rate']*100:5.1f}%  "
              f"PnL ${d['total_pnl']:+,.0f}  PF {d['profit_factor']:.2f}")

    print(f"\n  By Risk Zone:")
    for zone, d in sorted(metrics["by_zone"].items()):
        print(f"    {zone:10s}: {d['count']:4d} trades  WR {d['win_rate']*100:5.1f}%  "
              f"PnL ${d['total_pnl']:+,.0f}  PF {d['profit_factor']:.2f}")

    print(f"\n  By Exit Reason:")
    for reason, d in sorted(metrics["by_exit"].items()):
        print(f"    {reason:20s}: {d['count']:4d} trades  PnL ${d['total_pnl']:+,.0f}")

    print(f"\n  By Spread Type:")
    for st, d in metrics["by_type"].items():
        label = "PUT " if "put" in st else "CALL"
        print(f"    {label}: {d['count']:4d} trades  WR {d['win_rate']*100:5.1f}%  "
              f"PnL ${d['total_pnl']:+,.0f}  PF {d['profit_factor']:.2f}")

    if "by_tranche" in metrics and metrics["by_tranche"]:
        print(f"\n  By Tranche (escalonamiento de salidas):")
        for tid, d in sorted(metrics["by_tranche"].items()):
            print(f"    T{tid} target={d['target_label']:6s}: {d['count']:4d} trades  "
                  f"WR {d['win_rate']*100:5.1f}%  PnL ${d['total_pnl']:+,.0f}  "
                  f"avg ${d['avg_pnl']:+.2f}  PF {d['profit_factor']:.2f}")
