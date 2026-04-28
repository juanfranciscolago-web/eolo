# ============================================================
#  EOLO — Bollinger + RSI Sensitive (FASE 4 Winner)
#
#  Backtested on FASE 4 (252d real Schwab data):
#  ✅ Profit Factor: 38.52 en SPY, 14.78 en AAPL, 14.02 en QQQ
#  ✅ Avg PF en 5 activos: #1 del ranking (44 estrategias probadas)
#  ✅ Trades: ~8-15 por año → mean reversion de baja frecuencia
#
#  Parámetros:
#    BB Period: 20 candles (1h daily → mean-revert window)
#    BB StdDev: 2.0 (estándar), también se usa 1-std como zona "sensitive"
#    RSI Period: 14
#    Entry: close < (bb_sma - 1*std) AND rsi < 40
#    Exit: close > bb_sma OR rsi > 65
#    Stop-loss: 4% | Take-profit: 6%
#
#  Timeframe: daily candles (backtest se corrió con close diario)
#  Tickers: SPY, AAPL, QQQ (top 3 del backtesting)
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "BOLLINGER_RSI_SENSITIVE"
BB_PERIOD = 20
BB_STD = 2.0
BB_SENSITIVE_STD = 1.0   # entry threshold es más sensible (1 std) que 2 std
RSI_PERIOD = 14
RSI_ENTRY = 40
RSI_EXIT = 65
FREQUENCY = 1            # daily candles (minutos=1 con períodoType=day → daily)
CANDLES = 120            # ~6 meses de diarias
SL_PCT = 0.04            # 4% stop-loss → expuesto como metadata
TP_PCT = 0.06            # 6% take-profit → expuesto como metadata

# Tickers soportados (los 3 ganadores de FASE 4)
ELIGIBLE_TICKERS = {"SPY", "AAPL", "QQQ"}


def calculate_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega SMA 20 y bandas Bollinger (2-std y 1-std)."""
    df = df.copy()
    df["bb_sma"] = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_sma"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_sma"] - BB_STD * df["bb_std"]
    df["bb_lower_sensitive"] = df["bb_sma"] - BB_SENSITIVE_STD * df["bb_std"]
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
    Detecta señales según el winner de FASE 4.

    BUY : close < (bb_sma - 1*std)  AND rsi < 40
    SELL: close > bb_sma             OR  rsi > 65
    """
    if len(df) < BB_PERIOD + 2:
        return "HOLD"

    curr = df.iloc[-1]

    # Validar indicadores no-NaN
    for col in ("bb_sma", "bb_lower_sensitive", "rsi"):
        if pd.isna(curr.get(col)):
            return "HOLD"

    # ── SELL FIRST (más urgente que el entry) ──────────────
    if curr["close"] > curr["bb_sma"] or curr["rsi"] > RSI_EXIT:
        reason = []
        if curr["close"] > curr["bb_sma"]:
            reason.append(f"close>{curr['bb_sma']:.2f}")
        if curr["rsi"] > RSI_EXIT:
            reason.append(f"rsi>{RSI_EXIT}")
        logger.info(
            f"[{STRATEGY_NAME}] SELL — {' | '.join(reason)} | "
            f"close={curr['close']:.2f} rsi={curr['rsi']:.1f}"
        )
        return "SELL"

    # ── BUY: zona sensitive + RSI oversold ─────────────────
    if curr["close"] < curr["bb_lower_sensitive"] and curr["rsi"] < RSI_ENTRY:
        logger.info(
            f"[{STRATEGY_NAME}] BUY ✅ — close<{curr['bb_lower_sensitive']:.2f} "
            f"AND rsi<{RSI_ENTRY} | close={curr['close']:.2f} rsi={curr['rsi']:.1f}"
        )
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str) -> dict:
    """
    Pipeline completo FASE 4 winner.

    Ejecuta solo en SPY / AAPL / QQQ (el resto lo skipea).
    """
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return {
            "ticker": ticker,
            "signal": "SKIP",
            "strategy": STRATEGY_NAME,
            "reason": f"Strategy es solo para {sorted(ELIGIBLE_TICKERS)}",
            "price": None,
        }

    # 120 velas diarias ≈ 6 meses de lookback (suficiente para BB20 + RSI14)
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
    df = calculate_bollinger(df)
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
        "timeframe": "1d",
        "price": safe_round(last["close"]),
        "bb_upper": safe_round(last["bb_upper"]),
        "bb_sma": safe_round(last["bb_sma"]),
        "bb_lower": safe_round(last["bb_lower"]),
        "bb_lower_sensitive": safe_round(last["bb_lower_sensitive"]),
        "rsi": safe_round(last["rsi"], 2),
        "candle_time": str(last.get("datetime", "")),
        "sl_pct": SL_PCT,
        "tp_pct": TP_PCT,
        "pf_backtest_spy": 38.52,
        "pf_backtest_aapl": 14.78,
        "pf_backtest_qqq": 14.02,
    }
