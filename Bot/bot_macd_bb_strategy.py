# ============================================================
#  EOLO — Estrategia: MACD + Bollinger Upper Breakout
#
#  Basado en SimpleStrategy1STUDY.ts
#
#  Lógica:
#    BUY : Bollinger upper band en expansión
#          Y precio cruza arriba de BB_upper[1]
#          Y vela alcista (close > open)
#          Y MACD value > 0  Y  MACD value > signal line
#    SELL: MACD value cruza abajo de signal line
#
#  Indicadores:
#    BB   : SMA(12) ± 2σ
#    MACD : EMA(12) - EMA(26), signal EMA(9)
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 2-5 por día
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME  = "MACD_BB"
BB_PERIOD      = 12
BB_MULT        = 2.0
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
MIN_BARS       = 40


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Bollinger Bands
    sma             = df["close"].rolling(BB_PERIOD).mean()
    std             = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"]  = sma + BB_MULT * std
    df["bb_lower"]  = sma - BB_MULT * std

    # MACD
    ema_fast        = df["close"].ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow        = df["close"].ewm(span=MACD_SLOW,   adjust=False).mean()
    df["macd"]      = ema_fast - ema_slow
    df["macd_sig"]  = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_sig"]

    # NEW: EMA(50) for trend context (FASE 6 tuning — reduces false signals)
    df["ema50"]     = df["close"].ewm(span=50, adjust=False).mean()

    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    close       = float(curr["close"])
    open_       = float(curr["open"])
    bb_upper    = float(curr["bb_upper"])
    bb_upper_p  = float(prev["bb_upper"])
    macd        = float(curr["macd"])
    macd_sig    = float(curr["macd_sig"])
    macd_p      = float(prev["macd"])
    macd_sig_p  = float(prev["macd_sig"])
    ema50       = float(curr["ema50"]) if "ema50" in curr else None

    if any(pd.isna(v) for v in [bb_upper, bb_upper_p, macd, macd_sig]):
        return "HOLD"

    # NEW: Trend context from EMA(50) (FASE 6 tuning)
    in_uptrend   = close > ema50 if ema50 is not None else True
    in_downtrend = close < ema50 if ema50 is not None else True

    # ── SELL: MACD cruza abajo de signal + downtrend context ──────────────────
    if macd_p >= macd_sig_p and macd < macd_sig and in_downtrend:
        logger.info(
            f"[MACD_BB] {ticker} SELL ✅ — MACD cruzó bajo signal + downtrend | "
            f"macd={macd:.4f} sig={macd_sig:.4f} | close={close:.4f} ema50={ema50:.4f if ema50 else 'N/A'}"
        )
        return "SELL"

    # ── BUY: BB expandiendo + close cruza BB_upper + MACD alcista + uptrend ──
    bb_expanding  = bb_upper > bb_upper_p
    price_breakout = float(prev["close"]) <= bb_upper_p and close > bb_upper_p
    bullish_candle = close > open_
    macd_bullish   = macd > 0 and macd > macd_sig

    if bb_expanding and price_breakout and bullish_candle and macd_bullish and in_uptrend:
        logger.info(
            f"[MACD_BB] {ticker} BUY ✅ — BB breakout + MACD bullish + uptrend | "
            f"close={close:.4f} BB_upper_prev={bb_upper_p:.4f} | "
            f"MACD={macd:.4f} sig={macd_sig:.4f} | ema50={ema50:.4f if ema50 else 'N/A'}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60, days=1)

    if df is None or df.empty:
        logger.error(f"[MACD_BB] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "macd": None, "bb_upper": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    def safe(val):
        return round(float(val), 4) if not pd.isna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       safe(last["close"]),
        "bb_upper":    safe(last["bb_upper"]),
        "bb_lower":    safe(last["bb_lower"]),
        "macd":        safe(last["macd"]),
        "macd_sig":    safe(last["macd_sig"]),
        "candle_time": str(last["datetime"]),
    }
