# ============================================================
#  EOLO — Estrategia: Overnight Drift
#
#  Ref: trading_strategies_v2.md — nueva estrategia (2026-04-27)
#
#  Lógica:
#    El S&P y el QQQ tienen un drift sistemático overnight:
#    históricamente ~70-75% de los días ganan de close a open.
#    La estrategia entra cerca del cierre del mercado y cierra
#    en la primera vela de la siguiente sesión.
#
#    BUY:  vela entre 15:40 y 15:55 ET (L-V) si no hay posición.
#    SELL: vela entre 09:30 y 09:45 ET (L-V) si hay posición abierta.
#    HOLD: cualquier otra situación.
#
#    Filtros:
#      - Solo L-V (no operar viernes → lunes overnight por riesgo
#        de gap de fin de semana).
#      - No abrir en viernes (weekend risk). Override con env var.
#      - Si VIX > VIX_AVOID_THRESHOLD no abrir posición nueva
#        (mercados en stress extremo, riesgo de gap overnight alto).
#
#  Universo : SPY, QQQ
#  Timeframe: 1m (la señal se activa por timestamp, no por técnicos)
#  Categoría: overnight / carry
# ============================================================
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import pytz
from loguru import logger

STRATEGY_NAME      = "OVERNIGHT_DRIFT"
ELIGIBLE_TICKERS   = {"SPY", "QQQ"}

EASTERN            = pytz.timezone("America/New_York")

# Ventana de ENTRADA: 15:40–15:55 ET
ENTRY_START_H, ENTRY_START_M = 15, 40
ENTRY_END_H,   ENTRY_END_M   = 15, 55

# Ventana de SALIDA: 09:30–09:45 ET
EXIT_START_H, EXIT_START_M   = 9, 30
EXIT_END_H,   EXIT_END_M     = 9, 45

# Si VIX > este umbral, no abrimos posición nueva (stress alto)
VIX_AVOID_THRESHOLD = float(os.environ.get("OVERNIGHT_VIX_AVOID", "30.0"))

# Skip viernes → riesgo de gap de fin de semana (0=lun … 4=vie)
SKIP_FRIDAY_ENTRY   = os.environ.get("OVERNIGHT_SKIP_FRI", "1") in ("1", "true", "yes")


def _candle_time_et(df: pd.DataFrame) -> Optional[datetime]:
    """Retorna el timestamp ET de la última vela, o None si no disponible."""
    try:
        ts = df.iloc[-1]["datetime"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts, utc=True)
        elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        elif hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts = ts.astimezone(pytz.utc)
        return ts.astimezone(EASTERN).replace(tzinfo=None)
    except Exception as e:
        logger.debug(f"[{STRATEGY_NAME}] candle_time_et error: {e}")
        return None


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
    if len(df) < 2:
        return "HOLD"

    t = _candle_time_et(df)
    if t is None:
        return "HOLD"

    weekday = t.weekday()   # 0=lun, 4=vie, 5=sab, 6=dom
    h, m = t.hour, t.minute

    # ── Verificar VIX si macro disponible ────────────────────
    vix_ok = True
    if macro is not None:
        try:
            vix_val = macro.latest("VIX")
            if vix_val and float(vix_val) > VIX_AVOID_THRESHOLD:
                logger.debug(
                    f"[{STRATEGY_NAME}] {ticker} skip — VIX={vix_val:.1f} > {VIX_AVOID_THRESHOLD}"
                )
                vix_ok = False
        except Exception:
            pass

    # ── Ventana de ENTRADA ────────────────────────────────────
    entry_window = (
        (h == ENTRY_START_H and m >= ENTRY_START_M)
        or (h == ENTRY_END_H and m <= ENTRY_END_M)
        or (ENTRY_START_H < h < ENTRY_END_H)
    )
    if h == ENTRY_START_H and h == ENTRY_END_H:
        entry_window = ENTRY_START_M <= m <= ENTRY_END_M

    if entry_window:
        # No abrir en viernes (gap de fin de semana)
        if SKIP_FRIDAY_ENTRY and weekday == 4:
            logger.debug(f"[{STRATEGY_NAME}] {ticker} skip — viernes, no abrimos overnight")
            return "HOLD"
        if not vix_ok:
            return "HOLD"
        # No operar sábado/domingo (no debería llegar, pero por si acaso)
        if weekday >= 5:
            return "HOLD"
        price = float(df.iloc[-1]["close"])
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.2f} "
            f"(entry window 15:40-15:55 ET, weekday={weekday})"
        )
        return "BUY"

    # ── Ventana de SALIDA ─────────────────────────────────────
    exit_window = (
        (h == EXIT_START_H and m >= EXIT_START_M)
        or (h == EXIT_END_H and m <= EXIT_END_M)
        or (EXIT_START_H < h < EXIT_END_H)
    )
    if h == EXIT_START_H and h == EXIT_END_H:
        exit_window = EXIT_START_M <= m <= EXIT_END_M

    if exit_window:
        if entry_price is not None:
            # Solo cerrar si hay posición (entry_price indica que estamos long)
            price = float(df.iloc[-1]["close"])
            pnl = (price - entry_price) / entry_price * 100
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL @ {price:.2f} "
                f"(exit window 09:30-09:45 ET, P&L={pnl:+.2f}%)"
            )
            return "SELL"

    return "HOLD"


def analyze(market_data, ticker: str, macro=None, **kwargs) -> dict:
    """Wrapper para compatibilidad con bot_main.py run_cycle."""
    try:
        tf = kwargs.get("timeframe", 1)
        df = market_data.get_candles(
            ticker,
            period_type="day",
            period=2,
            frequency_type="minute",
            frequency=tf,
        )
        if df is None or df.empty:
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}

        entry_price = kwargs.get("entry_price")
        signal = detect_signal(df, ticker, macro=macro, entry_price=entry_price)
        price  = float(df.iloc[-1]["close"]) if not df.empty else 0
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
