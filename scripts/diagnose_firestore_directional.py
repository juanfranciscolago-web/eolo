#!/usr/bin/env python3
"""Diagnóstico read-only de Firestore para V1 real (colección eolo-config)."""
from google.cloud import firestore
import json, traceback

def main():
    db = firestore.Client(project="eolo-schwab-agent")

    print("[A] Documentos en eolo-config (V1 productivo):")
    for d in db.collection("eolo-config").stream():
        size = len(json.dumps(d.to_dict() or {}, default=str))
        print(f"  - {d.id}  (size~{size} bytes)")

    print()
    print("[B] strategies — keys totales y status:")
    strats = db.collection("eolo-config").document("strategies").get().to_dict() or {}
    print(f"  Total keys: {len(strats)}")
    on  = sorted([k for k, v in strats.items() if v is True])
    off = sorted([k for k, v in strats.items() if v is False])
    other = sorted([(k, v) for k, v in strats.items() if v not in (True, False)])
    print(f"  ON  ({len(on)}):  {on}")
    print(f"  OFF ({len(off)}): {off}")
    if other:
        print(f"  OTHER ({len(other)}): {other}")

    print()
    print("[C] Keys que matchean directional/_long/_short/vwap_momentum/net_bsv/donchian:")
    patterns = ["_long", "_short", "directional", "vwap_momentum", "net_bsv", "donchian"]
    hits_strats = [k for k in strats.keys() if any(p in k.lower() for p in patterns)]
    print(f"  En strategies: {hits_strats}")

    settings = db.collection("eolo-config").document("settings").get().to_dict() or {}
    hits_settings = [k for k in settings.keys() if any(p in k.lower() for p in patterns + ["short", "allow", "whitelist"])]
    print(f"  En settings (incluye allow/short/whitelist): {hits_settings}")

    print()
    print("[D] settings flags relevantes (valores):")
    for k in sorted(settings.keys()):
        if any(s in k.lower() for s in ["short", "allow", "auto_close", "bot_active", "whitelist", "claude"]):
            print(f"  {k:<40} = {settings[k]!r}")

    print()
    print("[E] positions (tickers con side actual):")
    pos = db.collection("eolo-config").document("positions").get().to_dict() or {}
    p = pos.get("positions") or {}
    e = pos.get("entry_prices") or {}
    longs  = [(t, e.get(t)) for t, s in sorted(p.items()) if s == "LONG"]
    shorts = [(t, e.get(t)) for t, s in sorted(p.items()) if s == "SHORT"]
    flat   = [t for t, s in sorted(p.items()) if s not in ("LONG", "SHORT")]
    print(f"  LONG  ({len(longs)}): {longs}")
    print(f"  SHORT ({len(shorts)}): {shorts}")
    print(f"  FLAT/none ({len(flat)}): {flat}")

if __name__ == "__main__":
    main()
