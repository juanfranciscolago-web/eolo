# ============================================================
#  EOLO — Estrategia: VIX Spike Fade
#
#  Ref: trading_strategies_v2.md — nueva estrategia (2026-04-27)
#
#  Lógica:
#    Cuando el VIX sube bruscamente intraday (>VIX_SPIKE_PCT%
#    desde el mínimo de la sesión), el mercado tiende a
#    sobrereaccionar y rebotar. La estrategia compra en el pico
#    de pánico con la expectativa de una reversión.
#
#    BUY : VIX subió > VIX_SPIKE_PCT% desde el mínimo de sesión
#          Y VIX nivel absoluto < VIX_MAX_LEVEL (no compramos
#          en crisis sistémica, solo en spikes transitorios)
#          Y precio del subyacente está cerca del mínimo de sesión
#            (confirmamos que el spike en VIX coincide con una caída)
#
#    SELL: señal de salida cuando VIX cae > VIX_FADE_PCT%
#          desde el pico registrado (normalización del spike)
#          O cuando la vela cierra >EXIT_PROFIT_PCT% sobre entry.
#
#    Filtro estructural:
#      - NOT term_structure_inverted (si VIX > VIX3M = estrés real,
#        no fadear)
#
#  Universo : SPY, QQQ, TQQQ
#  Requiere  : MacroFeeds (macro.series("VIX")) — sin macro → HOLD
#  Categoría : vix_regime / mean_reversion
# ============================================================
import os

import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME       = "VIX_SPIKE_FADE"
ELIGIBLE_TICKERS    = {"SPY", "QQQ", "TQQQ"}

# % de spike en VIX intraday para señal BUY (desde mínimo de sesión)
VIX_SPIKE_PCT       = float(os.environ.get("VSF_SPIKE_PCT",    "5.0"))

# % de caída de VIX desde pico para señal SELL (normalización)
VIX_FADE_PCT        = float(os.environ.get("VSF_FADE_PCT",     "3.0"))

# Nivel absoluto de VIX máximo para activar la estrategia
# (>50 = crisis real, no reversión esperada a corto plazo)
VIX_MAX_LEVEL       = float(os.environ.get("VSF_VIX_MAX",     "50.0"))

# Cuántas muestras de VIX mirar para calcular mínimo/máximo de sesión
# (si macro polling = 1min → 390 muestras = 6.5h = sesión completa)
VIX_WINDOW_MIN      = int(os.environ.get("VSF_WINDOW_MIN",    "390"))

# Profit target como alternativa a esperar la caída de VIX
EXIT_PROFIT_PCT     = float(os.environ.get("VSF_EXIT_PCT",     "1.5"))

# % máximo de caída del subyacente que toleramos antes de entrar
# (si ya cayó mucho sin bounce, evitamos)
PRICE_DROP_MAX_PCT  = float(os.environ.get("VSF_PRICE_DROP",   "3.0"))


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
    if macro is None:
        return "HOLD"
    if len(df) < 20:
        return "HOLD"

    # ── Leer VIX intraday ────────────────────────────────────
    try:
        vix_series = macro.series("VIX", minutes=VIX_WINDOW_MIN)
        if vix_series is None or len(vix_series) < 5:
            return "HOLD"
        vix_arr    = [float(v) for v in vix_series if v is not None and float(v) > 0]
        if not vix_arr:
            return "HOLD"
    except Exception as e:
        logger.debug(f"[{STRATEGY_NAME}] VIX read error: {e}")
        return "HOLD"

    vix_current = vix_arr[-1]
    vix_min     = min(vix_arr)
    vix_max     = max(vix_arr)

    # ── Filtro nivel absoluto ─────────────────────────────────
    if vix_current > VIX_MAX_LEVEL:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — VIX={vix_current:.1f} > {VIX_MAX_LEVEL} (crisis)"
        )
        return "HOLD"

    # ── Filtro estructural: no fadear estrés real ─────────────
    try:
        if macro.term_structure_inverted():
            logger.debug(f"[{STRATEGY_NAME}] {ticker} skip — term structure inverted")
            return "HOLD"
    except Exception:
        pass

    last        = df.iloc[-1]
    price       = float(last["close"])

    # ── SELL: fadeo confirmado ────────────────────────────────
    if entry_price is not None and entry_price > 0:
        pnl_pct = (price - entry_price) / entry_price * 100

        # Salida por profit target
        if pnl_pct >= EXIT_PROFIT_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — profit {pnl_pct:+.2f}% ≥ {EXIT_PROFIT_PCT}%"
            )
            return "SELL"

        # Salida por normalización de VIX
        vix_drop_pct = (vix_max - vix_current) / vix_max * 100
        if vix_drop_pct >= VIX_FADE_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — VIX normalized "
                f"({vix_current:.1f} ← {vix_max:.1f}, drop={vix_drop_pct:.1f}%)"
            )
            return "SELL"

    # ── BUY: spike de VIX detectado ──────────────────────────
    if vix_min > 0:
        vix_spike_pct = (vix_current - vix_min) / vix_min * 100
    else:
        vix_spike_pct = 0.0

    if vix_spike_pct < VIX_SPIKE_PCT:
        return "HOLD"

    # Confirmar que el precio del subyacente también bajó
    # (no queremos comprar cuando el VIX sube por otros motivos)
    high_session = float(df["high"].max())
    if high_session > 0:
        price_drop_pct = (high_session - price) / high_session * 100
        if price_drop_pct > PRICE_DROP_MAX_PCT:
            logger.debug(
                f"[{STRATEGY_NAME}] {ticker} skip BUY — price already dropped "
                f"{price_drop_pct:.1f}% > {PRICE_DROP_MAX_PCT}% (no chase)"
            )
            return "HOLD"
        if price_drop_pct < 0.2:
            # Precio no bajó significativamente — spike de VIX no correlaciona
            logger.debug(
                f"[{STRATEGY_NAME}] {ticker} skip BUY — price barely moved, VIX spike unrelated"
            )
            return "HOLD"

    logger.info(
        f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.2f} — VIX spike "
        f"{vix_spike_pct:.1f}% (min={vix_min:.1f} → cur={vix_current:.1f})"
    )
    return "BUY"


def analyze(market_data, ticker: str, macro=None, **kwargs) -> dict:
    """Wrapper para compatibilidad con bot_main.py run_cycle."""
    try:
        tf = kwargs.get("timeframe", 1)
        df = market_data.get_candles(
            ticker,
            period_type="day",
            period=1,
            frequency_type="minute",
            frequency=min(tf, 30),
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
