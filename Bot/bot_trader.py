# ============================================================
#  EOLO — Trader
#  Paper trading + live order placement via Schwab Trader API
#  Soporta todas las estrategias: EMA, Gap, VWAP_RSI, Bollinger, ORB
# ============================================================
import csv
import os
import time
import requests
from datetime import datetime, timezone
from loguru import logger

from helpers import retrieve_firestore_value, store_firestore_value, _invalidate_firestore_cache
from secret_stuff import collection_id, document_id, project_id

# OAuth refresh real — reemplaza el bug histórico donde _get_access_token
# solo re-leía Firestore y nunca hacía el refresh OAuth contra Schwab.
from marketdata import schwab_oauth_refresh

# Enrichment helper compartido — agrega entry_price, vix_snapshot,
# session_bucket, slippage_bps, trade_number_today, entry/exit_reason
# al payload de cada trade sin duplicar lógica entre v1/v2/crypto.
try:
    from eolo_common.trade_enrichment import build_enrichment
except Exception as _enrich_err:   # pragma: no cover
    build_enrichment = None        # type: ignore
    logger.warning(f"[TRADER] eolo_common.trade_enrichment no disponible: {_enrich_err}")

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
# Valores posibles: None (FLAT) | "LONG" | "SHORT"
positions     = {t: None  for t in ALL_TICKERS}
entry_prices  = {t: None  for t in ALL_TICKERS}  # precio de entrada
entry_open_ts = {t: None  for t in ALL_TICKERS}  # epoch seconds al BUY/SELL_SHORT

# ── Short selling flag ─────────────────────────────────────
# Controlado desde Firestore (eolo-config/settings.allow_short_selling).
# OFF por defecto — el bot opera long-only hasta que se habilite explícitamente.
# Requiere cuenta Schwab con margen y permisos de short habilitados.
ALLOW_SHORT_SELLING = False

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
            "positions":     {k: v for k, v in positions.items()},
            "entry_prices":  {k: v for k, v in entry_prices.items()},
            "entry_open_ts": {k: v for k, v in entry_open_ts.items()},
            "updated_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        saved_pos     = data.get("positions",     {})
        saved_entry   = data.get("entry_prices",  {})
        saved_open_ts = data.get("entry_open_ts", {})
        updated_at    = data.get("updated_at",    "unknown")

        # Aplicar solo los tickers que el bot conoce
        for t in ALL_TICKERS:
            if t in saved_pos:
                positions[t]     = saved_pos[t]
            if t in saved_entry:
                entry_prices[t]  = saved_entry[t]
            if t in saved_open_ts:
                entry_open_ts[t] = saved_open_ts[t]

        open_pos = [f"{t}({v})" for t, v in positions.items() if v in ("LONG", "SHORT")]
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
    # Retry-on-401 con OAuth refresh real. Antes, si el access_token estaba
    # expirado, esto devolvía None y el bot quedaba sin poder enviar órdenes
    # hasta el próximo ciclo — pero como refresh_access_token nunca refrescaba
    # de verdad, el problema se autoperpetuaba hasta que venciera el refresh_token.
    for attempt in range(2):
        token   = _get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp    = requests.get(f"{TRADER_BASE_URL}/accounts/accountNumbers", headers=headers)
        if resp.status_code == 200:
            return resp.json()[0]["hashValue"]
        if resp.status_code == 401 and attempt == 0:
            logger.warning("[TRADER] 401 en accountNumbers — OAuth refresh + retry")
            _invalidate_firestore_cache(collection_id, document_id, "access_token")
            if not schwab_oauth_refresh():
                logger.error("[TRADER] OAuth refresh falló. Correr `python3 init_auth.py`")
                return None
            continue
        logger.error(f"Failed to get account hash: {resp.status_code} {resp.text}")
        return None
    return None


