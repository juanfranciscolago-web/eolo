# ============================================================
#  EOLO — Estrategia: EMA Cloud + TSI + MACD
#
#  Basado en EMA_TSI_V104STRATEGY.ts
#
#  Lógica:
#    BUY : EMA5 > EMA12 (cloud alcista)
#          Y separación EMA5/EMA12 > 0.1% y creciendo
#          Y TSI subiendo (tsi > tsi[2])
#          Y TSI > MACD_avg (momentum confirmado)
#    SELL: EMA5 cruza abajo de EMA12
#          O TSI cruza abajo de MACD_avg
#          O separación EMA cae más del 40% desde entrada
#
#  Indicadores:
#    EMA5, EMA12, EMA34, EMA50
#    TSI   : double smoothed EWM de price changes (25, 13)
#    MACD  : EMA(10) - EMA(22), signal EMA(8)
#
#  Tickers recomendados: SPY, QQQ, AAPL, TSLA, NVDA
#  Señales esperadas   : 2-4 por día
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME    = "EMA_TSI"
TSI_LONG         = 25
TSI_SHORT        = 13
MACD_FAST        = 10
MACD_SLOW        = 22
MACD_SIGNAL      = 8
EMA_SEP_THRESH   = 0.10   # 0.10% separación mínima EMA5/EMA12
MIN_BARS         = 50


# ── TSI (True Strength Index) ─────────────────────────────

def calculate_tsi(series: pd.Series, long: int = TSI_LONG, short: int = TSI_SHORT) -> pd.Series:
    """
    TSI = 100 × double_smoothed(diff) / double_smoothed(|diff|)
    Double smooth = EWM(long) → EWM(short)
    """
    diff  = series.diff(1)
    ds    = diff.ewm(span=long,  adjust=False).mean().ewm(span=short, adjust=False).mean()
    ds_ab = diff.abs().ewm(span=long, adjust=False).mean().ewm(span=short, adjust=False).mean()
    return 100 * ds / ds_ab.replace(0, 1e-10)


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ema5"]  = df["close"].ewm(span=5,  adjust=False).mean()
    df["ema12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema34"] = df["close"].ewm(span=34, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # EMA cloud bullish
    df["ema_bullish"] = df["ema5"] > df["ema12"]

    # EMA separation %
    df["ema_sep"]  = ((df["ema5"] / df["ema12"]) * 100) - 100
    df["sep_bull"] = (df["ema_sep"] > EMA_SEP_THRESH) & \
                     (df["ema_sep"] >= df["ema_sep"].shift(1))

    # TSI
    df["tsi"] = calculate_tsi(df["close"])

    # MACD avg (signal line acts as threshold for TSI comparison)
    ema_fast        = df["close"].ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow        = df["close"].ewm(span=MACD_SLOW,   adjust=False).mean()
    macd_val        = ema_fast - ema_slow
    df["macd_avg"]  = macd_val.ewm(span=MACD_SIGNAL, adjust=False).mean()

    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    ema5      = float(curr["ema5"])
    ema12     = float(curr["ema12"])
    ema5_p    = float(prev["ema5"])
    ema12_p   = float(prev["ema12"])
    tsi       = float(curr["tsi"])
    tsi_2     = float(df.iloc[-3]["tsi"]) if len(df) >= 3 else tsi
    tsi_p     = float(prev["tsi"])
    macd_avg  = float(curr["macd_avg"])
    macd_avg_p = float(prev["macd_avg"])
    sep_bull  = bool(curr["sep_bull"])
    ema_bull  = bool(curr["ema_bullish"])

    if any(pd.isna(v) for v in [ema5, ema12, tsi, macd_avg]):
        return "HOLD"

    # ── SELL: EMA5 cruza bajo EMA12 ───────────────────────
    if ema5_p >= ema12_p and ema5 < ema12:
        logger.info(
            f"[EMA_TSI] {ticker} SELL ✅ — EMA5 cruzó bajo EMA12 | "
            f"EMA5={ema5:.4f} EMA12={ema12:.4f}"
        )
        return "SELL"

    # ── SELL: TSI cruza abajo de MACD_avg ─────────────────
    if tsi_p >= macd_avg_p and tsi < macd_avg:
        logger.info(
            f"[EMA_TSI] {ticker} SELL ✅ — TSI cruzó bajo MACD_avg | "
            f"TSI={tsi:.2f} MACD_avg={macd_avg:.4f}"
        )
        return "SELL"

    # ── BUY: EMA cloud alcista + sep creciendo + TSI bull ─
    tsi_rising   = tsi > tsi_2          # TSI subiendo vs 2 barras atrás
    tsi_vs_macd  = tsi > macd_avg       # TSI sobre MACD avg

    if ema_bull and sep_bull and tsi_rising and tsi_vs_macd:
        logger.info(
            f"[EMA_TSI] {ticker} BUY ✅ — EMA cloud + TSI bullish | "
            f"EMA5={ema5:.4f} EMA12={ema12:.4f} sep={curr['ema_sep']:.3f}% "
            f"TSI={tsi:.2f} MACD_avg={macd_avg:.4f}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60, days=1)

    if df is None or df.empty:
        logger.error(f"[EMA_TSI] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "tsi": None, "ema5": None}

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
        "ema5":        safe(last["ema5"]),
        "ema12":       safe(last["ema12"]),
        "tsi":         round(float(last["tsi"]), 2) if not pd.isna(last["tsi"]) else None,
        "macd_avg":    safe(last["macd_avg"]),
        "ema_sep_pct": round(float(last["ema_sep"]), 3) if not pd.isna(last["ema_sep"]) else None,
        "candle_time": str(last["datetime"]),
    }
