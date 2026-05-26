"""OAuth Health Check — alerta Telegram si el refresh_token Schwab está cerca de expirar.

Schwab refresh_token tiene vida ~7 días. Esta función corre cada día y avisa cuando edad
supera umbrales, para que se haga re-auth manual antes de que se rompa.
"""
import os, sys, time, requests
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(__file__))

from google.cloud import firestore

# Misma credencial Telegram que bot_trader.py V1 (ya en repo)
TELEGRAM_TOKEN   = "8207559403:AAGwiQS15APh3ivFsAUUu_DCMbltMoDYV-o"
TELEGRAM_CHAT_ID = "5802788501"

GCP_PROJECT     = "eolo-schwab-agent"
COLLECTION_ID   = "schwab-tokens"
DOCUMENT_ID     = "schwab-tokens-auth"

# Umbrales en segundos
WARN_AGE_SEC    = 5 * 86400   # 5d: warning "re-auth en 2 días"
URGENT_AGE_SEC  = 6 * 86400   # 6d: urgent "re-auth HOY"
EXPIRED_AGE_SEC = 7 * 86400   # 7d+: refresh_token caducado, bots caídos


def _send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def check(request):
    """HTTP entry point. Cloud Scheduler la dispara diariamente."""
    db = firestore.Client(project=GCP_PROJECT)
    doc = db.collection(COLLECTION_ID).document(DOCUMENT_ID).get()

    if not doc.exists:
        msg = "🚨 <b>OAuth Schwab</b>: doc schwab-tokens NO EXISTE en Firestore. Bots offline."
        _send_telegram(msg)
        return msg, 200

    data = doc.to_dict() or {}
    issued_at = data.get("refresh_token_issued_at")

    if issued_at is None:
        msg = "⚠️ <b>OAuth Schwab</b>: refresh_token_issued_at no está en Firestore (doc legacy). Próxima re-auth lo va a fijar."
        _send_telegram(msg)
        return msg, 200

    age_sec  = time.time() - float(issued_at)
    age_days = age_sec / 86400
    issued_dt = datetime.fromtimestamp(float(issued_at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if age_sec >= EXPIRED_AGE_SEC:
        msg = (f"🔴 <b>OAuth Schwab CADUCADO</b> ({age_days:.1f}d desde issued_at)\n"
               f"Issued: {issued_dt}\n"
               f"Los bots están offline. Re-auth INMEDIATA:\n"
               f"<code>cd ~/PycharmProjects/eolo && python3 -c \"import init_auth; init_auth.main(None)\"</code>")
    elif age_sec >= URGENT_AGE_SEC:
        msg = (f"🟠 <b>OAuth Schwab — URGENTE</b> ({age_days:.1f}d, expira en <24h)\n"
               f"Issued: {issued_dt}\n"
               f"Re-auth HOY antes del próximo open.")
    elif age_sec >= WARN_AGE_SEC:
        msg = (f"🟡 <b>OAuth Schwab — Heads up</b> ({age_days:.1f}d, expira en ~{7-age_days:.1f}d)\n"
               f"Issued: {issued_dt}\n"
               f"Re-auth manual cuando puedas (toma 2 min).")
    else:
        # Sano — no alert
        return f"OK — age {age_days:.1f}d (issued {issued_dt})", 200

    _send_telegram(msg)
    return msg, 200
