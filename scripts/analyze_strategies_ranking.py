#!/usr/bin/env python3
"""
analyze_strategies_ranking.py — Ranking de estrategias Eolo (v1 + v2 + crypto)

Pulls trades desde Firestore (eolo-trades, eolo-options-trades, eolo-crypto-trades),
agrupa por estrategia, computa métricas y output JSON a:

    /sessions/bold-compassionate-curie/mnt/PycharmProjects/eolo/scripts/strategy_ranking.json

Uso:
    cd ~/PycharmProjects/eolo
    python3 scripts/analyze_strategies_ranking.py
"""
import os
import json
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone

try:
    from google.cloud import firestore
except ImportError:
    print("ERROR: pip install google-cloud-firestore", file=sys.stderr)
    sys.exit(1)

PROJECT = os.environ.get("GCP_PROJECT", "eolo-schwab-agent")

# Output: dejarlo en el repo para que Claude lo lea
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "strategy_ranking.json")


def safe_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def load_v1_v2(db, col):
    """v1/v2: daily docs con trades como sub-fields. Retorna list de dicts."""
    trades = []
    for doc in db.collection(col).stream():
        data = doc.to_dict() or {}
        for key, t in data.items():
            if not isinstance(t, dict):
                continue
            t = dict(t)  # copy
            t["_doc_day"] = doc.id           # YYYY-MM-DD
            t["_field"] = key
            trades.append(t)
    return trades


def load_crypto(db):
    """crypto: un doc por trade. Filtramos por side=='SELL' porque solo los SELL
    son closes con pnl_usdt real; los BUY (entries) llevan pnl_usdt=0 y
    contaminan las métricas si se cuentan como flat trades."""
    trades = []
    side_counts = defaultdict(int)
    for doc in db.collection("eolo-crypto-trades").stream():
        data = doc.to_dict() or {}
        side = str(data.get("side") or "").upper()
        side_counts[side or "?"] += 1
        if side != "SELL":
            continue  # skip BUYs y desconocidos
        data = dict(data)
        data["_doc_id"] = doc.id
        trades.append(data)
    print(f"[crypto] sides breakdown: {dict(side_counts)}", file=sys.stderr)
    return trades


def metrics_for(trades, pnl_field="pnl_usd"):
    """Métricas para una lista de trades de UNA estrategia. None si no hay closes."""
    # Sort by timestamp (string sort works for ISO + 'YYYY-MM-DD HH:MM:SS' formats;
    # for crypto epoch we coerce to string with a wide-enough format).
    def _sort_key(t):
        ts = t.get("timestamp") or t.get("ts") or t.get("_doc_day") or ""
        return str(ts)

    trades_sorted = sorted(trades, key=_sort_key)

    pnls = []
    timestamps = []
    symbols = set()
    modes = defaultdict(int)
    timeframes = defaultdict(int)

    for t in trades_sorted:
        pnl = safe_float(t.get(pnl_field))
        if pnl is None:
            continue
        pnls.append(pnl)
        sym = t.get("ticker") or t.get("symbol") or ""
        if sym:
            symbols.add(str(sym))
        ts = t.get("timestamp") or t.get("ts") or t.get("_doc_day", "")
        timestamps.append(str(ts))
        m = t.get("mode") or ""
        if m:
            modes[str(m)] += 1
        tf = t.get("timeframe")
        if tf is not None and tf != "":
            timeframes[str(tf)] += 1

    n = len(pnls)
    if n == 0:
        return None  # no closed trades

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flat = [p for p in pnls if p == 0]

    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n
    win_rate = len(wins) / n

    sum_wins = sum(wins) if wins else 0
    sum_losses_abs = abs(sum(losses)) if losses else 0

    if sum_losses_abs > 0:
        profit_factor = round(sum_wins / sum_losses_abs, 3)
    elif sum_wins > 0:
        profit_factor = "inf"
    else:
        profit_factor = 0

    if n > 1:
        mean = avg_pnl
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        sharpe = round(mean / std, 3) if std > 0 else 0
    else:
        sharpe = 0

    # Path-based max drawdown sobre el equity acumulado
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    eq_curve = []
    for p in pnls:
        cum += p
        eq_curve.append(round(cum, 2))
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Streaks
    cur_w = cur_l = max_w = max_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif p < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0

    valid_ts = [t for t in timestamps if t and t != "0"]
    first_seen = min(valid_ts) if valid_ts else None
    last_seen = max(valid_ts) if valid_ts else None

    return {
        "n_trades": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_flat": len(flat),
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 4),
        "avg_win": round(sum_wins / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "max_win": round(max(pnls), 2),
        "max_loss": round(min(pnls), 2),
        "profit_factor": profit_factor,
        "sharpe_per_trade": sharpe,
        "max_drawdown": round(max_dd, 2),
        "max_consecutive_wins": max_w,
        "max_consecutive_losses": max_l,
        "symbols": sorted(symbols),
        "n_symbols": len(symbols),
        "modes": dict(modes),
        "timeframes": dict(timeframes),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "equity_curve_sample": eq_curve[-50:],   # últimos 50 puntos
    }


