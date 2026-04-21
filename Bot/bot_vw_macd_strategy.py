# ============================================================
#  EOLO — Estrategia: Volume-Weighted MACD
#
#  Ref: trading_strategies_v2.md #21
#
#  Lógica (momentum de capital):
#    pv = close * volume
#    ema_fast = EMA(pv, 12)
#    ema_slow = EMA(pv, 26)
#    macd     = ema_fast - ema_slow
#    signal   = EMA(macd, 9)
#    histogram = macd - signal
#    - BUY  si histogram cruza de - a +.
#    - Exit si histogram cruza a - (además de SL/TP).
#
#  Universo: todos, especialmente MSTR (flows ocultos).
#  Categoría: momentum_breakout.
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VW_MACD"

EMA_FAST  = 12
EMA_SLOW  = 26
EMA_SIG   = 9
ATR_PERIOD = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pv = df["close"] * df["volume"]
    ema_fast = pv.ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = pv.ewm(span=EMA_SLOW, adjust=False).mean()
    df["vw_macd"]   = ema_fast - ema_slow
    df["vw_signal"] = df["vw_macd"].ewm(span=EMA_SIG, adjust=False).mean()
    df["vw_hist"]   = df["vw_macd"] - df["vw_signal"]
    df["atr"]       = calculate_atr(df, ATR_PERIOD)
    return df


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if len(df) < EMA_SLOW + EMA_SIG + 2:
        return "HOLD"

    prev = df.iloc[-2]
    last = df.iloc[-1]
    price = float(last["close"])

    if pd.isna(last.get("vw_hist")) or pd.isna(prev.get("vw_hist")):
        return "HOLD"

    bullish_cross = float(prev["vw_hist"]) < 0 and float(last["vw_hist"]) > 0
    bearish_cross = float(prev["vw_hist"]) > 0 and float(last["vw_hist"]) < 0

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.025
        sl = stop_loss     if stop_loss     is not None else 0.012

        if bearish_cross:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — histogram cruzó a negativo")
            return "SELL"
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    if bullish_cross:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — histogram cruce alcista | "
            f"prev_hist={prev['vw_hist']:.0f} now={last['vw_hist']:.0f}"
        )
        return "BUY"

    return "HOLD"


def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    df = market_data.get_price_history(ticker, candles=100)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    def safe_round(val, nd=4):
        return round(float(val), nd) if pd.notna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "vw_macd":     safe_round(last.get("vw_macd"), 2),
        "vw_signal":   safe_round(last.get("vw_signal"), 2),
        "vw_hist":     safe_round(last.get("vw_hist"), 2),
        "atr":         safe_round(last.get("atr")),
        "candle_time": str(last["datetime"]),
    }
