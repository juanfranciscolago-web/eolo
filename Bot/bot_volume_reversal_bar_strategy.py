# ============================================================
#  EOLO — Estrategia: Volume Reversal Bar
#
#  Lógica:
#    BUY : Vela cierra opuesta a previa + volumen alto (>1.5x avg)
#    SELL: Reversal contrario + volumen alto
#
#  Tickers recomendados: AMZN, NVDA
#  Señales esperadas   : 2–3 por semana (bajo ruido, alta calidad)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VOLUME_REVERSAL_BAR"
AVG_VOL_PERIOD = 20
VOL_MULTIPLIER = 1.5

# FASE 6 Tier 2 tuning: Asset-specific volume multipliers
VOL_MULTIPLIER_BY_ASSET = {
    "TSLA": 2.0,   # Tier 2: Higher bar for noisy volume
    "AMZN": 1.5,   # Tier 1: Optimal, keep as is
    "JPM": 1.5,    # Tier 2: Optimal, keep as is
    "NVDA": 1.5,   # Tier 1: Optimal, keep as is
}


def calculate_volume_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega avg_vol al DataFrame."""
    df = df.copy()
    df["avg_vol"] = df["volume"].rolling(AVG_VOL_PERIOD).mean()
    return df


def detect_signal(df: pd.DataFrame, ticker: str = None) -> str:
    if len(df) < AVG_VOL_PERIOD + 2:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(curr["avg_vol"]):
        return "HOLD"

    # NEW: Asset-specific volume multiplier (FASE 6 tuning)
    vol_mult = VOL_MULTIPLIER_BY_ASSET.get(ticker, VOL_MULTIPLIER)

    # BUY: vela alcista después de bajista + volumen alto
    if (curr["close"] > curr["open"] and
        prev["close"] < prev["open"] and
        curr["volume"] > curr["avg_vol"] * vol_mult):
        logger.info(
            f"[VOLUME_REVERSAL_BAR] BUY ✅ — reversión alcista + volumen | "
            f"price={curr['close']:.2f} vol={curr['volume']:.0f} avg_vol={curr['avg_vol']:.0f} | mult={vol_mult}"
        )
        return "BUY"

    # SELL: vela bajista después de alcista + volumen alto
    if (curr["close"] < curr["open"] and
        prev["close"] > prev["open"] and
        curr["volume"] > curr["avg_vol"] * vol_mult):
        logger.info(
            f"[VOLUME_REVERSAL_BAR] SELL — reversión bajista + volumen | "
            f"price={curr['close']:.2f} vol={curr['volume']:.0f} avg_vol={curr['avg_vol']:.0f} | mult={vol_mult}"
        )
        return "SELL"

    return "HOLD"


def analyze(market_data, ticker: str, entry=None) -> dict:
    """
    entry: optional entry price (ignoring for now; reserved for future features)
    """
    df = market_data.get_price_history(ticker, candles=60)

    if df is None or df.empty:
        logger.error(f"[VOLUME_REVERSAL_BAR] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "volume": None, "avg_vol": None}

    df     = calculate_volume_profile(df)
    signal = detect_signal(df, ticker)  # Pass ticker for asset-specific tuning
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "volume":      int(last["volume"]),
        "avg_vol":     round(float(last["avg_vol"]), 0),
        "candle_time": str(last["datetime"]),
    }
