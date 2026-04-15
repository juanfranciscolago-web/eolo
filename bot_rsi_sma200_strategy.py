# ============================================================
#  EOLO — Estrategia: RSI + SMA200
#
#  Basado en RSI_strategy_Multiple_NinoSTRATEGY.ts
#
#  Lógica:
#    BUY : RSI(14) < 40  Y  close > SMA(200)
#          → mean reversion alcista con filtro de tendencia
#    SELL: RSI(14) > 60
#          → cierre cuando el RSI llega a zona de sobrecompra
#
#  Tickers recomendados: SPY, QQQ, AAPL, TSLA, NVDA
#  Señales esperadas   : 2-5 por día (selectiva)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "RSI_SMA200"
RSI_PERIOD    = 14
RSI_BUY_MAX   = 40    # BUY cuando RSI  < 40 (zona de oversold)
RSI_SELL_MIN  = 60    # SELL cuando RSI > 60 (zona de overbought)
SMA_PERIOD    = 200   # filtro de tendencia


# ── Indicadores ───────────────────────────────────────────

def calculate_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI con suavizado EWM (equivalente a Wilder's smoothing)."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]    = calculate_rsi(df["close"])
    df["sma200"] = df["close"].rolling(window=SMA_PERIOD).mean()
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < SMA_PERIOD + RSI_PERIOD:
        logger.debug(f"[RSI_SMA200] {ticker} — insuficientes barras ({len(df)})")
        return "HOLD"

    curr = df.iloc[-1]
    rsi    = curr["rsi"]
    sma200 = curr["sma200"]
    close  = curr["close"]

    if pd.isna(rsi) or pd.isna(sma200):
        return "HOLD"

    # ── SELL: RSI sobrecomprado ────────────────────────────
    if rsi > RSI_SELL_MIN:
        logger.info(
            f"[RSI_SMA200] {ticker} SELL ✅ — RSI={rsi:.1f} > {RSI_SELL_MIN} | "
            f"close={close:.4f}"
        )
        return "SELL"

    # ── BUY: RSI oversold + sobre SMA200 ──────────────────
    if rsi < RSI_BUY_MAX and close > sma200:
        logger.info(
            f"[RSI_SMA200] {ticker} BUY ✅ — RSI={rsi:.1f} < {RSI_BUY_MAX} | "
            f"close={close:.4f} > SMA200={sma200:.4f}"
        )
        return "BUY"

    if rsi < RSI_BUY_MAX and close <= sma200:
        logger.info(
            f"[RSI_SMA200] {ticker} BUY bloqueado — bajo SMA200: "
            f"close={close:.4f} <= sma200={sma200:.4f} | RSI={rsi:.1f}"
        )

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    # 1 día de velas 1-min → ≈390 candles → suficiente para SMA200 + RSI
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[RSI_SMA200] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "rsi": None, "sma200": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    sma200_val = float(last["sma200"]) if not pd.isna(last["sma200"]) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "rsi":         round(float(last["rsi"]),   2) if not pd.isna(last["rsi"]) else None,
        "sma200":      round(sma200_val, 4) if sma200_val else None,
        "above_sma200": float(last["close"]) > sma200_val if sma200_val else None,
        "candle_time": str(last["datetime"]),
    }