def _log_trade(
    action: str,
    ticker: str,
    shares: int,
    price: float,
    strategy: str = "",
    pnl_usd: float | None = None,
    timeframe: int | None = None,
    reason: str = "",
    # ── Enrichment extras (Phase 1 — 2026-04-21) ─────────
    macro_feeds=None,                   # MacroFeeds | None → vix_snapshot
    expected_price: float | None = None,  # para slippage real en LIVE
    fill_price:     float | None = None,
    entry_price_override: float | None = None,  # entry capturado en BUY (SELL only)
    opened_at_ts:   float | None = None,  # epoch sec del BUY (SELL only)
):
    """Guarda el trade en CSV local y en Firestore.

    `pnl_usd` sólo se persiste en SELLs (en BUYs es None → no se incluye).
    Esto permite al dashboard agregar stats por estrategia (win rate / net
    profit 24h / 7d) sin matching BUY↔SELL del lado cliente.

    `timeframe` (minutos: 1, 5, 15, 30, 60, 240, 1440) y `reason` (texto libre
    devuelto por la estrategia) se persisten siempre que vengan con valor — son
    claves para el análisis por estrategia en Sheets / dashboard.

    Los campos de enrichment (entry_price, hold_seconds, vix_snapshot,
    session_bucket, slippage_bps, trade_number_today, entry/exit_reason)
    los calcula `eolo_common.trade_enrichment.build_enrichment` y se mergean
    al payload antes de persistir.
    """
    mode      = "PAPER" if PAPER_TRADING else "LIVE"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today     = datetime.now().strftime("%Y-%m-%d")
    total_usd = round(shares * price, 2)

    # ── 1. Enrichment dict (best-effort) ──────────────────
    enrich: dict = {}
    if build_enrichment is not None:
        try:
            enrich = build_enrichment(
                ts_utc         = datetime.now(timezone.utc),
                asset_class    = "stock",
                side           = action,                         # "BUY" o "SELL"
                mode           = mode,
                expected_price = expected_price if expected_price is not None else price,
                fill_price     = fill_price     if fill_price     is not None else price,
                macro_feeds    = macro_feeds,
                spy_ret_5d_fn  = None,                            # TODO: fetcher SPY 5d
                entry_price    = entry_price_override,
                opened_at_ts   = opened_at_ts,
                reason         = reason,
                counter_key    = "eolo_v1",
            )
        except Exception as e:
            logger.warning(f"[TRADER] enrichment falló: {e} — sigo sin campos extra")
            enrich = {}

    # ── 2. CSV local ──────────────────────────────────────
    csv_extras_keys = [
        "entry_price", "hold_seconds", "vix_snapshot", "vix_bucket",
        "spy_ret_5d", "session_bucket", "slippage_bps",
        "trade_number_today", "entry_reason", "exit_reason",
    ]
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp","mode","action","ticker","shares","price",
                             "total_usd","strategy","pnl_usd","timeframe","reason",
                             *csv_extras_keys])
        writer.writerow([
            timestamp, mode, action, ticker, shares, price, total_usd,
            strategy, pnl_usd if pnl_usd is not None else "",
            timeframe if timeframe is not None else "",
            reason or "",
            *[enrich.get(k, "") for k in csv_extras_keys],
        ])

    # ── 3. Firestore ──────────────────────────────────────
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
        if timeframe is not None:
            trade_payload["timeframe"] = int(timeframe)
        if reason:
            # Capamos para no inflar el doc de Firestore
            trade_payload["reason"] = str(reason)[:500]
        # Enrichment: se mergea solo con keys presentes (build_enrichment
        # ya filtra Nones, así que esto no llena Firestore con vacíos).
        if enrich:
            trade_payload.update(enrich)
        doc.set({f"{timestamp}_{ticker}_{action}": trade_payload}, merge=True)
    except Exception as e:
        logger.warning(f"Firestore trade log fallo (CSV ok): {e}")

    pnl_part = f" pnl=${pnl_usd:+.2f}" if pnl_usd is not None else ""
    tf_part  = f" tf={timeframe}m" if timeframe is not None else ""
    sess_part = f" sess={enrich.get('session_bucket')}" if enrich.get("session_bucket") else ""
    logger.info(f"[{mode}] {action} {shares}x {ticker} @ ${price} (${total_usd}){pnl_part}{tf_part}{sess_part} [{strategy}] ✓")