def group_and_rank(trades, pnl_field="pnl_usd"):
    by_strat = defaultdict(list)
    for t in trades:
        strat = (t.get("strategy") or "unknown")
        if not isinstance(strat, str):
            strat = str(strat)
        strat = strat.strip() or "unknown"
        by_strat[strat].append(t)

    out = {}
    open_only = {}
    for strat, ts in by_strat.items():
        m = metrics_for(ts, pnl_field=pnl_field)
        if m is not None:
            out[strat] = m
        else:
            # Strategy generated trades pero ninguno cerrado todavía
            open_only[strat] = {"n_open_events": len(ts)}
    return out, open_only


def main():
    db = firestore.Client(project=PROJECT)
    print(f"[INFO] Connecting to project: {PROJECT}", file=sys.stderr)

    out = {
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_project": PROJECT,
    }

    # v1
    print("[v1] reading eolo-trades...", file=sys.stderr)
    try:
        v1 = load_v1_v2(db, "eolo-trades")
        print(f"[v1] loaded {len(v1)} trade-events across {len({t['_doc_day'] for t in v1})} days", file=sys.stderr)
        ranked, open_only = group_and_rank(v1, pnl_field="pnl_usd")
        out["v1"] = ranked
        out["v1_open_only"] = open_only
        out["v1_meta"] = {"total_events": len(v1), "n_strategies_closed": len(ranked), "n_strategies_open_only": len(open_only)}
        print(f"[v1] {len(ranked)} strategies with closed trades, {len(open_only)} only-open", file=sys.stderr)
    except Exception as e:
        out["v1"] = {"_error": str(e)}
        print(f"[v1] ERROR: {e}", file=sys.stderr)

    # v2
    print("[v2] reading eolo-options-trades...", file=sys.stderr)
    try:
        v2 = load_v1_v2(db, "eolo-options-trades")
        print(f"[v2] loaded {len(v2)} trade-events across {len({t['_doc_day'] for t in v2})} days", file=sys.stderr)
        ranked, open_only = group_and_rank(v2, pnl_field="pnl_usd")
        out["v2"] = ranked
        out["v2_open_only"] = open_only
        out["v2_meta"] = {"total_events": len(v2), "n_strategies_closed": len(ranked), "n_strategies_open_only": len(open_only)}
        print(f"[v2] {len(ranked)} strategies with closed trades, {len(open_only)} only-open", file=sys.stderr)
    except Exception as e:
        out["v2"] = {"_error": str(e)}
        print(f"[v2] ERROR: {e}", file=sys.stderr)

    # crypto
    print("[crypto] reading eolo-crypto-trades...", file=sys.stderr)
    try:
        cr = load_crypto(db)
        print(f"[crypto] loaded {len(cr)} trades", file=sys.stderr)
        ranked, open_only = group_and_rank(cr, pnl_field="pnl_usdt")
        out["crypto"] = ranked
        out["crypto_open_only"] = open_only
        out["crypto_meta"] = {"total_events": len(cr), "n_strategies_closed": len(ranked), "n_strategies_open_only": len(open_only)}
        print(f"[crypto] {len(ranked)} strategies with closed trades, {len(open_only)} only-open", file=sys.stderr)
    except Exception as e:
        out["crypto"] = {"_error": str(e)}
        print(f"[crypto] ERROR: {e}", file=sys.stderr)

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)

    print(f"\n[DONE] Wrote {OUT_PATH}", file=sys.stderr)
    print(f"[DONE] File size: {os.path.getsize(OUT_PATH)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
