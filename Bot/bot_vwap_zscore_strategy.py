# ============================================================
#  EOLO — Estrategia: VWAP Z-Score (time-filtered)
#
#  Ref: trading_strategies_v2.md #11
#
#  Lógica (mean reversion):
#    - Solo opera después de las 11:30 ET (antes de eso el noise
#      intradía inflá Z-scores falsos).
#    - Calcula la distancia close-VWAP y su Z-Score rolling(50).
#    - BUY  si z-score < -2.5 y volumen actual > SMA(20).
#    - SELL si z-score > +2.5 (long-only: reportado como HOLD).
#    - Target: volver al VWAP. Stop: 0.75 ATR contra la entrada.
#
#  Universo: todos (líquidos).
#  Categoría: mean_reversion.
# ============================================================
from datetime import datetime

import pandas as pd
import pytz
from loguru import logger

STRATEGY_NAME = "VWAP_ZSCORE"

ZSCORE_WINDOW   = 50
ZSCORE_THRESH   = 2.5
VOL_MA_PERIOD   = 20
ATR_PERIOD      = 14
STOP_ATR_MULT   = 0.75

# FASE 6 Tier 2 tuning: Asset-specific Z-score thresholds
ZSCORE_THRESH_BY_ASSET = {
    "JPM": 1.2,    # Tier 2: Tighter threshold for mean reversion
    "AMZN": 2.5,   # Tier 1: Keep aggressive threshold
}

EASTERN = pytz.timezone("America/New_York")

ACTIVE_FROM_HOUR   = 11
ACTIVE_FROM_MINUTE = 30


# ── Indicadores ───────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vwap"]     = calculate_vwap(df)
    df["distance"] = df["close"] - df["vwap"]
    df["z_mean"]   = df["distance"].rolling(ZSCORE_WINDOW).mean()
    df["z_std"]    = df["distance"].rolling(ZSCORE_WINDOW).std().replace(0, pd.NA)
    df["z_score"]  = (df["distance"] - df["z_mean"]) / df["z_std"]
    df["vol_ma"]   = df["volume"].rolling(VOL_MA_PERIOD).mean()
    df["atr"]      = calculate_atr(df, ATR_PERIOD)
    return df


def _is_active_window(ts) -> bool:
    """Acepta timestamp tz-naive (asumido UTC) o tz-aware."""
    if ts is None or pd.isna(ts):
        now = datetime.now(EASTERN)
    else:
        if ts.tzinfo is None:
            now = ts.tz_localize("UTC").tz_convert(EASTERN)
        else:
            now = ts.tz_convert(EASTERN)
    return (now.hour, now.minute) >= (ACTIVE_FROM_HOUR, ACTIVE_FROM_MINUTE)


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    need = max(ZSCORE_WINDOW, ATR_PERIOD, VOL_MA_PERIOD) + 2
    if len(df) < need:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    # Filtro temporal: antes de 11:30 ET no opera
    if not _is_active_window(last.get("datetime")):
        return "HOLD"

    z      = last.get("z_score")
    vwap   = last.get("vwap")
    vol_ma = last.get("vol_ma")

    if pd.isna(z) or pd.isna(vwap) or pd.isna(vol_ma):
        return "HOLD"

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.015
        sl = stop_loss     if stop_loss     is not None else 0.008

        # Target natural: retorno al VWAP
        if price >= vwap:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — volvió al VWAP {vwap:.2f}")
            return "SELL"
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    high_vol = float(last["volume"]) > float(vol_ma)

    # NEW: Asset-specific Z-score threshold (FASE 6 tuning)
    thresh = ZSCORE_THRESH_BY_ASSET.get(ticker, ZSCORE_THRESH)

    if z < -thresh and high_vol:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — z={z:.2f} (<-{thresh}) | "
            f"close={price:.2f} vwap={vwap:.2f} | target=VWAP"
        )
        return "BUY"

    if z > thresh and high_vol:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | z={z:.2f}"
        )
        return "HOLD"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    # 0=día entero (vwap acumulado), más histórico para rolling 50
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    def safe_round(val, nd=4):
        return round(float(val), nd) if pd.notna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "vwap":        safe_round(last.get("vwap")),
        "z_score":     safe_round(last.get("z_score"), 2),
        "atr":         safe_round(last.get("atr")),
        "candle_time": str(last["datetime"]),
    }
