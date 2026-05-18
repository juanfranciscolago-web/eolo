#!/usr/bin/env python3
"""
Cleanup state Firestore post-cierre manual de las 3 huérfanas en Schwab.

CORRER SOLO DESPUÉS de confirmar fills en Schwab paper account.

Acción: remover TQQQ, SOXL, TSLL del state positions + entry_prices +
entry_open_ts. NO toca strategies ni otras posiciones.

Salvavidas: snapshot del state pre-cleanup en /tmp/ con timestamp.
"""
from google.cloud import firestore
import json
from datetime import datetime, timezone

ORPHANS = ["TQQQ", "SOXL", "TSLL"]
NOW = datetime.now(timezone.utc).isoformat()

def main():
    db = firestore.Client(project="eolo-schwab-agent")
    pos_ref = db.collection("eolo-config").document("positions")

    # Salvavidas
    snap = pos_ref.get().to_dict() or {}
    backup_path = f"/tmp/eolo_positions_pre_cleanup_s4_{NOW.replace(':','_')}.json"
    with open(backup_path, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"[SALVAVIDAS] backup en {backup_path}")

    # BACKUP de las 3 que vamos a tocar
    print()
    print("[BACKUP] Valores actuales que serán removidos:")
    for t in ORPHANS:
        print(f"  {t}: side={snap.get('positions',{}).get(t)!r}  entry=${snap.get('entry_prices',{}).get(t)}")

    # UPDATE: usar firestore.DELETE_FIELD para remover keys específicas
    DELETE = firestore.DELETE_FIELD
    updates = {}
    for t in ORPHANS:
        updates[f"positions.{t}"]        = DELETE
        updates[f"entry_prices.{t}"]     = DELETE
        updates[f"entry_open_ts.{t}"]    = DELETE
        updates[f"entry_strategies.{t}"] = DELETE   # tracking Pure Isolation
    updates["_audit_log_s4"] = (snap.get("_audit_log_s4", []) or []) + [{
        "ts": NOW,
        "action": "cleanup_orphans",
        "tickers": ORPHANS,
        "reason": "S4 cleanup post Schwab manual close — fuera de tickers config",
    }]
    pos_ref.update(updates)
    print()
    print(f"[UPDATE] DELETE_FIELD aplicado a positions/entry_prices/entry_open_ts de {ORPHANS}")

    # VERIFY
    verif = pos_ref.get().to_dict() or {}
    print()
    print("[VERIFY] Estado post-cleanup:")
    for t in ORPHANS:
        in_pos = t in (verif.get("positions") or {})
        in_ent = t in (verif.get("entry_prices") or {})
        in_es  = t in (verif.get("entry_strategies") or {})
        status = "OK" if (not in_pos and not in_ent and not in_es) else "FAIL"
        print(f"  {t}: in positions={in_pos}  in entry_prices={in_ent}  in entry_strategies={in_es}  [{status}]")

    print()
    print("[VERIFY] Posiciones restantes:")
    for t, side in sorted((verif.get("positions") or {}).items()):
        print(f"  {t:<6} side={side!r}")

if __name__ == "__main__":
    main()
