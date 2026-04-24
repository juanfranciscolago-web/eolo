# ============================================================
#  EOLO — Estrategia: Time-Segmented Volume (TSV) Cross
#
#  Ref: trading_strategies_v2.md #20
#
#  Lógica (confirmación de trend):
#    TSV_t = Σ( (close - close_prev) * volume )  a lo largo de 18 velas
#    - BUY  si TSV cruza de negativo a positivo Y la vela actual
#            tiene cuerpo > 50% del rango (vela de fuerza).
#    - Exit: SL/TP convencional.
#
#  Universo: todos.
#  Categoría: momentum_breakout.
# ============================================================
import pandas as pd
# FASE 6 Tier 3: Asset-specific TSV periods
TSV_PERIOD_BY_ASSET = {"TQQQ": 12, "SOXL": 15, "SPY": 14}
FAST_MA_BY_ASSET = {"TSLA": 3, "NVDA": 3, "AAPL": 4}
from loguru import logger

STRATEGY_NAME = "TSV"

TSV_WINDOW    = 18
ATR_PERIOD    = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pv = df["close"].diff() * df["volume"]
    df["tsv"] = pv.rolling(TSV_WINDOW).sum()
    df["atr"] = calculate_atr(df, ATR_PERIOD)
    return df


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if len(df) < TSV_WINDOW + 2:
        return "HOLD"

    prev = df.iloc[-2]
    last = df.iloc[-1]
    price = float(last["close"])

    if pd.isna(last.get("tsv")) or pd.isna(prev.get("tsv")):
        return "HOLD"

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.02
        sl = stop_loss     if stop_loss     is not None else 0.01
        # TSV cruza a negativo → salida
        if last["tsv"] < 0 and prev["tsv"] >= 0:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TSV cruzó a negativo")
            return "SELL"
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    cross_up = float(prev["tsv"]) < 0 and float(last["tsv"]) > 0

    bar_range = float(last["high"]) - float(last["low"])
    if bar_range <= 0:
        return "HOLD"
    strength_bar = (float(last["close"]) - float(last["open"])) > 0.5 * bar_range

    if cross_up and strength_bar:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — TSV cruzó a positivo con vela de fuerza | "
            f"tsv prev={prev['tsv']:.0f} now={last['tsv']:.0f} body/range="
            f"{(last['close']-last['open'])/bar_range:.2f}"
        )
        return "BUY"

    return "HOLD"


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
        "tsv":         safe_round(last.get("tsv"), 2),
        "atr":         safe_round(last.get("atr")),
        "candle_time": str(last["datetime"]),
    }
