"""
Strategies inventory — Pure Isolation Readiness Assessment.

Para cada strategy detectada en V1 / V2 / Crypto, calcula:
  - conteos de opens / closes
  - P&L como opener vs como closer (pairs medibles)
  - métricas cruzadas (intra, cross_as_opener, cross_as_closer)
  - clasificación: complete / entry_only / exit_only / minimal / system

Reusa el pipeline de `dual_attribution_analysis.py`.

NO ESCRIBE A FIRESTORE. Lee del backup más reciente.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dual_attribution_analysis import (
    ROOT,
    latest_backup,
    load_json,
    extract_v1, extract_v2, extract_crypto,
    match_v1, match_v2, match_crypto,
    norm_strategy,
    is_system_forced, is_claude_override,
)

REPORT_PATH = ROOT / "analysis" / "strategies_inventory.md"

# ─────────────────────────────────────────────────────────────────────
# Classification thresholds (tunables)
# ─────────────────────────────────────────────────────────────────────
MINIMAL_MAX_EVENTS      = 5     # < 5 opens AND < 5 closes → "minimal"
ENTRY_ONLY_RATIO        = 0.85  # open_ratio ≥ 0.85 → entry_only (con opens ≥ 5)
EXIT_ONLY_RATIO         = 0.15  # open_ratio ≤ 0.15 → exit_only (con closes ≥ 5)

# Verdict for "complete" strategies (P&L when opener == closer)
VERDICT_KILL_THRESHOLD     = -50.0   # intra_pnl < -$50 → kill
VERDICT_KEEP_THRESHOLD     =   0.0   # intra_pnl > $0 → keep
                                     # (entre los dos: optimize)


# ─────────────────────────────────────────────────────────────────────
# Inventory builder
# ─────────────────────────────────────────────────────────────────────
def _new_strategy_record():
    return {
        "opens_count":            0,
        "closes_count":           0,
        "opens_pnl_sum":          0.0,
        "opens_measurable":       0,
        "opens_wins":             0,
        "closes_pnl_sum":         0.0,
        "closes_measurable":      0,
        "closes_wins":            0,
        "intra_count":            0,
        "intra_pnl_sum":          0.0,
        "cross_count_as_opener":  0,
        "cross_count_as_closer":  0,
        "top_closers":            defaultdict(lambda: {"n": 0, "pnl": 0.0}),
        "top_openers":            defaultdict(lambda: {"n": 0, "pnl": 0.0}),
        "raw_strategies":         set(),
    }


def build_inventory(events, pairs, is_open_fn, is_close_fn):
    """Construye dict[strategy] → record con todas las métricas."""
    inv: dict[str, dict] = defaultdict(_new_strategy_record)

    # 1) Conteo de eventos crudos (incluye opens sin pareo)
    for ev in events:
        raw   = ev.get("strategy", "")
        strat = norm_strategy(raw)
        inv[strat]["raw_strategies"].add(raw)
        if is_open_fn(ev):
            inv[strat]["opens_count"] += 1
        elif is_close_fn(ev):
            inv[strat]["closes_count"] += 1

    # 2) Métricas derivadas de pairs (P&L)
    for p in pairs:
        op  = p["opener_strategy"]
        cl  = p["closer_strategy"]
        pnl = p["pnl"]

        # opener side
        if pnl is not None:
            inv[op]["opens_pnl_sum"]   += pnl
            inv[op]["opens_measurable"] += 1
            if pnl > 0:
                inv[op]["opens_wins"] += 1

        # closer side
        if pnl is not None:
            inv[cl]["closes_pnl_sum"]   += pnl
            inv[cl]["closes_measurable"] += 1
            if pnl > 0:
                inv[cl]["closes_wins"] += 1

        # intra vs cross
        if op == cl:
            inv[op]["intra_count"] += 1
            if pnl is not None:
                inv[op]["intra_pnl_sum"] += pnl
        else:
            inv[op]["cross_count_as_opener"] += 1
            inv[cl]["cross_count_as_closer"] += 1
            # Top counterparts for findings
            inv[op]["top_closers"][cl]["n"]   += 1
            inv[op]["top_closers"][cl]["pnl"] += pnl or 0.0
            inv[cl]["top_openers"][op]["n"]   += 1
            inv[cl]["top_openers"][op]["pnl"] += pnl or 0.0

    return dict(inv)


# ─────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────
def classify(rec: dict) -> str:
    """Devuelve uno de: system, minimal, entry_only, exit_only, complete."""
    # 1) System: any raw label matches forced/claude patterns
    for raw in rec["raw_strategies"]:
        if is_system_forced(raw) or is_claude_override(raw):
            return "system"

    opens  = rec["opens_count"]
    closes = rec["closes_count"]
    total  = opens + closes

    # 2) Minimal: poca data en ambos lados
    if opens < MINIMAL_MAX_EVENTS and closes < MINIMAL_MAX_EVENTS:
        return "minimal"

    # 3) Cálculo de open_ratio
    open_ratio = opens / total if total else 0.0

    if open_ratio >= ENTRY_ONLY_RATIO and opens >= MINIMAL_MAX_EVENTS:
        return "entry_only"
    if open_ratio <= EXIT_ONLY_RATIO and closes >= MINIMAL_MAX_EVENTS:
        return "exit_only"

    return "complete"


def verdict_complete(rec: dict) -> str:
    """Para strategies 'complete', decisión basada en intra P&L."""
    if rec["intra_count"] == 0:
        return "no-intra-data"
    intra_pnl = rec["intra_pnl_sum"]
    if intra_pnl > VERDICT_KEEP_THRESHOLD:
        return "keep"
    if intra_pnl < VERDICT_KILL_THRESHOLD:
        return "kill"
    return "optimize"


# ─────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────
TYPE_EMOJI = {
    "complete":   "🟢",
    "entry_only": "🟡",
    "exit_only":  "🔴",
    "minimal":    "⚪",
    "system":     "⚫",
}

TYPE_ORDER = ["complete", "entry_only", "exit_only", "minimal", "system"]


def fmt_pnl(x: float) -> str:
    return f"${x:+,.2f}"


def open_ratio(rec):
    total = rec["opens_count"] + rec["closes_count"]
    return rec["opens_count"] / total if total else 0.0


def avg_pnl(sum_, n):
    return sum_ / n if n else 0.0


def winrate(wins, n):
    return wins / n * 100 if n else 0.0


def top_counterparts(d, k=3):
    """Devuelve los top-k (name, n, pnl) ordenados por n descendiente."""
    items = sorted(d.items(), key=lambda kv: -kv[1]["n"])[:k]
    return [(name, info["n"], info["pnl"]) for name, info in items]


# ─────────────────────────────────────────────────────────────────────
# Markdown rendering per bot
# ─────────────────────────────────────────────────────────────────────
def render_table(inv_with_type):
    """Tabla principal: una fila por strategy ordenada por (type, opens DESC)."""
    rows = sorted(
        inv_with_type.items(),
        key=lambda kv: (TYPE_ORDER.index(kv[1]["type"]), -kv[1]["opens_count"]),
    )
    out = [
        "| Strategy | Type | Opens | Closes | P&L (opener) | P&L (closer) | Intra | Open ratio |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s, rec in rows:
        emoji  = TYPE_EMOJI[rec["type"]]
        opens  = rec["opens_count"]
        closes = rec["closes_count"]
        opnl   = fmt_pnl(rec["opens_pnl_sum"])
        cpnl   = fmt_pnl(rec["closes_pnl_sum"])
        intra  = f"{rec['intra_count']}({fmt_pnl(rec['intra_pnl_sum'])})"
        oratio = f"{open_ratio(rec):.2f}"
        out.append(
            f"| `{s}` | {emoji} {rec['type']} | {opens} | {closes} | {opnl} | {cpnl} | {intra} | {oratio} |"
        )
    return "\n".join(out)


def render_entry_only_findings(inv_with_type):
    items = sorted(
        ((s, r) for s, r in inv_with_type.items() if r["type"] == "entry_only"),
        key=lambda kv: -kv[1]["opens_count"],
    )
    if not items:
        return "_(ninguna)_"
    out = []
    for s, r in items:
        out.append(f"#### `{s}`")
        out.append(f"- Volumen: **{r['opens_count']} opens** · P&L (como opener): {fmt_pnl(r['opens_pnl_sum'])} · "
                   f"win rate: {winrate(r['opens_wins'], r['opens_measurable']):.0f}% ({r['opens_measurable']}/{r['opens_count']} medibles)")
        out.append(f"- Cuándo abre: _TODO: investigar código de la strategy_")
        closers = top_counterparts(r["top_closers"], k=3)
        if closers:
            out.append("- Quién la cierra hoy (top 3):")
            for name, n, pnl in closers:
                out.append(f"  - `{name}`: {n} cierres ({fmt_pnl(pnl)})")
        else:
            out.append("- Quién la cierra hoy: _(sin cierres cross registrados)_")
        out.append("- Propuesta de SELL signal: _TODO: pendiente revisión humana_")
        out.append("")
    return "\n".join(out)


def render_exit_only_findings(inv_with_type):
    items = sorted(
        ((s, r) for s, r in inv_with_type.items() if r["type"] == "exit_only"),
        key=lambda kv: -kv[1]["closes_count"],
    )
    if not items:
        return "_(ninguna)_"
    out = []
    for s, r in items:
        out.append(f"#### `{s}`")
        out.append(f"- Volumen: **{r['closes_count']} closes** · P&L (como closer): {fmt_pnl(r['closes_pnl_sum'])} · "
                   f"win rate: {winrate(r['closes_wins'], r['closes_measurable']):.0f}% ({r['closes_measurable']}/{r['closes_count']} medibles)")
        out.append(f"- Cuándo cierra: _TODO: investigar código de la strategy_")
        openers = top_counterparts(r["top_openers"], k=3)
        if openers:
            out.append("- Qué cierra hoy (top 3 openers):")
            for name, n, pnl in openers:
                out.append(f"  - `{name}`: {n} pairs ({fmt_pnl(pnl)})")
        else:
            out.append("- Qué cierra hoy: _(sin opens cross registrados)_")
        out.append("- Propuesta de BUY signal: _TODO: pendiente revisión humana_")
        out.append("")
    return "\n".join(out)


def render_complete_findings(inv_with_type):
    items = sorted(
        ((s, r) for s, r in inv_with_type.items() if r["type"] == "complete"),
        key=lambda kv: -kv[1]["opens_count"],
    )
    if not items:
        return "_(ninguna)_"
    out = []
    for s, r in items:
        v = verdict_complete(r)
        out.append(f"#### `{s}` — verdict: **{v}**")
        out.append(f"- Opens: {r['opens_count']} · P&L (opener): {fmt_pnl(r['opens_pnl_sum'])} "
                   f"· avg {fmt_pnl(avg_pnl(r['opens_pnl_sum'], r['opens_measurable']))}")
        out.append(f"- Closes: {r['closes_count']} · P&L (closer): {fmt_pnl(r['closes_pnl_sum'])} "
                   f"· avg {fmt_pnl(avg_pnl(r['closes_pnl_sum'], r['closes_measurable']))}")
        out.append(f"- Intra (opener=closer): {r['intra_count']} pairs · P&L: {fmt_pnl(r['intra_pnl_sum'])}")
        out.append(f"- Cross: {r['cross_count_as_opener']} como opener · {r['cross_count_as_closer']} como closer")
        out.append("")
    return "\n".join(out)


def render_forced_distribution(events, pairs, is_close_fn):
    """Distribución de cierres por categoría: system / claude / empty / regular."""
    sys_n    = sum(1 for p in pairs if is_system_forced(p["closer_strategy_raw"]))
    claude_n = sum(1 for p in pairs if is_claude_override(p["closer_strategy_raw"]))
    empty_n  = sum(1 for p in pairs if not (p["closer_strategy_raw"] or "").strip())
    return (
        f"- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close): **{sys_n}**\n"
        f"- Claude override (claude_bot / claude_high / claude_medium): **{claude_n}**\n"
        f"- `<empty>` closer (strategy field vacío): **{empty_n}**\n"
        f"- Total pairs: **{len(pairs)}**"
    )


def bot_section(bot_label, inv_with_type, events, pairs, is_close_fn):
    counts = defaultdict(int)
    for r in inv_with_type.values():
        counts[r["type"]] += 1

    out = [f"## Bot: {bot_label}\n"]
    out.append("### Strategies clasificadas\n")
    out.append(render_table(inv_with_type) + "\n")
    out.append("### Findings por type\n")
    out.append("#### Entry-only (necesitan SELL signal)\n")
    out.append(render_entry_only_findings(inv_with_type) + "\n")
    out.append("#### Exit-only (necesitan BUY signal)\n")
    out.append(render_exit_only_findings(inv_with_type) + "\n")
    out.append("#### Complete (revisar performance)\n")
    out.append(render_complete_findings(inv_with_type) + "\n")
    out.append("### Distribución de cierres forzados\n")
    out.append(render_forced_distribution(events, pairs, is_close_fn) + "\n")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# Executive summary (3-column table V1 / V2 / Crypto)
# ─────────────────────────────────────────────────────────────────────
def render_executive_summary(invs_by_bot):
    rows = [
        ("Total strategies únicas", lambda inv: len(inv)),
        ("🟢 Complete",             lambda inv: sum(1 for r in inv.values() if r["type"] == "complete")),
        ("🟡 Entry-only",           lambda inv: sum(1 for r in inv.values() if r["type"] == "entry_only")),
        ("🔴 Exit-only",            lambda inv: sum(1 for r in inv.values() if r["type"] == "exit_only")),
        ("⚪ Minimal",              lambda inv: sum(1 for r in inv.values() if r["type"] == "minimal")),
        ("⚫ System (no son strats)", lambda inv: sum(1 for r in inv.values() if r["type"] == "system")),
    ]
    out = [
        "| Métrica | V1 Stock | V2 Options | Crypto |",
        "|---|---|---|---|",
    ]
    for label, fn in rows:
        out.append(f"| {label} | {fn(invs_by_bot['V1 Stock'])} | {fn(invs_by_bot['V2 Options'])} | {fn(invs_by_bot['Crypto'])} |")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    backup = latest_backup()
    print(f"[INFO] Backup: {backup}")

    v1_raw     = load_json(backup / "eolo-trades.json")
    v2_raw     = load_json(backup / "eolo-options-trades.json")
    crypto_raw = load_json(backup / "eolo-crypto-trades.json")

    v1_events     = extract_v1(v1_raw)
    v2_events     = extract_v2(v2_raw)
    crypto_events = extract_crypto(crypto_raw)

    v1_pairs,     _ = match_v1(v1_events)
    v2_pairs,     _ = match_v2(v2_events)
    crypto_pairs, _ = match_crypto(crypto_events)

    # is_open_fn / is_close_fn por bot
    fns = {
        "V1 Stock":   (lambda e: e.get("action") == "BUY",          lambda e: e.get("action") == "SELL"),
        "V2 Options": (lambda e: e.get("action") == "BUY_TO_OPEN",  lambda e: e.get("action") == "SELL_TO_CLOSE"),
        "Crypto":     (lambda e: e.get("action") == "BUY",          lambda e: e.get("action") == "SELL"),
    }

    invs_by_bot: dict[str, dict] = {}
    raw_by_bot = {
        "V1 Stock":   (v1_events, v1_pairs),
        "V2 Options": (v2_events, v2_pairs),
        "Crypto":     (crypto_events, crypto_pairs),
    }

    for bot, (events, pairs) in raw_by_bot.items():
        is_open, is_close = fns[bot]
        inv = build_inventory(events, pairs, is_open, is_close)
        for s, rec in inv.items():
            rec["type"] = classify(rec)
        invs_by_bot[bot] = inv
        print(f"[INFO] {bot}: {len(inv)} strategies")

    # Render
    md = [
        "# Inventario de Strategies — Pure Isolation Readiness Assessment",
        f"_Generado: {datetime.now().isoformat(timespec='seconds')}_  ",
        f"_Backup: `{backup.relative_to(ROOT)}`_\n",
        "Para cada strategy detectada en V1 / V2 / Crypto: conteos de eventos, P&L como opener vs closer, "
        "métricas cruzadas y clasificación automática (complete / entry_only / exit_only / minimal / system).",
        "",
        "Reusa el pipeline FIFO de `dual_attribution_analysis.py`. Las strategies se normalizan quitando el prefijo "
        "`consensus:` (crypto). La clasificación `system` se aplica si CUALQUIERA de las raw strategies asociadas "
        "matchea con patrones de cierre forzado (`RISK_WATCHDOG`, `CLOSE_ALL`, `SL/TP`, `auto_close`, `claude_*`).\n",
        "## Resumen Ejecutivo\n",
        render_executive_summary(invs_by_bot),
        "",
        "---\n",
    ]

    for bot in ("V1 Stock", "V2 Options", "Crypto"):
        events, pairs = raw_by_bot[bot]
        _, is_close = fns[bot]
        md.append(bot_section(bot, invs_by_bot[bot], events, pairs, is_close))
        md.append("---\n")

    REPORT_PATH.write_text("\n".join(md))
    print(f"[OK] Reporte → {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
