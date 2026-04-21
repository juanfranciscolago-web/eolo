"""Diagnóstico rápido del token Schwab guardado en Firestore."""
from google.cloud import firestore
from datetime import datetime, timezone
import requests, json

db = firestore.Client(project="eolo-schwab-agent")
ref = db.collection("schwab-tokens").document("schwab-tokens-auth")
snap = ref.get()
d = snap.to_dict() or {}

age = (datetime.now(timezone.utc) - snap.update_time).total_seconds()
print(f"Token update_time (Firestore): {snap.update_time.isoformat()}")
print(f"Token age:                     {age/60:.1f} min  ({age/3600:.2f} hr  /  {age/86400:.2f} días)")
print(f"expires_in (según Schwab):     {d.get('expires_in')} seg")
print(f"Schwab refresh_token TTL:      7 días. Si age > 7d → hay que re-auth manual.")

at = d.get("access_token", "")
r = requests.get(
    "https://api.schwabapi.com/trader/v1/accounts/accountNumbers",
    headers={"Authorization": f"Bearer {at}", "Accept": "application/json"},
    timeout=10,
)
print(f"\nSchwab accountNumbers ping: HTTP {r.status_code}")
ok = (r.status_code == 200)
if ok:
    print("  OK — access_token válido. Problema NO es auth.")
else:
    print("  body:", r.text[:400])

# Probá también una llamada de price history (que es lo que usa el bot)
print("\nSchwab priceHistory SPY ping:")
r2 = requests.get(
    "https://api.schwabapi.com/marketdata/v1/pricehistory",
    headers={"Authorization": f"Bearer {at}", "Accept": "application/json"},
    params={"symbol": "SPY", "periodType": "day", "period": 1,
            "frequencyType": "minute", "frequency": 5},
    timeout=10,
)
print(f"  HTTP {r2.status_code}")
if r2.status_code == 200:
    j = r2.json()
    n = len(j.get("candles", []))
    print(f"  candles: {n}")
else:
    print("  body:", r2.text[:400])
