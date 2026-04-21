# ============================================================
#  EOLO — Estrategia: Relative Volume (RVOL) Breakout
#
#  Ref: trading_strategies_v2.md #2
#
#  Lógica:
#    - RVOL = volumen última vela / SMA(volumen, 20)
#    - BUY : cierre > rolling high 20 Y rvol > umbral del ticker
#    - SELL: cierre < rolling low 20 Y rvol > umbral del ticker
#    - Sin posición abierta → genera señal de entrada
#    - Con posición abierta → sale por stop-loss (1 ATR bajo el nivel roto)
#      o take profit (entry + 1.5 * rango consolidación).
#
#  Tickers (umbrales):
#    SPY/QQQ 2.0, AAPL/NVDA 2.5, TSLA 3.0, MSTR 4.0,
#    SOXL/NVDL/TSLL 2.5, TQQQ 2.0, otros 2.5
#
#  Categoría: momentum_breakout  (stop = 1 * ATR contra el nivel)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "RVOL_BREAKOUT"

RVOL_THRESHOLDS = {
    "SPY":  2.0, "QQQ":  2.0, "TQQQ": 2.0,
    "AAPL": 2.5, "NVDA": 2.5, "SOXL": 2.5, "NVDL": 2.5, "TSLL": 2.5,
    "TSLA": 3.0,
    "MSTR": 4.0,
}
DEFAULT_RVOL_THRESHOLD = 2.5

VOL_MA_PERIOD    = 20
BREAKOUT_LOOKBACK = 20     # rolling high/low lookback
ATR_PERIOD       = 14
STOP_ATR_MULT    = 1.0     # stop 1 ATR contra el nivel
TP_CONSOLIDATION_MULT = 1.5


# ── Indicadores ───────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vol_ma"]        = df["volume"].rolling(VOL_MA_PERIOD).mean()
    df["rvol"]          = df["volume"] / df["vol_ma"].replace(0, pd.NA)
    df["roll_high"]     = df["high"].rolling(BREAKOUT_LOOKBACK).max().shift(1)
    df["roll_low"]      = df["low"].rolling(BREAKOUT_LOOKBACK).min().shift(1)
    df["atr"]           = calculate_atr(df, ATR_PERIOD)
    df["consolidation_range"] = df["roll_high"] - df["roll_low"]
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if len(df) < max(VOL_MA_PERIOD, BREAKOUT_LOOKBACK, ATR_PERIOD) + 2:
        return "HOLD"

    threshold = RVOL_THRESHOLDS.get(ticker.upper(), DEFAULT_RVOL_THRESHOLD)

    curr  = df.iloc[-1]
    price = float(curr["close"])

    rvol       = curr.get("rvol")
    roll_high  = curr.get("roll_high")
    roll_low   = curr.get("roll_low")
    atr        = curr.get("atr")
    cons_range = curr.get("consolidation_range")

    if pd.isna(rvol) or pd.isna(roll_high) or pd.isna(roll_low) or pd.isna(atr):
        return "HOLD"

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.02
        sl = stop_loss     if stop_loss     is not None else 0.01

        if profit_pct >= tp:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%} "
                f"(umbral {tp:+.2%})"
            )
            return "SELL"
        if profit_pct <= -sl:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}"
            )
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    break_up   = price > roll_high
    break_down = price < roll_low
    volume_ok  = rvol > threshold

    if volume_ok and break_up:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — rvol={rvol:.2f}>{threshold:.1f} "
            f"& close {price:.2f} > roll_high {roll_high:.2f} | ATR={atr:.2f}"
        )
        return "BUY"

    if volume_ok and break_down:
        # Nota: v1 long-only; reportamos como HOLD (SELL sin posición no aplica)
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} BREAKDOWN pero long-only → HOLD "
            f"(rvol={rvol:.2f} close {price:.2f} < roll_low {roll_low:.2f})"
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
                "price": None, "rvol": None, "roll_high": None, "roll_low": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    def safe_round(val, nd=4):
        return round(float(val), nd) if pd.notna(val) else None

    return {
        "ticker":       ticker,
        "signal":       signal,
        "strategy":     STRATEGY_NAME,
        "price":        round(float(last["close"]), 4),
        "rvol":         safe_round(last.get("rvol"), 2),
        "threshold":    RVOL_THRESHOLDS.get(ticker.upper(), DEFAULT_RVOL_THRESHOLD),
        "roll_high":    safe_round(last.get("roll_high")),
        "roll_low":     safe_round(last.get("roll_low")),
        "atr":          safe_round(last.get("atr")),
        "candle_time":  str(last["datetime"]),
    }
