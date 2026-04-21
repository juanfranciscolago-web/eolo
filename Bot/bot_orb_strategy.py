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
from datetime import datetime, timedelta
from loguru import logger

STRATEGY_NAME  = "ORB"
ORB_CANDLES    = 3      # primeras 3 velas = 15 min de opening range (default)
PROFIT_TARGET  = 0.02   # 2% take profit (default; overrideable desde dashboard)
STOP_LOSS      = 0.01   # 1% stop loss   (default; overrideable desde dashboard)
EASTERN        = pytz.timezone("America/New_York")

# ── Tiered ORB por ticker (ref: trading_strategies_v2.md #13) ─
#  Duración del opening range en minutos. Asumimos velas de 1 min
#  desde Schwab; si son velas de 5 min, dividir internamente.
ORB_DURATION_MIN = {
    "SPY": 5,  "QQQ": 5,  "TQQQ": 5,
    "AAPL": 15, "NVDA": 15, "TSLA": 15,
    "SOXL": 15, "NVDL": 15, "TSLL": 15,
    "MSTR": 30,
}
DEFAULT_ORB_DURATION_MIN = 15

# ── Filtro RVOL para breakout (opcional, #13 requiere volumen) ─
ORB_RVOL_MIN        = 2.0
ORB_VOL_MA_PERIOD   = 20


# ── Opening Range ─────────────────────────────────────────

def get_orb_duration_min(ticker: str) -> int:
    return ORB_DURATION_MIN.get(ticker.upper(), DEFAULT_ORB_DURATION_MIN)


def get_orb_range(df: pd.DataFrame, ticker: str = None) -> tuple:
    """
    Calcula el High/Low del opening range del día con duración
    adaptada por ticker (#13 tiered):
        SPY/QQQ/TQQQ:  5 min
        AAPL/NVDA/TSLA/SOXL/NVDL/TSLL: 15 min
        MSTR:          30 min
        otros:         15 min
    Retorna (orb_high, orb_low) o (None, None) si aún no hay suficientes datos.
    """
    df = df.copy()

    # Convertir datetime a Eastern para filtrar por horario de mercado
    if df["datetime"].dt.tz is None:
        df["datetime_et"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
    else:
        df["datetime_et"] = df["datetime"].dt.tz_convert(EASTERN)

    duration_min = get_orb_duration_min(ticker) if ticker else (ORB_CANDLES * 5)

    today = datetime.now(EASTERN).date()
    open_start = EASTERN.localize(datetime(today.year, today.month, today.day, 9, 30))
    open_end   = open_start + timedelta(minutes=duration_min)

    orb_df = df[(df["datetime_et"] >= open_start) & (df["datetime_et"] < open_end)]

    # Requerir al menos 3 velas independientemente de la duración
    if len(orb_df) < 3:
        return None, None

    return float(orb_df["high"].max()), float(orb_df["low"].min())


# ── Señal ─────────────────────────────────────────────────

def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    """
    Detecta BUY en breakout del ORB o SELL por take profit/stop loss.
    entry_price:   precio al que se entró (None si no hay posición abierta).
    profit_target: fracción (0.02 = 2%) o None → usa PROFIT_TARGET del módulo.
    stop_loss:     fracción (0.01 = 1%) o None → usa STOP_LOSS del módulo.
    """
    tp = PROFIT_TARGET if profit_target is None else float(profit_target)
    sl = STOP_LOSS     if stop_loss     is None else float(stop_loss)

    orb_high, orb_low = get_orb_range(df, ticker)

    if orb_high is None:
        # ORB todavía no está definido (antes del fin del opening range)
        return "HOLD"

    curr  = df.iloc[-1]
    prev  = df.iloc[-2]
    price = float(curr["close"])

    # ── Si hay posición abierta → evaluar salida ──────────
    if entry_price is not None:
        profit_pct = (price - entry_price) / entry_price

        if profit_pct >= tp:
            logger.info(
                f"[ORB] {ticker} SELL — Take profit {profit_pct:+.2%} "
                f"(umbral {tp:+.2%}) | entry={entry_price:.2f} now={price:.2f}"
            )
            return "SELL"

        if profit_pct <= -sl:
            logger.info(
                f"[ORB] {ticker} SELL — Stop loss {profit_pct:+.2%} "
                f"(umbral -{sl:.2%}) | entry={entry_price:.2f} now={price:.2f}"
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

    # RVOL gate (#13): breakout debe ir con volumen institucional
    vol_ma = df["volume"].rolling(ORB_VOL_MA_PERIOD).mean().iloc[-1]
    rvol = (float(curr["volume"]) / float(vol_ma)) if (pd.notna(vol_ma) and vol_ma > 0) else None

    if prev_price <= orb_high and price > orb_high:
        if rvol is not None and rvol < ORB_RVOL_MIN:
            logger.debug(
                f"[ORB] {ticker} Breakout sin volumen (rvol={rvol:.2f}<{ORB_RVOL_MIN}) → HOLD"
            )
            return "HOLD"
        logger.info(
            f"[ORB] {ticker} BUY — Breakout sobre ORB high {orb_high:.2f} | "
            f"price={price:.2f} | rvol={rvol:.2f}x" if rvol is not None
            else f"[ORB] {ticker} BUY — Breakout sobre ORB high {orb_high:.2f} | price={price:.2f}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    """
    entry_price  : precio de entrada si hay posición abierta (para calcular salida).
    profit_target: override del TP por defecto (fracción, p.ej. 0.04 = 4%).
    stop_loss    : override del SL por defecto (fracción, p.ej. 0.02 = 2%).
    Ambos overrides vienen desde el modal Config del dashboard.
    """
    # candles=0 → todas las velas del día (con 1-min ≈ 390 velas)
    # ORB necesita las primeras 3 velas (9:30-9:45) + el resto del día
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[ORB] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "orb_high": None, "orb_low": None}

    orb_high, orb_low = get_orb_range(df, ticker)
    signal            = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last              = df.iloc[-1]

    return {
        "ticker":       ticker,
        "signal":       signal,
        "strategy":     STRATEGY_NAME,
        "price":        round(float(last["close"]), 4),
        "orb_high":     round(orb_high, 4) if orb_high else None,
        "orb_low":      round(orb_low,  4) if orb_low  else None,
        "orb_duration": get_orb_duration_min(ticker),
        "entry_price":  entry_price,
        "candle_time":  str(last["datetime"]),
    }
