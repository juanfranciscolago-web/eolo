# ============================================================
#  EOLO — Estrategia: RSI + SMA200
#
#  Basado en RSI_strategy_Multiple_NinoSTRATEGY.ts
#
#  Lógica:
#    BUY : RSI(14) < 40  Y  close > SMA(200)
#          → mean reversion alcista con filtro de tendencia
#    SELL: RSI(14) > 60
#          → cierre cuando el RSI llega a zona de sobrecompra
#
#  Tickers recomendados: SPY, QQQ, AAPL, TSLA, NVDA
#  Señales esperadas   : 2-5 por día (selectiva)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "RSI_SMA200"
RSI_PERIOD    = 14
# FASE 6 Tier 3: Asset-specific RSI thresholds
RSI_BUY_MAX_BY_ASSET = {"JPM": 35, "SPY": 40, "QQQ": 38}
RSI_SELL_MIN_BY_ASSET = {"JPM": 65, "SPY": 60, "TSLA": 58}
RSI_BUY_MAX   = 40    # BUY cuando RSI  < 40 (zona de oversold)
RSI_SELL_MIN  = 60    # SELL cuando RSI > 60 (zona de overbought)
SMA_PERIOD    = 200   # filtro de tendencia


# ── Indicadores ───────────────────────────────────────────

def calculate_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI con suavizado EWM (equivalente a Wilder's smoothing)."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]    = calculate_rsi(df["close"])
    df["sma200"] = df["close"].rolling(window=SMA_PERIOD).mean()
    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str, sma200_daily: float | None = None) -> str:
    # H2 fix (audit 06-jun): el guard pedía 214 barras (SMA200+RSI) que NUNCA
    # se alcanzan intradía (~65-78 máx) → la estrategia quedaba en HOLD perpetuo.
    # El RSI solo necesita ~RSI_PERIOD*2 barras. El SMA200, como filtro de
    # tendencia, se calcula sobre velas DIARIAS (sma200_daily), no intradía.
    if len(df) < RSI_PERIOD * 2:
        logger.debug(f"[RSI_SMA200] {ticker} — insuficientes barras RSI ({len(df)})")
        return "HOLD"

    curr = df.iloc[-1]
    rsi    = curr["rsi"]
    close  = curr["close"]

    if pd.isna(rsi):
        return "HOLD"

    # ── SELL: RSI sobrecomprado ────────────────────────────
    if rsi > RSI_SELL_MIN:
        logger.info(
            f"[RSI_SMA200] {ticker} SELL ✅ — RSI={rsi:.1f} > {RSI_SELL_MIN} | "
            f"close={close:.4f}"
        )
        return "SELL"

    # ── BUY: RSI oversold + filtro de tendencia SMA200 diario ──
    # Si no hay SMA200 diario (fetch falló) → RSI-only, no bloquea.
    if rsi < RSI_BUY_MAX:
        if sma200_daily is not None and close < sma200_daily:
            logger.debug(
                f"[RSI_SMA200] {ticker} BUY filtrado — close={close:.4f} < "
                f"sma200_d={sma200_daily:.4f} (tendencia bajista)"
            )
            return "HOLD"
        trend = f"sma200_d={sma200_daily:.4f}" if sma200_daily is not None else "sin filtro daily"
        logger.info(
            f"[RSI_SMA200] {ticker} BUY ✅ — RSI={rsi:.1f} < {RSI_BUY_MAX} | "
            f"close={close:.4f} | {trend}"
        )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def _fetch_sma200_daily(market_data, ticker: str) -> float | None:
    """SMA200 sobre velas DIARIAS (filtro de tendencia). Best-effort → None.

    H2 fix: el SMA200 intradía es inalcanzable (~78 velas máx vs 200). El daily
    da ~290 sesiones (periodType=month, period 20) → SMA200 real. Usa cierres
    completos hasta ayer, que es exactamente lo que un filtro de tendencia quiere.
    """
    try:
        daily = market_data.get_price_history(ticker, candles=0, days=420, frequency=1440)
        if daily is None or daily.empty or len(daily) < SMA_PERIOD:
            logger.debug(f"[RSI_SMA200] {ticker} — daily insuficiente para SMA200 "
                         f"({0 if daily is None else len(daily)})")
            return None
        return float(daily["close"].rolling(window=SMA_PERIOD).mean().iloc[-1])
    except Exception as e:
        logger.debug(f"[RSI_SMA200] {ticker} — fetch SMA200 daily falló: {e}")
        return None


def analyze(market_data, ticker: str) -> dict:
    # RSI sobre el TF intradía activo; SMA200 sobre velas diarias (H2 fix).
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[RSI_SMA200] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "rsi": None, "sma200": None}

    df           = calculate_indicators(df)
    sma200_daily = _fetch_sma200_daily(market_data, ticker)
    signal       = detect_signal(df, ticker, sma200_daily=sma200_daily)
    last         = df.iloc[-1]

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       round(float(last["close"]), 4),
        "rsi":         round(float(last["rsi"]),   2) if not pd.isna(last["rsi"]) else None,
        "sma200":      round(sma200_daily, 4) if sma200_daily is not None else None,
        "above_sma200": (float(last["close"]) > sma200_daily) if sma200_daily is not None else None,
        "candle_time": str(last["datetime"]),
    }
