# ============================================================
#  EOLO — Estrategia: Sector RRG (Relative Rotation Graph)
#
#  Ref: nueva estrategia v1 (2026-04-27)
#
#  Lógica:
#    El RRG simplificado clasifica sectores en 4 cuadrantes:
#      Leading    (RS-Ratio > 100, RS-Momentum > 100) → comprar
#      Weakening  (RS-Ratio > 100, RS-Momentum < 100) → vigilar
#      Lagging    (RS-Ratio < 100, RS-Momentum < 100) → evitar
#      Improving  (RS-Ratio < 100, RS-Momentum > 100) → candidato
#
#    BUY : el sector ETF está en cuadrante "Leading" Y la señal
#          técnica del ETF individual confirma (precio sobre SMA20)
#    SELL: el sector ETF cae a "Weakening" o "Lagging"
#
#    Cálculo RS-Ratio y RS-Momentum:
#      rs_raw    = close(ETF) / close(SPY)  [normalizado * 100]
#      rs_ratio  = SMA(10, rs_raw) escalado al histórico (0-200)
#      rs_momentum = SMA(1, rs_ratio) vs SMA(4, rs_ratio) → si sube = >100
#
#  Universo : XLK, XLE, XLF, XLV, XLI, XLP, XLU, XLY, XLC, XLRE
#             (S&P 500 sector ETFs SPDR)
#  Requiere : market_data con acceso a SPY + sector ETFs
#  Categoría: momentum / sector rotation
# ============================================================
import os

import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME = "SECTOR_RRG"

SECTOR_ETFS = [
    "XLK",   # Technology
    "XLE",   # Energy
    "XLF",   # Financials
    "XLV",   # Health Care
    "XLI",   # Industrials
    "XLP",   # Consumer Staples
    "XLU",   # Utilities
    "XLY",   # Consumer Discretionary
    "XLC",   # Communication Services
    "XLRE",  # Real Estate
]

# Ventana para SMA corta (RS-Ratio)
RS_WINDOW_FAST = int(os.environ.get("RRG_FAST", "10"))
# Ventana para momentum del RS-Ratio
RS_WINDOW_MOM  = int(os.environ.get("RRG_MOM",  "4"))
# Período de datos para cálculo
DATA_PERIOD    = int(os.environ.get("RRG_PERIOD", "30"))  # días

# Profit target
PROFIT_PCT     = float(os.environ.get("RRG_PROFIT", "2.0"))
# Stop loss
STOP_PCT       = float(os.environ.get("RRG_STOP",   "1.5"))


