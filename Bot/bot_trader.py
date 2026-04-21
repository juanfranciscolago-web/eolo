# ============================================================
#  EOLO — Trader
#  Paper trading + live order placement via Schwab Trader API
#  Soporta todas las estrategias: EMA, Gap, VWAP_RSI, Bollinger, ORB
# ============================================================
import csv
import os
import requests
from datetime import datetime
from loguru import logger

from helpers import retrieve_firestore_value, store_firestore_value
from secret_stuff import collection_id, document_id, project_id

# ── Settings ──────────────────────────────────────────────
PAPER_TRADING        = True    # PAPER — simulación (flip a False cuando quieras ir live)
TRADE_BUDGET_USD     = 100     # USD por trade — se sobreescribe desde Firestore en cada ciclo
TRADE_BUDGET_MAX_USD = 5000    # techo absoluto de seguridad
TRADER_BASE_URL      = "https://api.schwabapi.com/trader/v1"
LOG_FILE             = "trades_log.csv"

# ── Telegram config ────────────────────────────────────────
TELEGRAM_TOKEN   = "8207559403:AAGwiQS15APh3ivFsAUUu_DCMbltMoDYV-o"
TELEGRAM_CHAT_ID = "5802788501"
TELEGRAM_ENABLED = True

# ── Tickers — todos los que puede operar el bot ────────────
TICKERS_EMA_GAP   = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]
TICKERS_LEVERAGED = ["SOXL", "TSLL", "NVDL", "TQQQ"]
ALL_TICKERS       = TICKERS_EMA_GAP + TICKERS_LEVERAGED

# ── Position state (por ticker) ────────────────────────────
# IMPORTANTE: se persiste en Firestore para sobrevivir reinicios de Cloud Run
positions    = {t: None  for t in ALL_TICKERS}  # None = FLAT, "LONG" = in position
entry_prices = {t: None  for t in ALL_TICKERS}  # precio de entrada (para ORB stop/tp)

POSITIONS_COLLECTION = "eolo-config"
POSITIONS_DOC        = "positions"


# ── Persistencia de posiciones en Firestore ───────────────

