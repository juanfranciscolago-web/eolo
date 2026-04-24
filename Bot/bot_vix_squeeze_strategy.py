# ============================================================
#  EOLO — Estrategia: VIX Volatility Squeeze Breakout
#
#  Ref: trading_strategies_v2.md #8
#
#  Lógica (VIX regime con direccionalidad):
#    - Squeeze: ancho de Bollinger(20) en VIX < quantile(20%, 60)
#    - Breakout: VIX[-1] > upper BB anterior → expansión alcista
#    - Direccionalidad via VWAP de SPY:
#        * SPY < VWAP → SHORT setup (long-only → HOLD)
#        * SPY > VWAP → esperar (posible whipsaw)
#
#  Universo: SPY, QQQ.
# ============================================================
# FASE 6 Tier 3: Asset-specific squeeze
SQUEEZE_THRESHOLD_BY_ASSET = {"QQQ": 0.8, "TQQQ": 0.75, "SPY": 0.85}
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VIX_SQUEEZE"
ELIGIBLE_TICKERS = {"SPY", "QQQ"}

BB_WINDOW    = 20
BB_STD       = 2.0
SQUEEZE_LOOKBACK = 60
SQUEEZE_PCT  = 0.20
ATR_PERIOD   = 14


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
    if macro is None or len(df) < max(BB_WINDOW, ATR_PERIOD) + 2:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    # Exit convencional (long-only)
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.02
        sl = stop_loss     if stop_loss     is not None else 0.012
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    vix = macro.series("VIX", minutes=24 * 60)
    if len(vix) < SQUEEZE_LOOKBACK + 5:
        return "HOLD"

    bb_mid   = vix.rolling(BB_WINDOW).mean()
    bb_std   = vix.rolling(BB_WINDOW).std()
    bb_upper = bb_mid + BB_STD * bb_std
    bb_lower = bb_mid - BB_STD * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid

    q20 = bb_width.rolling(SQUEEZE_LOOKBACK).quantile(SQUEEZE_PCT).iloc[-1]
    if pd.isna(q20):
        return "HOLD"

    squeeze         = float(bb_width.iloc[-1]) < float(q20)
    vix_breakout_up = float(vix.iloc[-1]) > float(bb_upper.iloc[-2])

    if squeeze and vix_breakout_up:
        vwap = calculate_vwap(df).iloc[-1]
        if price < float(vwap):
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
                f"SPY<VWAP vix_br={vix.iloc[-1]:.2f} > {bb_upper.iloc[-2]:.2f}"
            )
            return "HOLD"
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} squeeze+breakout pero SPY>VWAP → esperar"
        )
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
        "vix_latest":  (macro.latest("VIX") if macro is not None else None),
        "candle_time": str(last["datetime"]),
    }
