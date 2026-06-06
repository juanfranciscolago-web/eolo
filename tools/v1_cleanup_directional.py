#!/usr/bin/env python3
"""
V1 CLEANUP DIRECTIONAL — borra las keys _long/_short inertes de Firestore.

Contexto (docs/V1_STRATEGY_AUDIT_20260606.md H1): las variantes _long/_short
en eolo-config/strategies NO se ejecutan — run_cycle solo lee las keys base.
El registry direccional se conserva en código (lo usa diagnostics.py), pero
las keys de config son ruido. Este script las elimina.

Hace backup antes de tocar nada.

Uso (Mac de Juan):
    python3 tools/v1_cleanup_directional.py            # dry-run
    python3 tools/v1_cleanup_directional.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = "eolo-schwab-agent"
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Aplicar (default dry-run)")
    args = ap.parse_args()

    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    ref = db.collection("eolo-config").document("strategies")
    cfg = ref.get().to_dict() or {}

    targets = sorted(k for k in cfg if k.endswith("_long") or k.endswith("_short"))
    print(f"Config: {len(cfg)} keys. Keys direccionales inertes: {len(targets)}")
    for k in targets:
        print(f"  - {k} ({cfg[k]})")
    if not targets:
        print("Nada para limpiar.")
        return 0

    if not args.apply:
        print("\nDRY-RUN — nada borrado. Re-correr con --apply.")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "backups" / f"strategies_pre_cleanup_directional_{ts}.json"
    backup.parent.mkdir(exist_ok=True)
    backup.write_text(json.dumps(cfg, indent=2, default=str))
    db.collection("eolo-config").document(f"strategies_backup_{ts}").set(cfg)
    print(f"\n✅ Backup: {backup} + Firestore strategies_backup_{ts}")

    ref.update({k: firestore.DELETE_FIELD for k in targets})
    print(f"✅ {len(targets)} keys direccionales eliminadas")
    remaining = ref.get().to_dict() or {}
    print(f"   Config ahora: {len(remaining)} keys, {sum(1 for v in remaining.values() if v)} ON")
    return 0


if __name__ == "__main__":
    sys.exit(main())
