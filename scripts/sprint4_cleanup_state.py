#!/usr/bin/env python3
"""
Cleanup state Firestore post-cierre manual de TODAS las 9 posiciones en Schwab.

CORRER SOLO DESPUÉS de confirmar que las 9 LIMIT orders están PUESTAS en Schwab
(no necesariamente filleadas — sólo puestas. El fill ocurre lunes 9:30 ET).

Acción: remover las 9 tickers (3 huérfanas + 6 legítimas) del state positions,
entry_prices, entry_open_ts, entry_strategies. Deja el bot empezando lunes
con state limpio = fresh start sincronizado con Schwab.

Salvavidas: snapshot completo del state pre-cleanup en /tmp/ con timestamp.
"""
from google.cloud import firestore
import json
from datetime import datetime, timezone

# Las 9: 3 huérfanas + 6 legítimas (todas las que el bot tiene en state hoy)
ALL_OPEN = ["TQQQ", "SOXL", "TSLL",                          # huérfanas
            "NVDA", "SPY", "QQQ", "TSLA", "AAPL", "NVDL"]    # legítimas
NOW = datetime.now(timezone.utc).isoformat()

def main():
    db = firestore.Client(project="eolo-schwab-agent")
    pos_ref = db.collection("eolo-config").document("positions")

    # Salvavidas
    snap = pos_ref.get().to_dict() or {}
    safe_ts = NOW.replace(':','_').replace('+00:00','Z')
    backup_path = f"/tmp/eolo_positions_pre_cleanup_s4_{safe_ts}.json"
    with open(backup_path, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"[SALVAVIDAS] backup en {backup_path}")

    # BACKUP de las 9
    print()
    print("[BACKUP] Valores actuales (todas las posiciones que serán removidas del state):")
    for t in ALL_OPEN:
        side = (snap.get('positions') or {}).get(t)
        entry = (snap.get('entry_prices') or {}).get(t)
        strat = (snap.get('entry_strategies') or {}).get(t)
        ts = (snap.get('entry_open_ts') or {}).get(t)
        print(f"  {t:<6} side={side!r:<10} entry=${entry}  strategy={strat!r}  ts={ts}")

    # UPDATE: DELETE_FIELD para 4 keys nested por ticker
    DELETE = firestore.DELETE_FIELD
    updates = {}
    for t in ALL_OPEN:
        updates[f"positions.{t}"]        = DELETE
        updates[f"entry_prices.{t}"]     = DELETE
        updates[f"entry_open_ts.{t}"]    = DELETE
        updates[f"entry_strategies.{t}"] = DELETE   # tracking Pure Isolation
    updates["_audit_log_s4"] = (snap.get("_audit_log_s4", []) or []) + [{
        "ts": NOW,
        "action": "cleanup_all_open",
        "tickers": ALL_OPEN,
        "reason": "S4 fresh-start cleanup — Juan no disponible lunes, cierre manual Schwab via LIMIT pre-market",
    }]
    pos_ref.update(updates)
    print()
    print(f"[UPDATE] DELETE_FIELD aplicado a positions/entry_prices/entry_open_ts/entry_strategies de {ALL_OPEN}")

    # VERIFY
    verif = pos_ref.get().to_dict() or {}
    print()
    print("[VERIFY] Estado post-cleanup:")
    fails = []
    for t in ALL_OPEN:
        in_pos = t in (verif.get("positions") or {})
        in_ent = t in (verif.get("entry_prices") or {})
        in_es  = t in (verif.get("entry_strategies") or {})
        in_ts  = t in (verif.get("entry_open_ts") or {})
        ok = not (in_pos or in_ent or in_es or in_ts)
        if not ok: fails.append(t)
        print(f"  {t:<6} pos={in_pos} ent={in_ent} strat={in_es} ts={in_ts}  [{'OK' if ok else 'FAIL'}]")
    print()
    if fails:
        print(f"[FAIL] {len(fails)} tickers no se limpiaron: {fails}")
    else:
        print(f"[OK] Las 9 posiciones removidas del state. Schwab debe tener LIMIT orders pre-market para lunes.")

    print()
    print("[VERIFY] Posiciones que SIGUEN en el state (debería ser vacío):")
    remaining = sorted((verif.get("positions") or {}).items())
    if remaining:
        for t, s in remaining:
            print(f"  {t:<6} side={s!r}")
    else:
        print("  (vacío — state limpio)")

if __name__ == "__main__":
    main()