def _compute_rrg(sector_closes: pd.Series, spy_closes: pd.Series,
                 window_fast: int = RS_WINDOW_FAST,
                 window_mom:  int = RS_WINDOW_MOM) -> tuple[float, float]:
    """
    Retorna (rs_ratio, rs_momentum) en escala 0-200 (100 = neutral).
    rs_ratio > 100 = outperforming SPY
    rs_momentum > 100 = acelerando vs SPY
    """
    if len(sector_closes) < window_fast + window_mom + 2:
        return 100.0, 100.0

    spy = spy_closes.values.astype(float)
    sec = sector_closes.values.astype(float)

    # Evitar division by zero
    spy[spy == 0] = np.nan
    ratio_raw = sec / spy * 100.0

    ratio_s = pd.Series(ratio_raw)
    # RS-Ratio: SMA corta del ratio relativo, escalada
    rs_ratio_series = ratio_s.rolling(window_fast).mean()
    rs_ratio = float(rs_ratio_series.iloc[-1])

    # Escalar RS-Ratio a 0-200 basado en la ventana completa
    hist = rs_ratio_series.dropna()
    if len(hist) > 2:
        lo, hi = hist.min(), hist.max()
        if hi != lo:
            rs_ratio_norm = (rs_ratio - lo) / (hi - lo) * 200.0
        else:
            rs_ratio_norm = 100.0
    else:
        rs_ratio_norm = 100.0

    # RS-Momentum: SMA(1) vs SMA(mom) del ratio
    sma_fast_arr = rs_ratio_series.rolling(1).mean()
    sma_slow_arr = rs_ratio_series.rolling(window_mom).mean()
    sma_fast = float(sma_fast_arr.iloc[-1])
    sma_slow = float(sma_slow_arr.iloc[-1])

    if np.isnan(sma_fast) or np.isnan(sma_slow) or sma_slow == 0:
        return rs_ratio_norm, 100.0

    rs_momentum_raw = sma_fast / sma_slow * 100.0
    # Escalar igual que rs_ratio
    rs_momentum_norm = min(200.0, max(0.0, rs_momentum_raw))

    return rs_ratio_norm, rs_momentum_norm


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
    spy_df: pd.DataFrame = None,
) -> str:
    if ticker.upper() not in {e.upper() for e in SECTOR_ETFS}:
        return "HOLD"
    if spy_df is None or len(spy_df) < RS_WINDOW_FAST + RS_WINDOW_MOM + 5:
        return "HOLD"
    if len(df) < RS_WINDOW_FAST + RS_WINDOW_MOM + 5:
        return "HOLD"

    price = float(df.iloc[-1]["close"])

    # ── SELL ─────────────────────────────────────────────────
    if entry_price is not None and entry_price > 0:
        pnl = (price - entry_price) / entry_price * 100
        if pnl >= PROFIT_PCT:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — profit {pnl:+.2f}%")
            return "SELL"
        if pnl <= -STOP_PCT:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL (stop) — {pnl:+.2f}%")
            return "SELL"
        # Salir si salió de leading
        window = max(DATA_PERIOD * 1, RS_WINDOW_FAST + RS_WINDOW_MOM + 5)
        sec_c = df["close"].tail(window)
        spy_c = spy_df["close"].tail(window)
        rs_r, rs_m = _compute_rrg(sec_c, spy_c)
        if rs_r < 100 or rs_m < 100:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — salió de Leading "
                f"(ratio={rs_r:.1f} mom={rs_m:.1f})"
            )
            return "SELL"
        return "HOLD"

    # ── BUY: sector en cuadrante Leading ─────────────────────
    window = max(DATA_PERIOD * 1, RS_WINDOW_FAST + RS_WINDOW_MOM + 5)
    sec_c = df["close"].tail(window)
    spy_c = spy_df["close"].tail(window)
    rs_ratio, rs_momentum = _compute_rrg(sec_c, spy_c)

    logger.debug(
        f"[{STRATEGY_NAME}] {ticker} ratio={rs_ratio:.1f} "
        f"momentum={rs_momentum:.1f}"
    )

    # Cuadrante Leading: RS-Ratio > 100 Y RS-Momentum > 100
    if rs_ratio <= 100 or rs_momentum <= 100:
        return "HOLD"

    # Confirmación técnica: precio sobre SMA20 del sector
    sma20 = float(df["close"].tail(20).mean())
    if price < sma20:
        logger.debug(f"[{STRATEGY_NAME}] {ticker} skip — precio bajo SMA20")
        return "HOLD"

    logger.info(
        f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.2f} — "
        f"Leading (ratio={rs_ratio:.1f}, mom={rs_momentum:.1f}), "
        f"precio > SMA20={sma20:.2f}"
    )
    return "BUY"


def analyze(market_data, ticker: str, macro=None, **kwargs) -> dict:
    """Wrapper para bot_main.py."""
    if ticker.upper() not in {e.upper() for e in SECTOR_ETFS}:
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": "not eligible", "strategy": STRATEGY_NAME}
    try:
        tf = kwargs.get("timeframe", 1)
        freq = max(1, min(tf, 30))
        period = DATA_PERIOD + 5

        df = market_data.get_candles(
            ticker, period_type="day", period=period,
            frequency_type="minute", frequency=freq,
        )
        spy_df = market_data.get_candles(
            "SPY", period_type="day", period=period,
            frequency_type="minute", frequency=freq,
        )

        if df is None or df.empty:
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}

        entry_price = kwargs.get("entry_price")
        signal = detect_signal(
            df, ticker, macro=macro,
            entry_price=entry_price, spy_df=spy_df,
        )
        price = float(df.iloc[-1]["close"])
        return {
            "signal":   signal,
            "ticker":   ticker,
            "price":    price,
            "reason":   f"{STRATEGY_NAME} signal={signal}",
            "strategy": STRATEGY_NAME,
        }
    except Exception as e:
        logger.error(f"[{STRATEGY_NAME}] analyze error {ticker}: {e}")
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": f"error: {e}", "strategy": STRATEGY_NAME}
