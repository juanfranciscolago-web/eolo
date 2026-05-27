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


def _secret(name: str):
    """Lee un secret de GCP Secret Manager. Retorna str o None."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{GCP_PROJECT}/secrets/{name}/versions/latest"
        resp = client.access_secret_version(request={"name": path})
        return resp.payload.data.decode("utf-8").strip()
    except Exception as e:
        print(f"_secret({name}) error: {e}")
        return None


def _send_email(subject: str, body_html: str) -> bool:
    """Envía email HTML vía Gmail SMTP + App Password. Skip si falta secret."""
    try:
        import smtplib, ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.header import Header
        app_password = _secret("gmail-app-password")
        if not app_password:
            print("gmail-app-password no configurado, skip email")
            return False
        sender = _secret("gmail-sender-address") or "juanfranciscolago@gmail.com"
        recipient = _secret("gmail-recipient-address") or "juanfranciscolago@gmail.com"
        msg = MIMEMultipart("alternative")
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = Header(subject, "utf-8")
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=20) as smtp:
            smtp.login(sender, app_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"_send_email error: {e}")
        return False


def _build_alert_email(severity: str, age_days: float, issued_dt: str):
    """Construye (subject, html_body) según severidad."""
    cmd = ('cd ~/PycharmProjects/eolo\n'
           'python3 -c "import init_auth; init_auth.main(None)"')
    headings = {
        "warn":    (f"⚠️ OAuth Schwab — re-auth en ~{7-age_days:.1f} días",  "#F59E0B"),
        "urgent":  (f"🟠 OAuth Schwab — URGENTE (expira en <24h)",            "#EA580C"),
        "expired": (f"🔴 OAuth Schwab — CADUCADO ({age_days:.1f}d). Bots OFFLINE.", "#DC2626"),
    }
    subject, color = headings.get(severity, ("OAuth Schwab", "#6B7280"))
    html = f"""<html><body style="font-family:-apple-system,sans-serif;max-width:640px;margin:20px auto;color:#111827">
  <div style="border-left:4px solid {color};padding:16px 24px;background:#FAFAFA;border-radius:4px">
    <h2 style="color:{color};margin:0 0 12px">{subject}</h2>
    <p><b>Edad refresh_token:</b> {age_days:.1f} días<br>
       <b>Emitido:</b> {issued_dt}</p>
    <h3>Pasos para re-autenticar (2 min):</h3>
    <ol>
      <li>En tu terminal local:
        <pre style="background:#F3F4F6;padding:12px;border-radius:6px;font-size:13px">{cmd}</pre>
      </li>
      <li>Se abre el browser para login Schwab.</li>
      <li>Tras login, Schwab redirige a <code>https://127.0.0.1/?code=...</code> (la página no carga, es normal).
          Copiá la URL completa de la barra y pegala en el terminal cuando lo pida.</li>
      <li>Los tokens nuevos se persisten automáticamente. Los bots los toman en ~25 min.</li>
    </ol>
    <hr style="margin-top:24px;border:none;border-top:1px solid #E5E7EB">
    <p style="color:#6B7280;font-size:12px">Sistema OAuth proactive alert · oauth-health-check Cloud Function · Scheduler diario Mon-Fri 13:00 UTC</p>
  </div></body></html>"""
    return subject, html


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
        severity_tag = "expired"
        msg = (f"🔴 <b>OAuth Schwab CADUCADO</b> ({age_days:.1f}d desde issued_at)\n"
               f"Issued: {issued_dt}\n"
               f"Los bots están offline. Re-auth INMEDIATA:\n"
               f"<code>cd ~/PycharmProjects/eolo && python3 -c \"import init_auth; init_auth.main(None)\"</code>")
    elif age_sec >= URGENT_AGE_SEC:
        severity_tag = "urgent"
        msg = (f"🟠 <b>OAuth Schwab — URGENTE</b> ({age_days:.1f}d, expira en <24h)\n"
               f"Issued: {issued_dt}\n"
               f"Re-auth HOY antes del próximo open.")
    elif age_sec >= WARN_AGE_SEC:
        severity_tag = "warn"
        msg = (f"🟡 <b>OAuth Schwab — Heads up</b> ({age_days:.1f}d, expira en ~{7-age_days:.1f}d)\n"
               f"Issued: {issued_dt}\n"
               f"Re-auth manual cuando puedas (toma 2 min).")
    else:
        # Sano — no alert
        return f"OK — age {age_days:.1f}d (issued {issued_dt})", 200

    _send_telegram(msg)
    subj, html = _build_alert_email(severity_tag, age_days, issued_dt)
    _send_email(subj, html)
    return msg, 200
