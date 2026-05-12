"""
Dual-attribution analysis — V1 / V2 / Crypto.

Reconstruye pairs BUY → SELL por bot via FIFO matching y atribuye P&L
tanto al opener como al closer (las atribuciones se loguean por separado
en Firestore — la del opener se pierde, la del closer es la que persiste).

Lee del backup más reciente en backups/<timestamp>/ y produce
`analysis/dual_attribution_report.md` + summary por consola.

NO ESCRIBE A FIRESTORE.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Tunable thresholds
# ─────────────────────────────────────────────────────────────────────
HUERFANA_MIN_OPENS      = 5     # mínimo opens para considerar "huérfana"
HUERFANA_MAX_CLOSE_RATIO = 4    # closes ≤ opens / ratio (1/4 default)


# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).resolve().parents[1]              # ~/PycharmProjects/eolo
BACKUPS_DIR   = ROOT / "backups"
REPORT_PATH   = ROOT / "analysis" / "dual_attribution_report.md"


def latest_backup() -> Path:
    """Carpeta backups/<timestamp>/ más nueva por mtime."""
    candidates = [p for p in BACKUPS_DIR.iterdir() if p.is_dir()]
    if not candidates:
        sys.exit(f"[FATAL] no hay backups en {BACKUPS_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        print(f"[WARN] {path.name} no existe — se asume 0 trades")
        return {}
    with open(path) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────
# Normalización de strategy
# ─────────────────────────────────────────────────────────────────────
_CONSENSUS_PREFIX = re.compile(r"^consensus:\s*", re.IGNORECASE)

SYSTEM_FORCED_PATTERNS = re.compile(
    r"(RISK_WATCHDOG|CLOSE_ALL|auto[_\-]?close|stop[_\-]?loss|take[_\-]?profit)",
    re.IGNORECASE,
)

CLAUDE_OVERRIDE_PATTERNS = re.compile(
    r"(claude)",
    re.IGNORECASE,
)


def norm_strategy(s: str | None) -> str:
    if not s:
        return "<empty>"
    return _CONSENSUS_PREFIX.sub("", str(s)).strip() or "<empty>"


def is_system_forced(strategy: str) -> bool:
    return bool(SYSTEM_FORCED_PATTERNS.search(strategy or ""))


def is_claude_override(strategy: str) -> bool:
    return bool(CLAUDE_OVERRIDE_PATTERNS.search(strategy or ""))


# ─────────────────────────────────────────────────────────────────────
# Event extraction
# ─────────────────────────────────────────────────────────────────────
def parse_ts(s: str) -> datetime:
    """Tolera 'YYYY-MM-DD HH:MM:SS' y ISO 8601."""
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min


def flatten_daily_doc(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """V1/V2 docs son {key: payload}. Devuelve lista de payloads."""
    return [v for v in doc.values() if isinstance(v, dict)]


# ─────────────────────────────────────────────────────────────────────
# FIFO matching
# ─────────────────────────────────────────────────────────────────────
def fifo_match(events: list[dict[str, Any]],
               key_fn,
               is_open_fn,
               is_close_fn,
               pnl_field: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Empareja eventos FIFO. Devuelve (pairs, unmatched_opens).

    `events` se ordena por timestamp asc antes del matching.
    `key_fn(event)`         → clave de matching (hashable).
    `is_open_fn(event)`     → True si es apertura.
    `is_close_fn(event)`    → True si es cierre.
    `pnl_field`             → campo del cierre que tiene el P&L.
    """
    events_sorted = sorted(events, key=lambda e: parse_ts(e.get("timestamp") or e.get("ts_iso") or ""))
    queues: dict[Any, deque] = defaultdict(deque)
    pairs: list[dict[str, Any]] = []

    for ev in events_sorted:
        k = key_fn(ev)
        if is_open_fn(ev):
            queues[k].append(ev)
        elif is_close_fn(ev):
            q = queues.get(k)
            if not q:
                # cierre huérfano — sin opener registrado en el backup
                continue
            opener = q.popleft()
            pnl_raw = ev.get(pnl_field)
            try:
                pnl_val = float(pnl_raw) if pnl_raw is not None else None
            except (TypeError, ValueError):
                pnl_val = None
            pairs.append({
                "opener_strategy_raw": opener.get("strategy", ""),
                "closer_strategy_raw": ev.get("strategy", ""),
                "opener_strategy":     norm_strategy(opener.get("strategy")),
                "closer_strategy":     norm_strategy(ev.get("strategy")),
                "opener_ts":           opener.get("timestamp") or opener.get("ts_iso"),
                "closer_ts":           ev.get("timestamp") or ev.get("ts_iso"),
                "key":                 k,
                "pnl":                 pnl_val,
            })

    unmatched_opens = [op for q in queues.values() for op in q]
    return pairs, unmatched_opens


