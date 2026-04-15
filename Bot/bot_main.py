# ============================================================
#  EOLO Bot — Main Runner
#  10 estrategias activables desde el dashboard o Firestore:
#    - EMA 3/8 Crossover   (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Gap Fade/Follow      (SPY, QQQ, AAPL, TSLA, NVDA)
#    - VWAP + RSI           (SOXL, TSLL, NVDL, TQQQ)
#    - Bollinger Bands      (SOXL, TSLL, NVDL, TQQQ)
#    - Opening Range Break  (SOXL, TSLL, NVDL, TQQQ)
#    - RSI + SMA200         (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Supertrend           (SOXL, TSLL, NVDL, TQQQ)
#    - High/Low Breakout    (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Heikin Ashi Cloud    (SOXL, TSLL, NVDL, TQQQ)
#    - Bollinger/Keltner Squeeze (SOXL, TSLL, NVDL, TQQQ)
#  Correr: python bot_main.py
# ============================================================
import time
from datetime import datetime, timedelta
import pytz
from loguru import logger
from google.cloud import firestore

from marketdata import MarketData
import bot_strategy         as ema_strategy
import bot_gap_strategy     as gap_strategy
import bot_vwap_rsi_strategy as vwap_strategy
import bot_bollinger_strategy as bollinger_strategy
import bot_orb_strategy        as orb_strategy
import bot_rsi_sma200_strategy as rsi_sma200_strategy
import bot_supertrend_strategy as supertrend_strategy
import bot_hh_ll_strategy     as hh_ll_strategy
import bot_ha_cloud_strategy  as ha_cloud_strategy
import bot_squeeze_strategy   as squeeze_strategy
import bot_trader             as trader

# ── Tickers por grupo ─────────────────────────────────────
TICKERS_EMA_GAP   = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]
TICKERS_LEVERAGED = ["SOXL", "TSLL", "NVDL", "TQQQ"]

# ── Timing ───────────────────────────────────────────────
CANDLE_MINUTES   = 1   # velas de 1 minuto — señales 5x más frecuentes
CANDLE_BUFFER_S  = 5   # 5s de buffer — Schwab tarda 2-10s en publicar la vela
MARKET_OPEN_ET   = (9, 30)
MARKET_CLOSE_ET  = (16, 0)
EASTERN          = pytz.timezone("America/New_York")
GCP_PROJECT      = "eolo-schwab-agent"

# ── Config por defecto ────────────────────────────────────
DEFAULT_STRATEGIES = {
    "ema_crossover":   True,
    "sma200_filter":   True,   # filtro de tendencia para EMA crossover
    "gap_fade":        True,
    "vwap_rsi":        True,
    "bollinger":       True,
    "orb":             True,
    "rsi_sma200":      True,
    "supertrend":      True,
    "hh_ll":           True,
    "ha_cloud":        True,
    "squeeze":         True,
}


# ── Firestore strategy config ─────────────────────────────

def get_active_strategies() -> dict:
    """
    Lee qué estrategias están habilitadas desde Firestore.
    Se actualiza desde el dashboard sin reiniciar el bot.
    """
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("strategies").get()
        if doc.exists:
            config = doc.to_dict()
            # Asegurar que todos los keys existan
            for k, v in DEFAULT_STRATEGIES.items():
                config.setdefault(k, v)
            return config
    except Exception as e:
        logger.warning(f"Could not read strategy config: {e}")
    return DEFAULT_STRATEGIES.copy()


def get_global_settings() -> dict:
    """
    Lee settings globales desde Firestore (eolo-config/settings):
      - bot_active : bool  — si es False el bot no opera (pausa desde dashboard)
      - close_all  : bool  — si es True cierra todas las posiciones abiertas
      - budget     : float — USD por trade
    """
    defaults = {"bot_active": True, "close_all": False, "budget": 100}
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("settings").get()
        if doc.exists:
            data = doc.to_dict() or {}
            defaults.update(data)
    except Exception as e:
        logger.warning(f"Could not read global settings: {e}")
    return defaults


