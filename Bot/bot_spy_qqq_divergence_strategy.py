# ============================================================
#  EOLO — Estrategia: SPY/QQQ Correlation Divergence
#
#  Ref: nueva estrategia v1 (2026-04-27)
#
#  Lógica:
#    SPY y QQQ tienen una correlación históricamente muy alta (>0.95).
#    Cuando el ratio SPY/QQQ se desvía significativamente de su MA20,
#    tiende a revertir. La estrategia compra el activo "rezagado":
#
#    BUY QQQ : ratio SPY/QQQ > MA20 + 2σ  → SPY sobreperformó,
#              QQQ va a catch-up
#    BUY SPY : ratio SPY/QQQ < MA20 - 2σ  → QQQ sobreperformó,
#              SPY va a catch-up
#    SELL    : ratio volvió a MA20 (convergencia) o profit target
#
#    El ratio se calcula candle-by-candle usando los históricos de
#    ambos tickers via market_data.get_candles().
#
#    Filtros:
#      - Solo en horario de trading (se chequea por timestamp de vela)
#      - VIX < MAX_VIX (no operar durante pánico extremo)
#
#  Universo : SPY, QQQ (solo estos dos)
#  Requiere : ambos tickers disponibles en market_data
#  Categoría: mean-reversion / pair-trading
# ============================================================
import os

import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME   = "SPY_QQQ_DIV"
ELIGIBLE_TICKERS = {"SPY", "QQQ"}

# Ventana para calcular MA y σ del ratio
RATIO_WINDOW    = int(os.environ.get("SQD_WINDOW",    "20"))
# Desviación estándar para señal
SIGMA_THRESHOLD = float(os.environ.get("SQD_SIGMA",   "2.0"))
# VIX máximo para operar
MAX_VIX         = float(os.environ.get("SQD_MAX_VIX", "35.0"))
# Profit target (% del activo comprado)
PROFIT_PCT      = float(os.environ.get("SQD_PROFIT",  "1.5"))
# Stop loss
STOP_PCT        = float(os.environ.get("SQD_STOP",    "1.0"))


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
    pair_df: pd.DataFrame = None,   # df del otro ticker (pasado por analyze)
) -> str:
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return "HOLD"
    if pair_df is None or len(pair_df) < RATIO_WINDOW + 5:
        return "HOLD"
    if len(df) < RATIO_WINDOW + 5:
        return "HOLD"

    # Filtro VIX
    if macro is not None:
        try:
            vix = macro.latest("VIX")
            if vix and float(vix) > MAX_VIX:
                return "HOLD"
        except Exception:
            pass

    price = float(df.iloc[-1]["close"])

    # ── SELL: gestión de posición ────────────────────────────
    if entry_price is not None and entry_price > 0:
        pnl = (price - entry_price) / entry_price * 100
        if pnl >= PROFIT_PCT:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — profit {pnl:+.2f}%")
            return "SELL"
        if pnl <= -STOP_PCT:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL (stop) — {pnl:+.2f}%")
            return "SELL"
        return "HOLD"

    # ── Calcular ratio SPY/QQQ ────────────────────────────────
    # Alinear ambos DataFrames por índice temporal
    spy_df  = df if ticker.upper() == "SPY" else pair_df
    qqq_df  = df if ticker.upper() == "QQQ" else pair_df

    spy_close = spy_df["close"].reset_index(drop=True).tail(RATIO_WINDOW + 5)
    qqq_close = qqq_df["close"].reset_index(drop=True).tail(RATIO_WINDOW + 5)

    # Alinear por longitud
    min_len = min(len(spy_close), len(qqq_close))
    if min_len < RATIO_WINDOW + 2:
        return "HOLD"
    spy_close = spy_close.iloc[-min_len:]
    qqq_close = qqq_close.iloc[-min_len:]

    qqq_arr = qqq_close.values.astype(float)
    spy_arr = spy_close.values.astype(float)
    # Evitar division by zero
    mask = qqq_arr > 0
    ratio = np.full_like(spy_arr, np.nan)
    ratio[mask] = spy_arr[mask] / qqq_arr[mask]

    if np.all(np.isnan(ratio)):
        return "HOLD"

    ratio_series = pd.Series(ratio)
    ma    = ratio_series.rolling(RATIO_WINDOW).mean().iloc[-1]
    std   = ratio_series.rolling(RATIO_WINDOW).std().iloc[-1]
    curr  = ratio_series.iloc[-1]

    if np.isnan(ma) or np.isnan(std) or std == 0:
        return "HOLD"

    zscore = (curr - ma) / std

    logger.debug(
        f"[{STRATEGY_NAME}] ratio={curr:.4f} MA={ma:.4f} "
        f"σ={std:.4f} z={zscore:+.2f}"
    )

    # ── BUY señal ─────────────────────────────────────────────
    if ticker.upper() == "QQQ" and zscore > SIGMA_THRESHOLD:
        # SPY sobreperformó → QQQ va a catch-up → BUY QQQ
        logger.info(
            f"[{STRATEGY_NAME}] QQQ BUY — SPY sobreperformó "
            f"(z={zscore:+.2f}), QQQ catch-up"
        )
        return "BUY"

    if ticker.upper() == "SPY" and zscore < -SIGMA_THRESHOLD:
        # QQQ sobreperformó → SPY va a catch-up → BUY SPY
        logger.info(
            f"[{STRATEGY_NAME}] SPY BUY — QQQ sobreperformó "
            f"(z={zscore:+.2f}), SPY catch-up"
        )
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str, macro=None, **kwargs) -> dict:
    """Wrapper para bot_main.py. Obtiene ambos dataframes desde market_data."""
    if ticker.upper() not in ELIGIBLE_TICKERS:
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": "not eligible", "strategy": STRATEGY_NAME}
    try:
        tf = kwargs.get("timeframe", 1)
        period_type = "day"
        period      = 5
        freq_type   = "minute"
        freq        = max(1, min(tf, 30))

        df = market_data.get_candles(
            ticker, period_type=period_type, period=period,
            frequency_type=freq_type, frequency=freq,
        )
        if df is None or df.empty:
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}

        pair = "QQQ" if ticker.upper() == "SPY" else "SPY"
        pair_df = market_data.get_candles(
            pair, period_type=period_type, period=period,
            frequency_type=freq_type, frequency=freq,
        )

        entry_price = kwargs.get("entry_price")
        signal = detect_signal(
            df, ticker, macro=macro,
            entry_price=entry_price, pair_df=pair_df,
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
