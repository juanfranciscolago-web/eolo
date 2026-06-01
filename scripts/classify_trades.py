#!/usr/bin/env python3
"""classify_trades.py — Auto-classifier de trades históricos para curation GOLD/SILVER/BRONZE.

Lee Firestore eolo-crop-trades, filtra UUID docs (skip day-doc legacy de pre-Sprint 9),
asigna un quality_score 0-100 basado en heuristics, y rankea candidates para curation
manual posterior a casos del KB.

Uso:
    cd ~/PycharmProjects/eolo
    python3 scripts/classify_trades.py
    python3 scripts/classify_trades.py --days 90 --min-score 70 --top 30
    python3 scripts/classify_trades.py --output-json /tmp/candidates.json --output-md /tmp/candidates.md

Output:
    1. STDOUT: tabla ranked con top N candidates
    2. Opcional: JSON con todos los trades scored
    3. Opcional: Markdown report con detalle de TOP candidates para curation manual

Heurística scoring (0-100):

    Componente              | Peso | Cálculo
    ------------------------|------|--------------------------------------
    PnL pct                 | 40   | Lineal: 0% → 0, 80%+ → 40 puntos
    Time efficiency         | 20   | hours_held / expected_hours, mejor < 50%
    Exit clean (no panic)   | 15   | TAKE_PROFIT_* = 15, STOP_LOSS = 0, EOD = 8
    LLM-driven (no fallback)| 10   | LLM_SONNET_CONSULT = 10, LLM_HAIKU_PASS = 7, FALLBACK = 0
    Decision_meta completo  | 10   | tacit_rules_applied + main_reason + confidence presentes
    Confidence apropiada    | 5    | Si confidence > 6 Y pnl_pct > 0, bonus

    Total: 0-100. Cutoffs sugeridos:
        ≥75 → GOLD candidate
        50-74 → SILVER candidate
        <50 → BRONZE (lecciones de qué NO hacer)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Expected average hours per DTE (heuristic based on Theta Harvest):
# DTE 0 → expected 4h (intraday)
# DTE 1-2 → expected 8h
# DTE 3-4 → expected 16h
_EXPECTED_HOURS_BY_DTE = {0: 4, 1: 8, 2: 8, 3: 16, 4: 16}


@dataclass
class ScoredTrade:
    trade_id: str
    ticker: str
    timestamp_open: str
    timestamp_close: Optional[str]
    decision_source: str
    verdict: str
    confidence: int
    main_reason: str
    tacit_rules_applied: list[str]
    pnl_pct: Optional[float]
    pnl_usd: Optional[float]
    hours_held: Optional[float]
    exit_reason: Optional[str]
    dte: Optional[int]
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)
    classification: str = "BRONZE"  # GOLD / SILVER / BRONZE


def _score_pnl(pnl_pct: Optional[float]) -> int:
    """0-40 puntos. Lineal: 0% → 0, 80%+ → 40."""
    if pnl_pct is None:
        return 0
    if pnl_pct <= 0:
        return 0
    return min(int(pnl_pct / 80 * 40), 40)


def _score_time_efficiency(
    hours_held: Optional[float], dte: Optional[int]
) -> int:
    """0-20 puntos. Cuanto antes captura, mejor."""
    if hours_held is None or dte is None:
        return 0
    expected = _EXPECTED_HOURS_BY_DTE.get(dte, 8)
    if expected <= 0:
        return 10
    ratio = hours_held / expected
    if ratio < 0.3:
        return 20
    if ratio < 0.5:
        return 15
    if ratio < 0.7:
        return 10
    if ratio < 1.0:
        return 5
    return 0


def _score_exit_clean(exit_reason: Optional[str]) -> int:
    """0-15 puntos. Cómo se cerró."""
    if not exit_reason:
        return 5
    reason = exit_reason.upper()
    if reason.startswith("TAKE_PROFIT") or reason.startswith("PROFIT_TARGET"):
        return 15
    if reason in ("EOD_CLOSE", "TIME_STOP", "EOD"):
        return 8
    if reason.startswith("STOP_LOSS"):
        return 0
    return 5  # unknown / other


def _score_decision_source(source: Optional[str]) -> int:
    """0-10 puntos. LLM-driven mejor que fallback."""
    if not source:
        return 0
    s = source.upper()
    if "SONNET_CONSULT" in s:
        return 10
    if "HAIKU_PASS" in s:
        return 7
    if "HAIKU_SKIP" in s:
        return 5
    if s == "RULE_BASED":
        return 3
    if "FALLBACK" in s:
        return 0
    return 5


def _score_meta_completeness(
    tacit_rules: list, main_reason: str, confidence: int
) -> int:
    """0-10 puntos. Decision_meta poblado completo = útil para curation."""
    score = 0
    if tacit_rules and len(tacit_rules) > 0:
        score += 4
    if main_reason and len(main_reason) > 20:
        score += 4
    if confidence > 0:
        score += 2
    return min(score, 10)


def _score_confidence_apropiada(
    confidence: int, pnl_pct: Optional[float]
) -> int:
    """0-5 puntos bonus. Confidence alta + outcome positivo = calibración OK."""
    if pnl_pct is None or pnl_pct <= 0:
        return 0
    if confidence >= 7:
        return 5
    if confidence >= 5:
        return 2
    return 0


def _classify(score: int) -> str:
    if score >= 75:
        return "GOLD"
    if score >= 50:
        return "SILVER"
    return "BRONZE"


def _score_trade(doc_id: str, data: dict) -> ScoredTrade:
    dm = data.get("decision_meta") or {}
    outcome = data.get("outcome") or {}
    setup = data.get("setup") or {}

    confidence = int(dm.get("confidence") or 0)
    tacit_rules = dm.get("tacit_rules_applied") or []
    if not isinstance(tacit_rules, list):
        tacit_rules = []
    main_reason = str(dm.get("main_reason") or "")
    pnl_pct = outcome.get("pnl_pct")
    pnl_usd = outcome.get("pnl_usd")
    hours_held = outcome.get("hours_held")
    exit_reason = outcome.get("exit_reason")
    dte = setup.get("dte")

    try:
        pnl_pct_f = float(pnl_pct) if pnl_pct is not None else None
    except (TypeError, ValueError):
        pnl_pct_f = None
    try:
        pnl_usd_f = float(pnl_usd) if pnl_usd is not None else None
    except (TypeError, ValueError):
        pnl_usd_f = None
    try:
        hours_held_f = float(hours_held) if hours_held is not None else None
    except (TypeError, ValueError):
        hours_held_f = None
    try:
        dte_i = int(dte) if dte is not None else None
    except (TypeError, ValueError):
        dte_i = None

    score_breakdown = {
        "pnl": _score_pnl(pnl_pct_f),
        "time_efficiency": _score_time_efficiency(hours_held_f, dte_i),
        "exit_clean": _score_exit_clean(exit_reason),
        "decision_source": _score_decision_source(data.get("decision_source")),
        "meta_completeness": _score_meta_completeness(
            tacit_rules, main_reason, confidence
        ),
        "confidence_apropiada": _score_confidence_apropiada(
            confidence, pnl_pct_f
        ),
    }
    total = sum(score_breakdown.values())
    classification = _classify(total)

    return ScoredTrade(
        trade_id=doc_id,
        ticker=str(data.get("ticker") or "?"),
        timestamp_open=str(data.get("timestamp_open") or ""),
        timestamp_close=data.get("timestamp_close"),
        decision_source=str(data.get("decision_source") or "?"),
        verdict=str(dm.get("verdict") or "?"),
        confidence=confidence,
        main_reason=main_reason,
        tacit_rules_applied=tacit_rules,
        pnl_pct=pnl_pct_f,
        pnl_usd=pnl_usd_f,
        hours_held=hours_held_f,
        exit_reason=exit_reason,
        dte=dte_i,
        score=total,
        score_breakdown=score_breakdown,
        classification=classification,
    )


def _fetch_trades(project_id: str, days: int) -> list[tuple[str, dict]]:
    """Yield (doc_id, doc_data) para trades en últimos `days` días."""
    from google.cloud import firestore as _fs

    db = _fs.Client(project=project_id)
    coll = db.collection("eolo-crop-trades")

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff_dt.isoformat()

    # Query: timestamp_open >= cutoff (orden desc)
    # Buffer x3 para filtrar legacy doc-ids
    query = (
        coll
        .where(filter=_fs.FieldFilter("timestamp_open", ">=", cutoff_iso))
        .order_by("timestamp_open", direction=_fs.Query.DESCENDING)
        .limit(10000)  # safety cap
    )

    result = []
    for doc in query.stream():
        if not _UUID_RE.match(doc.id):
            continue  # skip day-doc legacy
        data = doc.to_dict() or {}
        # Solo trades CLOSED (con outcome) son scoreables
        if data.get("timestamp_close") is None:
            continue
        if not data.get("outcome"):
            continue
        result.append((doc.id, data))
    return result


def _render_table(trades: list[ScoredTrade], top_n: int) -> str:
    """Tabla ASCII ranked para STDOUT."""
    lines = []
    lines.append("")
    lines.append(f"{'='*108}")
    lines.append(f"TOP {top_n} candidates by score (de {len(trades)} closed UUID trades)")
    lines.append(f"{'='*108}")
    lines.append(
        f"{'Score':>5} {'Class':>7} {'Ticker':>6} {'Date':>10} {'Verdict':>10} "
        f"{'PnL%':>6} {'Conf':>4} {'Source':<22} {'Rules':<5}"
    )
    lines.append("-" * 108)
    for t in trades[:top_n]:
        date_short = (t.timestamp_open or "")[:10]
        rules_count = len(t.tacit_rules_applied)
        pnl_str = f"{t.pnl_pct:.1f}" if t.pnl_pct is not None else "-"
        lines.append(
            f"{t.score:>5} {t.classification:>7} {t.ticker:>6} {date_short:>10} "
            f"{t.verdict[:10]:>10} {pnl_str:>6} {t.confidence:>4} "
            f"{t.decision_source[:22]:<22} {rules_count:>5}"
        )
    lines.append("")
    return "\n".join(lines)


def _render_summary(trades: list[ScoredTrade]) -> str:
    classifications = Counter(t.classification for t in trades)
    n = len(trades) or 1
    lines = []
    lines.append("")
    lines.append("─" * 60)
    lines.append(f"Total closed UUID trades scored: {len(trades)}")
    for cls in ("GOLD", "SILVER", "BRONZE"):
        c = classifications.get(cls, 0)
        pct = c / n * 100
        lines.append(f"  {cls:>6}: {c:>4} ({pct:5.1f}%)")
    lines.append("─" * 60)
    return "\n".join(lines)


def _render_md_report(trades: list[ScoredTrade], top_n: int) -> str:
    """Markdown detallado para curation manual de TOP candidates."""
    lines = [
        f"# Trade Curation Candidates — Top {top_n}",
        f"",
        f"**Generado:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Total trades evaluados:** {len(trades)} closed UUID trades",
        f"",
        f"Heurística scoring (0-100):",
        f"- PnL (40 pts): 0% → 0, 80%+ → 40",
        f"- Time efficiency (20 pts): hours_held / expected_hours, mejor <50%",
        f"- Exit clean (15 pts): TAKE_PROFIT_* = 15, STOP_LOSS = 0, EOD = 8",
        f"- LLM-driven (10 pts): SONNET_CONSULT = 10, HAIKU = 5-7, FALLBACK = 0",
        f"- Meta completeness (10 pts): tacit_rules + main_reason + confidence",
        f"- Confidence calibration (5 pts): confidence > 7 con pnl > 0 = bonus",
        f"",
        f"Cutoffs:",
        f"- **≥75 → GOLD candidate** (curar prioritariamente)",
        f"- 50-74 → SILVER candidate",
        f"- <50 → BRONZE (lecciones de qué NO hacer)",
        f"",
        f"---",
        f"",
    ]
    for rank, t in enumerate(trades[:top_n], start=1):
        lines.append(f"## #{rank}. {t.classification} — Score {t.score}/100 — {t.ticker} {t.verdict}")
        lines.append("")
        lines.append(f"- **Trade ID:** `{t.trade_id}`")
        lines.append(f"- **Opened:** {t.timestamp_open}")
        lines.append(f"- **Closed:** {t.timestamp_close or '(OPEN)'}")
        lines.append(f"- **Decision source:** {t.decision_source}")
        lines.append(f"- **Confidence:** {t.confidence}/10")
        lines.append(f"- **PnL:** {t.pnl_pct}% (${t.pnl_usd})" if t.pnl_pct is not None else "- **PnL:** -")
        lines.append(f"- **Hours held:** {t.hours_held}" if t.hours_held is not None else "- **Hours held:** -")
        lines.append(f"- **Exit reason:** {t.exit_reason or '-'}")
        lines.append(f"- **DTE:** {t.dte if t.dte is not None else '-'}")
        lines.append("")
        lines.append(f"**Main reason:**")
        lines.append(f"> {t.main_reason or '(empty)'}")
        lines.append("")
        if t.tacit_rules_applied:
            lines.append(f"**Tacit rules applied ({len(t.tacit_rules_applied)}):**")
            for r in t.tacit_rules_applied:
                lines.append(f"- `{r}`")
            lines.append("")
        lines.append(f"**Score breakdown:**")
        for k, v in t.score_breakdown.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append(f"**Curator notes:** _Llenar aquí si este caso amerita curar como GOLD case en KB v1.3+_")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent"))
    p.add_argument("--days", type=int, default=30, help="Trade lookback window (default 30d)")
    p.add_argument("--min-score", type=int, default=0, help="Filter trades with score >= this (default 0)")
    p.add_argument("--top", type=int, default=20, help="Top N to show in summary table + MD report (default 20)")
    p.add_argument("--output-json", type=str, default=None, help="Write all scored trades as JSON to this path")
    p.add_argument("--output-md", type=str, default=None, help="Write Markdown report with TOP candidates")
    args = p.parse_args()

    print(f"[classify_trades] Fetching trades from Firestore (project={args.project}, days={args.days})...")
    try:
        raw_trades = _fetch_trades(args.project, args.days)
    except ImportError:
        print("ERROR: google.cloud.firestore not installed. Run: pip3 install --user google-cloud-firestore", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR fetching trades: {e}", file=sys.stderr)
        return 3

    print(f"[classify_trades] Fetched {len(raw_trades)} closed UUID trades")
    if not raw_trades:
        print("[classify_trades] No trades to score. Exiting.")
        return 0

    scored = [_score_trade(doc_id, data) for doc_id, data in raw_trades]
    scored.sort(key=lambda t: t.score, reverse=True)

    # Filter min-score
    filtered = [t for t in scored if t.score >= args.min_score]

    print(_render_summary(scored))
    print(_render_table(filtered, args.top))

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump([asdict(t) for t in scored], f, indent=2, default=str)
        print(f"[classify_trades] Wrote {len(scored)} scored trades to {args.output_json}")

    if args.output_md:
        with open(args.output_md, "w") as f:
            f.write(_render_md_report(filtered, args.top))
        print(f"[classify_trades] Wrote TOP {args.top} markdown report to {args.output_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