def clear_close_all_flag():
    """Limpia el flag close_all en Firestore después de ejecutarlo."""
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("settings")
        doc.set({"close_all": False}, merge=True)
    except Exception as e:
        logger.warning(f"Could not clear close_all flag: {e}")


def get_active_tickers(group: list) -> list:
    """
    Filtra la lista de tickers según lo habilitado en Firestore (eolo-config/tickers).
    Si un ticker no aparece en config → se asume activo (True).
    Si Firestore no responde → devuelve el grupo completo.
    """
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("tickers").get()
        if doc.exists:
            config = doc.to_dict()
            active = [t for t in group if config.get(t, True)]
            return active if active else group  # nunca devolver lista vacía
    except Exception as e:
        logger.warning(f"Could not read ticker config: {e}")
    return group  # fallback: todos activos


# ── Candle-aligned timing ─────────────────────────────────

def seconds_to_next_candle() -> float:
    """
    Calcula cuántos segundos faltan para el próximo cierre de vela,
    alineado al reloj real del mercado (9:30, 9:35, 9:40...).
    Agrega CANDLE_BUFFER_S de margen para que el dato llegue de Schwab.
    """
    now     = datetime.now(EASTERN)
    elapsed = (now.minute % CANDLE_MINUTES) * 60 + now.second + now.microsecond / 1e6
    return (CANDLE_MINUTES * 60 - elapsed) + CANDLE_BUFFER_S


# ── Market hours ──────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=MARKET_OPEN_ET[0],  minute=MARKET_OPEN_ET[1],  second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE_ET[0], minute=MARKET_CLOSE_ET[1], second=0, microsecond=0)
    return open_t <= now < close_t


# ── One scan cycle ────────────────────────────────────────

def close_all_open_positions(market_data: MarketData, budget: float):
    """Cierra en market order todas las posiciones con estado LONG."""
    logger.warning("🚨 CLOSE ALL — cerrando todas las posiciones abiertas")
    all_tickers = TICKERS_EMA_GAP + TICKERS_LEVERAGED
    closed = 0
    for ticker in all_tickers:
        if trader.positions.get(ticker) == "LONG":
            try:
                # Usar precio en tiempo real para el cierre
                price = market_data.get_quote(ticker)
                if not price:
                    # Fallback: último cierre de vela
                    candles = market_data.get_candles(ticker, period_type="day", period=1)
                    price = float(candles["close"].iloc[-1]) if candles is not None and not candles.empty else 0
                if price and price > 0:
                    result = {"ticker": ticker, "signal": "SELL",
                              "price": price, "strategy": "CLOSE_ALL", "_budget": budget}
                    trader.execute(result)
                    closed += 1
            except Exception as e:
                logger.error(f"  Error closing {ticker}: {e}")
    logger.info(f"🚨 CLOSE ALL — {closed} posiciones cerradas")
    clear_close_all_flag()


