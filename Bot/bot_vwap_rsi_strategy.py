# ============================================================
#  EOLO — Estrategia A: VWAP + RSI
#
#  Lógica:
#    BUY : precio cruza VWAP de abajo hacia arriba
#           Y RSI(14) < RSI_BUY_MAX (no sobrecomprado)
#    SELL: precio cruza VWAP de arriba hacia abajo
#           O RSI(14) > RSI_SELL_MIN (sobrecomprado)
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 3–6 por día
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VWAP_RSI"
RSI_PERIOD    = 14
RSI_BUY_MAX   = 62    # RSI debe estar BAJO este valor para comprar (era 55, muy restrictivo)
RSI_SELL_MIN  = 75    # RSI por encima de esto → vender


# ── Indicadores ───────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    VWAP = suma(precio_típico * volumen) / suma(volumen)
    Acumulado desde la primera vela del día (intraday).
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


def calculate_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.Series:
    """RSI usando EWM (equivalente a Wilder's smoothing)."""
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame) -> str:
    if len(df) < RSI_PERIOD + 2:
        return "HOLD"

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_above_vwap = prev["close"] > prev["vwap"]
    curr_above_vwap = curr["close"] > curr["vwap"]

    # BUY: cruzó VWAP hacia arriba Y RSI no sobrecomprado
    if not prev_above_vwap and curr_above_vwap:
        if curr["rsi"] < RSI_BUY_MAX:
            logger.info(
                f"[VWAP_RSI] BUY ✅ — cruce VWAP arriba | "
                f"price={curr['close']:.2f} vwap={curr['vwap']:.2f} rsi={curr['rsi']:.1f}"
            )
            return "BUY"
        else:
            logger.info(
                f"[VWAP_RSI] BUY bloqueado — RSI muy alto: {curr['rsi']:.1f} >= {RSI_BUY_MAX} | "
                f"price={curr['close']:.2f} vwap={curr['vwap']:.2f}"
            )

    # SELL: cruzó VWAP hacia abajo
    if prev_above_vwap and not curr_above_vwap:
        logger.info(
            f"[VWAP_RSI] SELL — cruce VWAP abajo | "
            f"price={curr['close']:.2f} vwap={curr['vwap']:.2f}"
        )
        return "SELL"

    # SELL: RSI sobrecomprado
    if curr["rsi"] > RSI_SELL_MIN:
        logger.info(
            f"[VWAP_RSI] SELL — RSI sobrecomprado {curr['rsi']:.1f}"
        )
        return "SELL"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    # candles=0 → todas las velas del día (≈390 con 1-min)
    # VWAP es acumulado intraday, necesita todas las velas desde 9:30
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[VWAP_RSI] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "vwap": None, "rsi": None}

    df = df.copy()
    df["vwap"] = calculate_vwap(df)
    df["rsi"]  = calculate_rsi(df)

    signal = detect_signal(df)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "vwap":        round(float(last["vwap"]),  4),
        "rsi":         round(float(last["rsi"]),   2),
        "candle_time": str(last["datetime"]),
    }
