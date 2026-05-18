#!/usr/bin/env python3
"""
SPRINT 3 — Firestore writes V1 P0 sobre eolo-config.

Tres operaciones, cada una con backup-update-verify:
  3.A — Apagar 4 strategies (bollinger + 3 variantes _long con WR 0%)
  3.B — Setear short_strategies_whitelist con 4 strategies base validadas
  3.C — Verify final consolidado

Salvavidas: si algo sale mal, los valores originales están impresos en stdout
antes de cada update. Para rollback manual, ejecutar el update inverso.
"""
from google.cloud import firestore
import json
from datetime import datetime, timezone

PROJECT = "eolo-schwab-agent"
STRATS_TO_DISABLE = [
    "bollinger",                # P0 #1 — blowup −$172k post-PI
    "vwap_momentum_long",       # P0 #2a — 0% WR, wrapper bug
    "net_bsv_long",             # P0 #2b — 0% WR, wrapper bug
    "donchian_turtle_long",     # P0 #2c — 0% WR, wrapper bug
]
SHORT_WHITELIST = [
    "SQUEEZE",
    "VOL_REVERSAL_BAR",
    "RSI_SMA200",
    "HH_LL",
]
NOW = datetime.now(timezone.utc).isoformat()

def main():
    db = firestore.Client(project=PROJECT)
    strats_ref   = db.collection("eolo-config").document("strategies")
    settings_ref = db.collection("eolo-config").document("settings")

    # ── 3.A — Apagar 4 strategies ─────────────────────────────
    print("=" * 60)
    print("3.A — Apagar 4 strategies en eolo-config/strategies")
    print("=" * 60)
    strats_snap = strats_ref.get().to_dict() or {}
    backup = {k: strats_snap.get(k) for k in STRATS_TO_DISABLE}
    print(f"[BACKUP] Valores actuales: {backup}")

    updates = {k: False for k in STRATS_TO_DISABLE}
    updates["_audit_log"] = (strats_snap.get("_audit_log", []) or []) + [{
        "ts": NOW,
        "action": "disable",
        "keys": STRATS_TO_DISABLE,
        "reason": "P0 auditoría 17-may — BOLLINGER blowup + _LONG WR 0% bug",
    }]
    strats_ref.update(updates)
    print(f"[UPDATE] Aplicado: {list(updates.keys())[:-1]}")

    verif = strats_ref.get().to_dict() or {}
    for k in STRATS_TO_DISABLE:
        val = verif.get(k)
        status = "OK" if val is False else "FAIL"
        print(f"[VERIFY] {k}: {val!r}  [{status}]")

    print()
    # ── 3.B — Setear short_strategies_whitelist ────────────────
    print("=" * 60)
    print("3.B — Setear short_strategies_whitelist en eolo-config/settings")
    print("=" * 60)
    settings_snap = settings_ref.get().to_dict() or {}
    backup_wl = settings_snap.get("short_strategies_whitelist", "<not set>")
    print(f"[BACKUP] short_strategies_whitelist actual: {backup_wl!r}")

    settings_ref.update({
        "short_strategies_whitelist": SHORT_WHITELIST,
        "short_strategies_whitelist_updated_at": NOW,
        "short_strategies_whitelist_reason": "P0 auditoría 17-may — restringir SHORTs a strategies que históricamente saben cubrir (xlsx 15-apr/15-may)",
    })

    verif_s = settings_ref.get().to_dict() or {}
    new_wl = verif_s.get("short_strategies_whitelist")
    status_wl = "OK" if new_wl == SHORT_WHITELIST else "FAIL"
    print(f"[VERIFY] short_strategies_whitelist: {new_wl!r}  [{status_wl}]")

    print()
    # ── 3.C — Verify final consolidado ────────────────────────
    print("=" * 60)
    print("3.C — Verify final consolidado")
    print("=" * 60)
    strats_final = strats_ref.get().to_dict() or {}
    settings_final = settings_ref.get().to_dict() or {}
    enabled_count = sum(1 for v in strats_final.values() if v is True)
    disabled_count = sum(1 for v in strats_final.values() if v is False)
    print(f"Strategies — total con bool: {enabled_count + disabled_count}")
    print(f"  ENABLED: {enabled_count}")
    print(f"  DISABLED: {disabled_count}")
    print(f"Settings.short_strategies_whitelist: {settings_final.get('short_strategies_whitelist')}")
    print(f"Settings.allow_short_selling: {settings_final.get('allow_short_selling')}")
    print(f"Settings.bot_active: {settings_final.get('bot_active')}")

    print()
    print("[OK] Sprint 3 completado.")
    print("[NEXT] Cerrar 3 posiciones huérfanas (TQQQ, SOXL, TSLL) manual desde Schwab — Sprint 4.")

if __name__ == "__main__":
    main()
