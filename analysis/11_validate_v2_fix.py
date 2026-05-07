"""
11_validate_v2_fix.py — Valida que Bug 2 fix está activo en producción.

Lee Firestore y clasifica SELL_TO_CLOSE post-deploy en 3 buckets según
si el BUY_TO_OPEN correspondiente fue pre- o post-deploy.

Collections: eolo-options-trades (V2) y eolo-crop-trades (CROP).

Uso:
  python3 analysis/11_validate_v2_fix.py
  python3 analysis/11_validate_v2_fix.py --bot v2
  python3 analysis/11_validate_v2_fix.py --bot crop
  python3 analysis/11_validate_v2_fix.py --cutoff-v2 "2026-05-06 21:25:00" \\
                                          --cutoff-crop "2026-05-06 21:35:00"
"""
import argparse
from collections import Counter
from datetime import datetime

from google.cloud import firestore

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT = "eolo-schwab-agent"

# Real deploy timestamps UTC (from Cloud Build logs)
CUTOFF_V2_DEFAULT   = "2026-05-06 21:25:00"
CUTOFF_CROP_DEFAULT = "2026-05-06 21:35:00"

BOTS = {
    "v2":   {"collection": "eolo-options-trades", "revision": "eolo-bot-v2-00018-5c9"},
    "crop": {"collection": "eolo-crop-trades",    "revision": "eolo-bot-crop-00023-dkb"},
}

# ── CLI ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--cutoff-v2",   default=CUTOFF_V2_DEFAULT,
                    help=f'Deploy UTC para V2.   Default: "{CUTOFF_V2_DEFAULT}"')
parser.add_argument("--cutoff-crop", default=CUTOFF_CROP_DEFAULT,
                    help=f'Deploy UTC para CROP. Default: "{CUTOFF_CROP_DEFAULT}"')
parser.add_argument("--bot", choices=["v2", "crop", "both"], default="both",
                    help="Qué bot analizar (default: both)")
args = parser.parse_args()

CUTOFFS = {
    "v2":   args.cutoff_v2,
    "crop": args.cutoff_crop,
}

# ── Firestore ────────────────────────────────────────────────────────────────
db = firestore.Client(project=PROJECT)


# ── Helpers ──────────────────────────────────────────────────────────────────
def p(*a):
    print(" ".join(str(x) for x in a))

def sep():
    p("═" * 52)


def _position_key(val: dict) -> tuple | None:
    """Clave canónica de posición para match BUY↔SELL."""
    ticker  = val.get("ticker")
    exp     = val.get("expiration")
    strike  = val.get("strike")
    opttype = val.get("option_type")
    if None in (ticker, exp, strike, opttype):
        return None
    return (ticker, exp, strike, opttype)