def run_cycle(market_data: MarketData, settings: dict):
    now        = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")
    mode       = "📄 PAPER" if trader.PAPER_TRADING else "💰 LIVE"
    strategies = get_active_strategies()
    budget     = float(settings.get("budget", 100))

    # Leer tickers activos desde Firestore (dashboard toggle)
    tickers_ema_gap   = get_active_tickers(TICKERS_EMA_GAP)
    tickers_leveraged = get_active_tickers(TICKERS_LEVERAGED)

    active = [name for name, enabled in strategies.items() if enabled]
    active_str = " + ".join(active) if active else "ninguna activa"

    print(f"\n{'='*76}")
    print(f"  {mode}  |  {now}")
    print(f"  Estrategias activas : {active_str}")
    print(f"  Budget por trade    : ${budget:.0f}")
    print(f"  Clásicas activas   : {tickers_ema_gap}")
    print(f"  Apalancadas activas : {tickers_leveraged}")
    print(f"{'='*76}")

    def _exec(result, strat_name):
        result["strategy"] = strat_name
        result["_budget"]  = budget
        trader.print_status(result)

        if result["signal"] not in ("HOLD", "ERROR"):
            ticker = result["ticker"]

            # ── Precio de ejecución en tiempo real ────────
            # La señal se detecta sobre el cierre de la vela (histórico),
            # pero la orden se ejecuta al precio de mercado ACTUAL.
            # Esto evita comprar al precio de una vela cerrada hace 15-30s.
            live_price = market_data.get_quote(ticker)
            if live_price and live_price > 0:
                candle_price = result["price"]
                slippage     = round(live_price - candle_price, 4)
                logger.info(
                    f"[{strat_name}] {ticker} — "
                    f"vela={candle_price} quote={live_price} "
                    f"slippage={slippage:+.4f}"
                )
                result["price"] = live_price   # ejecutar al precio real
            else:
                logger.warning(f"[{strat_name}] {ticker} — quote falló, usando precio de vela")

            trader.execute(result)

    # ── EMA 3/8 Crossover (+ filtro SMA200 opcional) ─────
    if strategies.get("ema_crossover"):
        use_sma200 = strategies.get("sma200_filter", True)
        filter_str = "SMA200 ON" if use_sma200 else "SMA200 OFF"
        print(f"\n  [EMA 3/8 | {filter_str}] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = ema_strategy.analyze(market_data, ticker,
                                          use_sma200_filter=use_sma200)
            _exec(result, "EMA")

    # ── Gap Fade ─────────────────────────────────────────
    if strategies.get("gap_fade"):
        print(f"\n  [GAP] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = gap_strategy.analyze(market_data, ticker)
            _exec(result, "GAP")

    # ── VWAP + RSI ───────────────────────────────────────
    if strategies.get("vwap_rsi"):
        print(f"\n  [VWAP_RSI] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = vwap_strategy.analyze(market_data, ticker)
            _exec(result, "VWAP+RSI")

    # ── Bollinger Bands ──────────────────────────────────
    if strategies.get("bollinger"):
        print(f"\n  [BOLLINGER] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = bollinger_strategy.analyze(market_data, ticker)
            _exec(result, "BOLLINGER")

    # ── Opening Range Breakout ───────────────────────────
    if strategies.get("orb"):
        print(f"\n  [ORB] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            entry_price = trader.entry_prices.get(ticker)
            result      = orb_strategy.analyze(market_data, ticker, entry_price)
            _exec(result, "ORB")

    # ── RSI + SMA200 ─────────────────────────────────────
    if strategies.get("rsi_sma200"):
        print(f"\n  [RSI_SMA200] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = rsi_sma200_strategy.analyze(market_data, ticker)
            _exec(result, "RSI_SMA200")

    # ── Supertrend ───────────────────────────────────────
    if strategies.get("supertrend"):
        print(f"\n  [SUPERTREND] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = supertrend_strategy.analyze(market_data, ticker)
            _exec(result, "SUPERTREND")

    # ── High/Low Breakout ────────────────────────────────
    if strategies.get("hh_ll"):
        print(f"\n  [HH_LL] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = hh_ll_strategy.analyze(market_data, ticker)
            _exec(result, "HH_LL")

    # ── Heikin Ashi + EMA Cloud ──────────────────────────
    if strategies.get("ha_cloud"):
        print(f"\n  [HA_CLOUD] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = ha_cloud_strategy.analyze(market_data, ticker)
            _exec(result, "HA_CLOUD")

    # ── Bollinger/Keltner Squeeze ────────────────────────
    if strategies.get("squeeze"):
        print(f"\n  [SQUEEZE] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = squeeze_strategy.analyze(market_data, ticker)
            _exec(result, "SQUEEZE")

    wait      = seconds_to_next_candle()
    next_time = (datetime.now(EASTERN) + timedelta(seconds=wait)).strftime("%H:%M:%S")
    print(f"\n{'='*76}")
    print(f"  ⏱  Próxima vela: {next_time} ET  ({wait:.0f}s)\n")


def interruptible_sleep(seconds: float, check_interval: float = 3.0):
    """
    Duerme `seconds` segundos pero chequea Firestore cada `check_interval`
    para reaccionar rápido a close_all o bot_active=False desde el dashboard.
    Retorna True si se interrumpió (close_all detectado), False si durmió completo.
    """
    elapsed = 0.0
    while elapsed < seconds:
        chunk = min(check_interval, seconds - elapsed)
        time.sleep(chunk)
        elapsed += chunk

        # Chequeo rápido de flags urgentes
        try:
            db   = firestore.Client(project=GCP_PROJECT)
            snap = db.collection("eolo-config").document("settings").get()
            if snap.exists:
                cfg = snap.to_dict() or {}
                if cfg.get("close_all"):
                    logger.info("⚡ close_all detectado durante sleep — interrumpiendo espera")
                    return True   # salir del sleep inmediatamente
        except Exception:
            pass   # si Firestore falla, seguir durmiendo normal

    return False


# ── Main ──────────────────────────────────────────────────

def main():
    logger.info("🚀 EOLO Bot arrancando... (10 estrategias)")
    logger.info(f"   EMA/Gap/RSI_SMA200/HH_LL : {TICKERS_EMA_GAP}")
    logger.info(f"   VWAP/BB/ORB/ST/HA/SQ     : {TICKERS_LEVERAGED}")
    logger.info(f"   Budget   : ${trader.TRADE_BUDGET_USD} por trade")
    logger.info(f"   Modo     : {'PAPER' if trader.PAPER_TRADING else 'LIVE'}")

    # Inicializar / sincronizar config de estrategias en Firestore
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("strategies")
        snap = doc.get()
        if not snap.exists:
            doc.set(DEFAULT_STRATEGIES)
            logger.info(f"   Firestore config inicializado: {DEFAULT_STRATEGIES}")
        else:
            existing = snap.to_dict()
            # Agregar keys que faltan (nuevas estrategias agregadas al código)
            missing = {k: v for k, v in DEFAULT_STRATEGIES.items() if k not in existing}
            if missing:
                doc.set(missing, merge=True)
                logger.info(f"   Firestore config: nuevas estrategias agregadas: {missing}")
            # Log del estado actual
            active = [k for k, v in existing.items() if v and k != "sma200_filter"]
            logger.info(f"   Estrategias activas en Firestore: {active}")
    except Exception as e:
        logger.warning(f"   No se pudo inicializar Firestore config: {e}")

    # ── Restaurar posiciones desde Firestore ──────────────
    # Previene doble-compra si Cloud Run reinicia el container
    trader.load_positions()

    market_data = MarketData()

    while True:
        if is_market_open():
            # ── Leer settings globales cada ciclo ─────────
            settings = get_global_settings()

            # ── Close All flag ─────────────────────────────
            if settings.get("close_all"):
                try:
                    close_all_open_positions(market_data, float(settings.get("budget", 100)))
                except Exception as e:
                    logger.error(f"Close all error: {e}")
                    clear_close_all_flag()

            # ── Bot Active check ───────────────────────────
            if not settings.get("bot_active", True):
                now_str = datetime.now(EASTERN).strftime("%H:%M ET")
                logger.info(f"[{now_str}] ⏸  Bot PAUSADO desde el dashboard — skipping cycle")
                # Sleep interrumpible: reacciona a close_all/reactivación en ≤3s
                interruptible_sleep(seconds_to_next_candle())
                continue

            # ── Run normal cycle ───────────────────────────
            try:
                run_cycle(market_data, settings)
            except Exception as e:
                logger.error(f"Cycle error: {e}")

            # Sleep interrumpible — reacciona a close_all en ≤3s
            interruptible_sleep(seconds_to_next_candle())

        else:
            now_str = datetime.now(EASTERN).strftime("%H:%M ET")
            logger.info(f"[{now_str}] Mercado cerrado — esperando apertura...")
            interruptible_sleep(60)


if __name__ == "__main__":
    main()
