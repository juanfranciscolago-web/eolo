# ============================================================
#  EOLO — Estrategia: Heikin Ashi + EMA Cloud
#
#  Basado en Nino3_Mariano_Cloud_SepSTRATEGY.ts
#
#  Lógica:
#    BUY : Primera vela HA que cruza arriba de EMA5
#          + BullCloud activo (EMA5 > EMA12 > EMA17)
#          + separación entre EMAs > umbral (tendencia acelerando)
#    SELL: Vela HA cruza abajo de EMA5
#
#  Heikin Ashi sintético:
#    HA_close = (open + high + low + close) / 4
#    HA_open  = (prev_HA_open + prev_HA_close) / 2
#
#  Targets (visuales, no automáticos): EMA5 ± 1/2/3 × ATR(14)
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 3-6 por día
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME  = "HA_CLOUD"
EMA_SEP_THRESH = 0.20   # separación mínima EMA5/EMA12 en % (0.20 = 0.20%)
MIN_BARS       = 30


# ── Heikin Ashi sintético ──────────────────────────────────

def calculate_ha(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula velas Heikin Ashi sintéticas sobre precios reales.
    HA_close = (O+H+L+C) / 4
    HA_open  = (prev_HA_open + prev_HA_close) / 2  (iterativo)
    """
    df = df.copy()
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4

    ha_open = ha_close.copy()
    # Primera barra: HA_open = (open + close) / 2
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2

    df["ha_open"]  = ha_open
    df["ha_close"] = ha_close
    return df


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema5"]  = df["close"].ewm(span=5,  adjust=False).mean()
    df["ema12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema17"] = df["close"].ewm(span=17, adjust=False).mean()

    # BullCloud: EMA5 > EMA12 > EMA17
    df["bull_cloud"] = (df["ema5"] > df["ema12"]) & (df["ema12"] > df["ema17"])
    df["bear_cloud"] = (df["ema5"] < df["ema12"]) & (df["ema12"] < df["ema17"])

    # Separación EMA5/EMA12 en %
    df["ema_sep_pct"]  = ((df["ema5"] / df["ema12"]) * 100) - 100
    df["ema_sep_bull"] = (df["ema_sep_pct"] > EMA_SEP_THRESH) & \
                         (df["ema_sep_pct"] >= df["ema_sep_pct"].shift(1))

    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    ha_open   = float(curr["ha_open"])
    ha_close  = float(curr["ha_close"])
    ema5      = float(curr["ema5"])
    bull      = bool(curr["bull_cloud"])
    sep_bull  = bool(curr["ema_sep_bull"])

    # ── SELL: vela HA cruza abajo de EMA5 ─────────────────
    if float(prev["ha_close"]) >= float(prev["ema5"]) and ha_close < ema5:
        logger.info(
            f"[HA_CLOUD] {ticker} SELL ✅ — HA_close={ha_close:.4f} cruzó bajo EMA5={ema5:.4f}"
        )
        return "SELL"

    # ── BUY: primer cruce HA sobre EMA5 + BullCloud + sep ─
    prev_bull_cond = float(prev["ha_open"]) < float(prev["ema5"]) and \
                     float(prev["ha_close"]) > float(prev["ema5"])
    curr_bull_cond = ha_open < ema5 and ha_close > ema5

    if curr_bull_cond and not prev_bull_cond and bull:
        if sep_bull:
            logger.info(
                f"[HA_CLOUD] {ticker} BUY ✅ — HA cruzó EMA5 | "
                f"HA_open={ha_open:.4f} HA_close={ha_close:.4f} EMA5={ema5:.4f} | "
                f"sep={curr['ema_sep_pct']:.3f}%"
            )
            return "BUY"
        else:
            logger.info(
                f"[HA_CLOUD] {ticker} BUY bloqueado — sep EMA insuficiente: "
                f"{curr['ema_sep_pct']:.3f}% < {EMA_SEP_THRESH}%"
            )

    if curr_bull_cond and not bull:
        logger.debug(
            f"[HA_CLOUD] {ticker} BUY bloqueado — sin BullCloud: "
            f"EMA5={float(curr['ema5']):.4f} EMA12={float(curr['ema12']):.4f}"
        )

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60, days=1)

    if df is None or df.empty:
        logger.error(f"[HA_CLOUD] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "ha_close": None, "ema5": None}

    df = calculate_ha(df)
    df = calculate_indicators(df)

    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]),    4),
        "ha_open":     round(float(last["ha_open"]),  4),
        "ha_close":    round(float(last["ha_close"]), 4),
        "ema5":        round(float(last["ema5"]),     4),
        "ema12":       round(float(last["ema12"]),    4),
        "bull_cloud":  bool(last["bull_cloud"]),
        "ema_sep_pct": round(float(last["ema_sep_pct"]), 3),
        "candle_time": str(last["datetime"]),
    }
