# ============================================================
#  EOLO — Estrategia: Volume-Validated Reversal Bar
#
#  Ref: trading_strategies_v2.md #15r
#
#  Lógica (reversal bar cuantitativa, sin candlestick subjetivo):
#    La vela actual cumple:
#      - rango > 2 × ATR
#      - volumen > 3 × SMA(20)
#      - cierra en el 20% superior (BUY) o inferior (SHORT) del rango
#      - viene precedida por ≥ 5 velas bajistas (BUY) o alcistas (SHORT)
#        en las últimas 6.
#
#  Categoría: reversal_bar (stop al extremo de la barra).
#  Universo: todos.
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "REVERSAL_BAR"

VOL_MA_PERIOD  = 20
ATR_PERIOD     = 14
RANGE_ATR_MULT = 2.0
VOL_SPIKE_MULT = 3.0
CLOSE_TOP_PCT  = 0.80
CLOSE_BOT_PCT  = 0.20
PRIOR_WINDOW   = 6
PRIOR_REQ      = 5


# ── Indicadores ───────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["atr"]    = calculate_atr(df, ATR_PERIOD)
    df["vol_ma"] = df["volume"].rolling(VOL_MA_PERIOD).mean()
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    need = max(ATR_PERIOD, VOL_MA_PERIOD, PRIOR_WINDOW) + 2
    if len(df) < need:
        return "HOLD"

    last = df.iloc[-1]
    price = float(last["close"])

    atr    = last.get("atr")
    vol_ma = last.get("vol_ma")
    if pd.isna(atr) or pd.isna(vol_ma):
        return "HOLD"

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.025   # 2 ATR ≈ ~2.5%
        sl = stop_loss     if stop_loss     is not None else 0.012

        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Entrada ───────────────────────────────────────────
    bar_range = float(last["high"]) - float(last["low"])
    if bar_range <= 0:
        return "HOLD"

    wide_range   = bar_range > RANGE_ATR_MULT * float(atr)
    high_volume  = float(last["volume"]) > VOL_SPIKE_MULT * float(vol_ma)
    close_pos    = (float(last["close"]) - float(last["low"])) / bar_range
    close_top    = close_pos > CLOSE_TOP_PCT
    close_bottom = close_pos < CLOSE_BOT_PCT

    prior = df.iloc[-(PRIOR_WINDOW + 1):-1]
    prior_bearish_ct = int((prior["close"] < prior["open"]).sum())
    prior_bullish_ct = int((prior["close"] > prior["open"]).sum())

    if wide_range and high_volume and close_top and prior_bearish_ct >= PRIOR_REQ:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY — reversal bar alcista | "
            f"range={bar_range:.2f} ({bar_range/atr:.1f}×ATR) vol={last['volume']:.0f} "
            f"close_pos={close_pos:.0%} prior_bearish={prior_bearish_ct}/{PRIOR_WINDOW}"
        )
        return "BUY"

    if wide_range and high_volume and close_bottom and prior_bullish_ct >= PRIOR_REQ:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} SHORT setup (long-only → HOLD) | "
            f"range={bar_range:.2f} close_pos={close_pos:.0%} "
            f"prior_bullish={prior_bullish_ct}/{PRIOR_WINDOW}"
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
    df = market_data.get_price_history(ticker, candles=40)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    def safe_round(val, nd=4):
        return round(float(val), nd) if pd.notna(val) else None

    bar_range = float(last["high"]) - float(last["low"])
    close_pos = (
        (float(last["close"]) - float(last["low"])) / bar_range if bar_range > 0 else None
    )

    return {
        "ticker":       ticker,
        "signal":       signal,
        "strategy":     STRATEGY_NAME,
        "price":        round(float(last["close"]), 4),
        "atr":          safe_round(last.get("atr")),
        "bar_range":    round(bar_range, 4),
        "close_pos":    round(close_pos, 2) if close_pos is not None else None,
        "vol_ratio":    safe_round(
                            float(last["volume"]) / float(last["vol_ma"])
                            if pd.notna(last.get("vol_ma")) and last.get("vol_ma") else None,
                            2,
                       ),
        "candle_time":  str(last["datetime"]),
    }
