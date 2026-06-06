#!/usr/bin/env python3
"""
V1 RETEST MONITOR — chequeo de salud diario del cohort RETEST_V1.

Corre en la Mac de Juan (tiene gcloud ADC + Firestore). Escribe un JSON de
estado dentro del repo (docs/retest_status/) que la tarea de Claude lee y
reporta. NO depende de que la app de Claude esté abierta.

Chequea:
  - settings: bot_active, allow_short_selling, budget
  - revisión de eolo-bot serving
  - candle freshness vía logs (ausencia de [CANDLE_STALE] reciente)
  - presencia de señales exit del wrapper fix ([LONG-only] exit / [SHORT-only] exit)
  - conteo de trades (closes con pnl) por estrategia desde el corte del cohort

Si la fecha actual >= review_at del cohort, dispara v1_retest_evaluate.py.

Uso:
    python3 tools/v1_retest_monitor.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT  = "eolo-schwab-agent"
SERVICE  = "eolo-bot"
REGION   = "us-central1"
ROOT     = Path(__file__).resolve().parent.parent
STATUS_DIR = ROOT / "docs" / "retest_status"
STATUS_DIR.mkdir(parents=True, exist_ok=True)


def _gcloud(args: list[str], timeout: int = 60) -> str:
    try:
        return subprocess.check_output(
            ["gcloud", *args, f"--project={PROJECT}"],
            stderr=subprocess.DEVNULL, timeout=timeout,
        ).decode("utf-8", "replace")
    except Exception as e:
        return f"__ERROR__ {e}"


def main() -> int:
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    now = datetime.now(timezone.utc)
    out: dict = {"generated_utc": now.isoformat(), "checks": {}, "alerts": []}

    # ── Cohort marker ──
    cohort = (db.collection("eolo-config").document("retest_v1").get().to_dict() or {})
    out["cohort"] = cohort
    cutoff_date = (cohort.get("cutoff_utc") or "2026-06-04")[:10]
    review_at   = cohort.get("review_at", "2026-07-03")
    n_target    = int(cohort.get("n_target", 30))

    # ── Settings ──
    s = db.collection("eolo-config").document("settings").get().to_dict() or {}
    out["checks"]["settings"] = {
        "bot_active":          s.get("bot_active"),
        "allow_short_selling": s.get("allow_short_selling"),
        "budget":              s.get("budget"),
    }
    if not s.get("bot_active"):
        out["alerts"].append("bot_active=False — el bot está pausado, el cohort no acumula trades")

    # ── Revisión serving ──
    rev = _gcloud(["run", "services", "describe", SERVICE, f"--region={REGION}",
                   "--format=value(status.latestReadyRevisionName)"]).strip()
    out["checks"]["serving_revision"] = rev
    if rev.startswith("__ERROR__"):
        out["alerts"].append(f"No pude leer la revisión serving: {rev}")

    # ── Candle freshness (logs última hora) ──
    stale = _gcloud(["logging", "read",
        f'resource.labels.service_name={SERVICE} AND textPayload=~"CANDLE_STALE"',
        "--freshness=1h", "--limit=5", "--format=value(textPayload)"])
    out["checks"]["candle_stale_last_1h"] = 0 if (not stale.strip() or stale.startswith("__ERROR__")) \
        else len([l for l in stale.splitlines() if l.strip()])
    if out["checks"]["candle_stale_last_1h"] > 0:
        out["alerts"].append(f"{out['checks']['candle_stale_last_1h']} líneas CANDLE_STALE en la última hora — el feed podría estar roto de nuevo")

    # ── Señales exit del wrapper fix (últimas 24h) ──
    ex = _gcloud(["logging", "read",
        f'resource.labels.service_name={SERVICE} AND textPayload=~"LONG-only. exit|SHORT-only. exit"',
        "--freshness=24h", "--limit=50", "--format=value(textPayload)"])
    out["checks"]["exit_signals_24h"] = 0 if (not ex.strip() or ex.startswith("__ERROR__")) \
        else len([l for l in ex.splitlines() if l.strip()])

    # ── Conteo de closes con pnl por estrategia desde el corte ──
    per_strat: dict[str, int] = defaultdict(int)
    per_strat_pnl: dict[str, float] = defaultdict(float)
    total_closes = 0
    for doc in db.collection("eolo-trades").stream():
        if doc.id < cutoff_date:
            continue
        for v in (doc.to_dict() or {}).values():
            if not isinstance(v, dict):
                continue
            if v.get("action") not in ("SELL", "BUY_TO_COVER"):
                continue
            strat = v.get("strategy") or ""
            if strat in ("", "TEST", "CLOSE_ALL", "RISK_WATCHDOG", None):
                continue
            pnl = v.get("pnl_usd")
            if pnl is None:
                continue
            try:
                per_strat_pnl[strat] += float(pnl)
            except (TypeError, ValueError):
                pass
            per_strat[strat] += 1
            total_closes += 1

    ranked = sorted(per_strat.items(), key=lambda kv: -kv[1])
    out["checks"]["total_closes_cohort"] = total_closes
    out["checks"]["closes_per_strategy"] = {
        k: {"n": per_strat[k], "total_pnl": round(per_strat_pnl[k], 2)} for k, _ in ranked
    }
    out["checks"]["strategies_at_n_target"] = sum(1 for _, n in ranked if n >= n_target)
    out["checks"]["n_target"] = n_target

    # ── Status global ──
    out["status"] = "ALERT" if out["alerts"] else "OK"
    out["review_at"] = review_at
    out["review_reached"] = now.date().isoformat() >= review_at

    # ── Persistir ──
    (STATUS_DIR / "latest.json").write_text(json.dumps(out, indent=2, default=str))
    stamp = now.strftime("%Y%m%d")
    (STATUS_DIR / f"status_{stamp}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"[monitor] status={out['status']} closes={total_closes} "
          f"strat@n>={n_target}: {out['checks']['strategies_at_n_target']} "
          f"exit_signals_24h={out['checks']['exit_signals_24h']}")
    if out["alerts"]:
        for a in out["alerts"]:
            print(f"  ALERT: {a}")

    # ── Disparar evaluación final si llegó la fecha ──
    if out["review_reached"]:
        print("[monitor] review_at alcanzado — corriendo evaluación final")
        subprocess.run([sys.executable, str(ROOT / "tools" / "v1_retest_evaluate.py")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
