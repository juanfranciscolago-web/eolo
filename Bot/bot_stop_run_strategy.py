# ============================================================
#  EOLO — Estrategia: Stop Run Reversal (Volume Spike)
#
#  Ref: trading_strategies_v2.md #5
#
#  Lógica (scalp, time-stopped):
#    - Detecta una vela con volumen ≥ 3× SMA(20) que rompe un swing
#      reciente (últimos 5) pero CIERRA de vuelta DENTRO del rango.
#    - BUY  si rompió el swing-low y cerró por encima (stop run de bears)
#    - SELL si rompió el swing-high y cerró por debajo (stop run de bulls)
#      → v1 long-only: short se reporta como HOLD.
#
#  Universo ideal: SPY, QQQ (liquidez)
#  Categoría: scalp_institutional (stop 0.5 ATR, time-stop 3 min)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "STOP_RUN"

VOL_MA_PERIOD   = 20
VOL_SPIKE_MULT  = 3.0
SWING_LOOKBACK  = 5
ATR_PERIOD      = 14
STOP_ATR_MULT   = 0.5
TIME_STOP_MIN   = 3


# ── Indicadores ───────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vol_ma"] = df["volume"].rolling(VOL_MA_PERIOD).mean()
    df["atr"]    = calculate_atr(df, ATR_PERIOD)
    # Swings previos: excluimos la vela actual
    df["swing_low"]  = df["low"].rolling(SWING_LOOKBACK).min().shift(1)
    df["swing_high"] = df["high"].rolling(SWING_LOOKBACK).max().shift(1)
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    need = max(VOL_MA_PERIOD, ATR_PERIOD, SWING_LOOKBACK) + 2
    if len(df) < need:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    vol_ma     = last.get("vol_ma")
    swing_low  = last.get("swing_low")
    swing_high = last.get("swing_high")
    atr        = last.get("atr")

    if pd.isna(vol_ma) or pd.isna(swing_low) or pd.isna(swing_high) or pd.isna(atr):
        return "HOLD"

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.008   # scalp TP ~0.8%
        sl = stop_loss     if stop_loss     is not None else 0.005   # scalp SL ~0.5%

        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    spike = float(last["volume"]) > VOL_SPIKE_MULT * float(vol_ma)

    broke_low  = float(last["low"])  < float(swing_low)
    broke_high = float(last["high"]) > float(swing_high)

    reversed_low  = broke_low  and price > float(swing_low)
    reversed_high = broke_high and price < float(swing_high)

    if spike and reversed_low:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — stop run bears | "
            f"low={last['low']:.2f} < swing_low {swing_low:.2f}, close={price:.2f} | "
            f"vol={last['volume']:.0f} ({last['volume']/vol_ma:.1f}× avg)"
        )
        return "BUY"

    if spike and reversed_high:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
            f"high={last['high']:.2f} > swing_high {swing_high:.2f}, close={price:.2f}"
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
    df = market_data.get_price_history(ticker, candles=60)

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
        "vol_ratio":   safe_round(
                            float(last["volume"]) / float(last["vol_ma"])
                            if pd.notna(last.get("vol_ma")) and last.get("vol_ma") else None,
                            2,
                       ),
        "swing_low":   safe_round(last.get("swing_low")),
        "swing_high":  safe_round(last.get("swing_high")),
        "atr":         safe_round(last.get("atr")),
        "candle_time": str(last["datetime"]),
    }
