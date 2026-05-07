"""
07_v2_inventory_breakeven.py — Datos para V2_INVENTORY.md y V2_OPTIONSBRAIN_BREAKEVEN.md
Read-only. Output: analysis/outputs/07_v2_inventory_breakeven.txt
"""
import os, json
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(HERE, "outputs", "07_v2_inventory_breakeven.txt")
os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)

from google.cloud import firestore
db = firestore.Client(project="eolo-schwab-agent")

lines = []
def p(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    lines.append(s)

def sep(c="═", n=70): p(c * n)
def hsep(): sep("─")

p(f"07_V2_INVENTORY_BREAKEVEN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
sep()

# ── Recolectar todos los eventos V2 ─────────────────────────────────────
all_events = []
docs_v2    = []
for doc in db.collection("eolo-options-trades").stream():
    docs_v2.append(doc.id)
    dd = doc.to_dict()
    for k, v in dd.items():
        if not isinstance(v, dict):
            continue
        v["_key"]   = k
        v["_doc"]   = doc.id
        all_events.append(v)

p(f"Docs V2: {len(docs_v2)}  ({min(docs_v2)} → {max(docs_v2)})")
p(f"Eventos totales: {len(all_events)}")
sep()

# ── SECCIÓN INVENTARIO ───────────────────────────────────────────────────
p("\nINVENTARIO GENERAL")
hsep()

action_counts = Counter(v.get("action", "?") for v in all_events)
for a, n in sorted(action_counts.items(), key=lambda x: -x[1]):
    p(f"  {str(a):50s}  {n}")

# BUY_TO_OPEN por strategy
p("\n-- BUY_TO_OPEN por strategy --")
buy_open = [v for v in all_events if v.get("action") == "BUY_TO_OPEN"]
bto_strat = Counter((v.get("strategy") or "__missing__").strip().lower() for v in buy_open)
for s, n in sorted(bto_strat.items(), key=lambda x: -x[1]):
    p(f"  {str(s):50s}  {n}")

# SELL_TO_CLOSE breakdown pnl
p("\n-- SELL_TO_CLOSE breakdown pnl --")
sells = [v for v in all_events if v.get("action") == "SELL_TO_CLOSE"]
pnl_counts = {"pnl!=0": 0, "pnl==0": 0, "pnl==None": 0}
pnl_sum    = 0.0
for v in sells:
    pnl = v.get("pnl_usd")
    if pnl is None:    pnl_counts["pnl==None"] += 1
    elif pnl == 0:     pnl_counts["pnl==0"]    += 1
    else:
        pnl_counts["pnl!=0"] += 1
        pnl_sum += float(pnl)

p(f"  SELL_TO_CLOSE total: {len(sells)}")
for k, n in pnl_counts.items():
    p(f"    {k:15s}: {n}")
p(f"  pnl agregado (pnl!=0): ${pnl_sum:+.2f}  (disclaimer: sin atribución por estrategia)")

# SELL_TO_CLOSE por strategy
p("\n-- SELL_TO_CLOSE por strategy --")
stc_strat = Counter((v.get("strategy") or "__missing__").strip().lower() for v in sells)
for s, n in sorted(stc_strat.items(), key=lambda x: -x[1]):
    p(f"  {str(s):50s}  {n}")

# Tickers en BUY_TO_OPEN
p("\n-- Tickers en BUY_TO_OPEN --")
bto_ticker = Counter(v.get("ticker", "?") for v in buy_open)
for t, n in sorted(bto_ticker.items(), key=lambda x: -x[1]):
    p(f"  {str(t):10s}  {n}")

sep()

# ── SECCIÓN BREAKEVEN ────────────────────────────────────────────────────
p("\nANÁLISIS BREAKEVEN (pnl=0 + entry_price==limit_price)")
sep()

# Clasificar SELL_TO_CLOSE en: breakeven, market_order, pnl_real
breakeven_events = []
market_order_events = []
pnl_real_events = []
pnl_none_events = []

for v in sells:
    pnl = v.get("pnl_usd")
    ep  = v.get("entry_price")
    lp  = v.get("limit_price")
    if pnl is None:
        pnl_none_events.append(v)
    elif pnl == 0 and lp is None:
        market_order_events.append(v)
    elif pnl == 0 and ep is not None and lp is not None and abs(float(ep) - float(lp)) < 0.001:
        breakeven_events.append(v)
    elif pnl == 0:
        breakeven_events.append(v)  # pnl=0 but entry/limit may be absent (pre-enrichment)
    else:
        pnl_real_events.append(v)

p(f"  Breakeven genuino (pnl=0):    {len(breakeven_events)}")
p(f"  Market order sin limit:        {len(market_order_events)}")
p(f"  pnl real (!=0):                {len(pnl_real_events)}")
p(f"  pnl=None:                      {len(pnl_none_events)}")

# Distribución hold_seconds en breakeven
p("\n-- Breakeven: distribución hold_seconds --")
hold_dist = Counter()
for v in breakeven_events:
    hs = v.get("hold_seconds")
    if hs is None: hold_dist["None"] += 1
    elif hs < 120:  hold_dist["<2min"] += 1
    elif hs < 300:  hold_dist["2-5min"] += 1
    elif hs < 600:  hold_dist["5-10min"] += 1
    elif hs < 1800: hold_dist["10-30min"] += 1
    else:           hold_dist[">30min"] += 1
for k, n in sorted(hold_dist.items()):
    p(f"  {k:15s}: {n}")

# exit_reason más frecuentes en breakeven
p("\n-- Breakeven: exit_reason más frecuentes (top 20 strings truncados) --")
exit_reasons = Counter()
for v in breakeven_events:
    r = str(v.get("exit_reason", v.get("reason", "")))[:120].strip()
    if r:
        exit_reasons[r] += 1
for r, n in sorted(exit_reasons.items(), key=lambda x: -x[1])[:20]:
    p(f"  [{n:3d}] {r[:110]}")

# Breakeven por ticker
p("\n-- Breakeven por ticker --")
be_ticker = Counter(v.get("ticker", "?") for v in breakeven_events)
for t, n in sorted(be_ticker.items(), key=lambda x: -x[1]):
    p(f"  {str(t):10s}  {n}")

# Breakeven por strategy (los que tienen strategy no vacía)
p("\n-- Breakeven por strategy (no vacía) --")
be_strat = Counter((v.get("strategy") or "").strip().lower() for v in breakeven_events if v.get("strategy"))
for s, n in sorted(be_strat.items(), key=lambda x: -x[1]):
    p(f"  {str(s):40s}  {n}")

# Comparar hold_seconds breakeven vs pnl_real
be_hold_avg  = sum(v.get("hold_seconds", 0) or 0 for v in breakeven_events) / max(len(breakeven_events), 1)
real_hold_avg = sum(v.get("hold_seconds", 0) or 0 for v in pnl_real_events) / max(len(pnl_real_events), 1)
p(f"\n-- hold_seconds promedio --")
p(f"  Breakeven:  {be_hold_avg:.1f}s")
p(f"  pnl_real:   {real_hold_avg:.1f}s")

# 5 ejemplos breakeven con hold > 1800s (cerraron tarde)
p("\n-- 5 breakevens con hold_seconds > 1800s (30min) --")
long_be = sorted([v for v in breakeven_events if (v.get("hold_seconds") or 0) > 1800],
                  key=lambda x: -(x.get("hold_seconds") or 0))[:5]
for v in long_be:
    p(f"  {v['_doc']} {v.get('ticker')} {v.get('symbol','')} "
      f"hold={v.get('hold_seconds'):.0f}s entry={v.get('entry_price')} lp={v.get('limit_price')} strat={v.get('strategy','')}")

sep()
p(f"Output: {OUTPUT_FILE}")
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
