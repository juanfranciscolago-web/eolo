# ============================================================
#  EOLO — Estrategia: Vela Pivot (Volume Breakout + Daily Pivot)
#
#  Basado en Robot_VELA_Pivot_TempoSTUDY.ts
#
#  Lógica:
#    "Vela Magenta" = vela con volumen×rango excepcional
#    (alta energía de precio) Y alcista.
#
#    BUY : Vela de alta energía en la barra anterior
#          Y precio actual > Pivot del día anterior
#          (PP = (prev_high + prev_low + prev_close) / 3)
#    SELL: precio cae bajo el Pivot del día anterior
#          O precio sube 2×ATR desde entrada (take profit)
#
#  Indicadores:
#    value2 = volume × range  → energía absoluta
#    value3 = volume / range  → densidad de volumen
#    Condición: value2 >= max(value2, 20) × 0.9
#               AND value3 >= max(value3, 20) × 0.9
#               AND close > open
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 1-3 por día
# ============================================================
import pandas as pd
import numpy as np
from loguru import logger

STRATEGY_NAME  = "VELA_PIVOT"
LOOKBACK       = 20    # ventana para detectar vela de alta energía
TOLERANCE      = 0.90  # % del máximo histórico (0.90 = dentro del 10%)
MIN_BARS       = 30


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    rng = df["high"] - df["low"]
    rng = rng.replace(0, 1e-10)

    df["value2"] = df["volume"] * rng                    # energía absoluta
    df["value3"] = df["volume"] / rng                    # densidad de volumen

    # Máximos históricos de la ventana (excluyendo barra actual con shift)
    df["max_v2"] = df["value2"].shift(1).rolling(LOOKBACK).max()
    df["max_v3"] = df["value3"].shift(1).rolling(LOOKBACK).max()

    # Condición: cerca del máximo de energía Y vela alcista
    df["high_energy"] = (
        (df["value2"] >= df["max_v2"] * TOLERANCE) &
        (df["value3"] >= df["max_v3"] * TOLERANCE) &
        (df["close"]  >  df["open"])
    )

    # Pivot del día anterior: (prev_H + prev_L + prev_C) / 3
    # Usamos shift sobre las barras del día — para 1-min candles,
    # agrupamos por fecha y tomamos el día anterior
    df["date"] = pd.to_datetime(df["datetime"]).dt.date
    daily_stats = df.groupby("date").agg(
        prev_high=("high",  "max"),
        prev_low= ("low",   "min"),
        prev_close=("close","last")
    ).reset_index()
    daily_stats["pivot"] = (daily_stats["prev_high"] +
                             daily_stats["prev_low"]  +
                             daily_stats["prev_close"]) / 3
    daily_stats["date_next"] = daily_stats["date"].shift(-1)

    # Mapa: fecha → pivot del día anterior
    pivot_map = {}
    for _, row in daily_stats.iterrows():
        if pd.notna(row["date_next"]):
            pivot_map[row["date_next"]] = row["pivot"]

    df["pivot"] = df["date"].map(pivot_map)

    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    close       = float(curr["close"])
    pivot       = curr["pivot"]
    high_energy_prev = bool(prev["high_energy"])

    if pd.isna(pivot):
        return "HOLD"

    pivot = float(pivot)

    # ── SELL: precio cae bajo el pivot ────────────────────
    if float(prev["close"]) >= pivot and close < pivot:
        logger.info(
            f"[VELA_PIVOT] {ticker} SELL ✅ — close cayó bajo pivot | "
            f"close={close:.4f} pivot={pivot:.4f}"
        )
        return "SELL"

    # ── BUY: vela de alta energía anterior + sobre pivot ──
    if high_energy_prev and close > pivot:
        logger.info(
            f"[VELA_PIVOT] {ticker} BUY ✅ — vela energía + sobre pivot | "
            f"close={close:.4f} pivot={pivot:.4f} | "
            f"v2={prev['value2']:.0f} max_v2={prev['max_v2']:.0f}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=0, days=2)

    if df is None or df.empty:
        logger.error(f"[VELA_PIVOT] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "pivot": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    def safe(val):
        return round(float(val), 4) if not pd.isna(val) else None

    return {
        "ticker":       ticker,
        "signal":       signal,
        "strategy":     STRATEGY_NAME,
        "price":        safe(last["close"]),
        "pivot":        safe(last["pivot"]),
        "high_energy":  bool(last["high_energy"]) if not pd.isna(last["high_energy"]) else None,
        "candle_time":  str(last["datetime"]),
    }
