# ============================================================
#  EOLO — Estrategia C: Opening Range Breakout (ORB 15 min)
#
#  Lógica:
#    - Las primeras 3 velas de 5 min (9:30–9:45 ET) definen el rango
#    - BUY : primer cierre por encima del máximo del rango (breakout)
#    - SELL:
#        (a) +2% desde el precio de entrada (take profit)
#        (b) -1% desde el precio de entrada (stop loss)
#        (c) precio cierra por debajo del mínimo del rango
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 1–2 por día (muy selectiva)
# ============================================================
import pandas as pd
import pytz
from datetime import datetime
from loguru import logger

STRATEGY_NAME  = "ORB"
ORB_CANDLES    = 3      # primeras 3 velas = 15 min de opening range
PROFIT_TARGET  = 0.02   # 2% take profit
STOP_LOSS      = 0.01   # 1% stop loss
EASTERN        = pytz.timezone("America/New_York")


# ── Opening Range ─────────────────────────────────────────

def get_orb_range(df: pd.DataFrame) -> tuple:
    """
    Calcula el High/Low de las primeras 3 velas del día (9:30–9:45 ET).
    Retorna (orb_high, orb_low) o (None, None) si aún no hay suficientes datos.
    """
    df = df.copy()

    # Convertir datetime a Eastern para filtrar por horario de mercado
    if df["datetime"].dt.tz is None:
        df["datetime_et"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
    else:
        df["datetime_et"] = df["datetime"].dt.tz_convert(EASTERN)

    today = datetime.now(EASTERN).date()
    open_start = EASTERN.localize(datetime(today.year, today.month, today.day, 9, 30))
    open_end   = EASTERN.localize(datetime(today.year, today.month, today.day, 9, 45))

    orb_df = df[(df["datetime_et"] >= open_start) & (df["datetime_et"] < open_end)]

    if len(orb_df) < ORB_CANDLES:
        return None, None

    return float(orb_df["high"].max()), float(orb_df["low"].min())


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str, entry_price: float = None) -> str:
    """
    Detecta BUY en breakout del ORB o SELL por take profit/stop loss.
    entry_price: precio al que se entró (None si no hay posición abierta).
    """
    orb_high, orb_low = get_orb_range(df)

    if orb_high is None:
        # ORB todavía no está definido (antes de 9:45 ET)
        return "HOLD"

    curr  = df.iloc[-1]
    prev  = df.iloc[-2]
    price = float(curr["close"])

    # ── Si hay posición abierta → evaluar salida ──────────
    if entry_price is not None:
        profit_pct = (price - entry_price) / entry_price

        if profit_pct >= PROFIT_TARGET:
            logger.info(
                f"[ORB] {ticker} SELL — Take profit {profit_pct:+.2%} | "
                f"entry={entry_price:.2f} now={price:.2f}"
            )
            return "SELL"

        if profit_pct <= -STOP_LOSS:
            logger.info(
                f"[ORB] {ticker} SELL — Stop loss {profit_pct:+.2%} | "
                f"entry={entry_price:.2f} now={price:.2f}"
            )
            return "SELL"

        if price < orb_low:
            logger.info(
                f"[ORB] {ticker} SELL — Precio bajo ORB low {orb_low:.2f}"
            )
            return "SELL"

        return "HOLD"   # mantener posición

    # ── Sin posición → buscar breakout ───────────────────
    prev_price = float(prev["close"])

    if prev_price <= orb_high and price > orb_high:
        logger.info(
            f"[ORB] {ticker} BUY — Breakout sobre ORB high {orb_high:.2f} | "
            f"price={price:.2f}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str, entry_price: float = None) -> dict:
    """
    entry_price: precio de entrada si hay posición abierta (para calcular salida).
    Se pasa desde bot_main.py usando trader.entry_prices.get(ticker).
    """
    # candles=0 → todas las velas del día (con 1-min ≈ 390 velas)
    # ORB necesita las primeras 3 velas (9:30-9:45) + el resto del día
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[ORB] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "orb_high": None, "orb_low": None}

    orb_high, orb_low = get_orb_range(df)
    signal            = detect_signal(df, ticker, entry_price)
    last              = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "orb_high":    round(orb_high, 4) if orb_high else None,
        "orb_low":     round(orb_low,  4) if orb_low  else None,
        "entry_price": entry_price,
        "candle_time": str(last["datetime"]),
    }
