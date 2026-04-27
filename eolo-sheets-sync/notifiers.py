# ============================================================
#  EOLO — Notifiers para Daily Health
#
#  Envía el reporte a Telegram (1 línea), Gmail (HTML full),
#  y persiste a Firestore (para el artifact Cowork lea histórico).
#
#  Cada notifier es tolerante a falla — si no puede mandar, loguea
#  y devuelve {"ok": False, "error": ...}. Nunca rompe el cron.
# ============================================================
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from typing import Any

import requests
from google.cloud import firestore, secretmanager
from loguru import logger

GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")

# Firestore histórico del daily health
HEALTH_HISTORY_COLLECTION = "eolo-daily-health"
HEALTH_HISTORY_DOC_FMT    = "%Y-%m-%d"   # un doc por día

_db = None
def _fs() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=GCP_PROJECT)
    return _db


def _secret(name: str) -> str | None:
    try:
        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{GCP_PROJECT}/secrets/{name}/versions/latest"
        return client.access_secret_version(request={"name": path}).payload.data.decode("utf-8")
    except Exception as e:
        logger.warning(f"[secret:{name}] {e}")
        return None


# ═══════════════════════════════════════════════════════════
#  TELEGRAM — 1 línea de resumen + segundo mensaje si crit
# ═══════════════════════════════════════════════════════════
def build_telegram_summary(report: dict) -> str:
    """
    Construye una línea compacta tipo:
      🟢 Eolo OK | v1:27s v2:14s crypto:10s | Trades24h:12 | Balance:$84
    """
    overall = report.get("overall", "?")
    emoji = {"ok": "🟢", "warn": "⚠️", "crit": "🚨", "err": "💥"}.get(overall, "❓")

    # Extraer métricas clave
    def find(name):
        return next((c for c in report["checks"] if c["name"] == name), None)

    strat = find("strategies")
    trades = find("trades_24h")
    anthropic = find("anthropic_credits")
    gcp = find("gcp_billing")
    cloud_run = find("cloud_run")

    parts = [f"{emoji} Eolo {overall.upper()}"]

    if strat and strat.get("by_bot"):
        bb = strat["by_bot"]
        parts.append(
            f"v1:{bb.get('v1', {}).get('on', '?')}s "
            f"v2:{bb.get('v2', {}).get('on', '?')}s "
            f"crypto:{bb.get('crypto', {}).get('on', '?')}s"
        )

    if trades:
        parts.append(f"Trades24h:{trades['value']}")

    if cloud_run:
        parts.append(f"CR:{cloud_run['value']}")

    if anthropic and anthropic["value"] is not None and anthropic["name"] == "anthropic_credits":
        if "balance" in str(anthropic["message"]).lower():
            parts.append(f"Anth:${anthropic['value']:.0f}")

    if gcp and gcp["value"] is not None:
        parts.append(f"GCP:${gcp['value']:.0f}")

    return " | ".join(parts)


def build_telegram_crit_alert(report: dict) -> list[str]:
    """Segundo mensaje con los items critical/error — uno por ítem."""
    alerts = []
    for c in report.get("checks", []):
        if c["status"] in ("crit", "err"):
            alerts.append(f"🚨 <b>{c['name']}</b>: {c['message']}")
    return alerts


def send_telegram(report: dict) -> dict:
    """Manda resumen (+ críticos si hay). Reusa secret telegram-bot-token / chat-id."""
    token = _secret("telegram-bot-token")
    chat_id = _secret("telegram-chat-id")
    if not token or not chat_id:
        return {"ok": False, "error": "telegram-bot-token o telegram-chat-id secret missing"}

    # Limpiar whitespace/newlines accidentales del secret
    token = token.strip()
    chat_id = chat_id.strip()

    last_err = None

    def _post(text: str) -> tuple[bool, str | None]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=8,
            )
            if r.status_code != 200:
                msg = f"HTTP {r.status_code}: {r.text[:300]}"
                logger.warning(f"[telegram] {msg}")
                return False, msg
            return True, None
        except Exception as e:
            msg = f"exception: {type(e).__name__}: {str(e)[:200]}"
            logger.warning(f"[telegram] {msg}")
            return False, msg

    # Mensaje 1: resumen
    summary = build_telegram_summary(report)
    ok_summary, err = _post(summary)
    if err:
        last_err = err

    # Mensaje 2+: críticos
    alerts = build_telegram_crit_alert(report)
    sent_alerts = 0
    for a in alerts:
        ok_a, err_a = _post(a)
        if ok_a:
            sent_alerts += 1
        elif err_a:
            last_err = err_a

    resp = {"ok": ok_summary, "summary_sent": ok_summary,
            "alerts_sent": sent_alerts, "alerts_total": len(alerts),
            "token_len": len(token), "chat_id_len": len(chat_id)}
    if last_err:
        resp["error"] = last_err
    return resp


