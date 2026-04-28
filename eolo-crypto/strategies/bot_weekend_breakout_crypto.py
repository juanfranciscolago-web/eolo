# ============================================================
#  EOLO Crypto — Estrategia: Weekend Breakout
#
#  Ref: nueva estrategia crypto-native (2026-04-27)
#
#  Lógica:
#    Crypto opera 24/7 incluyendo fines de semana. Pero el
#    "weekend drift" es real: cuando BTC/ETH cierran el viernes
#    en zona alcista, la primera sesión del domingo tiende a
#    continuar el momentum (ausencia de operadores institucionales
#    de cobertura = menor resistencia vendedora).
#
#    BUY  : es domingo UTC, primera vela de la semana,
#           AND viernes cerró bullish (close > open + MIN_BULL_PCT%)
#           AND precio actual rompió el máximo del viernes
#
#    SELL : es lunes UTC ≥ 09:00, cerrar posición overnight
#           OR si cae > STOP_PCT% desde entry (no se confirmó)
#
#    Filtros:
#      - Solo para BTCUSDT y ETHUSDT (los más predecibles el weekend)
#      - No abrir si ya hay posición abierta en el símbolo
#
#  Universo : BTCUSDT, ETHUSDT
#  Timeframe : 4h (evaluado al cierre de vela 240m)
#  Categoría : weekend / time-based momentum
# ============================================================
import os
from datetime import datetime, timezone

import pandas as pd
from loguru import logger

STRATEGY_NAME   = "WEEKEND_BREAKOUT"
ELIGIBLE_TICKERS = {"BTCUSDT", "ETHUSDT"}

# El viernes tiene que haber cerrado >= este % por encima de la apertura
MIN_BULL_PCT   = float(os.environ.get("WKB_BULL_PCT",  "0.5"))   # 0.5%
# Stop loss si no se confirma el breakout
STOP_PCT       = float(os.environ.get("WKB_STOP_PCT",  "2.0"))   # 2%
# Profit target del swing de fin de semana
PROFIT_PCT     = float(os.environ.get("WKB_PROFIT_PCT","3.0"))   # 3%
# La sesión del lunes donde cerramos (hora UTC)
MONDAY_CLOSE_HOUR_UTC = int(os.environ.get("WKB_MON_HOUR", "9"))


def _utc_weekday(df: pd.DataFrame) -> int:
    """Retorna el día de la semana UTC de la última vela (0=lun, 6=dom)."""
    try:
        ts = df.iloc[-1]["datetime"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts, utc=True)
        elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.astimezone(timezone.utc).weekday()
    except Exception:
        return -1


def _utc_datetime(df: pd.DataFrame) -> datetime | None:
    try:
        ts = df.iloc[-1]["datetime"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts, utc=True)
        elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _friday_candle(df: pd.DataFrame):
    """Retorna la vela más reciente del viernes UTC, o None."""
    try:
        df_copy = df.copy()
        df_copy["_dt"] = pd.to_datetime(df_copy["datetime"], utc=True, errors="coerce")
        df_copy["_dow"] = df_copy["_dt"].dt.weekday
        fridays = df_copy[df_copy["_dow"] == 4]
        if fridays.empty:
            return None
        return fridays.iloc[-1]
    except Exception:
        return None


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return "HOLD"
    if len(df) < 40:   # necesitamos al menos 2 semanas de velas 4h
        return "HOLD"

    now_utc = _utc_datetime(df)
    if now_utc is None:
        return "HOLD"

    weekday = now_utc.weekday()  # 0=lun, 4=vie, 6=dom → Python: 6=dom
    price   = float(df.iloc[-1]["close"])

    # ── SELL: lunes temprano → cerrar posición weekend ────────
    if entry_price is not None and entry_price > 0:
        # Lunes UTC ≥ MONDAY_CLOSE_HOUR_UTC → salida programada
        if weekday == 0 and now_utc.hour >= MONDAY_CLOSE_HOUR_UTC:
            pnl_pct = (price - entry_price) / entry_price * 100
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — lunes 09:00 UTC, "
                f"P&L={pnl_pct:+.2f}%"
            )
            return "SELL"
        # Profit target
        pnl_pct = (price - entry_price) / entry_price * 100
        if pnl_pct >= PROFIT_PCT:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — profit {pnl_pct:+.2f}%")
            return "SELL"
        # Stop
        if pnl_pct <= -STOP_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL (stop) — P&L {pnl_pct:+.2f}%"
            )
            return "SELL"
        return "HOLD"

    # ── BUY: primera vela alcista del domingo ────────────────
    # weekday 6 = domingo en Python datetime
    if weekday != 6:
        return "HOLD"

    # Verificar que el viernes fue bullish
    fri = _friday_candle(df)
    if fri is None:
        return "HOLD"

    fri_open  = float(fri["open"])
    fri_close = float(fri["close"])
    if fri_open <= 0:
        return "HOLD"

    fri_bull_pct = (fri_close - fri_open) / fri_open * 100
    if fri_bull_pct < MIN_BULL_PCT:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — viernes no bullish "
            f"({fri_bull_pct:+.2f}% < {MIN_BULL_PCT}%)"
        )
        return "HOLD"

    # Precio actual debe estar por encima del cierre del viernes (breakout)
    fri_high = float(fri["high"])
    if price <= fri_high:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — precio {price:.4f} "
            f"≤ máx viernes {fri_high:.4f} (aún no breakout)"
        )
        return "HOLD"

    logger.info(
        f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.4f} — "
        f"domingo UTC, viernes bullish ({fri_bull_pct:+.2f}%), "
        f"breakout sobre máx viernes ({fri_high:.4f})"
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
