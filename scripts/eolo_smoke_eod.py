#!/usr/bin/env python3
"""Smoke EOD lunes 18-may post-fixes V1."""
from google.cloud import firestore
import datetime
from collections import Counter, defaultdict

db = firestore.Client(project='eolo-schwab-agent')
today = datetime.date.today().strftime('%Y-%m-%d')

print(f"=== Trades del dia {today} (eolo-trades/{today}) ===")
doc = db.collection('eolo-trades').document(today).get()

if not doc.exists:
    print(f"  ⚠️  No hay doc — bot no operó hoy?")
else:
    data = doc.to_dict() or {}
    actions = Counter()
    by_strat = defaultdict(lambda: Counter())
    pnls = defaultdict(float)
    total_pnl = 0.0
    n_pnl = 0

    for k, v in data.items():
        if not isinstance(v, dict) or k.startswith('_'):
            continue
        action = v.get('action', '?')
        strat = v.get('strategy', '?')
        actions[action] += 1
        by_strat[strat][action] += 1
        pnl = v.get('pnl_usd')
        if pnl is not None:
            try:
                pnls[strat] += float(pnl)
                total_pnl += float(pnl)
                n_pnl += 1
            except (TypeError, ValueError):
                pass

    print(f"  Total eventos: {sum(actions.values())}")
    print(f"  Por action: {dict(actions)}")
    print(f"  Trades con PnL: {n_pnl}  Total PnL: ${total_pnl:.2f}")
    print()
    print(f"  Por strategy (top 25 por volumen):")
    items = sorted(by_strat.items(), key=lambda x: -sum(x[1].values()))[:25]
    for strat, ctr in items:
        total = sum(ctr.values())
        pnl_s = pnls.get(strat, 0.0)
        print(f"    {strat:<32} total={total:>5}  pnl=${pnl_s:>11.2f}  actions={dict(ctr)}")

    print()
    print("  Verificación P0 disabled — ningún BOLLINGER/_LONG debería aparecer:")
    disabled = ['BOLLINGER', 'VWAP_MOMENTUM_LONG', 'NET_BSV_LONG', 'DONCHIAN_TURTLE_LONG']
    for s in disabled:
        if s in by_strat:
            print(f"    ⚠️  {s}: {sum(by_strat[s].values())} eventos - REGRESIÓN")
        else:
            print(f"    ✓ {s}: 0 eventos")
