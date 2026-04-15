# ============================================================
#  EOLO — Cloud Function: Reporte Diario
#  Lee trades del día desde Firestore y manda resumen a Telegram
#  Deploy: ver cloudbuild_report.yaml
#  Schedule: todos los días hábiles a las 4:05pm ET
# ============================================================
import os
import requests
from datetime import datetime, date
from loguru import logger
from google.cloud import firestore, secretmanager


# ── Config ────────────────────────────────────────────────
GCP_PROJECT    = "eolo-schwab-agent"
TRADES_COLLECTION = "eolo-trades"


def _get_telegram_credentials() -> tuple:
    """Lee token y chat_id desde Google Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()

    def get_secret(secret_id):
        name = f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"
        return client.access_secret_version(request={"name": name}).payload.data.decode("UTF-8")

    token   = get_secret("telegram-bot-token")
    chat_id = get_secret("telegram-chat-id")
    return token, chat_id


def _send_telegram(token: str, chat_id: str, message: str):
    """Envía mensaje a Telegram."""
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Telegram error: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram fallo: {e}")


def _load_today_trades() -> list:
    """Lee los trades de hoy desde Firestore."""
    today = date.today().strftime("%Y-%m-%d")
    db    = firestore.Client(project=GCP_PROJECT)
    doc   = db.collection(TRADES_COLLECTION).document(today).get()

    if not doc.exists:
        return []

    data   = doc.to_dict()
    trades = list(data.values())
    trades.sort(key=lambda x: x["timestamp"])
    return trades


def _calculate_pnl(trades: list) -> dict:
    """Empareja BUY con SELL y calcula P&L por ticker."""
    from collections import defaultdict
    by_ticker = defaultdict(list)
    for t in trades:
        by_ticker[t["ticker"]].append(t)

    total_pnl    = 0.0
    total_wins   = 0
    total_losses = 0
    total_rounds = 0
    open_pos     = []
    by_ticker_result = {}

    for ticker, ticker_trades in by_ticker.items():
        pnl       = 0.0
        wins      = 0
        losses    = 0
        rounds    = 0
        buy_price = None

        for trade in ticker_trades:
            price = float(trade["price"])
            if trade["action"] == "BUY":
                buy_price = price
            elif trade["action"] == "SELL" and buy_price is not None:
                gain = price - buy_price
                pnl += gain
                rounds += 1
                if gain >= 0:
                    wins += 1
                else:
                    losses += 1
                buy_price = None

        if buy_price is not None:
            open_pos.append(f"{ticker} (comprado a ${buy_price})")

        by_ticker_result[ticker] = {
            "pnl": round(pnl, 4), "rounds": rounds,
            "wins": wins, "losses": losses,
        }
        total_pnl    += pnl
        total_wins   += wins
        total_losses += losses
        total_rounds += rounds

    return {
        "by_ticker":    by_ticker_result,
        "total_pnl":    round(total_pnl, 4),
        "total_rounds": total_rounds,
        "total_wins":   total_wins,
        "total_losses": total_losses,
        "open_pos":     open_pos,
        "total_signals": len(trades),
    }


def _build_message(stats: dict) -> str:
    """Arma el texto del reporte."""
    today    = date.today().strftime("%d/%m/%Y")
    now_time = datetime.now().strftime("%H:%M ET")
    pnl      = stats["total_pnl"]
    emoji    = "🟢" if pnl >= 0 else "🔴"
    win_rate = (
        round(stats["total_wins"] / stats["total_rounds"] * 100, 1)
        if stats["total_rounds"] > 0 else 0
    )

    lines = [
        f"📊 <b>REPORTE DIARIO EOLO</b>",
        f"📅 {today}  |  🕐 {now_time}",
        f"{'─' * 30}",
        f"{emoji} P&amp;L del día : <b>${pnl:+.4f}</b> por acción",
        f"🔄 Operaciones : {stats['total_rounds']} round trips",
        f"✅ Wins        : {stats['total_wins']}",
        f"❌ Losses      : {stats['total_losses']}",
        f"🎯 Win rate    : {win_rate}%",
        f"{'─' * 30}",
    ]

    if stats["by_ticker"]:
        lines.append("📈 <b>Por ticker:</b>")
        for ticker, data in stats["by_ticker"].items():
            if data["rounds"] > 0:
                e = "🟢" if data["pnl"] >= 0 else "🔴"
                lines.append(
                    f"  {e} {ticker}: ${data['pnl']:+.4f} "
                    f"({data['rounds']} ops, {data['wins']}W/{data['losses']}L)"
                )
    else:
        lines.append("📭 Sin operaciones cerradas hoy")

    if stats["open_pos"]:
        lines.append(f"{'─' * 30}")
        lines.append("⚠️ <b>Posiciones abiertas al cierre:</b>")
        for p in stats["open_pos"]:
            lines.append(f"  • {p}")

    lines.append(f"{'─' * 30}")
    lines.append(f"📝 Señales totales: {stats['total_signals']}")
    return "\n".join(lines)


def daily_report(request):
    """Entry point para Cloud Function."""
    logger.info("Generando reporte diario...")

    token, chat_id = _get_telegram_credentials()
    trades         = _load_today_trades()
    stats          = _calculate_pnl(trades)
    message        = _build_message(stats)

    logger.info(f"\n{message}")
    _send_telegram(token, chat_id, message)
    logger.info("Reporte enviado ✓")
    return "Done!"