# ── Core analysis ────────────────────────────────────────────────────────────
def analyze_bot(label: str, config: dict, cutoff_str: str) -> None:
    col_name = config["collection"]
    revision = config["revision"]
    cutoff_date = cutoff_str[:10]

    # Load ALL docs (keyed by date "YYYY-MM-DD"); need pre-deploy data for match.
    p(f"  Cargando {col_name}…")
    all_docs = {d.id: d.to_dict() for d in db.collection(col_name).stream()}

    # ── Pass 1: index every BUY_TO_OPEN by order_id and position key ─────────
    # buy_by_order[order_id]   = {"ts": "...", "strategy": "..."}
    # buy_by_pos[pos_key]      = list of {"ts": "...", "strategy": "..."}
    buy_by_order: dict = {}
    buy_by_pos:   dict = {}

    for doc_id, dd in all_docs.items():
        for field_key, val in dd.items():
            if not isinstance(val, dict):
                continue
            if "BUY_TO_OPEN" not in field_key:
                continue
            ts      = field_key[:19]
            oid     = val.get("order_id", "")
            entry   = {"ts": ts, "strategy": val.get("strategy", "")}
            if oid:
                buy_by_order[oid] = entry
            pos_key = _position_key(val)
            if pos_key:
                buy_by_pos.setdefault(pos_key, []).append(entry)

    # ── Pass 2: classify SELL_TO_CLOSE post-deploy ───────────────────────────
    # Buckets:
    #   post_fix  — BUY_TO_OPEN was after cutoff  → strategy MUST be populated
    #   pre_fix   — BUY_TO_OPEN was before cutoff → strategy="" is expected
    #   no_match  — no BUY_TO_OPEN found          → anomalous

    post_fix_ok:   list = []   # POST-FIX OPEN, strategy populated ✓
    post_fix_fail: list = []   # POST-FIX OPEN, strategy empty     ✗
    pre_fix:       list = []   # PRE-FIX OPEN  (expected empty)
    no_match:      list = []   # no BUY found
    strat_dist: Counter = Counter()

    relevant_dates = [d for d in all_docs if d >= cutoff_date]

    for doc_id in relevant_dates:
        dd = all_docs[doc_id]
        for field_key, val in dd.items():
            if not isinstance(val, dict):
                continue
            if "SELL_TO_CLOSE" not in field_key:
                continue
            ts = field_key[:19]
            if ts < cutoff_str:
                continue

            strategy = (val.get("strategy") or "").strip().lower()
            oid      = val.get("order_id", "")
            pos_key  = _position_key(val)

            # Find the matching BUY_TO_OPEN
            buy_entry = None
            if oid and oid in buy_by_order:
                buy_entry = buy_by_order[oid]
            elif pos_key and pos_key in buy_by_pos:
                # Pick the most recent BUY before this SELL
                candidates = [b for b in buy_by_pos[pos_key] if b["ts"] <= ts]
                if candidates:
                    buy_entry = max(candidates, key=lambda b: b["ts"])

            record = {
                "ts":          ts,
                "ticker":      val.get("ticker", "?"),
                "strategy":    strategy,
                "exit_reason": str(val.get("exit_reason", val.get("reason", "")))[:60],
                "buy_ts":      buy_entry["ts"] if buy_entry else None,
            }

            if buy_entry is None:
                no_match.append(record)
            elif buy_entry["ts"] >= cutoff_str:
                if strategy:
                    post_fix_ok.append(record)
                    strat_dist[strategy] += 1
                else:
                    post_fix_fail.append(record)
            else:
                pre_fix.append(record)

    # ── Verdict — based only on POST-FIX OPEN bucket ─────────────────────────
    n_post = len(post_fix_ok) + len(post_fix_fail)
    if n_post == 0:
        verdict = "⏳ SIN DATOS: no hay cierres de posiciones abiertas post-deploy"
    elif not post_fix_fail:
        verdict = "✓ FIX VALIDADO: todos los cierres post-fix tienen strategy"
    else:
        verdict = f"✗ FIX FALLA: {len(post_fix_fail)} cierre(s) post-fix sin strategy"

    pct = f"{len(post_fix_ok) / n_post * 100:.0f}%" if n_post else "—"

    p()
    p(f"{label} ({revision}):")
    p(f"  Cutoff deploy:       {cutoff_str} UTC")
    p()
    p(f"  SELL_TO_CLOSE post-deploy: {n_post + len(pre_fix) + len(no_match)}")
    p(f"    POST-FIX OPEN:     {n_post}  (BUY >= cutoff → estrategia obligatoria)")
    p(f"      Con strategy:    {len(post_fix_ok)} ({pct})")
    p(f"      Sin strategy:    {len(post_fix_fail)}  ← falla del fix si > 0")
    p(f"    PRE-FIX OPEN:      {len(pre_fix)}  (BUY < cutoff → strategy='' esperado)")
    p(f"    SIN MATCH:         {len(no_match)}")
    p()
    p(f"  Verdict: {verdict}")

    if strat_dist:
        p("  Top strategies (POST-FIX OPEN):")
        for strat, cnt in strat_dist.most_common(5):
            p(f"    - {strat}: {cnt}")

    if post_fix_fail:
        p(f"  POST-FIX OPEN sin strategy ({min(len(post_fix_fail), 5)} ejemplos):")
        for ex in post_fix_fail[:5]:
            p(f"    [{ex['ts']}] {ex['ticker']} | buy_ts: {ex['buy_ts']} | exit: {ex['exit_reason']}")

    if no_match:
        p(f"  SIN MATCH ({min(len(no_match), 3)} ejemplos):")
        for ex in no_match[:3]:
            p(f"    [{ex['ts']}] {ex['ticker']} | strategy: '{ex['strategy']}'")


# ── Main ─────────────────────────────────────────────────────────────────────
sep()
p(f"Validación Bug 2 Fix — Post-deploy 2026-05-06")
sep()

bots_to_run = ["v2", "crop"] if args.bot == "both" else [args.bot]
for bot_key in bots_to_run:
    analyze_bot(bot_key.upper(), BOTS[bot_key], CUTOFFS[bot_key])

p()
sep()
p(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local")
