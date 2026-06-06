#!/usr/bin/env python3
"""
V1 RETEST EVALUATE — veredicto CI95 del cohort RETEST_V1 (metodología Master Recap).

Corre en la Mac de Juan. Lee eolo-trades desde el corte del cohort, calcula
expectancy + CI95 (bootstrap 1000 resamples) + win rate por estrategia, y
emite un veredicto por estrategia. Escribe docs/V1_RETEST_RESULTS_<fecha>.md
y un JSON paralelo que la tarea de Claude lee y resume.

Veredictos:
  KEEP          — CI95 low > 0: edge positivo confirmado con datos sanos
  DISABLE       — CI95 high < 0: pérdida confirmada (re-apagar con evidencia)
  INCONCLUSIVE  — CI95 cruza 0: extender o restringir por ticker
  INSUFFICIENT  — n < n_target: extender ventana, no concluir

Uso:
    python3 tools/v1_retest_evaluate.py
"""
from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = "eolo-schwab-agent"
ROOT    = Path(__file__).resolve().parent.parent
OUTDIR  = ROOT / "docs"
STATUS  = ROOT / "docs" / "retest_status"
EXCLUDE_STRATS = {"", "TEST", "CLOSE_ALL", "RISK_WATCHDOG", None}


def compute(pnls: list[float]) -> dict:
    arr = np.array(pnls, dtype=float)
    n = len(arr)
    wins = arr[arr > 0]
    wr = len(wins) / n * 100 if n else 0.0
    exp = float(arr.mean()) if n else 0.0
    if n >= 5:
        random.seed(42)
        boot = np.array([np.mean(np.random.choice(arr, size=n, replace=True)) for _ in range(1000)])
        lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    else:
        lo = hi = float("nan")
    return {"n": n, "win_rate": round(wr, 1), "expectancy": round(exp, 2),
            "ci95_low": round(lo, 2) if not np.isnan(lo) else None,
            "ci95_high": round(hi, 2) if not np.isnan(hi) else None,
            "total_pnl": round(float(arr.sum()), 2) if n else 0.0}


def verdict(m: dict, n_target: int) -> str:
    if m["n"] < n_target:
        return "INSUFFICIENT"
    if m["ci95_low"] is not None and m["ci95_low"] > 0:
        return "KEEP"
    if m["ci95_high"] is not None and m["ci95_high"] < 0:
        return "DISABLE"
    return "INCONCLUSIVE"


def main() -> int:
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    now = datetime.now(timezone.utc)

    cohort = (db.collection("eolo-config").document("retest_v1").get().to_dict() or {})
    cutoff = (cohort.get("cutoff_utc") or "2026-06-04")[:10]
    n_target = int(cohort.get("n_target", 30))

    by_strat: dict[str, list[float]] = defaultdict(list)
    for doc in db.collection("eolo-trades").stream():
        if doc.id < cutoff:
            continue
        for v in (doc.to_dict() or {}).values():
            if not isinstance(v, dict):
                continue
            if v.get("action") not in ("SELL", "BUY_TO_COVER"):
                continue
            strat = v.get("strategy")
            if strat in EXCLUDE_STRATS:
                continue
            pnl = v.get("pnl_usd")
            if pnl is None:
                continue
            try:
                by_strat[strat].append(float(pnl))
            except (TypeError, ValueError):
                pass

    rows = []
    for strat, pnls in by_strat.items():
        m = compute(pnls)
        m["strategy"] = strat
        m["verdict"] = verdict(m, n_target)
        rows.append(m)
    rows.sort(key=lambda r: (-r["n"]))

    counts = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1

    stamp = now.strftime("%Y%m%d")
    result = {"generated_utc": now.isoformat(), "cohort": cohort.get("cohort"),
              "cutoff": cutoff, "n_target": n_target,
              "verdict_counts": dict(counts), "rows": rows}
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / "evaluation_latest.json").write_text(json.dumps(result, indent=2, default=str))

    # CSV
    csv_path = OUTDIR / f"V1_RETEST_RESULTS_{stamp}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["strategy", "verdict", "n", "win_rate",
                                          "expectancy", "ci95_low", "ci95_high", "total_pnl"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    # Markdown
    md = [f"# V1 RETEST — Resultados {now.strftime('%Y-%m-%d')}",
          "",
          f"**Cohort:** {cohort.get('cohort')} · corte {cutoff} · n_target {n_target}",
          f"**Metodología:** expectancy + CI95 bootstrap (1000 resamples), igual que Master Recap 6-may.",
          f"**Estrategias evaluadas:** {len(rows)} · "
          f"KEEP {counts['KEEP']} · DISABLE {counts['DISABLE']} · "
          f"INCONCLUSIVE {counts['INCONCLUSIVE']} · INSUFFICIENT {counts['INSUFFICIENT']}",
          "",
          "| Estrategia | Veredicto | n | WR% | Exp $ | CI95 low | CI95 high | PnL total |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        md.append(f"| {r['strategy']} | **{r['verdict']}** | {r['n']} | {r['win_rate']} | "
                  f"{r['expectancy']} | {r['ci95_low']} | {r['ci95_high']} | {r['total_pnl']} |")
    md += ["",
           "## Interpretación",
           "- **KEEP**: CI95 low > 0 → edge positivo confirmado con datos sanos. Mantener ON.",
           "- **DISABLE**: CI95 high < 0 → pérdida confirmada post-fix. Re-apagar con evidencia definitiva.",
           "- **INCONCLUSIVE**: CI95 cruza 0 → extender ventana o restringir por ticker (cell-level).",
           "- **INSUFFICIENT**: n < n_target → no concluir, extender el cohort.",
           "",
           "> Las estrategias removidas originalmente sobre datos corruptos (bollinger, "
           "carroñeras, _long con WR 0%) ahora tienen veredicto sobre datos sanos. "
           "Comparar contra el Master Recap 6-may para ver cuáles cambiaron de signo."]
    md_path = OUTDIR / f"V1_RETEST_RESULTS_{stamp}.md"
    md_path.write_text("\n".join(md))

    print(f"[evaluate] {len(rows)} estrategias · KEEP {counts['KEEP']} "
          f"DISABLE {counts['DISABLE']} INCONCLUSIVE {counts['INCONCLUSIVE']} "
          f"INSUFFICIENT {counts['INSUFFICIENT']}")
    print(f"[evaluate] reporte: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
