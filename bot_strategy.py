# ============================================================
#  EOLO EMA CROSSOVER BOT — Strategy
#  Fetches 1-min candles from Schwab, calculates EMA 3 / EMA 8
#  Filtro opcional SMA200: solo BUY si close > SMA(200)
#  Matching TOS strategy by dap711
# ============================================================
import pandas as pd
from loguru import logger

# ── EMA settings ──────────────────────────────────────────
EMA_SHORT    = 3
EMA_LONG     = 8
SMA_TREND    = 200   # filtro de tendencia (SMA200)
CANDLES      = 50    # velas 1-min para EMA3/EMA8 (últimas 50 velas)
CANDLES_SMA  = 220   # velas para SMA200 — 1 día 1-min ≈ 390 velas → suficiente
DAYS_NORMAL  = 1     # días a pedir sin filtro SMA200
DAYS_SMA200  = 1     # 1 día de velas 1-min ≈ 390 candles → SMA200 disponible


def calculate_emas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula EMA3, EMA8 y SMA200.
    adjust=False hace que la EMA coincida con TOS/TradingView.
    """
    df = df.copy()
    df["ema_short"] = df["close"].ewm(span=EMA_SHORT, adjust=False).mean()
    df["ema_long"]  = df["close"].ewm(span=EMA_LONG,  adjust=False).mean()

    # SMA200 — solo si hay suficientes barras
    if len(df) >= SMA_TREND:
        df["sma200"] = df["close"].rolling(window=SMA_TREND).mean()
    else:
        df["sma200"] = float("nan")

    return df


def detect_crossover(df: pd.DataFrame, use_sma200_filter: bool = True) -> str:
    """
    Compara las últimas 2 velas completadas para detectar cruce.

    BUY  : EMA3 cruza ARRIBA de EMA8
           + si use_sma200_filter=True → solo cuando close > SMA200
    SELL : EMA3 cruza ABAJO de EMA8 (sin filtro — siempre cerrar)

    El filtro SMA200 evita entrar en tendencia bajista (igual que TOS).
    """
    if len(df) < 2:
        return "HOLD"

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    prev_above = prev["ema_short"] > prev["ema_long"]
    curr_above = curr["ema_short"] > curr["ema_long"]

    # ── SELL: siempre que haya cruce hacia abajo ───────────
    if prev_above and not curr_above:
        logger.info(f"[EMA] SELL ✅ — cruce EMA3 bajo EMA8")
        return "SELL"

    # ── BUY: cruce hacia arriba ────────────────────────────
    if not prev_above and curr_above:
        if use_sma200_filter:
            sma200 = curr.get("sma200")
            close  = curr["close"]
            if pd.isna(sma200):
                logger.warning(f"[EMA] BUY bloqueado — SMA200 sin datos ({len(df)} barras, se necesitan 200+)")
                return "HOLD"
            if close <= sma200:
                logger.info(f"[EMA] BUY bloqueado — bajo SMA200: close={close:.2f} <= sma200={sma200:.2f}")
                return "HOLD"
            logger.info(f"[EMA] BUY ✅ — cruce arriba + sobre SMA200: close={close:.2f} > sma200={sma200:.2f}")
        else:
            logger.info(f"[EMA] BUY ✅ — cruce arriba (sin filtro SMA200)")
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str, use_sma200_filter: bool = True) -> dict:
    """
    Pipeline completo: fetch candles → EMAs → SMA200 → señal de cruce.

    use_sma200_filter: leído desde Firestore/dashboard en bot_main.py
    Cuando está activo pide 3 días de datos (≈234 velas) para poder
    calcular SMA200 de velas de 5 minutos.
    """
    if use_sma200_filter:
        # 3 días de historia → ≈ 234 velas → suficiente para SMA200
        df = market_data.get_price_history(ticker, candles=0, days=DAYS_SMA200)
    else:
        df = market_data.get_price_history(ticker, candles=CANDLES, days=DAYS_NORMAL)

    if df is None or df.empty:
        logger.error(f"No candle data for {ticker}")
        return {"ticker": ticker, "signal": "ERROR",
                "price": None, "ema_short": None, "ema_long": None,
                "sma200": None, "sma200_filter": use_sma200_filter}

    df     = calculate_emas(df)
    signal = detect_crossover(df, use_sma200_filter=use_sma200_filter)
    last   = df.iloc[-1]

    sma200_val = float(last["sma200"]) if not pd.isna(last["sma200"]) else None
    above_sma  = (float(last["close"]) > sma200_val) if sma200_val else None

    return {
        "ticker":         ticker,
        "signal":         signal,
        "price":          round(float(last["close"]),      4),
        "ema_short":      round(float(last["ema_short"]),  4),
        "ema_long":       round(float(last["ema_long"]),   4),
        "sma200":         round(sma200_val, 4) if sma200_val else None,
        "above_sma200":   above_sma,
        "sma200_filter":  use_sma200_filter,
        "candle_time":    str(last["datetime"]),
    }
