#!/usr/bin/env python3
# ============================================================
#  Muestra las últimas N decisiones del Claude Bot (v1.2).
#
#  Uso:
#    cd /Users/JUAN/PycharmProjects/eolo
#    python3 check_claude_decisions.py          # últimas 10 de hoy
#    python3 check_claude_decisions.py 30       # últimas 30
#    python3 check_claude_decisions.py 20 v2    # v2 (opciones)
# ============================================================
import sys
from datetime import date
from google.cloud import firestore

PROJECT = "eolo-schwab-agent"
COLLECTIONS = {
    "v12": "eolo-claude-decisions-v12",
    "v2":  "eolo-claude-decisions-v2",
}

limit   = int(sys.argv[1]) if len(sys.argv) > 1 else 10
version = sys.argv[2] if len(sys.argv) > 2 else "v12"
coll    = COLLECTIONS.get(version, COLLECTIONS["v12"])

db    = firestore.Client(project=PROJECT)
today = date.today().strftime("%Y-%m-%d")

q = (
    db.collection(coll)
      .document(today)
      .collection("decisions")
      .order_by("recorded_ts", direction=firestore.Query.DESCENDING)
      .limit(limit)
)

docs = list(q.stream())
print(f"\n━━ Claude Bot {version.upper()} — {len(docs)} decisiones de {today} ━━\n")

if not docs:
    print("  (sin decisiones registradas aún)")
    print("\n  Chequeá que:")
    print("    1. El toggle 'Claude Bot' esté ON en el dashboard")
    print("    2. El modo sea 'PAPER' (o 'LIVE')")
    print("    3. El mercado esté abierto (o esperar hasta mañana)")
    print("    4. El ANTHROPIC_API_KEY esté en Secret Manager\n")
    sys.exit(0)

for d in docs:
    x = d.to_dict() or {}
    ct     = str(x.get("candle_time", "?"))[:19]
    sig    = x.get("signal", "?")
    tick   = x.get("ticker", "?")
    price  = x.get("price", 0)
    conf   = x.get("confidence", 0)
    strat  = x.get("strategy_used", "")
    reason = str(x.get("reasoning", ""))[:110]
    paper  = x.get("_paper_mode", True)
    mode   = "PAPER" if paper else "LIVE"

    print(f"  [{mode}] {ct} | {sig:>4} {tick:<5} @ ${price:<7} "
          f"conf={conf:.2f} | {strat}")
    print(f"         └── {reason}\n")
