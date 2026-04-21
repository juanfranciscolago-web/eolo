# ============================================================
#  EOLO — Estrategia: VIX-Price Correlation Flip
#
#  Ref: trading_strategies_v2.md #7
#
#  Lógica:
#    - Correlación normal(rolling 20) entre returns de SPY y VIX
#      es ~ -0.7. Si flippa a > -0.2 y ambos suben, smart money
#      está hedgeando mientras compra → techo probable.
#    - v1 long-only: devuelve HOLD para "SHORT setup".
#    - Se declara como filtro macro más que señal primaria.
#
#  Universo: SPY, QQQ.
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VIX_CORR_FLIP"
ELIGIBLE_TICKERS = {"SPY", "QQQ"}

CORR_WINDOW  = 20
CORR_FLIP_TH = -0.2
ATR_PERIOD   = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


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
    if macro is None or len(df) < CORR_WINDOW + 5:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    # Exit si ya hay posición (long-only: SELL convencional)
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.015
        sl = stop_loss     if stop_loss     is not None else 0.01
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # Alinear VIX series a las últimas N muestras por timestamp.
    vix = macro.series("VIX", minutes=24 * 60)
    if len(vix) < CORR_WINDOW + 2:
        return "HOLD"

    # Resample VIX a las últimas CORR_WINDOW+5 muestras y alinear
    # con las velas de price en base index (usamos últimas CORR_WINDOW+5
    # velas y últimas CORR_WINDOW+5 muestras VIX — aproximación).
    px_ret = df["close"].pct_change().iloc[-(CORR_WINDOW + 1):].dropna()
    vix_ret = vix.pct_change().iloc[-(CORR_WINDOW + 1):].dropna()

    n = min(len(px_ret), len(vix_ret))
    if n < CORR_WINDOW:
        return "HOLD"

    correlation = float(px_ret.iloc[-n:].reset_index(drop=True).corr(
        vix_ret.iloc[-n:].reset_index(drop=True)
    ))
    if pd.isna(correlation):
        return "HOLD"

    spy_uptrend = float(df["close"].iloc[-1]) > float(df["close"].rolling(CORR_WINDOW).mean().iloc[-1])
    vix_uptrend = float(vix.iloc[-1]) > float(vix.rolling(CORR_WINDOW).mean().iloc[-1])
    flip = correlation > CORR_FLIP_TH

    if flip and spy_uptrend and vix_uptrend:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
            f"corr={correlation:+.2f}"
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
    df = market_data.get_price_history(ticker, candles=60)
    if df is None or df.empty:
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME, "price": None}

    signal = detect_signal(df, ticker, macro, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    return {
        "ticker":     ticker,
        "signal":     signal,
        "strategy":   STRATEGY_NAME,
        "price":      round(float(last["close"]), 4),
        "vix_latest": (macro.latest("VIX") if macro is not None else None),
        "candle_time": str(last["datetime"]),
    }
