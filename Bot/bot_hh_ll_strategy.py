# ============================================================
#  EOLO — Estrategia: High/Low Breakout + EMA Filter
#
#  Basado en High_Low_Beta_podadaSTRATEGY.ts
#
#  Lógica:
#    BUY LONG : close > EMA3/8/17  Y  close rompe máximo de 10 velas
#               → breakout alcista con tendencia confirmada
#    SALIDA   : close cruza abajo de EMA5 (stop dinámico)
#
#    NOTA: El original también tiene SHORT pero Eolo opera solo LONG.
#          La señal de SELL es el stop de la posición LONG.
# FASE 6 Tier 3: Asset-specific lookback
HH_LL_PERIOD_BY_ASSET = {"JPM": 20, "NVDA": 15, "QQQ": 18}
#
#  Indicadores:
#    EMA3, EMA5, EMA8, EMA17  (rápidas para tendencia inmediata)
#    HH = Highest(high, 10)[1] — máximo de las 10 velas anteriores
#    LL = Lowest (low,  10)[1] — mínimo de las 10 velas anteriores
#
#  Tickers recomendados: SPY, QQQ, AAPL, TSLA, NVDA
#  Señales esperadas   : 2-4 por día
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME  = "HH_LL"
HH_LL_PERIOD   = 10    # ventana para máximos/mínimos
MIN_BARS       = 25


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema3"]  = df["close"].ewm(span=3,  adjust=False).mean()
    df["ema5"]  = df["close"].ewm(span=5,  adjust=False).mean()
    df["ema8"]  = df["close"].ewm(span=8,  adjust=False).mean()
    df["ema17"] = df["close"].ewm(span=17, adjust=False).mean()

    # Máximo/mínimo de las N velas ANTERIORES (shift(1) = excluye barra actual)
    df["hh"] = df["high"].shift(1).rolling(window=HH_LL_PERIOD).max()
    df["ll"] = df["low"].shift(1).rolling(window=HH_LL_PERIOD).min()
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    close  = float(curr["close"])
    ema3   = float(curr["ema3"])
    ema5   = float(curr["ema5"])
    ema8   = float(curr["ema8"])
    ema17  = float(curr["ema17"])
    hh     = float(curr["hh"])
    ll     = float(curr["ll"])

    if any(pd.isna(v) for v in [hh, ll, ema3, ema5, ema8, ema17]):
        return "HOLD"

    # ── SELL: stop — close cruza abajo de EMA5 ────────────
    if float(prev["close"]) >= float(prev["ema5"]) and close < ema5:
        logger.info(
            f"[HH_LL] {ticker} SELL ✅ — stop EMA5: close={close:.4f} < ema5={ema5:.4f}"
        )
        return "SELL"

    # ── BUY: breakout de máximo + tendencia alcista ────────
    # close cruza arriba del HH: vela anterior estaba bajo HH, actual encima
    prev_close = float(prev["close"])
    above_trend = close > ema3 and close > ema8 and close > ema17
    breakout_up = prev_close <= float(prev["hh"] if not pd.isna(prev["hh"]) else hh) and close > hh

    if above_trend and breakout_up:
        logger.info(
            f"[HH_LL] {ticker} BUY ✅ — breakout HH={hh:.4f} | "
            f"close={close:.4f} EMA3={ema3:.4f} EMA8={ema8:.4f} EMA17={ema17:.4f}"
        )
        return "BUY"

    if not above_trend and close > hh:
        logger.debug(
            f"[HH_LL] {ticker} BUY bloqueado — sin tendencia: "
            f"close={close:.4f} ema3={ema3:.4f} ema8={ema8:.4f} ema17={ema17:.4f}"
        )

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=60, days=1)

    if df is None or df.empty:
        logger.error(f"[HH_LL] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "hh": None, "ll": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "ema5":        round(float(last["ema5"]),  4),
        "ema17":       round(float(last["ema17"]), 4),
        "hh":          round(float(last["hh"]), 4) if not pd.isna(last["hh"]) else None,
        "ll":          round(float(last["ll"]), 4) if not pd.isna(last["ll"]) else None,
        "candle_time": str(last["datetime"]),
    }
