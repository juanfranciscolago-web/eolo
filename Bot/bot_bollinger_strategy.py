# ============================================================
#  EOLO — Estrategia B: Bollinger Bands Bounce
#
#  Lógica:
#    BUY : la vela anterior tocó/bajó de la banda inferior
#           Y la vela actual cierra DENTRO de las bandas (rebote)
#    SELL: la vela actual toca la banda superior
#           O el precio cae nuevamente por debajo de la banda inferior
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 2–4 por día
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "BOLLINGER"
BB_PERIOD     = 25  # Increased from 20 — FASE 6 tuning (reduces whipsaws on 30m)
BB_STD        = 2.0


# ── Indicadores ───────────────────────────────────────────

def calculate_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega bb_mid, bb_upper, bb_lower al DataFrame."""
    df = df.copy()
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame) -> str:
    if len(df) < BB_PERIOD + 2:
        return "HOLD"

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    # Necesitamos bandas válidas
    if pd.isna(curr["bb_upper"]) or pd.isna(curr["bb_lower"]):
        return "HOLD"

    # BUY: vela anterior tocó/cruzó banda inferior (por low O por close),
    #      Y vela actual cierra dentro de las bandas (rebote confirmado)
    prev_touched_lower = (prev["low"] <= prev["bb_lower"] or
                          prev["close"] <= prev["bb_lower"])
    curr_inside        = curr["close"] > curr["bb_lower"]

    if prev_touched_lower and curr_inside:
        logger.info(
            f"[BOLLINGER] BUY ✅ — rebote en banda inferior | "
            f"price={curr['close']:.2f} lower={curr['bb_lower']:.2f} upper={curr['bb_upper']:.2f}"
        )
        return "BUY"

    # SELL: vela actual toca la banda superior
    if curr["high"] >= curr["bb_upper"]:
        logger.info(
            f"[BOLLINGER] SELL — toca banda superior | "
            f"price={curr['close']:.2f} upper={curr['bb_upper']:.2f}"
        )
        return "SELL"

    # SELL adicional: precio cae de nuevo bajo la banda inferior (señal fallida)
    if curr["close"] < curr["bb_lower"]:
        logger.info(
            f"[BOLLINGER] SELL — precio bajo banda inferior (stop) | "
            f"price={curr['close']:.2f} lower={curr['bb_lower']:.2f}"
        )
        return "SELL"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60)

    if df is None or df.empty:
        logger.error(f"[BOLLINGER] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "bb_upper": None, "bb_lower": None}

    df     = calculate_bollinger(df)
    signal = detect_signal(df)
    last   = df.iloc[-1]

    def safe_round(val):
        return round(float(val), 4) if pd.notna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "bb_upper":    safe_round(last["bb_upper"]),
        "bb_mid":      safe_round(last["bb_mid"]),
        "bb_lower":    safe_round(last["bb_lower"]),
        "candle_time": str(last["datetime"]),
    }