# ─────────────────────────────────────────────────────────────────────
# Per-bot adapters
# ─────────────────────────────────────────────────────────────────────
def extract_v1(daily_collection: dict[str, Any]) -> list[dict[str, Any]]:
    """eolo-trades: {date: {key: payload, ...}, ...}. Solo BUY/SELL (long)."""
    out = []
    for _date, day_doc in daily_collection.items():
        if not isinstance(day_doc, dict):
            continue
        for payload in flatten_daily_doc(day_doc):
            action = payload.get("action")
            if action in ("BUY", "SELL"):
                out.append(payload)
    return out


def extract_v2(daily_collection: dict[str, Any]) -> list[dict[str, Any]]:
    """eolo-options-trades: idem pero acciones son BUY_TO_OPEN / SELL_TO_CLOSE."""
    out = []
    for _date, day_doc in daily_collection.items():
        if not isinstance(day_doc, dict):
            continue
        for payload in flatten_daily_doc(day_doc):
            action = payload.get("action")
            if action in ("BUY_TO_OPEN", "SELL_TO_CLOSE"):
                out.append(payload)
    return out


def extract_crypto(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """eolo-crypto-trades: doc por trade. Normaliza `side` → action y deriva ts_iso."""
    out = []
    for _doc_id, payload in collection.items():
        if not isinstance(payload, dict):
            continue
        side = (payload.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        ts_iso = payload.get("ts_iso")
        if not ts_iso:
            ts = payload.get("ts")
            if ts is not None:
                try:
                    ts_iso = datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
                except (TypeError, ValueError):
                    ts_iso = None
        out.append({**payload, "action": side, "ts_iso": ts_iso})
    return out


# ─────────────────────────────────────────────────────────────────────
# Per-bot matching wrappers
# ─────────────────────────────────────────────────────────────────────
def match_v1(events):
    return fifo_match(
        events,
        key_fn      = lambda e: e.get("ticker"),
        is_open_fn  = lambda e: e.get("action") == "BUY",
        is_close_fn = lambda e: e.get("action") == "SELL",
        pnl_field   = "pnl_usd",
    )


def match_v2(events):
    return fifo_match(
        events,
        key_fn      = lambda e: (e.get("ticker"), e.get("expiration"), e.get("strike"), e.get("option_type")),
        is_open_fn  = lambda e: e.get("action") == "BUY_TO_OPEN",
        is_close_fn = lambda e: e.get("action") == "SELL_TO_CLOSE",
        pnl_field   = "pnl_usd",
    )


def match_crypto(events):
    return fifo_match(
        events,
        key_fn      = lambda e: e.get("symbol"),
        is_open_fn  = lambda e: e.get("action") == "BUY",
        is_close_fn = lambda e: e.get("action") == "SELL",
        pnl_field   = "pnl_usdt",
    )


# ─────────────────────────────────────────────────────────────────────
# Stats & reporting
# ─────────────────────────────────────────────────────────────────────
def aggregate_by(pairs, field):
    """Agrupa por `pairs[*][field]` → dict[strategy] -> {count, pnl_sum, pnl_list, wins}."""
    g = defaultdict(lambda: {"count": 0, "pnl_sum": 0.0, "pnl_list": [], "wins": 0, "measurable": 0})
    for p in pairs:
        s = p[field]
        g[s]["count"] += 1
        if p["pnl"] is not None:
            g[s]["pnl_sum"]  += p["pnl"]
            g[s]["pnl_list"].append(p["pnl"])
            g[s]["measurable"] += 1
            if p["pnl"] > 0:
                g[s]["wins"] += 1
    return g


def top_n_table(agg, n=5, label="Strategy", count_label="N"):
    rows = sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)[:n]
    lines = [
        f"| {label} | {count_label} | Total P&L | Avg P&L | Win rate (measurable) |",
        "|---|---|---|---|---|",
    ]
    for s, d in rows:
        avg = (d["pnl_sum"] / d["measurable"]) if d["measurable"] else 0.0
        wr  = (d["wins"] / d["measurable"] * 100) if d["measurable"] else 0.0
        lines.append(
            f"| `{s}` | {d['count']} | ${d['pnl_sum']:+,.2f} | ${avg:+,.2f} | {wr:.0f}% ({d['measurable']}/{d['count']}) |"
        )
    return "\n".join(lines)


def cross_strategy_matrix(pairs, top_openers, top_closers):
    """Matriz opener × closer (cells = N($pnl))."""
    cells = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for p in pairs:
        if p["opener_strategy"] in top_openers and p["closer_strategy"] in top_closers:
            c = cells[(p["opener_strategy"], p["closer_strategy"])]
            c["n"]   += 1
            c["pnl"] += p["pnl"] or 0.0

    header = "| Opener \\ Closer | " + " | ".join(f"`{c}`" for c in top_closers) + " |"
    sep    = "|---" + "|---" * len(top_closers) + "|"
    rows   = [header, sep]
    for o in top_openers:
        row = [f"`{o}`"]
        for c in top_closers:
            cell = cells.get((o, c))
            row.append(f"{cell['n']}(${cell['pnl']:+,.0f})" if cell else "—")
        rows.append("| " + " | ".join(row) + " |")
    return "\n".join(rows)


def findings(pairs, agg_open, agg_close):
    intra = [p for p in pairs if p["opener_strategy"] == p["closer_strategy"]]
    cross = [p for p in pairs if p["opener_strategy"] != p["closer_strategy"]]
    cross_pnl = sum(p["pnl"] for p in cross if p["pnl"] is not None)

    orphans   = []   # abren mucho, cierran poco
    scavenger = []   # cierran mucho, abren poco
    for s in set(agg_open) | set(agg_close):
        opens  = agg_open.get(s,  {}).get("count", 0)
        closes = agg_close.get(s, {}).get("count", 0)
        if opens >= HUERFANA_MIN_OPENS and closes <= max(1, opens // HUERFANA_MAX_CLOSE_RATIO):
            orphans.append((s, opens, closes))
        if closes >= HUERFANA_MIN_OPENS and opens <= max(1, closes // HUERFANA_MAX_CLOSE_RATIO):
            scavenger.append((s, opens, closes))

    sys_forced    = [p for p in pairs if is_system_forced(p["closer_strategy_raw"])]
    claude_forced = [p for p in pairs if is_claude_override(p["closer_strategy_raw"])]
    sys_forced_pnl    = sum(p["pnl"] for p in sys_forced    if p["pnl"] is not None)
    claude_forced_pnl = sum(p["pnl"] for p in claude_forced if p["pnl"] is not None)

    return {
        "intra": len(intra),
        "cross": len(cross),
        "cross_pnl": cross_pnl,
        "orphans": sorted(orphans, key=lambda x: -x[1])[:10],
        "scavenger": sorted(scavenger, key=lambda x: -x[2])[:10],
        "sys_forced_n": len(sys_forced),
        "sys_forced_pnl": sys_forced_pnl,
        "claude_forced_n": len(claude_forced),
        "claude_forced_pnl": claude_forced_pnl,
    }


def bot_section(bot_label, pairs, unmatched):
    agg_open  = aggregate_by(pairs, "opener_strategy")
    agg_close = aggregate_by(pairs, "closer_strategy")
    total_pnl = sum(p["pnl"] for p in pairs if p["pnl"] is not None)
    measurable = sum(1 for p in pairs if p["pnl"] is not None)

    f = findings(pairs, agg_open, agg_close)
    intra_pct = (f["intra"] / len(pairs) * 100) if pairs else 0.0
    cross_pct = (f["cross"] / len(pairs) * 100) if pairs else 0.0

    top_openers = [s for s, _ in sorted(agg_open.items(),  key=lambda kv: -kv[1]["count"])[:5]]
    top_closers = [s for s, _ in sorted(agg_close.items(), key=lambda kv: -kv[1]["count"])[:5]]

    out = []
    out.append(f"## Bot: {bot_label}\n")
    out.append("### Resumen")
    out.append(f"- Total trades pareados: **{len(pairs)}**")
    out.append(f"- Trades sin pareo (BUY sin SELL al final): **{len(unmatched)}**")
    out.append(f"- Total P&L (sólo pairs con pnl medible: {measurable}/{len(pairs)}): **${total_pnl:+,.2f}**\n")

    out.append("### Top 5 strategies por OPENER attribution")
    out.append(top_n_table(agg_open,  n=5, label="Opener", count_label="Opens") + "\n")

    out.append("### Top 5 strategies por CLOSER attribution")
    out.append(top_n_table(agg_close, n=5, label="Closer", count_label="Closes") + "\n")

    out.append("### Cross-strategy interference")
    out.append(f"- Intra-strategy (opener = closer): **{f['intra']}** ({intra_pct:.1f}%)")
    out.append(f"- Cross-strategy (opener ≠ closer): **{f['cross']}** ({cross_pct:.1f}%)")
    out.append(f"- P&L atribuible a cross-strategy: **${f['cross_pnl']:+,.2f}**\n")

    out.append("### Matriz opener × closer (top 5 × top 5 por volumen)")
    if top_openers and top_closers:
        out.append(cross_strategy_matrix(pairs, top_openers, top_closers) + "\n")
    else:
        out.append("_(sin datos)_\n")

    out.append("### Findings clave")
    if f["orphans"]:
        out.append("**Huérfanas** (abren ≫ cierran):")
        for s, o, c in f["orphans"]:
            out.append(f"- `{s}`: {o} opens / {c} closes")
    if f["scavenger"]:
        out.append("\n**Carroñeras** (cierran ≫ abren):")
        for s, o, c in f["scavenger"]:
            out.append(f"- `{s}`: {o} opens / {c} closes")
    out.append(f"\n**Cierres forzados** (clasificados):")
    out.append(f"- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close):")
    out.append(f"  - N: **{f['sys_forced_n']}** · P&L: **${f['sys_forced_pnl']:+,.2f}**")
    out.append(f"- Claude override (claude_bot / claude_high / claude_medium):")
    out.append(f"  - N: **{f['claude_forced_n']}** · P&L: **${f['claude_forced_pnl']:+,.2f}**\n")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    backup = latest_backup()
    print(f"[INFO] Backup origen: {backup}")

    v1_raw     = load_json(backup / "eolo-trades.json")
    v2_raw     = load_json(backup / "eolo-options-trades.json")
    crypto_raw = load_json(backup / "eolo-crypto-trades.json")

    v1_events     = extract_v1(v1_raw)
    v2_events     = extract_v2(v2_raw)
    crypto_events = extract_crypto(crypto_raw)
    print(f"[INFO] Eventos: V1={len(v1_events):,}  V2={len(v2_events):,}  Crypto={len(crypto_events):,}")

    v1_pairs,     v1_unmatched     = match_v1(v1_events)
    v2_pairs,     v2_unmatched     = match_v2(v2_events)
    crypto_pairs, crypto_unmatched = match_crypto(crypto_events)
    print(f"[INFO] Pairs:   V1={len(v1_pairs):,}  V2={len(v2_pairs):,}  Crypto={len(crypto_pairs):,}")
    print(f"[INFO] Unmatched opens: V1={len(v1_unmatched)}  V2={len(v2_unmatched)}  Crypto={len(crypto_unmatched)}")

    md = [
        "# Dual-attribution analysis — V1 / V2 / Crypto",
        f"_Generado: {datetime.now().isoformat(timespec='seconds')}_  ",
        f"_Backup: `{backup.relative_to(ROOT)}`_\n",
        "Reconstrucción FIFO de pairs BUY→SELL por bot, con P&L atribuido tanto al opener como al closer.",
        "El P&L visible en Firestore se atribuye al **closer**; este reporte expone la doble vista para detectar interferencia entre estrategias.\n",
        "---\n",
        bot_section("V1 Stock",     v1_pairs,     v1_unmatched),
        "---\n",
        bot_section("V2 Options",   v2_pairs,     v2_unmatched),
        "---\n",
        bot_section("Crypto",       crypto_pairs, crypto_unmatched),
    ]

    REPORT_PATH.write_text("\n".join(md))
    print(f"[OK] Reporte → {REPORT_PATH.relative_to(ROOT)}")

    # Console summary
    print("\n── Summary ─────────────────────────────────────────────────")
    for label, pairs in [("V1", v1_pairs), ("V2", v2_pairs), ("Crypto", crypto_pairs)]:
        cross = sum(1 for p in pairs if p["opener_strategy"] != p["closer_strategy"])
        pnl   = sum(p["pnl"] for p in pairs if p["pnl"] is not None)
        pct   = (cross / len(pairs) * 100) if pairs else 0.0
        print(f"  {label:7s}  pairs={len(pairs):>6,}  cross={cross:>6,} ({pct:5.1f}%)  P&L=${pnl:+,.2f}")


if __name__ == "__main__":
    main()
