# ============================================================
#  EOLO Crypto — Estrategia: BTC Lead-Lag
#
#  Ref: nueva estrategia crypto-native (2026-04-27)
#
#  Lógica:
#    BTC es el "alfa" del mercado crypto. Cuando BTC mueve
#    significativamente en una vela 4h, las altcoins correlacionadas
#    suelen seguir con un lag de 0–2 velas. La estrategia detecta
#    el movimiento de BTC y entra en altcoins que aún no reaccionaron.
#
#    Mecanismo:
#      1. Se evalúa BTCUSDT primero (el orquestador lo procesa).
#         `register_btc_candle(df)` actualiza el cache modular.
#      2. Cuando se evalúa una altcoin, se lee el cache de BTC.
#      3. BUY si:
#         - BTC subió > BTC_MOVE_PCT en la vela anterior (cierre-cierre)
#         - La altcoin subió < ALT_LAG_THRESHOLD (todavía no siguió)
#         - BTC no está en caída (filtro: close[-1] > open[-1])
#      4. SELL si:
#         - La altcoin ya capturó el movimiento (profit target)
#         - O BTC revirtió (filtro de seguridad)
#
#  El cache BTC se actualiza automáticamente en el orquestador via
#  `register_btc_candle(df_btc)` antes de evaluar las altcoins.
#
#  Universo : altcoins del core (NO BTCUSDT — es el líder, no el seguidor)
#  Timeframe : 4h
#  Categoría : lead-lag / momentum cross-asset
# ============================================================
import os
import time
import threading

import pandas as pd
from loguru import logger

STRATEGY_NAME = "BTC_LEAD_LAG"

# BTC debe haber movido >= este % en la vela 4h para activar
BTC_MOVE_PCT       = float(os.environ.get("BLL_BTC_MOVE",     "1.5"))  # 1.5%
# La altcoin debe haber movido <= este % (aún no siguió a BTC)
ALT_LAG_THRESHOLD  = float(os.environ.get("BLL_ALT_LAG",      "0.5"))  # 0.5%
# Profit target: cuánto esperamos que la altcoin alcance a BTC
PROFIT_TARGET_PCT  = float(os.environ.get("BLL_PROFIT",        "2.0"))  # 2%
# Stop loss
STOP_PCT           = float(os.environ.get("BLL_STOP",          "1.5"))  # 1.5%
# Ventana de validez de la señal BTC (segundos). Pasado este tiempo, la señal caduca.
BTC_SIGNAL_TTL_SEC = float(os.environ.get("BLL_TTL_SEC",       str(4 * 60 * 60)))  # 4h

# ── Cache BTC (compartido en el proceso) ──────────────────
_btc_state: dict = {
    "pct_change": None,   # % cambio cierre-cierre de la última vela 4h
    "direction":  0,      # +1 alcista, -1 bajista, 0 neutro
    "ts":         0.0,    # timestamp del último update
    "price":      None,   # precio actual de BTC
}
_btc_lock = threading.Lock()


def register_btc_candle(df: pd.DataFrame) -> None:
    """
    Llamar desde el orquestador cuando se evalúa BTCUSDT.
    Actualiza el cache con el % de cambio de la vela 4h más reciente.
    """
    if df is None or len(df) < 2:
        return
    try:
        close_curr = float(df.iloc[-1]["close"])
        close_prev = float(df.iloc[-2]["close"])
        if close_prev <= 0:
            return
        pct = (close_curr - close_prev) / close_prev * 100
        direction = 1 if close_curr > float(df.iloc[-1]["open"]) else -1

        with _btc_lock:
            _btc_state["pct_change"] = pct
            _btc_state["direction"]  = direction
            _btc_state["ts"]         = time.time()
            _btc_state["price"]      = close_curr

        logger.debug(
            f"[{STRATEGY_NAME}] BTC cache actualizado: "
            f"pct={pct:+.2f}% dir={direction:+d} price={close_curr:.2f}"
        )
    except Exception as e:
        logger.debug(f"[{STRATEGY_NAME}] register_btc_candle error: {e}")


def _get_btc_state() -> tuple[float | None, int, bool]:
    """Retorna (pct_change, direction, is_fresh)."""
    with _btc_lock:
        age    = time.time() - _btc_state["ts"]
        fresh  = age < BTC_SIGNAL_TTL_SEC
        return _btc_state["pct_change"], _btc_state["direction"], fresh


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    # No aplica a BTC mismo
    if ticker.upper() in ("BTCUSDT", "WBTCUSDT"):
        return "HOLD"
    if len(df) < 3:
        return "HOLD"

    btc_pct, btc_dir, btc_fresh = _get_btc_state()
    price = float(df.iloc[-1]["close"])

    # ── SELL: gestión de posición ────────────────────────────
    if entry_price is not None and entry_price > 0:
        pnl_pct = (price - entry_price) / entry_price * 100
        if pnl_pct >= PROFIT_TARGET_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — profit {pnl_pct:+.2f}%"
            )
            return "SELL"
        if pnl_pct <= -STOP_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL (stop) — P&L {pnl_pct:+.2f}%"
            )
            return "SELL"
        # Salir si BTC revirtió (ya no hay lead alcista)
        if btc_fresh and btc_pct is not None and btc_pct < -BTC_MOVE_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — BTC revirtió "
                f"({btc_pct:+.2f}%)"
            )
            return "SELL"
        return "HOLD"

    # ── BUY: altcoin lagged tras movimiento alcista de BTC ────
    if btc_pct is None or not btc_fresh:
        return "HOLD"

    if btc_pct < BTC_MOVE_PCT:
        return "HOLD"  # BTC no movió suficiente

    if btc_dir <= 0:
        return "HOLD"  # BTC vela bajista — no seguir

    # Calcular cuánto movió la altcoin en la misma vela
    close_curr = float(df.iloc[-1]["close"])
    close_prev = float(df.iloc[-2]["close"])
    if close_prev <= 0:
        return "HOLD"
    alt_pct = (close_curr - close_prev) / close_prev * 100

    if alt_pct > ALT_LAG_THRESHOLD:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — ya movió {alt_pct:+.2f}% "
            f"(BTC={btc_pct:+.2f}%, threshold={ALT_LAG_THRESHOLD}%)"
        )
        return "HOLD"

    logger.info(
        f"[{STRATEGY_NAME}] {ticker} BUY @ {close_curr:.4f} — "
        f"BTC={btc_pct:+.2f}%, alt lag={alt_pct:+.2f}% (aún no siguió)"
    )
    return "BUY"


def analyze(market_data, ticker: str, **kwargs) -> dict:
    """Wrapper para StrategyRunner de crypto_adapters."""
    try:
        df = (market_data.get_candles_resampled(ticker, 240)
              if hasattr(market_data, "get_candles_resampled") else market_data)
        if df is None or (hasattr(df, "empty") and df.empty):
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}
        # Actualizar cache si es BTCUSDT
        if ticker.upper() == "BTCUSDT":
            register_btc_candle(df)
        entry_price = kwargs.get("entry_price")
        signal = detect_signal(df, ticker, entry_price=entry_price)
        price  = float(df.iloc[-1]["close"]) if not df.empty else 0
        return {
            "signal":   signal,
            "ticker":   ticker,
            "price":    price,
            "reason":   f"{STRATEGY_NAME} signal={signal}",
            "strategy": STRATEGY_NAME,
        }
    except Exception as e:
        logger.error(f"[{STRATEGY_NAME}] analyze error {ticker}: {e}")
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": f"error: {e}", "strategy": STRATEGY_NAME}