def _place_live_order(action: str, ticker: str, shares: int):
    """Places a real market order via Schwab Trader API.

    action puede ser:
      "BUY"           → abrir long (instruction: BUY)
      "SELL"          → cerrar long (instruction: SELL)
      "SELL_SHORT"    → abrir short (instruction: SELL_SHORT)
      "BUY_TO_COVER"  → cerrar short (instruction: BUY_TO_COVER)

    SELL_SHORT y BUY_TO_COVER requieren cuenta con margen habilitado en Schwab
    (Account Settings → Margin & Options → Margin Trading).
    """
    # Mapeo acción interna → instrucción Schwab
    _INSTRUCTION_MAP = {
        "BUY":          "BUY",
        "SELL":         "SELL",
        "SELL_SHORT":   "SELL_SHORT",
        "BUY_TO_COVER": "BUY_TO_COVER",
    }
    instruction = _INSTRUCTION_MAP.get(action)
    if instruction is None:
        logger.error(f"[LIVE] Instrucción desconocida: {action} — orden cancelada")
        return

    account_hash = _get_account_hash()
    if not account_hash:
        logger.error("Cannot place order — no account hash.")
        return

    order   = {
        "orderType":         "MARKET",
        # SEAMLESS permite ejecución en pre-market (4:00-9:30 ET) y after-hours
        # (16:00-20:00 ET) además de RTH. Requiere "Extended Hours Trading"
        # habilitado en la cuenta Schwab (Service → Account Settings → Trading).
        "session":           "SEAMLESS",
        "duration":          "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": instruction,
            "quantity":    shares,
            "instrument":  {"symbol": ticker, "assetType": "EQUITY"},
        }],
    }

    # Retry-on-401 con OAuth refresh real.
    for attempt in range(2):
        token   = _get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post(
            f"{TRADER_BASE_URL}/accounts/{account_hash}/orders",
            headers=headers, json=order,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[LIVE] Order placed: {action} ({instruction}) {shares}x {ticker} ✓")
            return
        if resp.status_code == 401 and attempt == 0:
            logger.warning(f"[LIVE] 401 en order {ticker} — OAuth refresh + retry")
            _invalidate_firestore_cache(collection_id, document_id, "access_token")
            if not schwab_oauth_refresh():
                logger.error("[LIVE] OAuth refresh falló. Correr `python3 init_auth.py`")
                return
            continue
        logger.error(f"[LIVE] Order FAILED: {resp.status_code} {resp.text}")
        return


# ── Main execute function ──────────────────────────────────

def execute(result: dict):
    """
    Lee la señal y maneja la lógica de posición.

    Transiciones de estado:
      FLAT  + BUY  → abre LONG
      LONG  + SELL → cierra LONG (→ FLAT)
      FLAT  + SELL → abre SHORT  (solo si _allow_short=True)
      SHORT + BUY  → cierra SHORT (BUY_TO_COVER) (→ FLAT)

    Reversals (SHORT→LONG o LONG→SHORT) se hacen en dos ciclos:
      Ciclo 1: cierra posición actual → FLAT
      Ciclo 2: abre nueva posición en dirección opuesta
    Esto evita órdenes compuestas y simplifica el riesgo.
    """
    ticker      = result["ticker"]
    signal      = result["signal"]
    price       = result["price"]
    strategy    = result.get("strategy", "")
    current     = positions.get(ticker)
    budget      = result.get("_budget")
    tf_min      = result.get("_timeframe")
    reason      = result.get("reason", "")
    macro       = result.get("_macro_feeds")
    allow_short = result.get("_allow_short", False)
    shares      = calculate_shares(price, budget)
    mode        = "📄 PAPER" if PAPER_TRADING else "💰 LIVE"
    total       = shares * price

    # ── BUY ───────────────────────────────────────────────
    if signal == "BUY":
        if current == "SHORT":
            # Cerrar short (BUY TO COVER) → FLAT
            entry     = entry_prices.get(ticker)
            opened_at = entry_open_ts.get(ticker)
            pnl       = round((entry - price) * shares, 2) if entry else None  # ganancia si bajó
            pnl_str   = f"${pnl:+.2f}" if pnl is not None else "—"
            _log_trade("BUY_TO_COVER", ticker, shares, price, strategy, pnl_usd=pnl,
                       timeframe=tf_min, reason=reason,
                       macro_feeds=macro, expected_price=price, fill_price=price,
                       entry_price_override=entry, opened_at_ts=opened_at)
            if not PAPER_TRADING:
                _place_live_order("BUY_TO_COVER", ticker, shares)
            positions[ticker]     = None
            entry_prices[ticker]  = None
            entry_open_ts[ticker] = None
            save_positions()
            _send_telegram(
                f"🟡 <b>COVER SHORT — {strategy}</b>\n"
                f"📈 Ticker   : <b>{ticker}</b>\n"
                f"💵 Precio   : <b>${price}</b>\n"
                f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
                f"💰 P&amp;L    : <b>{pnl_str}</b>\n"
                f"🕐 Hora     : {datetime.now().strftime('%H:%M:%S ET')}\n"
                f"🔖 Modo     : {mode}"
            )

        elif current != "LONG":
            # Abrir LONG (desde FLAT)
            _log_trade("BUY", ticker, shares, price, strategy,
                       timeframe=tf_min, reason=reason,
                       macro_feeds=macro, expected_price=price, fill_price=price)
            if not PAPER_TRADING:
                _place_live_order("BUY", ticker, shares)
            positions[ticker]     = "LONG"
            entry_prices[ticker]  = price
            entry_open_ts[ticker] = time.time()
            save_positions()
            _send_telegram(
                f"🟢 <b>SEÑAL BUY — {strategy}</b>\n"
                f"📈 Ticker   : <b>{ticker}</b>\n"
                f"💵 Precio   : <b>${price}</b>\n"
                f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
                f"🕐 Hora     : {datetime.now().strftime('%H:%M:%S ET')}\n"
                f"🔖 Modo     : {mode}"
            )

    # ── SELL ──────────────────────────────────────────────
    elif signal == "SELL":
        if current == "LONG":
            # Cerrar LONG → FLAT
            entry     = entry_prices.get(ticker)
            opened_at = entry_open_ts.get(ticker)
            pnl       = round((price - entry) * shares, 2) if entry else None
            pnl_str   = f"${pnl:+.2f}" if pnl is not None else "—"
            _log_trade("SELL", ticker, shares, price, strategy, pnl_usd=pnl,
                       timeframe=tf_min, reason=reason,
                       macro_feeds=macro, expected_price=price, fill_price=price,
                       entry_price_override=entry, opened_at_ts=opened_at)
            if not PAPER_TRADING:
                _place_live_order("SELL", ticker, shares)
            positions[ticker]     = None
            entry_prices[ticker]  = None
            entry_open_ts[ticker] = None
            save_positions()
            _send_telegram(
                f"🔴 <b>SEÑAL SELL — {strategy}</b>\n"
                f"📉 Ticker   : <b>{ticker}</b>\n"
                f"💵 Precio   : <b>${price}</b>\n"
                f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
                f"💰 P&amp;L    : <b>{pnl_str}</b>\n"
                f"🕐 Hora     : {datetime.now().strftime('%H:%M:%S ET')}\n"
                f"🔖 Modo     : {mode}"
            )

        elif current != "SHORT" and allow_short:
            # Abrir SHORT (desde FLAT, solo si allow_short=True)
            _log_trade("SELL_SHORT", ticker, shares, price, strategy,
                       timeframe=tf_min, reason=reason,
                       macro_feeds=macro, expected_price=price, fill_price=price)
            if not PAPER_TRADING:
                _place_live_order("SELL_SHORT", ticker, shares)
            positions[ticker]     = "SHORT"
            entry_prices[ticker]  = price
            entry_open_ts[ticker] = time.time()
            save_positions()
            _send_telegram(
                f"🔻 <b>SEÑAL SHORT — {strategy}</b>\n"
                f"📉 Ticker   : <b>{ticker}</b>\n"
                f"💵 Precio   : <b>${price}</b>\n"
                f"📦 Acciones : {shares} (≈ ${total:.0f})\n"
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
