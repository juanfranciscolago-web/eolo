# ============================================================
#  EOLO — Estrategia: Opening Drive Exhaustion (leveraged ETFs)
#
#  Ref: trading_strategies_v2.md #25
#
#  Lógica (mean reversion, leveraged-only):
#    - Solo opera tickers de LEVERAGED_MAPPING (TQQQ, SOXL, TSLL, NVDL).
#    - Durante los primeros 30 min de sesión:
#        * RVOL acumulado de esas 30 velas > 3.0
#        * move |(close30 / open0 - 1)| > 2 × stdev diario histórico
#      → fade del movimiento (SHORT si subió, LONG si bajó).
#    - v1 long-only: SHORT reportado como HOLD; solo LONG cuando
#      la apertura bajó extremamente.
#    - time_stop de 60 min se respeta via TTL en el trader.
#
#  Universo: TQQQ, SOXL, TSLL, NVDL.
#  Categoría: mean_reversion.
# ============================================================
from datetime import datetime

import pandas as pd
import pytz
from loguru import logger

STRATEGY_NAME = "OPENING_DRIVE"

LEVERAGED_TICKERS = {"TQQQ", "SOXL", "TSLL", "NVDL"}

OPEN_MINUTES   = 30
VOL_MA_PERIOD  = 20
RVOL_THRESH    = 3.0
STD_MULT       = 2.0
ATR_PERIOD     = 14
TIME_STOP_MIN  = 60

EASTERN = pytz.timezone("America/New_York")


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _first_n_bars_today(df: pd.DataFrame, n: int) -> pd.DataFrame:
    today = datetime.now(EASTERN).date()
    if df["datetime"].dt.tz is None:
        dt_et = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
    else:
        dt_et = df["datetime"].dt.tz_convert(EASTERN)
    today_df = df[dt_et.dt.date == today].copy()
    return today_df.head(n)


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if ticker.upper() not in LEVERAGED_TICKERS:
        return "HOLD"

    if len(df) < VOL_MA_PERIOD + OPEN_MINUTES + 2:
        return "HOLD"

    last  = df.iloc[-1]
    price = float(last["close"])

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.03
        sl = stop_loss     if stop_loss     is not None else 0.015
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Solo evaluamos una vez la apertura completa ─────
    opening = _first_n_bars_today(df, OPEN_MINUTES)
    if len(opening) < OPEN_MINUTES:
        return "HOLD"

    # Rvol promedio de las primeras 30 velas vs SMA(20) histórico
    vol_ma = float(df["volume"].rolling(VOL_MA_PERIOD).mean().iloc[-1])
    if vol_ma <= 0:
        return "HOLD"
    rvol_open = float(opening["volume"].sum()) / (vol_ma * OPEN_MINUTES)

    # Move %: close de la última vela de apertura / open de la primera
    open0  = float(opening["open"].iloc[0])
    close30 = float(opening["close"].iloc[-1])
    move_30min = (close30 / open0 - 1) if open0 > 0 else 0

    # StdDev diario de returns por vela (aproximación)
    daily_std = float(df["close"].pct_change().std())
    if daily_std <= 0:
        return "HOLD"

    extreme_move = abs(move_30min) > STD_MULT * daily_std

    if rvol_open > RVOL_THRESH and extreme_move:
        if move_30min < 0:
            # Apertura muy bajista → fade LONG
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} BUY — opening drive exhaustion (fade down) | "
                f"move_30min={move_30min:+.2%} rvol_open={rvol_open:.2f}×"
            )
            return "BUY"
        else:
            logger.debug(
                f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
                f"move_30min={move_30min:+.2%} rvol_open={rvol_open:.2f}×"
            )
            return "HOLD"

    return "HOLD"


def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    df = market_data.get_price_history(ticker, candles=0, days=2)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "leveraged":   ticker.upper() in LEVERAGED_TICKERS,
        "candle_time": str(last["datetime"]),
    }