# ═══════════════════════════════════════════════════════════
#  GMAIL — HTML full report via SMTP + App Password
# ═══════════════════════════════════════════════════════════
def build_gmail_html(report: dict) -> str:
    """Construye HTML del reporte con secciones por check."""
    overall = report.get("overall", "?")
    color = {"ok": "#10b981", "warn": "#f59e0b",
             "crit": "#ef4444", "err": "#dc2626"}.get(overall, "#6b7280")
    emoji = {"ok": "🟢", "warn": "⚠️",
             "crit": "🚨", "err": "💥"}.get(overall, "❓")

    started = report.get("started_at", "")
    elapsed = report.get("elapsed_sec", 0)

    rows = []
    for c in report.get("checks", []):
        s = c["status"]
        scolor = {"ok": "#10b981", "warn": "#f59e0b",
                  "crit": "#ef4444", "err": "#dc2626"}.get(s, "#6b7280")
        rows.append(f"""
<tr>
  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">
    <span style="display:inline-block;padding:2px 8px;border-radius:4px;
                 background:{scolor};color:#fff;font-weight:600;font-size:12px;">{s.upper()}</span>
  </td>
  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:monospace;">{c['name']}</td>
  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{c['message']}</td>
  <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:monospace;color:#6b7280;">{c.get('value', '')}</td>
</tr>""")

    return f"""<!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;
max-width:720px;margin:20px auto;padding:20px;color:#111827;">
<div style="background:{color};color:#fff;padding:16px 20px;border-radius:8px;margin-bottom:16px;">
  <h1 style="margin:0;font-size:22px;">{emoji} Eolo Daily Health — {overall.upper()}</h1>
  <div style="opacity:0.9;font-size:13px;margin-top:4px;">{started} • {elapsed}s</div>
</div>

<div style="display:flex;gap:8px;margin-bottom:20px;">
  <div style="flex:1;background:#f0fdf4;padding:12px;border-radius:6px;">
    <div style="font-size:24px;font-weight:700;color:#10b981;">{report.get('n_ok', 0)}</div>
    <div style="font-size:12px;color:#6b7280;">OK</div>
  </div>
  <div style="flex:1;background:#fffbeb;padding:12px;border-radius:6px;">
    <div style="font-size:24px;font-weight:700;color:#f59e0b;">{report.get('n_warn', 0)}</div>
    <div style="font-size:12px;color:#6b7280;">WARN</div>
  </div>
  <div style="flex:1;background:#fef2f2;padding:12px;border-radius:6px;">
    <div style="font-size:24px;font-weight:700;color:#ef4444;">{report.get('n_crit', 0)}</div>
    <div style="font-size:12px;color:#6b7280;">CRIT</div>
  </div>
  <div style="flex:1;background:#fef2f2;padding:12px;border-radius:6px;">
    <div style="font-size:24px;font-weight:700;color:#dc2626;">{report.get('n_err', 0)}</div>
    <div style="font-size:12px;color:#6b7280;">ERR</div>
  </div>
</div>

<table style="width:100%;border-collapse:collapse;background:#fff;
              border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
<thead><tr style="background:#f9fafb;">
  <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;">STATUS</th>
  <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;">CHECK</th>
  <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;">DETAIL</th>
  <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;">VALUE</th>
</tr></thead>
<tbody>{''.join(rows)}</tbody></table>

<div style="margin-top:24px;font-size:11px;color:#9ca3af;text-align:center;">
  Eolo Daily Reset — GCP project: eolo-schwab-agent<br>
  Para ajustar thresholds, ver env-vars en eolo-sheets-sync cloudbuild.yaml
</div>
</body></html>"""


def send_gmail(report: dict) -> dict:
    """
    Envía HTML report via SMTP Gmail + App Password.
    Si el secret `gmail-app-password` no existe, skipea sin romper.
    """
    app_password = _secret("gmail-app-password")
    if not app_password:
        return {"ok": False, "error": "secret gmail-app-password no configurado (skip)",
                "skipped": True}

    sender = _secret("gmail-sender-address") or "juanfranciscolago@gmail.com"
    recipient = _secret("gmail-recipient-address") or "juanfranciscolago@gmail.com"

    overall = report.get("overall", "?")
    subject_emoji = {"ok": "🟢", "warn": "⚠️",
                     "crit": "🚨", "err": "💥"}.get(overall, "❓")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"{subject_emoji} Eolo Daily Health [{overall.upper()}] — {today}"

    try:
        # UTF-8 throughout: Subject wrapped in Header (handles emojis + tildes),
        # HTML body emits with explicit charset, and send_message() uses the
        # message's own encoding instead of forcing ASCII via as_string().
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"]    = sender
        msg["To"]      = recipient
        msg.attach(MIMEText(build_gmail_html(report), "html", "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=20) as smtp:
            smtp.login(sender, app_password)
            smtp.send_message(msg)

        return {"ok": True, "to": recipient, "subject": subject}
    except Exception as e:
        logger.exception("[gmail] send failed")
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
#  FIRESTORE — persist para histórico del artifact
# ═══════════════════════════════════════════════════════════
def persist_to_firestore(report: dict) -> dict:
    """Escribe un doc por día en eolo-daily-health/{YYYY-MM-DD}."""
    try:
        day = datetime.now(timezone.utc).strftime(HEALTH_HISTORY_DOC_FMT)
        ref = _fs().collection(HEALTH_HISTORY_COLLECTION).document(day)
        ref.set(report, merge=False)
        return {"ok": True, "doc": f"{HEALTH_HISTORY_COLLECTION}/{day}"}
    except Exception as e:
        logger.exception("[firestore] persist failed")
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
#  Orquestador
# ═══════════════════════════════════════════════════════════
def dispatch_all(report: dict) -> dict:
    """Manda a los 3 canales. Devuelve status por canal."""
    return {
        "telegram":  send_telegram(report),
        "gmail":     send_gmail(report),
        "firestore": persist_to_firestore(report),
    }
