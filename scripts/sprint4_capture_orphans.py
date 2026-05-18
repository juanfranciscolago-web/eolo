#!/usr/bin/env python3
"""Capture state de las 3 huérfanas para Sprint 4 + salvavidas."""
from google.cloud import firestore
import json
from datetime import datetime, timezone

ORPHANS = ["TQQQ", "SOXL", "TSLL"]
CONFIG_TICKERS = ["SPY", "QQQ", "TSLA", "AAPL", "NVDA", "NVDL"]
NOW = datetime.now(timezone.utc).isoformat()

def main():
    db = firestore.Client(project="eolo-schwab-agent")
    pos_ref = db.collection("eolo-config").document("positions")
    snap = pos_ref.get().to_dict() or {}

    # Salvavidas: snapshot completo del doc positions
    backup_path = f"/tmp/eolo_positions_snapshot_pre_s4_{NOW.replace(':','_').replace('+00:00','Z')}.json"
    with open(backup_path, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"[SALVAVIDAS] snapshot guardado en {backup_path}")

    positions     = snap.get("positions") or {}
    entry_prices  = snap.get("entry_prices") or {}
    entry_open_ts = snap.get("entry_open_ts") or {}

    print()
    print("=" * 70)
    print("ESTADO ACTUAL DE LAS 3 HUÉRFANAS")
    print("=" * 70)
    for t in ORPHANS:
        side  = positions.get(t)
        entry = entry_prices.get(t)
        ts    = entry_open_ts.get(t)
        if ts:
            try:
                opened_at = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except Exception:
                opened_at = str(ts)
        else:
            opened_at = "<sin timestamp>"
        in_config = "EN CONFIG" if t in CONFIG_TICKERS else "FUERA DE CONFIG (huérfana)"
        print(f"  {t}:  side={side!r:<10}  entry=${entry}  opened={opened_at}  [{in_config}]")

    print()
    print("=" * 70)
    print("RESTO DE POSICIONES (las 6 NO huérfanas, queden como están)")
    print("=" * 70)
    for t, side in sorted(positions.items()):
        if t in ORPHANS or side in (None, "", "FLAT"):
            continue
        entry = entry_prices.get(t)
        print(f"  {t:<6} side={side!r:<10} entry=${entry}  [EN CONFIG]")

    print()
    print(f"[NEXT] Sprint 4.1: cerrar manual desde Schwab paper account las 3 huérfanas.")
    print(f"       Operaciones: BUY_TO_COVER TQQQ + BUY_TO_COVER SOXL + SELL TSLL")
    print(f"[NEXT] Sprint 4.2: ejecutar scripts/sprint4_cleanup_state.py después de confirmar cierre Schwab.")

if __name__ == "__main__":
    main()
