# ============================================================
#  EOLO EMA BOT — Reporte Diario
#  Lee trades_log.csv y manda resumen por Telegram a las 4:05pm ET
#  Uso: python bot_report.py
# ============================================================
import csv
import os
import requests
from datetime import datetime, date
from collections import defaultdict
from loguru import logger

# ── Config Telegram (mismas credenciales que bot_trader.py) ──
from bot_trader import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LOG_FILE


def _send_telegram(message: str):
    """Envía mensaje a Telegram."""
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Telegram error: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram fallo: {e}")


def load_today_trades() -> list:
    """Lee trades_log.csv y filtra solo los de hoy."""
    if not os.path.isfile(LOG_FILE):
        return []

    today = date.today().strftime("%Y-%m-%d")
    trades = []

    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["timestamp"].startswith(today):
                trades.append(row)

    return trades


def calculate_pnl(trades: list) -> dict:
    """
    Calcula P&L por ticker emparejando BUY con el SELL siguiente.
    Retorna dict con stats por ticker y totales.
    """
    # Agrupa trades por ticker
    by_ticker = defaultdict(list)
    for t in trades:
        by_ticker[t["ticker"]].append(t)

    results = {}
    total_pnl      = 0.0
    total_wins     = 0
    total_losses   = 0
    total_trades   = 0
    open_positions = []

    for ticker, ticker_trades in by_ticker.items():
        pnl        = 0.0
        wins       = 0
        losses     = 0
        buy_price  = None
        rounds     = 0

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

        # Posición abierta sin cerrar
        if buy_price is not None:
            open_positions.append(f"{ticker} (comprado a ${buy_price})")

        results[ticker] = {
            "pnl":    round(pnl, 4),
            "rounds": rounds,
            "wins":   wins,
            "losses": losses,
        }

        total_pnl    += pnl
        total_wins   += wins
        total_losses += losses
        total_trades += rounds

    return {
        "by_ticker":      results,
        "total_pnl":      round(total_pnl, 4),
        "total_trades":   total_trades,
        "total_wins":     total_wins,
        "total_losses":   total_losses,
        "open_positions": open_positions,
    }


def build_report(trades: list, stats: dict) -> str:
    """Arma el mensaje del reporte diario."""
    today     = date.today().strftime("%d/%m/%Y")
    now_time  = datetime.now().strftime("%H:%M ET")
    pnl       = stats["total_pnl"]
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    win_rate  = (
        round(stats["total_wins"] / stats["total_trades"] * 100, 1)
        if stats["total_trades"] > 0 else 0
    )

    lines = [
        f"📊 <b>REPORTE DIARIO EOLO</b>",
        f"📅 {today}  |  🕐 {now_time}",
        f"{'─' * 30}",
        f"{pnl_emoji} P&L del día : <b>${pnl:+.4f}</b> por acción",
        f"🔄 Operaciones: {stats['total_trades']} round trips",
        f"✅ Wins        : {stats['total_wins']}",
        f"❌ Losses      : {stats['total_losses']}",
        f"🎯 Win rate    : {win_rate}%",
        f"{'─' * 30}",
    ]

    # Detalle por ticker
    if stats["by_ticker"]:
        lines.append("📈 <b>Por ticker:</b>")
        for ticker, data in stats["by_ticker"].items():
            if data["rounds"] > 0:
                emoji = "🟢" if data["pnl"] >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {ticker}: ${data['pnl']:+.4f} "
                    f"({data['rounds']} ops, {data['wins']}W/{data['losses']}L)"
                )
    else:
        lines.append("📭 Sin operaciones cerradas hoy")

    # Posiciones abiertas al cierre
    if stats["open_positions"]:
        lines.append(f"{'─' * 30}")
        lines.append("⚠️ <b>Posiciones abiertas al cierre:</b>")
        for pos in stats["open_positions"]:
            lines.append(f"  • {pos}")

    lines.append(f"{'─' * 30}")
    lines.append(f"📝 Señales totales hoy: {len(trades)}")

    return "\n".join(lines)


def send_daily_report():
    """Función principal — llamada al cierre del mercado."""
    logger.info("Generando reporte diario...")
    trades = load_today_trades()
    stats  = calculate_pnl(trades)
    report = build_report(trades, stats)

    logger.info(f"\n{report}")
    _send_telegram(report)
    logger.info("Reporte enviado por Telegram ✓")


if __name__ == "__main__":
    send_daily_report()
