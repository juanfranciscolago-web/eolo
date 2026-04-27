# ============================================================
#  EOLO — Momentum Score (FASE 7a Winner)
#
#  Backtested on FASE 7a (2016-2026 real Schwab data):
#  ✅ Profit Factor: 4.58 en QQQ, 3.14 en SPY
#  ✅ Win Rate: 66.7%
#  ✅ Trades: 20-21 por período
#
#  Parámetros:
#    ROC Period: 12 (Rate of Change)
#    RSI Period: 14
#    Entry: ROC > 0 AND RSI > 50 (momentum + strength)
#    Exit: ROC < 0 OR RSI < 30 (momentum loss)
#    Stop-loss: 2% | Take-profit: 4%
#
#  Timeframe: 30m candles (intraday - confluencia multi-TF)
#  Tickers: SPY, QQQ (ganadores de FASE 7a)
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "MOMENTUM_SCORE"
ROC_PERIOD = 12
RSI_PERIOD = 14
FREQUENCY = 30           # 30-minute candles
CANDLES = 240            # ~5 días de 30m candles
SL_PCT = 0.02            # 2% stop-loss
TP_PCT = 0.04            # 4% take-profit

# Tickers soportados
ELIGIBLE_TICKERS = {"SPY", "QQQ"}


def calculate_roc(df: pd.DataFrame, period: int = ROC_PERIOD) -> pd.DataFrame:
    """Calcula Rate of Change (momentum)."""
    df = df.copy()
    df["roc"] = ((df["close"] - df["close"].shift(period)) / df["close"].shift(period)) * 100
    return df


def calculate_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    """RSI Wilder clásico."""
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def detect_signal(df: pd.DataFrame) -> str:
    """
    Detecta señales Momentum Score.

    BUY : ROC > 0 AND RSI > 50 (momentum positivo + strength)
    SELL: ROC < 0 OR RSI < 30 (momentum loss)
    """
    if len(df) < ROC_PERIOD + 2:
        return "HOLD"

    curr = df.iloc[-1]

    # Validar indicadores no-NaN
    for col in ("roc", "rsi"):
        if pd.isna(curr.get(col)):
            return "HOLD"

    # ── SELL FIRST (más urgente) ──────────────────────────
    if curr["roc"] < 0 or curr["rsi"] < 30:
        reason = []
        if curr["roc"] < 0:
            reason.append(f"roc<0 ({curr['roc']:.2f}%)")
        if curr["rsi"] < 30:
            reason.append(f"rsi<30 ({curr['rsi']:.1f})")
        logger.info(
            f"[{STRATEGY_NAME}] SELL — {' | '.join(reason)} | "
            f"close={curr['close']:.2f}"
        )
        return "SELL"

    # ── BUY: momentum positivo + strength ──────────────────
    if curr["roc"] > 0 and curr["rsi"] > 50:
        logger.info(
            f"[{STRATEGY_NAME}] BUY ✅ — roc>0 ({curr['roc']:.2f}%) AND rsi>50 ({curr['rsi']:.1f}) | "
            f"close={curr['close']:.2f}"
        )
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str) -> dict:
    """
    Pipeline completo FASE 7a Momentum Score.

    Ejecuta solo en SPY / QQQ.
    """
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return {
            "ticker": ticker,
            "signal": "SKIP",
            "strategy": STRATEGY_NAME,
            "reason": f"Strategy es solo para {sorted(ELIGIBLE_TICKERS)}",
            "price": None,
        }

    # 240 velas de 30m ≈ 5 días de lookback
    df = market_data.get_price_history(ticker, candles=CANDLES, frequency=FREQUENCY)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {
            "ticker": ticker,
            "signal": "ERROR",
            "strategy": STRATEGY_NAME,
            "price": None,
            "error": "No price history",
        }

    # Indicadores
    df = calculate_roc(df)
    df = calculate_rsi(df)

    # Señal
    signal = detect_signal(df)
    last = df.iloc[-1]

    def safe_round(val, n=4):
        return round(float(val), n) if pd.notna(val) else None

    return {
        "ticker": ticker,
        "signal": signal,
        "strategy": STRATEGY_NAME,
        "timeframe": "30m",
        "price": safe_round(last["close"]),
        "roc": safe_round(last["roc"], 2),
        "rsi": safe_round(last["rsi"], 2),
        "candle_time": str(last.get("datetime", "")),
        "sl_pct": SL_PCT,
        "tp_pct": TP_PCT,
        "pf_backtest_qqq": 4.58,
        "pf_backtest_spy": 3.14,
    }