def save_positions():
    """
    Guarda el estado de posiciones en Firestore para sobrevivir
    reinicios de Cloud Run. Se llama después de cada BUY o SELL.
    """
    try:
        from google.cloud import firestore
        from secret_stuff import project_id
        db  = firestore.Client(project=project_id)
        doc = db.collection(POSITIONS_COLLECTION).document(POSITIONS_DOC)
        doc.set({
            "positions":    {k: v for k, v in positions.items()},
            "entry_prices": {k: v for k, v in entry_prices.items()},
            "updated_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        logger.debug(f"Positions saved to Firestore: {[t for t,v in positions.items() if v]}")
    except Exception as e:
        logger.warning(f"Could not save positions to Firestore: {e}")


def load_positions():
    """
    Carga el estado de posiciones desde Firestore al arrancar el bot.
    Previene doble-compra después de un reinicio de Cloud Run.
    Retorna True si encontró posiciones guardadas.
    """
    try:
        from google.cloud import firestore
        from secret_stuff import project_id
        db  = firestore.Client(project=project_id)
        doc = db.collection(POSITIONS_COLLECTION).document(POSITIONS_DOC).get()
        if not doc.exists:
            logger.info("No saved positions found in Firestore — starting fresh (all FLAT)")
            return False
        data = doc.to_dict() or {}
        saved_pos    = data.get("positions",    {})
        saved_entry  = data.get("entry_prices", {})
        updated_at   = data.get("updated_at",   "unknown")

        # Aplicar solo los tickers que el bot conoce
        for t in ALL_TICKERS:
            if t in saved_pos:
                positions[t]    = saved_pos[t]
            if t in saved_entry:
                entry_prices[t] = saved_entry[t]

        open_pos = [t for t, v in positions.items() if v == "LONG"]
        logger.info(f"✅ Positions loaded from Firestore (saved at {updated_at})")
        if open_pos:
            logger.info(f"   Open positions restored: {open_pos}")
        else:
            logger.info("   No open positions — all FLAT")
        return True
    except Exception as e:
        logger.warning(f"Could not load positions from Firestore: {e}")
        return False


# ── Budget — leído dinámicamente desde Firestore ──────────

def get_trade_budget() -> float:
    """
    Lee el budget por trade desde Firestore (eolo-config/settings).
    Si la lectura falla, usa el valor en memoria TRADE_BUDGET_USD.
    """
    try:
        from google.cloud import firestore
        from secret_stuff import project_id
        db  = firestore.Client(project=project_id)
        doc = db.collection("eolo-config").document("settings").get()
        if doc.exists:
            budget = float(doc.to_dict().get("budget", TRADE_BUDGET_USD))
            return max(10, min(TRADE_BUDGET_MAX_USD, budget))
    except Exception as e:
        logger.warning(f"Could not read budget from Firestore: {e}")
    return TRADE_BUDGET_USD


# ── Share calculation ─────────────────────────────────────

def calculate_shares(price: float, budget: float = None) -> int:
    """
    Calcula cuántas acciones enteras comprar con el budget activo.
    - budget se lee de Firestore via get_trade_budget() si no se pasa.
    - Si precio > budget → 1 acción (mínimo garantizado).
    - Ejemplo: budget=$100, precio=$18 → int(100/18) = 5 acciones
    - Ejemplo: budget=$100, precio=$450 → 1 acción (SPY)
    """
    if not price or price <= 0:
        return 1
    b      = budget if budget is not None else TRADE_BUDGET_USD
    shares = int(b / price)
    return max(1, shares)


# ── Telegram ──────────────────────────────────────────────

def _send_telegram(message: str):
    """Envía un mensaje al chat de Telegram configurado."""
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Telegram error: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram fallo: {e}")


# ── Helpers ────────────────────────────────────────────────

def _get_access_token() -> str:
    return retrieve_firestore_value(
        collection_id=collection_id,
        document_id=document_id,
        key="access_token",
    )


def _get_account_hash() -> str:
    token   = _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp    = requests.get(f"{TRADER_BASE_URL}/accounts/accountNumbers", headers=headers)
    if resp.status_code == 200:
        return resp.json()[0]["hashValue"]
    logger.error(f"Failed to get account hash: {resp.status_code} {resp.text}")
    return None


def _log_trade(
    action: str,
    ticker: str,
    shares: int,
    price: float,
    strategy: str = "",
    pnl_usd: float | None = None,
):
    """Guarda el trade en CSV local y en Firestore.

    `pnl_usd` sólo se persiste en SELLs (en BUYs es None → no se incluye).
    Esto permite al dashboard agregar stats por estrategia (win rate / net
    profit 24h / 7d) sin matching BUY↔SELL del lado cliente.
    """
    mode      = "PAPER" if PAPER_TRADING else "LIVE"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today     = datetime.now().strftime("%Y-%m-%d")
    total_usd = round(shares * price, 2)

    # ── 1. CSV local ──────────────────────────────────────
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp","mode","action","ticker","shares","price","total_usd","strategy","pnl_usd"])
        writer.writerow([timestamp, mode, action, ticker, shares, price, total_usd, strategy, pnl_usd if pnl_usd is not None else ""])

    # ── 2. Firestore ──────────────────────────────────────
    try:
        from google.cloud import firestore
        db  = firestore.Client(project=project_id)
        doc = db.collection("eolo-trades").document(today)
        trade_payload = {
            "timestamp": timestamp,
            "mode":      mode,
            "action":    action,
            "ticker":    ticker,
            "shares":    shares,
            "price":     price,
            "total_usd": total_usd,
            "strategy":  strategy,
        }
        if pnl_usd is not None:
            trade_payload["pnl_usd"] = float(pnl_usd)
        doc.set({f"{timestamp}_{ticker}_{action}": trade_payload}, merge=True)
    except Exception as e:
        logger.warning(f"Firestore trade log fallo (CSV ok): {e}")

    pnl_part = f" pnl=${pnl_usd:+.2f}" if pnl_usd is not None else ""
    logger.info(f"[{mode}] {action} {shares}x {ticker} @ ${price} (${total_usd}){pnl_part} [{strategy}] ✓")


def _place_live_order(action: str, ticker: str, shares: int):
    """Places a real market order via Schwab Trader API."""
    account_hash = _get_account_hash()
    if not account_hash:
        logger.error("Cannot place order — no account hash.")
        return

    token   = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    order   = {
        "orderType":         "MARKET",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": "BUY" if action == "BUY" else "SELL",
            "quantity":    shares,
            "instrument":  {"symbol": ticker, "assetType": "EQUITY"},
        }],
    }

    resp = requests.post(
        f"{TRADER_BASE_URL}/accounts/{account_hash}/orders",
        headers=headers, json=order,
    )
    if resp.status_code in (200, 201):
        logger.info(f"[LIVE] Order placed: {action} {shares}x {ticker} ✓")
    else:
        logger.error(f"[LIVE] Order FAILED: {resp.status_code} {resp.text}")


