# ============================================================
#  EOLO — Estrategia: OBV Trend Follower (multi-TF)
#
#  Ref: trading_strategies_v2.md #17
#
#  Lógica (trend confirmation):
#    - Calcula OBV en 5m y su rollup 60m (1h)
#    - BUY si:
#        * OBV_1h rompe el máximo de las últimas 20 velas 1h
#        * OBV_5m rompe el máximo de las últimas 20 velas 5m
#        * Precio 1h también rompe resistencia (últimos 20 highs 1h)
#    - Sin posición abierta: BUY si se cumple la confluencia.
#    - Posición abierta: SELL si OBV_5m cae bajo su SMA(20).
#
#  Notas:
#    * Requiere velas de 1min o 5min; el rollup a 1h se hace con
#      pandas.resample.
#    * Usa `datetime` de cada barra como índice temporal.
#
#  Universo: todos.
#  Categoría: momentum_breakout (filtro).
# ============================================================
import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME = "OBV_MTF"

LOOKBACK   = 20          # para detectar breakouts en OBV
OBV_MA_P   = 20          # SMA(OBV) para salida
TF_5M      = "5min"      # resolución intradía
TF_1H      = "60min"


# ── Resampling ────────────────────────────────────────────

def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "datetime" not in df.columns:
        return df
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    return df


def _resample(df_indexed: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }
    return df_indexed.resample(rule).agg(agg).dropna(how="all")


def _calc_obv(bars: pd.DataFrame) -> pd.Series:
    direction = np.sign(bars["close"].diff().fillna(0))
    return (direction * bars["volume"].fillna(0)).cumsum()


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if df is None or df.empty:
        return "HOLD"

    df_i = _ensure_datetime_index(df)
    if df_i.empty:
        return "HOLD"

    bars_5m = _resample(df_i, TF_5M)
    bars_1h = _resample(df_i, TF_1H)

    if len(bars_5m) < LOOKBACK + 2 or len(bars_1h) < LOOKBACK + 2:
        return "HOLD"

    obv_5m = _calc_obv(bars_5m)
    obv_1h = _calc_obv(bars_1h)

    price = float(bars_5m["close"].iloc[-1])

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        obv_5m_sma = obv_5m.rolling(OBV_MA_P).mean()
        if pd.notna(obv_5m_sma.iloc[-1]) and obv_5m.iloc[-1] < obv_5m_sma.iloc[-1]:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — OBV_5m cruzó bajo SMA(20)")
            return "SELL"

        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.03
        sl = stop_loss     if stop_loss     is not None else 0.015
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    obv_1h_breakout = obv_1h.iloc[-1] > obv_1h.iloc[-(LOOKBACK + 1):-1].max()
    obv_5m_breakout = obv_5m.iloc[-1] > obv_5m.iloc[-(LOOKBACK + 1):-1].max()
    price_1h_break  = bars_1h["close"].iloc[-1] > bars_1h["high"].iloc[-(LOOKBACK + 1):-1].max()

    if obv_1h_breakout and obv_5m_breakout and price_1h_break:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — OBV 1h/5m + precio 1h en breakout | "
            f"price={price:.2f}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    # Necesitamos histórico suficiente para rollups 1h × 20 = ≥ 20h.
    df = market_data.get_price_history(ticker, candles=0, days=5)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)

    last = df.iloc[-1]
    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "candle_time": str(last["datetime"]),
    }
