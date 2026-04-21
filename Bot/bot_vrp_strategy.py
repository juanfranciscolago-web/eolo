# ============================================================
#  EOLO — Estrategia: Volatility Risk Premium (VRP) Intraday
#
#  Ref: trading_strategies_v2.md #23
#
#  Lógica:
#    VRP = IV30 - RV10 (ambos en %).
#    - Si hace 5 barras VRP era > +5 y ahora < 0  (colapsó):
#      → institucionales vendiendo gamma → continuación de la
#        tendencia del precio.
#    - Direccional: close vs VWAP.
#        close > VWAP → LONG
#        close < VWAP → SHORT (long-only → HOLD)
#
#  Universo: SPY, QQQ.
#
#  Para IV30 usamos macro.latest("VIX") como proxy (VIX ~ IV30 del
#  S&P 500). Para QQQ el proxy estricto sería VXN, pero VIX es
#  aproximadamente direccional.
# ============================================================
import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VRP_INTRADAY"
ELIGIBLE_TICKERS = {"SPY", "QQQ"}

RV_WINDOW_BARS = 78 * 10   # 10 días × 78 barras de 5 min
RV_ANNUAL      = 252 * 78
VRP_PRIOR_POS  = 5.0
LOOKBACK       = 5
ATR_PERIOD     = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


def realized_vol_series_pct(close: pd.Series, window: int = RV_WINDOW_BARS,
                            ann: int = RV_ANNUAL) -> pd.Series:
    """Realized vol rolling anualizada en %."""
    rets = close.pct_change()
    return rets.rolling(window).std() * np.sqrt(ann) * 100.0


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
    if macro is None or len(df) < ATR_PERIOD + LOOKBACK + 2:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

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

    iv30 = macro.latest("VIX")
    if iv30 is None:
        return "HOLD"

    rv_series = realized_vol_series_pct(df["close"])
    if rv_series.dropna().empty:
        return "HOLD"

    # VRP ahora y VRP hace LOOKBACK velas — ambos usando IV30 actual
    # (aproximación, no tenemos histórico de IV30 intradía).
    rv_now = float(rv_series.iloc[-1])
    rv_prev = float(rv_series.iloc[-(LOOKBACK + 1)]) if len(rv_series) > LOOKBACK else float("nan")
    if pd.isna(rv_now) or pd.isna(rv_prev):
        return "HOLD"

    vrp_now  = iv30 - rv_now
    vrp_prev = iv30 - rv_prev

    prior_positive   = vrp_prev > VRP_PRIOR_POS
    current_negative = vrp_now < 0

    if prior_positive and current_negative:
        vwap = float(calculate_vwap(df).iloc[-1])
        if price > vwap:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} BUY — VRP collapse | "
                f"vrp prev={vrp_prev:+.2f} now={vrp_now:+.2f} | close>{vwap:.2f}"
            )
            return "BUY"
        else:
            logger.debug(
                f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
                f"vrp prev={vrp_prev:+.2f} now={vrp_now:+.2f} | close<{vwap:.2f}"
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
    df = market_data.get_price_history(ticker, candles=0, days=2)
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
