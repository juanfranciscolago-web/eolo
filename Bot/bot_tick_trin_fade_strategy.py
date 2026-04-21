# ============================================================
#  EOLO — Estrategia: TICK/TRIN Extreme Fades
#
#  Ref: trading_strategies_v2.md #22
#
#  Lógica (mean reversion intradía NYSE breadth):
#    Bull extreme: TICK > 1000  y  TRIN < 0.7 → fade con SHORT
#    Bear extreme: TICK < -1000 y  TRIN > 1.3 → fade con LONG
#    v1 long-only: solo BUY en bear extreme.
#
#  Universo: SPY, QQQ.
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "TICK_TRIN_FADE"
ELIGIBLE_TICKERS = {"SPY", "QQQ"}

TICK_BULL_TH = 1000
TICK_BEAR_TH = -1000
TRIN_BULL_TH = 0.7
TRIN_BEAR_TH = 1.3

ATR_PERIOD = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


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
    if macro is None or len(df) < ATR_PERIOD + 2:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    if entry_price is not None and entry_price > 0:
        # Target: volver al VWAP del día
        vwap = float(calculate_vwap(df).iloc[-1])
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.01
        sl = stop_loss     if stop_loss     is not None else 0.006
        if price >= vwap:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — volvió al VWAP {vwap:.2f}")
            return "SELL"
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    tick = macro.latest("TICK")
    trin = macro.latest("TRIN")
    if tick is None or trin is None:
        return "HOLD"

    bull_extreme = tick > TICK_BULL_TH and trin < TRIN_BULL_TH
    bear_extreme = tick < TICK_BEAR_TH and trin > TRIN_BEAR_TH

    if bear_extreme:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — bear extreme | "
            f"tick={tick:.0f} trin={trin:.2f}"
        )
        return "BUY"
    if bull_extreme:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
            f"tick={tick:.0f} trin={trin:.2f}"
        )
        return "HOLD"

    return "HOLD"


def analyze(
    market_data,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    df = market_data.get_price_history(ticker, candles=0, days=1)
    if df is None or df.empty:
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME, "price": None}

    signal = detect_signal(df, ticker, macro, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "tick_latest": (macro.latest("TICK") if macro is not None else None),
        "trin_latest": (macro.latest("TRIN") if macro is not None else None),
        "candle_time": str(last["datetime"]),
    }
