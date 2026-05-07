"""
00d_close_anomalies.py — Cerrar las 3 anomalías abiertas del inventory.
Read-only. Output: analysis/outputs/00d_anomalies_output.txt
"""
import os, random
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(HERE, "outputs", "00d_anomalies_output.txt")
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

# ════════════════════════════════════════════════════════════════════
p(f"00d_CLOSE_ANOMALIES — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
sep()

# ════════════════════════════════════════════════════════════════════
p("\nANOMALÍA 1 — V1: ¿Por qué no hay BUYs en el cohort?")
sep()

# Leer el día más reciente con data sustancial
for target_day in ["2026-05-06", "2026-05-05"]:
    doc = db.collection("eolo-trades").document(target_day).get()
    if doc.exists and len(doc.to_dict()) > 5:
        break

d = doc.to_dict()
p(f"Doc usado: {target_day}  total campos: {len(d)}")
hsep()

buys  = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("action") == "BUY"}
sells = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("action") == "SELL"}
other = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("action") not in ("BUY", "SELL")}

p(f"BUYs : {len(buys)}")
p(f"SELLs: {len(sells)}")
p(f"Other: {len(other)}")

# Analizar BUYs: strategy attribution y pnl_usd
p("\n-- BUYs: distribución de strategy --")
buy_strat = Counter((v.get("strategy") or "__missing__").strip().lower() for v in buys.values())
for s, n in sorted(buy_strat.items(), key=lambda x: -x[1]):
    p(f"  {str(s):40s}  {n}")

p("\n-- BUYs: pnl_usd presente? --")
buy_pnl = Counter()
for v in buys.values():
    pnl = v.get("pnl_usd")
    if pnl is None:
        buy_pnl["None"] += 1
    elif pnl == 0:
        buy_pnl["zero"] += 1
    else:
        buy_pnl["nonzero"] += 1
for k, n in buy_pnl.items():
    p(f"  {k}: {n}")

p("\n-- 3 BUYs completos --")
for i, (k, v) in enumerate(list(buys.items())[:3]):
    p(f"\n  BUY [{i+1}] key={k}")
    for fk in sorted(v.keys()):
        p(f"    {fk:35s} = {str(v[fk])[:100]}")

p("\n-- 3 SELLs completos --")
for i, (k, v) in enumerate(list(sells.items())[:3]):
    p(f"\n  SELL [{i+1}] key={k}")
    for fk in sorted(v.keys()):
        p(f"    {fk:35s} = {str(v[fk])[:100]}")

p("\n-- SELLs: strategy attribution --")
sell_strat = Counter((v.get("strategy") or "__missing__").strip().lower() for v in sells.values())
for s, n in sorted(sell_strat.items(), key=lambda x: -x[1]):
    p(f"  {str(s):40s}  {n}")

p("\n-- SELLs: pnl_usd --")
sell_pnl = Counter()
for v in sells.values():
    pnl = v.get("pnl_usd")
    if pnl is None:      sell_pnl["None"] += 1
    elif pnl == 0:       sell_pnl["zero"] += 1
    else:                sell_pnl["nonzero"] += 1
for k, n in sell_pnl.items():
    p(f"  {k}: {n}")

# Verificar en TODOS los docs: ¿algún BUY tiene pnl_usd?
p("\n-- Scan global V1: BUYs con pnl_usd presente (todos los docs) --")
all_buy_pnl = {"None": 0, "zero": 0, "nonzero": 0}
all_buy_strat_nontest = 0
for doc in db.collection("eolo-trades").stream():
    dd = doc.to_dict()
    for k, v in dd.items():
        if not isinstance(v, dict) or v.get("action") != "BUY":
            continue
        pnl = v.get("pnl_usd")
        s = v.get("strategy", "")
        if s not in ("TEST", "", None):
            all_buy_strat_nontest += 1
        if pnl is None:   all_buy_pnl["None"] += 1
        elif pnl == 0:    all_buy_pnl["zero"] += 1
        else:             all_buy_pnl["nonzero"] += 1

p(f"  BUYs con strategy not in [TEST,'',None]: {all_buy_strat_nontest}")
p(f"  BUYs pnl_usd=None: {all_buy_pnl['None']}")
p(f"  BUYs pnl_usd=0:    {all_buy_pnl['zero']}")
p(f"  BUYs pnl_usd≠0:    {all_buy_pnl['nonzero']}")

# ════════════════════════════════════════════════════════════════════
p("\n\nANOMALÍA 2 — V2: pnl=0 en 448 SELL_TO_CLOSE — worthless vs bug")
sep()

# Extraer todos los SELL_TO_CLOSE de V2
v2_closes_zero    = []
v2_closes_nonzero = []