# ── Main execute function ──────────────────────────────────

def execute(result: dict):
    """
    Lee la señal y maneja la lógica de posición.
    - Calcula acciones automáticamente según TRADE_BUDGET_USD / precio
    - Evita doble compra o venta cuando ya está FLAT
    - Registra el precio de entrada para estrategias con take profit/stop loss (ORB)
    """
    ticker   = result["ticker"]
    signal   = result["signal"]
    price    = result["price"]
    strategy = result.get("strategy", "")
    current  = positions.get(ticker)
    budget   = result.get("_budget")  # injected by bot_main per cycle
    shares   = calculate_shares(price, budget)
    mode     = "📄 PAPER" if PAPER_TRADING else "💰 LIVE"
    total    = shares * price

    if signal == "BUY" and current != "LONG":
        _log_trade("BUY", ticker, shares, price, strategy)
        if not PAPER_TRADING:
            _place_live_order("BUY", ticker, shares)
        positions[ticker]    = "LONG"
        entry_prices[ticker] = price
        save_positions()   # ← persiste en Firestore para sobrevivir reinicios
        _send_telegram(
            f"🟢 <b>SEÑAL BUY — {strategy}</b>\n"
            f"📈 Ticker   : <b>{ticker}</b>\n"
            f"💵 Precio   : <b>${price}</b>\n"
            f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
            f"🕐 Hora     : {datetime.now().strftime('%H:%M:%S ET')}\n"
            f"🔖 Modo     : {mode}"
        )

    elif signal == "SELL" and current == "LONG":
        entry = entry_prices.get(ticker)
        pnl   = round((price - entry) * shares, 2) if entry else None
        pnl_str = f"${pnl:+.2f}" if pnl is not None else "—"

        _log_trade("SELL", ticker, shares, price, strategy, pnl_usd=pnl)
        if not PAPER_TRADING:
            _place_live_order("SELL", ticker, shares)
        positions[ticker]    = None
        entry_prices[ticker] = None
        save_positions()   # ← persiste en Firestore para sobrevivir reinicios
        _send_telegram(
            f"🔴 <b>SEÑAL SELL — {strategy}</b>\n"
            f"📉 Ticker   : <b>{ticker}</b>\n"
            f"💵 Precio   : <b>${price}</b>\n"
            f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
            f"💰 P&amp;L    : <b>{pnl_str}</b>\n"
            f"🕐 Hora     : {datetime.now().strftime('%H:%M:%S ET')}\n"
            f"🔖 Modo     : {mode}"
        )


def print_status(result: dict):
    """Imprime una línea de estado para cada ticker escaneado."""
    t        = result["ticker"]
    sig      = result["signal"]
    strategy = result.get("strategy", "???")
    px       = str(result.get("price") or "—")
    pos      = positions.get(t) or "FLAT"
    mode     = "📄PAPER" if PAPER_TRADING else "💰LIVE"

    print(f"  {mode} | {strategy:<10} | {t:<5} | {sig:<5} | ${px:<10} | {pos}")
