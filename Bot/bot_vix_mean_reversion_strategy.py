# ============================================================
#  EOLO — Estrategia: VIX Mean Reversion (term-structure filtered)
#
#  Ref: trading_strategies_v2.md #6
#
#  Lógica (VIX regime):
#    - VIX extremo: último > mean(20m) + 2*std(20m)
#    - Filtro estructural: NO fade si contango está invertido
#      (VIX > VIX3M) — implica estrés real sostenido.
#    - Filtro: VIX9D está rolling over (último < max últimos 5).
#    - BUY (SPY/QQQ/TQQQ) si los 3 se cumplen.
#
#  Universo: SPY, QQQ, TQQQ.
#  Requiere: MacroFeeds inyectado via kwarg `macro`.
#  Categoría: vix_regime (stop 2 * ATR).
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "VIX_MEAN_REV"
ELIGIBLE_TICKERS = {"SPY", "QQQ", "TQQQ"}

VIX_WINDOW_MIN   = 20 * 60    # VIX muestras (si poll=1min → 20 horas)
VIX_STD_MULT     = 2.0
ATR_PERIOD       = 14


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return "HOLD"
    if len(df) < ATR_PERIOD + 2:
        return "HOLD"

    last  = df.iloc[-1]
    price = float(last["close"])

    # ── Exit ──────────────────────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.025
        sl = stop_loss     if stop_loss     is not None else 0.012
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    if macro is None:
        logger.debug(f"[{STRATEGY_NAME}] {ticker} HOLD — macro feeds no inyectado")
        return "HOLD"

    vix_series = macro.series("VIX", minutes=VIX_WINDOW_MIN)
    if len(vix_series) < 20:
        return "HOLD"

    vix_now   = float(vix_series.iloc[-1])
    vix_mean  = float(vix_series.mean())
    vix_std   = float(vix_series.std())
    if vix_std <= 0:
        return "HOLD"

    extreme_vix = vix_now > vix_mean + VIX_STD_MULT * vix_std

    vix9d_series = macro.series("VIX9D", minutes=VIX_WINDOW_MIN)
    vix9d_rollover = False
    if len(vix9d_series) >= 5:
        vix9d_rollover = float(vix9d_series.iloc[-1]) < float(vix9d_series.iloc[-5:-1].max())

    contango_inverted = macro.term_structure_inverted()

    if extreme_vix and vix9d_rollover and contango_inverted is False:
        atr = float(calculate_atr(df).iloc[-1])
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — fade VIX | "
            f"vix={vix_now:.2f} mean={vix_mean:.2f} std={vix_std:.2f} | ATR={atr:.2f}"
        )
        return "BUY"

    return "HOLD"


def analyze(
    market_data,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    df = market_data.get_price_history(ticker, candles=60)
    if df is None or df.empty:
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME, "price": None}

    signal = detect_signal(df, ticker, macro, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "vix_latest":  (macro.latest("VIX") if macro is not None else None),
        "candle_time": str(last["datetime"]),
    }
