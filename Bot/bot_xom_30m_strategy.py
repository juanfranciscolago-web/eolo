# ============================================================
#  EOLO — XOM 30m Bollinger Bands
#
#  Backtested on FASE 5 (60d real Schwab data):
#  ✅ Profit Factor: 1.38 (breakeven is 1.0)
#  ✅ Win Rate: ~30-40%
#  ✅ Trades: 10-15 per 60 days
#
#  Parámetros:
#    BB Period: 20 candles (30m each = 10 hours lookback)
#    BB StdDev: 2.0 (standard deviation multiplier)
#    Entry: Price touches lower Bollinger Band + volume confirmation
#    Exit: Touch upper band OR fail to bounce
#
#  Timeframe: 30-minute candles ONLY
#  Ticker: XOM (energy sector, mean-revert friendly)
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "XOM_30M_BB"
BB_PERIOD = 20
BB_STD = 2.0
MIN_VOLUME_PERCENTILE = 20
FREQUENCY = 30  # 30-minute candles


def calculate_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega bandas de Bollinger al DataFrame."""
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    return df


def detect_signal(df: pd.DataFrame) -> str:
    """
    Detecta señales de Bollinger Bands optimizadas para XOM 30m.

    BUY: Price toca banda inferior + volumen > 20 percentil
    SELL: Touch banda superior O precio rechaza por segunda vez
    """
    if len(df) < BB_PERIOD + 2:
        return "HOLD"

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    # Validar bandas válidas
    if pd.isna(curr["bb_upper"]) or pd.isna(curr["bb_lower"]):
        return "HOLD"

    # Calcular threshold de volumen (20 percentil del histórico)
    vol_threshold = np.percentile(df["volume"].values[-60:], MIN_VOLUME_PERCENTILE)

    # BUY: Vela anterior tocó banda inferior AND vela actual sube CON volumen
    prev_touched_lower = prev["low"] <= prev["bb_lower"]
    curr_bounced = curr["close"] > prev["close"]
    curr_vol_ok = curr["volume"] > vol_threshold

    if prev_touched_lower and curr_bounced and curr_vol_ok:
        logger.info(
            f"[XOM_30M] BUY ✅ — Bollinger bounce | "
            f"close={curr['close']:.2f} lower={curr['bb_lower']:.2f} vol={curr['volume']:.0f}>"
            f"{vol_threshold:.0f}"
        )
        return "BUY"

    # SELL: Toca banda superior (take profit)
    if curr["high"] >= curr["bb_upper"]:
        logger.info(
            f"[XOM_30M] SELL TP — upper band touch | "
            f"close={curr['close']:.2f} upper={curr['bb_upper']:.2f}"
        )
        return "SELL"

    # SELL: Rechaza segunda vez en banda inferior (stop loss)
    if curr["close"] < curr["bb_lower"] and prev["low"] <= prev["bb_lower"]:
        logger.info(
            f"[XOM_30M] SELL SL — failed bounce | "
            f"close={curr['close']:.2f} lower={curr['bb_lower']:.2f}"
        )
        return "SELL"

    return "HOLD"


def analyze(market_data, ticker: str) -> dict:
    """
    Pipeline completo para XOM 30m analysis.

    Descarga últimas 120 velas de 30m (~60 horas / ~2.5 days)
    """
    # Solo opera XOM — ignore other tickers
    if ticker.upper() != "XOM":
        return {
            "ticker": ticker,
            "signal": "SKIP",
            "strategy": STRATEGY_NAME,
            "reason": "Strategy is XOM-only",
            "price": None,
        }

    # Descargar 30m candles
    df = market_data.get_price_history(ticker, candles=120, frequency=FREQUENCY)

    if df is None or df.empty:
        logger.error(f"[XOM_30M] Sin datos para {ticker}")
        return {
            "ticker": ticker,
            "signal": "ERROR",
            "strategy": STRATEGY_NAME,
            "price": None,
            "error": "No price history",
        }

    # Calcular Bollinger Bands
    df = calculate_bollinger(df)

    # Detectar señal
    signal = detect_signal(df)
    last = df.iloc[-1]

    def safe_round(val):
        return round(float(val), 4) if pd.notna(val) else None

    return {
        "ticker": ticker,
        "signal": signal,
        "strategy": STRATEGY_NAME,
        "timeframe": "30m",
        "price": round(float(last["close"]), 4),
        "bb_upper": safe_round(last["bb_upper"]),
        "bb_mid": safe_round(last["bb_mid"]),
        "bb_lower": safe_round(last["bb_lower"]),
        "volume": int(last["volume"]),
        "candle_time": str(last["datetime"]),
        "pf_backtest": 1.38,  # Reference for monitoring
    }