for doc in db.collection("eolo-options-trades").stream():
    dd = doc.to_dict()
    for k, v in dd.items():
        if not isinstance(v, dict):
            continue
        if v.get("action") != "SELL_TO_CLOSE":
            continue
        pnl = v.get("pnl_usd")
        v["_key"] = k
        v["_doc"] = doc.id
        if pnl == 0:
            v2_closes_zero.append(v)
        elif pnl not in (None, 0):
            v2_closes_nonzero.append(v)

p(f"SELL_TO_CLOSE pnl=0:    {len(v2_closes_zero)}")
p(f"SELL_TO_CLOSE pnl≠0:    {len(v2_closes_nonzero)}")

# Samplear 10 con pnl=0
random.seed(42)
sample_zero    = random.sample(v2_closes_zero,    min(10, len(v2_closes_zero)))
sample_nonzero = random.sample(v2_closes_nonzero, min(10, len(v2_closes_nonzero)))

p("\n-- 10 SELL_TO_CLOSE con pnl=0 --")
p(f"  {'doc':12s} {'ticker':6s} {'symbol':35s} {'strike':8s} {'exp':12s} {'lim_price':10s} {'pnl':8s}")
for v in sample_zero:
    sym    = str(v.get("symbol", ""))[:34]
    p(f"  {v['_doc']:12s} {str(v.get('ticker','?')):6s} {sym:35s} "
      f"{str(v.get('strike','')):8s} {str(v.get('expiration','')):12s} "
      f"{str(v.get('limit_price','')):10s} {str(v.get('pnl_usd','')):8s}")

p("\n-- 10 SELL_TO_CLOSE con pnl≠0 --")
p(f"  {'doc':12s} {'ticker':6s} {'symbol':35s} {'strike':8s} {'exp':12s} {'lim_price':10s} {'pnl':8s}")
for v in sample_nonzero:
    sym    = str(v.get("symbol", ""))[:34]
    p(f"  {v['_doc']:12s} {str(v.get('ticker','?')):6s} {sym:35s} "
      f"{str(v.get('strike','')):8s} {str(v.get('expiration','')):12s} "
      f"{str(v.get('limit_price','')):10s} {str(v.get('pnl_usd','')):8s}")

# ¿Los pnl=0 tienen limit_price=0 o limit_price=None?
p("\n-- Distribución de limit_price en pnl=0 --")
lp_dist = Counter()
for v in v2_closes_zero:
    lp = v.get("limit_price")
    if lp is None:   lp_dist["None"] += 1
    elif lp == 0:    lp_dist["0.0"] += 1
    elif lp < 0.05:  lp_dist["<0.05 (near-zero)"] += 1
    else:            lp_dist[f">0.05 (val={lp:.2f})"] += 1
for k, n in sorted(lp_dist.items(), key=lambda x: -x[1]):
    p(f"  limit_price {k:30s}: {n}")

# ════════════════════════════════════════════════════════════════════
p("\n\nANOMALÍA 3 — CROP: confirmar n=2 con pnl")
sep()

crop_doc = db.collection("eolo-crop-trades").document("2026-05-06").get()
if not crop_doc.exists:
    p("  doc 2026-05-06 no encontrado — buscando cualquier doc")
    crop_docs = list(db.collection("eolo-crop-trades").stream())
    if not crop_docs:
        p("  CROP vacío")
        raise SystemExit(0)
    crop_doc = crop_docs[0]

cd = crop_doc.to_dict()
p(f"Doc: {crop_doc.id}  total campos: {len(cd)}")

# Distribución de actions
action_dist = Counter()
for k, v in cd.items():
    if isinstance(v, dict):
        action_dist[v.get("action", "__missing__")] += 1
    else:
        action_dist["__not_dict__"] += 1

p("\n-- Distribución de actions en CROP --")
for a, n in sorted(action_dist.items(), key=lambda x: -x[1]):
    p(f"  {str(a):55s}  {n}")

# Cierres con pnl
p("\n-- Todos los eventos con pnl_usd != 0 (CROP) --")
crop_pnl = [(k, v) for k, v in cd.items()
            if isinstance(v, dict) and v.get("pnl_usd") not in (None, 0)]
p(f"  n = {len(crop_pnl)}")
for k, v in crop_pnl:
    p(f"\n  key={k}")
    for fk in sorted(v.keys()):
        p(f"    {fk:35s} = {str(v[fk])[:100]}")

# ════════════════════════════════════════════════════════════════════
sep()
p("RESUMEN ANOMALÍAS")
hsep()
p("ANOMALÍA 1 — resultado: ver distribución BUYs/SELLs arriba")
p("ANOMALÍA 2 — resultado: ver distribución limit_price en pnl=0")
p("ANOMALÍA 3 — resultado: ver eventos CROP con pnl arriba")
sep()
p(f"Output: {OUTPUT_FILE}")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
