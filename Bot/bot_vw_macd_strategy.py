# ============================================================
#  EOLO — Estrategia: Volume-Weighted MACD
#
#  Lógica:
#    BUY : MACD cruza signal line hacia arriba (volumen ponderado)
#    SELL: MACD cruza signal line hacia abajo
#
#  Tickers recomendados: MSFT, META
#  Señales esperadas   : 3–5 por día (medio-alto ruido)
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "VW_MACD"
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
# FASE 6 Tier 3: Asset-specific MACD
MACD_FAST_BY_ASSET = {"TSLA": 10, "AAPL": 12, "SPY": 12}
MACD_SLOW_BY_ASSET = {"TSLA": 24, "AAPL": 26, "SPY": 26}
VWP_PERIOD = 20


def calculate_vw_macd(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula Volume-Weighted MACD."""
    df = df.copy()
    
    # Volume-weighted price
    df["vwp"] = (df["close"] * df["volume"]).rolling(VWP_PERIOD).sum() / df["volume"].rolling(VWP_PERIOD).sum()
    
    # MACD on VWP
    df["macd"] = df["vwp"].ewm(span=MACD_FAST, adjust=False).mean() - df["vwp"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    
    return df


def detect_signal(df: pd.DataFrame, ticker=None) -> str:
    if len(df) < MACD_SLOW + 2:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(curr["macd"]) or pd.isna(curr["macd_signal"]):
        return "HOLD"

    # BUY: MACD cruza signal line hacia arriba
    if curr["macd"] > curr["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
        logger.info(
            f"[VW_MACD] BUY ✅ — MACD cruzó signal line (alcista) | "
            f"macd={curr['macd']:.4f} signal={curr['macd_signal']:.4f}"
        )
        return "BUY"

    # SELL: MACD cruza signal line hacia abajo
    if curr["macd"] < curr["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
        logger.info(
            f"[VW_MACD] SELL — MACD cruzó signal line (bajista) | "
            f"macd={curr['macd']:.4f} signal={curr['macd_signal']:.4f}"
        )
        return "SELL"

    return "HOLD"


def analyze(market_data, ticker: str, entry=None) -> dict:
    """
    entry: optional entry price (ignoring for now; reserved for future features)
    """
    df = market_data.get_price_history(ticker, candles=60)

    if df is None or df.empty:
        logger.error(f"[VW_MACD] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "macd": None, "signal_line": None}

    df     = calculate_vw_macd(df)
    signal = detect_signal(df)
    last   = df.iloc[-1]

    def safe_round(val):
        return round(float(val), 4) if pd.notna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "macd":        safe_round(last["macd"]),
        "signal_line": safe_round(last["macd_signal"]),
        "histogram":   safe_round(last["macd_hist"]),
        "candle_time": str(last["datetime"]),
    }
