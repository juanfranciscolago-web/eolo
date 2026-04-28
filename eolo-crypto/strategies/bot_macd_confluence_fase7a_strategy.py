# ============================================================
#  EOLO — MACD Confluence (FASE 7a Winner)
#
#  Backtested on FASE 7a (2016-2026 real Schwab data):
#  ✅ Profit Factor: 4.58 en QQQ, 3.14 en SPY
#  ✅ Win Rate: 66.7%
#  ✅ Trades: 20-21 por período
#
#  Parámetros:
#    MACD Fast: 12
#    MACD Slow: 26
#    MACD Signal: 9
#    Entry: MACD > Signal (bullish crossover)
#    Exit: MACD < Signal (bearish crossover)
#    Stop-loss: 2% | Take-profit: 4%
#
#  Timeframe: 30m candles (intraday - confluencia multi-TF)
#  Tickers: QQQ, SPY (top 2 del backtesting FASE 7a)
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "MACD_CONFLUENCE"
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
FREQUENCY = 30           # 30-minute candles
CANDLES = 240            # ~5 días de 30m candles (8.5h/día × 5 días)
SL_PCT = 0.02            # 2% stop-loss
TP_PCT = 0.04            # 4% take-profit

# Tickers soportados (los 2 ganadores de FASE 7a)
ELIGIBLE_TICKERS = {"SPY", "QQQ"}


def calculate_macd(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula MACD y línea de señal."""
    df = df.copy()

    # EMA 12 y EMA 26
    ema12 = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema26 = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()

    # MACD line
    df["macd"] = ema12 - ema26

    # Signal line (EMA 9 del MACD)
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()

    # MACD Histogram
    df["macd_histogram"] = df["macd"] - df["macd_signal"]

    return df


def detect_signal(df: pd.DataFrame) -> str:
    """
    Detecta señales MACD Confluence.

    BUY : MACD > Signal (bullish crossover) - confluencia
    SELL: MACD < Signal (bearish crossover)
    """
    if len(df) < MACD_SLOW + 2:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Validar indicadores no-NaN
    for col in ("macd", "macd_signal"):
        if pd.isna(curr.get(col)) or pd.isna(prev.get(col)):
            return "HOLD"

    # ── SELL FIRST (más urgente) ──────────────────────────
    if prev["macd"] >= prev["macd_signal"] and curr["macd"] < curr["macd_signal"]:
        logger.info(
            f"[{STRATEGY_NAME}] SELL — MACD bearish crossover | "
            f"macd={curr['macd']:.4f} signal={curr['macd_signal']:.4f} | "
            f"close={curr['close']:.2f}"
        )
        return "SELL"

    # ── BUY: MACD bullish crossover ────────────────────────
    if prev["macd"] < prev["macd_signal"] and curr["macd"] >= curr["macd_signal"]:
        logger.info(
            f"[{STRATEGY_NAME}] BUY ✅ — MACD bullish crossover | "
            f"macd={curr['macd']:.4f} signal={curr['macd_signal']:.4f} | "
            f"close={curr['close']:.2f}"
        )
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str) -> dict:
    """
    Pipeline completo FASE 7a MACD Confluence.

    Ejecuta solo en SPY / QQQ (el resto lo skipea).
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
    df = calculate_macd(df)

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
        "macd": safe_round(last["macd"], 6),
        "macd_signal": safe_round(last["macd_signal"], 6),
        "macd_histogram": safe_round(last["macd_histogram"], 6),
        "candle_time": str(last.get("datetime", "")),
        "sl_pct": SL_PCT,
        "tp_pct": TP_PCT,
        "pf_backtest_qqq": 4.58,
        "pf_backtest_spy": 3.14,
    }
