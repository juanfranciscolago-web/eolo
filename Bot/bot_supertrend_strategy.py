# ============================================================
#  EOLO — Estrategia: Supertrend
#
#  Basado en Heikin_Ashi_MACDSTRATEGY.ts
#
#  Lógica:
#    Supertrend = banda ATR trailing que cambia de lado
#    cuando el precio la cruza.
#
#    BUY : Supertrend cambia de bajista → alcista
#          (close cruza ARRIBA de la banda superior)
#    SELL: Supertrend cambia de alcista → bajista
#          (close cruza ABAJO de la banda inferior)
#
#  Parámetros originales ThinkScript:
#    AtrMult = 0.60, nATR = 6, AvgType = HULL
#    (usamos SMA para compatibilidad con pandas)
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 3-8 por día
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME = "SUPERTREND"
ATR_PERIOD    = 6      # nATR del ThinkScript original
ATR_MULT      = 0.60   # multiplicador de banda
MIN_BARS      = 20     # mínimo de velas para calcular

# FASE 6 Tier 2 tuning: Asset-specific ATR periods
ATR_PERIOD_BY_ASSET = {
    "QQQ": 7,      # Tier 2: Faster volatility response for QQQ
    "UNH": 6,      # Tier 1: Keep original (already optimal)
}


# ── Indicadores ───────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range simple (SMA de TR)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def calculate_supertrend(df: pd.DataFrame,
                         period: int = ATR_PERIOD,
                         multiplier: float = ATR_MULT) -> pd.DataFrame:
    """
    Calcula el Supertrend y la dirección (1=alcista, -1=bajista).
    Retorna el DataFrame original con columnas 'supertrend' y 'st_direction'.
    """
    df   = df.copy()
    atr  = calculate_atr(df, period)
    hl2  = (df["high"] + df["low"]) / 2

    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper     = upper_basic.copy()
    lower     = lower_basic.copy()
    st        = pd.Series(np.nan, index=df.index)
    direction = pd.Series(0,      index=df.index)

    for i in range(1, len(df)):
        # Actualizar banda superior (trailing)
        if upper_basic.iloc[i] < upper.iloc[i - 1] or df["close"].iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i - 1]

        # Actualizar banda inferior (trailing)
        if lower_basic.iloc[i] > lower.iloc[i - 1] or df["close"].iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i - 1]

        # Determinar Supertrend
        prev_st  = st.iloc[i - 1]
        prev_dir = direction.iloc[i - 1]

        if pd.isna(prev_st):
            # Primera barra: asignar según precio vs banda
            if df["close"].iloc[i] > upper.iloc[i]:
                st.iloc[i]        = lower.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i]        = upper.iloc[i]
                direction.iloc[i] = -1
        elif prev_st == upper.iloc[i - 1]:
            # Estaba en banda superior (bajista)
            if df["close"].iloc[i] > upper.iloc[i]:
                st.iloc[i]        = lower.iloc[i]   # flip alcista
                direction.iloc[i] = 1
            else:
                st.iloc[i]        = upper.iloc[i]
                direction.iloc[i] = -1
        else:
            # Estaba en banda inferior (alcista)
            if df["close"].iloc[i] < lower.iloc[i]:
                st.iloc[i]        = upper.iloc[i]   # flip bajista
                direction.iloc[i] = -1
            else:
                st.iloc[i]        = lower.iloc[i]
                direction.iloc[i] = 1

    df["supertrend"]    = st
    df["st_direction"]  = direction
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    curr_dir = int(curr["st_direction"])
    prev_dir = int(prev["st_direction"])

    # BUY: Supertrend acaba de cambiar a alcista (flip)
    if prev_dir != 1 and curr_dir == 1:
        logger.info(
            f"[SUPERTREND] {ticker} BUY ✅ — flip alcista | "
            f"close={curr['close']:.4f} ST={curr['supertrend']:.4f}"
        )
        return "BUY"

    # SELL: Supertrend acaba de cambiar a bajista (flip)
    if prev_dir != -1 and curr_dir == -1:
        logger.info(
            f"[SUPERTREND] {ticker} SELL ✅ — flip bajista | "
            f"close={curr['close']:.4f} ST={curr['supertrend']:.4f}"
        )
        return "SELL"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60, days=1)

    if df is None or df.empty:
        logger.error(f"[SUPERTREND] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "supertrend": None, "direction": None}

    # NEW: Asset-specific ATR period (FASE 6 tuning)
    atr_period = ATR_PERIOD_BY_ASSET.get(ticker, ATR_PERIOD)
    df     = calculate_supertrend(df, period=atr_period)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]),      4),
        "supertrend":  round(float(last["supertrend"]), 4) if not pd.isna(last["supertrend"]) else None,
        "direction":   int(last["st_direction"]),
        "candle_time": str(last["datetime"]),
    }
