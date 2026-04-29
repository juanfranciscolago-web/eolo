# ============================================================
#  EOLO v2 — Estrategia: Put Skew Normalization
#
#  Ref: nueva estrategia v2 (2026-04-27)
#
#  Lógica:
#    El "put skew" mide cuánto más cara está la IV de los puts
#    OTM respecto a los calls OTM (o al ATM). Cuando el skew
#    está en percentil histórico > 90, el mercado tiene miedo
#    extremo y paga demasiado por protección → revertir.
#
#    El skew proxy que usamos:
#      skew = IV_put_25d - IV_call_25d   (25-delta strangle spread)
#      Si no hay opciones chain: skew proxy = VIX_term_slope o HV-implied spread
#
#    Señal FADE_SKEW (vender put spread / recibir prima):
#      1. skew_percentile > SKEW_PERCENTILE_MIN (90)
#      2. VIX < PANIC_THRESHOLD (40) → no operar en pánico extremo
#      3. Mercado no en tendencia bajista fuerte (close < SMA50 con fuerza)
#      4. No hay macro news hoy
#
#    Señal CLOSE_SKEW:
#      skew_percentile < SKEW_CLOSE_PCT (50) → skew normalizó
#
#    Output: PutSkewSignal — se usa en eolo_v2_main.py
#
#  Nota de implementación:
#    Si no hay opciones chain con IV por strike, se usa como proxy
#    el ratio VIX/VXN o la diferencia VIX-HV_5d como medida de skew.
#    Esto es una aproximación conservadora pero funcional.
#
#  Universo: SPY, QQQ, IWM
#  Requiere: opciones chain o VIX/VXN, datos diarios del subyacente
# ============================================================
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ── Config ────────────────────────────────────────────────
SKEW_PERCENTILE_MIN  = float(os.environ.get("PSN_PCT_MIN",    "90.0"))   # percentil para abrir
SKEW_CLOSE_PCT       = float(os.environ.get("PSN_PCT_CLOSE",  "50.0"))   # percentil para cerrar
PANIC_THRESHOLD      = float(os.environ.get("PSN_PANIC_VIX",  "40.0"))
SKEW_WINDOW_DAYS     = int(os.environ.get("PSN_WINDOW",        "252"))   # días histórico para percentil
SMA50_WINDOW         = int(os.environ.get("PSN_SMA50",         "50"))    # ventana SMA tendencia
TREND_FILTER_PCT     = float(os.environ.get("PSN_TREND_FILTER", "3.0"))  # % bajo SMA50 para bloquear


@dataclass
class PutSkewSignal:
    ticker:           str
    signal:           str           # "FADE_SKEW" | "CLOSE_SKEW" | "HOLD"
    skew_value:       float = 0.0   # valor actual del skew (pts IV o proxy)
    skew_percentile:  float = 50.0  # percentil 0-100
    vix:              float = 0.0
    confidence:       float = 0.0
    reason:           str = ""


# ── Helpers ────────────────────────────────────────────────

def _compute_skew_percentile(skew_history: pd.Series, skew_now: float) -> float:
    """Percentil de skew_now dentro del histórico (0-100)."""
    arr = skew_history.dropna().values.astype(float)
    if len(arr) < 10:
        return 50.0
    pct = float(np.mean(arr <= skew_now)) * 100.0
    return round(pct, 1)


def _compute_skew_proxy(daily_df: pd.DataFrame, vix_current: float) -> tuple[float, pd.Series]:
    """
    Proxy de skew cuando no hay opciones chain con IV por strike.

    Método: usa la diferencia entre la vol realizada de 5 días (vols cortas
    capturan miedo reciente) vs HV de 22 días. Cuanto mayor la diferencia,
    mayor el "fear premium" que refleja el skew implícito.

    Retorna (skew_actual, skew_history_series).
    La skew_history se estima rolling.
    """
    closes = daily_df["close"].dropna().values.astype(float)
    if len(closes) < SKEW_WINDOW_DAYS // 4:
        return 0.0, pd.Series(dtype=float)

    def _hv(arr, n):
        if len(arr) < n + 1:
            return None
        lr = np.diff(np.log(arr[-n-1:]))
        return float(np.std(lr, ddof=1)) * np.sqrt(252) * 100

    # Rolling HV5 vs HV22 diferencia como proxy de short-term fear
    skew_hist = []
    min_idx = 25  # necesitamos al menos HV22 + buffer
    for i in range(min_idx, len(closes)):
        hv5  = _hv(closes[max(0,i-6):i+1], 5)
        hv22 = _hv(closes[max(0,i-23):i+1], 22)
        if hv5 is not None and hv22 is not None and hv22 > 0:
            skew_hist.append(hv5 - hv22)
        else:
            skew_hist.append(np.nan)

    # Añadir componente VIX como amplificador del skew (VIX - HV22 del subyacente)
    hv22_current = _hv(closes, 22)
    if hv22_current is not None and hv22_current > 0:
        skew_current = (vix_current - hv22_current)
    else:
        skew_current = skew_hist[-1] if skew_hist and not np.isnan(skew_hist[-1]) else 0.0

    return skew_current, pd.Series(skew_hist).dropna()


