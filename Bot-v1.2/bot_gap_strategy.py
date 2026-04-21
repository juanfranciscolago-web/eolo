# ============================================================
#  EOLO — Gap Fade/Follow Strategy
#
#  Detecta dos tipos de gaps:
#    1. GAP DE APERTURA: primera vela del día vs cierre del día anterior
#       → El gap más importante y confiable
#    2. GAP INTRADAY: low actual > high anterior (gap up entre velas 5-min)
#       → Raro pero válido
#
#  Un gap mínimo de GAP_MIN_PCT% filtra el ruido
#
#  Gap Up  → BUY  (seguir momentum alcista)
#  Gap Down → SELL (seguir momentum bajista)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "GAP"
GAP_MIN_PCT   = 0.002   # mínimo 0.2% para considerar gap real (filtra ruido)


def detect_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detecta gaps entre velas consecutivas de 5 minutos.
    isgapup  : low actual > high anterior (gap alcista)
    isgapdwn : high actual < low anterior (gap bajista)
    gap_pct  : tamaño del gap en porcentaje
    """
    df = df.copy()
    prev_high = df["high"].shift(1)
    prev_low  = df["low"].shift(1)

    df["isgapup"]  = df["low"]  > prev_high
    df["isgapdwn"] = df["high"] < prev_low

    # Tamaño del gap en %
    df["gap_pct"] = 0.0
    gap_up_mask   = df["isgapup"]
    gap_dn_mask   = df["isgapdwn"]
    df.loc[gap_up_mask, "gap_pct"] = (
        (df.loc[gap_up_mask, "low"] - prev_high[gap_up_mask]) /
         prev_high[gap_up_mask]
    )
    df.loc[gap_dn_mask, "gap_pct"] = (
        (prev_low[gap_dn_mask] - df.loc[gap_dn_mask, "high"]) /
         df.loc[gap_dn_mask, "high"]
    )

    # Solo contar gaps que superen el mínimo
    df.loc[df["gap_pct"] < GAP_MIN_PCT, ["isgapup", "isgapdwn"]] = False

    return df


def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < 3:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Priorizar el gap de la vela de apertura (segunda vela = 9:35, comparada con 9:30)
    # También detecta gaps intraday
    if curr["isgapup"]:
        logger.info(
            f"[GAP] {ticker} BUY ✅ — Gap UP {curr['gap_pct']:.2%} | "
            f"prev_high={prev['high']:.4f} curr_low={curr['low']:.4f}"
        )
        return "BUY"
    elif curr["isgapdwn"]:
        logger.info(
            f"[GAP] {ticker} SELL ✅ — Gap DOWN {curr['gap_pct']:.2%} | "
            f"prev_low={prev['low']:.4f} curr_high={curr['high']:.4f}"
        )
        return "SELL"

    return "HOLD"


def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=20)

    if df is None or df.empty:
        logger.error(f"[GAP] No candle data for {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "gap_up": False, "gap_down": False}

    df     = detect_gaps(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    return {
        "ticker":    ticker,
        "signal":    signal,
        "strategy":  STRATEGY_NAME,
        "price":     round(float(last["close"]), 4),
        "gap_up":    bool(last["isgapup"]),
        "gap_down":  bool(last["isgapdwn"]),
        "gap_pct":   round(float(last["gap_pct"]) * 100, 3),
        "candle_time": str(last["datetime"]),
    }
