#!/usr/bin/env python3
"""
V1 RETEST SETUP — reinserción de estrategias + corte de cohort.

Contexto (docs/ANALISIS_REINSERCION_ESTRATEGIAS_20260605.md):
el CANDLE-BUFFER-FIX (04-jun) invalidó toda la evidencia V1 15-abr→04-jun.
Este script prepara el re-test de 4 semanas con datos sanos:

  1. Backup del doc eolo-config/strategies (JSON local + doc Firestore).
  2. Reactiva las estrategias removidas por performance con datos corruptos.
  3. (opcional --include-directional) reactiva variantes _long/_short
     — SOLO válido con el RETEST-FIX del wrapper deployado.
  4. Escribe el marker de cohort eolo-config/retest_v1 con el corte.

Uso (desde la Mac de Juan, requiere gcloud ADC):
    python3 tools/v1_retest_setup.py                  # dry-run: muestra plan
    python3 tools/v1_retest_setup.py --apply          # aplica
    python3 tools/v1_retest_setup.py --apply --include-directional

NO deploya el bot. El re-test solo es válido tras deployar los RETEST-FIX
(wrapper + trader + auto_close) — ver docs/V1_RETEST_PLAN_20260605.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = "eolo-schwab-agent"

# ── Set de reinserción ──────────────────────────────────────
# Removidas por PERFORMANCE evaluada con candles stale (re-test legítimo):
REINSERT_PERFORMANCE = [
    "bollinger",                 # blowup -$172k — señales sobre data de ayer + pnl=null
    "rvol_breakout",             # carroñera en F4
    "anchor_vwap",               # carroñera killer F4
    "tick_trin_fade",            # carroñera killer F4
    "opening_drive",             # carroñera killer F4
    "bollinger_rsi_sensitive",   # carroñera killer F4
    "ema_8_21",                  # disabled silencioso pre-F4
    "tsv",                       # disabled post-11-may
]

# Removidas por BUG ARQUITECTURAL del wrapper (solo con RETEST-FIX deployado).
# NOTA 2026-06-06: verificado contra eolo-config/strategies. El registry
# direccional (post Phase-A) solo parte en _long/_short estas 16 bases:
# bulls_bsp, buy_pressure, combo1..7, donchian_turtle, ema_3_8, ema_8_21,
# macd_accel, net_bsv, orb_v3, sell_pressure, volume_breakout, vwap_momentum.
# Los "orphan SHORTs" de la auditoría 17-may (vw_macd/ha_cloud/ema_tsi/xom_30m)
# eran del sistema viejo pre-Phase-A: hoy vw_macd/ha_cloud/ema_tsi existen solo
# como base NO-direccional y xom_30m no existe. No hay nada que reinsertar ahí.
REINSERT_DIRECTIONAL = [
    "vwap_momentum_long",   # WR 0% por wrapper — el _LONG no recibía su SELL de cierre
    "net_bsv_long",         # idem
    "donchian_turtle_long", # idem
    "ema_3_8_short",
]

COHORT_DOC = {
    "cohort":          "RETEST_V1_2026H1",
    "cutoff_utc":      "2026-06-04T15:44:00Z",   # deploy 502f3b6 (candle fix eolo-bot)
    "started_at":      None,                       # se setea al aplicar
    "n_target":        30,                         # mínimo por estrategia para veredicto
    "review_at":       "2026-07-03",               # ~4 semanas
    "methodology":     "expectancy + CI95 + cell-level por ticker (Master Recap 6-may)",
    "data_validity":   "candles sanos post CANDLE-BUFFER-FIX (1659adb + 502f3b6)",
    "warning":         "NO mezclar con trades pre-cutoff: señales calculadas sobre sesión anterior",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Escribir cambios (default: dry-run)")
    ap.add_argument("--include-directional", action="store_true",
                    help="Reinsertar también variantes _long/_short (requiere RETEST-FIX deployado)")
    args = ap.parse_args()

    from google.cloud import firestore
    db  = firestore.Client(project=PROJECT)
    ref = db.collection("eolo-config").document("strategies")
    doc = ref.get()
    if not doc.exists:
        print("ERROR: eolo-config/strategies no existe", file=sys.stderr)
        return 1
    config = doc.to_dict() or {}
    print(f"Config actual: {len(config)} keys, {sum(1 for v in config.values() if v)} ON")

    targets = list(REINSERT_PERFORMANCE)
    if args.include_directional:
        targets += REINSERT_DIRECTIONAL

    changes, missing = {}, []
    for key in targets:
        if key not in config:
            missing.append(key)
        elif config[key] is not True:
            changes[key] = True

    print("\nPlan de reinserción:")
    for k in sorted(changes):
        print(f"  {k}: {config.get(k)} → True")
    for k in sorted(set(targets) & set(config)):
        if k not in changes:
            print(f"  {k}: ya estaba ON (skip)")
    if missing:
        print("\n⚠️  Keys NO encontradas en Firestore (verificar nombre exacto):")
        for k in missing:
            print(f"  {k}")
        cand = [c for c in config if any(m.split('_')[0] in c for m in missing)]
        if cand:
            print(f"  Candidatos similares en config: {sorted(cand)[:15]}")

    if not args.apply:
        print("\nDRY-RUN — nada escrito. Re-correr con --apply para ejecutar.")
        return 0

    # 1. Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = Path(__file__).resolve().parent.parent / "backups" / f"strategies_pre_retest_{ts}.json"
    backup_path.parent.mkdir(exist_ok=True)
    backup_path.write_text(json.dumps(config, indent=2, default=str))
    db.collection("eolo-config").document(f"strategies_backup_{ts}").set(config)
    print(f"\n✅ Backup: {backup_path} + Firestore strategies_backup_{ts}")

    # 2. Aplicar toggles
    if changes:
        ref.set(changes, merge=True)
        print(f"✅ {len(changes)} estrategias reactivadas")
    else:
        print("Nada para cambiar (todas ya ON)")

    # 3. Cohort marker
    cohort = dict(COHORT_DOC)
    cohort["started_at"] = datetime.now(timezone.utc).isoformat()
    cohort["reinserted"] = sorted(changes)
    cohort["directional_included"] = args.include_directional
    db.collection("eolo-config").document("retest_v1").set(cohort)
    print(f"✅ Cohort marker escrito: eolo-config/retest_v1 ({cohort['cohort']})")
    print(f"\nPróximo paso: deploy del bot con los RETEST-FIX y verificar logs"
          f" [V3/...] exit señales. Review: {cohort['review_at']} (n≥{cohort['n_target']}/estrategia)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