def _trend_too_bearish(daily_df: pd.DataFrame) -> bool:
    """
    True si el precio está más de TREND_FILTER_PCT% por debajo de su SMA50.
    En ese caso, el skew elevado puede ser "legítimo" — no lo fades.
    """
    closes = daily_df["close"].dropna()
    if len(closes) < SMA50_WINDOW:
        return False
    price = float(closes.iloc[-1])
    sma50 = float(closes.tail(SMA50_WINDOW).mean())
    if sma50 == 0:
        return False
    pct_below = (sma50 - price) / sma50 * 100
    return pct_below > TREND_FILTER_PCT


# ── Scanner principal ─────────────────────────────────────

def scan_put_skew(
    ticker: str,
    daily_df: pd.DataFrame,          # OHLCV diario del subyacente (≥252 días ideal)
    vix_current: float,
    skew_current: float = 0.0,       # IV_put_25d - IV_call_25d en pts. 0 = usar proxy
    skew_history: Optional[pd.Series] = None,   # historial de skew para percentil
    macro_news_today: bool = False,
    has_open_position: bool = False,
) -> PutSkewSignal:
    """
    Evalúa si el put skew está en niveles extremos y hay oportunidad de fading.

    Parámetros:
        ticker            : ticker del subyacente
        daily_df          : DataFrame con columna 'close' (datos diarios)
        vix_current       : VIX actual
        skew_current      : IV spread put-call explícito (0 = calcular proxy)
        skew_history      : serie histórica de skew para el percentil
        macro_news_today  : bloquea entradas en día macro
        has_open_position : True si ya hay una posición de skew abierta
    """
    if daily_df is None or daily_df.empty:
        return PutSkewSignal(ticker=ticker, signal="HOLD", reason="no daily data", vix=vix_current)

    # ── Calcular skew y percentil ─────────────────────────
    if skew_current == 0.0 or skew_history is None:
        skew_current, skew_history_computed = _compute_skew_proxy(daily_df, vix_current)
        _skew_hist = skew_history if skew_history is not None else skew_history_computed
    else:
        _skew_hist = skew_history

    skew_pct = _compute_skew_percentile(_skew_hist, skew_current)

    logger.debug(
        f"[PUT_SKEW] {ticker} skew={skew_current:+.2f} pct={skew_pct:.0f} "
        f"VIX={vix_current:.1f}"
    )

    # ── Señal de cierre ───────────────────────────────────
    if has_open_position and skew_pct < SKEW_CLOSE_PCT:
        reason = f"skew normalizado (pct={skew_pct:.0f} < {SKEW_CLOSE_PCT})"
        logger.info(f"[PUT_SKEW] {ticker} CLOSE_SKEW — {reason}")
        return PutSkewSignal(
            ticker=ticker, signal="CLOSE_SKEW",
            skew_value=skew_current, skew_percentile=skew_pct,
            vix=vix_current, reason=reason,
        )

    # ── Gating ────────────────────────────────────────────
    if macro_news_today:
        return PutSkewSignal(
            ticker=ticker, signal="HOLD",
            skew_value=skew_current, skew_percentile=skew_pct,
            vix=vix_current, reason="macro_news_day",
        )
    if vix_current > PANIC_THRESHOLD:
        return PutSkewSignal(
            ticker=ticker, signal="HOLD",
            skew_value=skew_current, skew_percentile=skew_pct,
            vix=vix_current,
            reason=f"VIX={vix_current:.1f} > PANIC={PANIC_THRESHOLD}",
        )
    if _trend_too_bearish(daily_df):
        return PutSkewSignal(
            ticker=ticker, signal="HOLD",
            skew_value=skew_current, skew_percentile=skew_pct,
            vix=vix_current,
            reason=f"tendencia bajista fuerte (precio>{TREND_FILTER_PCT}% bajo SMA50)",
        )

    # ── Señal de apertura ─────────────────────────────────
    if skew_pct < SKEW_PERCENTILE_MIN:
        return PutSkewSignal(
            ticker=ticker, signal="HOLD",
            skew_value=skew_current, skew_percentile=skew_pct,
            vix=vix_current,
            reason=f"skew_pct={skew_pct:.0f} < {SKEW_PERCENTILE_MIN} (no extremo)",
        )

    confidence = min(1.0, (skew_pct - SKEW_PERCENTILE_MIN) / 10.0 + 0.5)
    reason = (
        f"skew_pct={skew_pct:.0f}≥{SKEW_PERCENTILE_MIN} "
        f"skew={skew_current:+.2f} VIX={vix_current:.1f}"
    )
    logger.info(
        f"[PUT_SKEW] {ticker} FADE_SKEW — {reason} "
        f"confidence={confidence:.2f}"
    )
    return PutSkewSignal(
        ticker=ticker,
        signal="FADE_SKEW",
        skew_value=skew_current,
        skew_percentile=skew_pct,
        vix=vix_current,
        confidence=confidence,
        reason=reason,
    )
