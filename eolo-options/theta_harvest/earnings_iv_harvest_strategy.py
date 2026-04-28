# ============================================================
#  EOLO v2 — Estrategia: Earnings IV Harvest
#
#  Ref: nueva estrategia v2 (2026-04-27)
#
#  Lógica:
#    Antes de un earnings announcement el mercado compra
#    opciones agresivamente, inflando la IV implícita.
#    Si el IV Rank (percentil histórico) está > 70, la prima
#    está "cara" y conviene VENDERLA (iron condor o crédito spread).
#
#    Señal de apertura (OPEN_SPREAD):
#      1. Hay earnings en 2–5 días calendario
#      2. IV Rank (IV actual vs rango 52 semanas) > IV_RANK_MIN (70)
#      3. VIX < PANIC_THRESHOLD (40) → no operar en pánico
#      4. No hay evento macro mayor hoy (macro_news_today)
#
#    Señal de cierre post-earnings (CLOSE_POST_EARNINGS):
#      - El earnings ya ocurrió (días_para_earnings == 0 o negativo)
#      - La IV colapsó (IV actual < IV_COLLAPSE_THRESHOLD)
#
#    Output: EarningsIVSignal — se usa en eolo_v2_main.py
#
#    Fuente earnings calendar: yfinance (Ticker.calendar)
#    La IV rank se calcula internamente con histórico 52 semanas.
#
#  Universo: SPY, QQQ, AAPL, MSFT, NVDA, TSLA + cualquier ticker
#            que el orchestrator tenga en su lista
#  Requiere: yfinance, opciones chain con IV, datos diarios subyacente
# ============================================================
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    _HAS_YFINANCE = False
    logger.warning("[EARNINGS_IV] yfinance no instalado — earnings calendar deshabilitado")

# ── Config ────────────────────────────────────────────────
IV_RANK_MIN        = float(os.environ.get("EIH_IV_RANK_MIN",    "70.0"))   # percentil mínimo
PANIC_THRESHOLD    = float(os.environ.get("EIH_PANIC_VIX",      "40.0"))
MIN_DAYS_TO_EARN   = int(os.environ.get("EIH_MIN_DAYS",         "2"))      # mínimo días antes
MAX_DAYS_TO_EARN   = int(os.environ.get("EIH_MAX_DAYS",         "5"))      # máximo días antes
IV_COLLAPSE_THRESH = float(os.environ.get("EIH_IV_COLLAPSE",    "0.5"))    # IV cae a <50% del IV al abrir
IV_RANK_HV_WINDOW  = int(os.environ.get("EIH_HV_WINDOW",        "252"))    # días histórico para IV rank


@dataclass
class EarningsIVSignal:
    ticker:           str
    signal:           str          # "OPEN_SPREAD" | "CLOSE_POST_EARNINGS" | "HOLD"
    iv_rank:          float = 0.0  # percentil 0-100
    iv_current:       float = 0.0  # IV actual (implícita ATM promedio)
    days_to_earnings: Optional[int] = None
    earnings_date:    Optional[date] = None
    confidence:       float = 0.0
    reason:           str = ""


# ── Helpers ────────────────────────────────────────────────

def _compute_iv_rank(iv_history: pd.Series, iv_now: float) -> float:
    """
    IV Rank = percentil de iv_now dentro del rango histórico.
    IV Rank = (iv_now - iv_min) / (iv_max - iv_min) * 100
    """
    if len(iv_history) < 10:
        return 50.0
    arr = iv_history.dropna().values.astype(float)
    iv_min, iv_max = arr.min(), arr.max()
    if iv_max == iv_min:
        return 50.0
    rank = (iv_now - iv_min) / (iv_max - iv_min) * 100.0
    return float(np.clip(rank, 0.0, 100.0))


def _days_to_earnings(ticker: str, today: date) -> tuple[Optional[int], Optional[date]]:
    """
    Usa yfinance para obtener la próxima fecha de earnings.
    Retorna (días_hasta_earnings, fecha_earnings) o (None, None) si no disponible.
    """
    if not _HAS_YFINANCE:
        return None, None
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or cal.empty:
            return None, None

        # yfinance calendar tiene columna 'Earnings Date' como datetime o date
        if "Earnings Date" in cal.columns:
            earn_dates = pd.to_datetime(cal["Earnings Date"], errors="coerce").dropna()
        elif hasattr(cal, "index") and "Earnings Date" in cal.index:
            earn_dates = pd.to_datetime([cal.loc["Earnings Date"]], errors="coerce").dropna()
        else:
            return None, None

        if earn_dates.empty:
            return None, None

        # Filtrar solo fechas futuras
        future = [d.date() for d in earn_dates if d.date() >= today]
        if not future:
            return None, None

        next_earn = min(future)
        days = (next_earn - today).days
        return days, next_earn

    except Exception as e:
        logger.debug(f"[EARNINGS_IV] yfinance calendar error {ticker}: {e}")
        return None, None


def _estimate_iv_from_hv(daily_closes: pd.Series) -> float:
    """
    Estimación de IV cuando no hay opciones chain: usar HV reciente * 1.3 (IV premium tipico).
    Expresado en % anualizado.
    """
    if len(daily_closes) < 22:
        return 25.0  # fallback
    closes = daily_closes.tail(22).values.astype(float)
    closes = closes[closes > 0]
    if len(closes) < 5:
        return 25.0
    log_ret = np.diff(np.log(closes))
    hv_22 = float(np.std(log_ret, ddof=1)) * np.sqrt(252) * 100
    return round(hv_22 * 1.3, 2)   # IV premium típico ~30% sobre HV


# ── Scanner principal ─────────────────────────────────────

def scan_earnings_iv(
    ticker: str,
    daily_df: pd.DataFrame,        # OHLCV diario del subyacente (≥252 días ideal)
    vix_current: float,
    iv_current: float = 0.0,       # IV actual del ticker (ATM promedio). 0 = estimar con HV
    iv_history: Optional[pd.Series] = None,   # Serie histórica IV 52sem para IV rank
    macro_news_today: bool = False,
    today: Optional[date] = None,
) -> EarningsIVSignal:
    """
    Evalúa si hay oportunidad de Earnings IV Harvest para `ticker`.

    Parámetros:
        ticker          : ticker del subyacente
        daily_df        : DataFrame con columna 'close' y datos diarios
        vix_current     : VIX actual
        iv_current      : IV implícita ATM actual (0 = estimar desde HV)
        iv_history      : Serie histórica de IV para calcular IV rank
                          Si None, usa HV histórico como proxy
        macro_news_today: bloquea entradas en día de macro mayor
        today           : fecha hoy (None = date.today())
    """
    _today = today or date.today()

    if daily_df is None or daily_df.empty:
        return EarningsIVSignal(ticker=ticker, signal="HOLD", reason="no daily data")

    # ── Estimar IV si no se provee ────────────────────────
    if iv_current <= 0.0:
        iv_current = _estimate_iv_from_hv(daily_df["close"])

    # ── IV Rank ───────────────────────────────────────────
    if iv_history is not None and len(iv_history) >= 10:
        iv_rank = _compute_iv_rank(iv_history, iv_current)
    else:
        # Proxy: usar HV rolling 252d como histórico de IV
        closes = daily_df["close"].tail(IV_RANK_HV_WINDOW)
        hv_series = []
        if len(closes) >= 30:
            for i in range(22, len(closes)):
                window = closes.iloc[i-22:i].values.astype(float)
                window = window[window > 0]
                if len(window) > 2:
                    lr = np.diff(np.log(window))
                    hv_series.append(float(np.std(lr, ddof=1)) * np.sqrt(252) * 100 * 1.3)
        iv_rank = _compute_iv_rank(pd.Series(hv_series), iv_current) if hv_series else 50.0

    logger.debug(
        f"[EARNINGS_IV] {ticker} IV={iv_current:.1f} IV_rank={iv_rank:.0f} "
        f"VIX={vix_current:.1f}"
    )

    # ── Obtener earnings date ─────────────────────────────
    days_to_earn, earn_date = _days_to_earnings(ticker, _today)

    # ── Señal de cierre post-earnings ────────────────────
    if days_to_earn is not None and days_to_earn <= 0:
        # El earnings ya pasó → cerrar si IV colapsó
        if iv_rank < IV_COLLAPSE_THRESH * 100:
            return EarningsIVSignal(
                ticker=ticker,
                signal="CLOSE_POST_EARNINGS",
                iv_rank=iv_rank,
                iv_current=iv_current,
                days_to_earnings=days_to_earn,
                earnings_date=earn_date,
                reason=f"post-earnings IV collapse (rank={iv_rank:.0f})",
            )
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            days_to_earnings=days_to_earn,
            reason="post-earnings, IV aún elevada",
        )

    # ── Gating ────────────────────────────────────────────
    if macro_news_today:
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            reason="macro_news_day",
        )
    if vix_current > PANIC_THRESHOLD:
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            reason=f"VIX={vix_current:.1f} > PANIC={PANIC_THRESHOLD}",
        )

    # ── Señal de apertura ─────────────────────────────────
    if days_to_earn is None:
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            reason="earnings date not available (yfinance)",
        )

    if not (MIN_DAYS_TO_EARN <= days_to_earn <= MAX_DAYS_TO_EARN):
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            days_to_earnings=days_to_earn,
            earnings_date=earn_date,
            reason=f"earnings en {days_to_earn}d (ventana: {MIN_DAYS_TO_EARN}-{MAX_DAYS_TO_EARN}d)",
        )

    if iv_rank < IV_RANK_MIN:
        return EarningsIVSignal(
            ticker=ticker, signal="HOLD",
            iv_rank=iv_rank, iv_current=iv_current,
            days_to_earnings=days_to_earn,
            earnings_date=earn_date,
            reason=f"IV rank={iv_rank:.0f} < min={IV_RANK_MIN}",
        )

    confidence = min(1.0, (iv_rank - IV_RANK_MIN) / 30.0 + 0.5)
    reason = (
        f"IV_rank={iv_rank:.0f}≥{IV_RANK_MIN} "
        f"earnings en {days_to_earn}d ({earn_date}) "
        f"IV={iv_current:.1f}%"
    )
    logger.info(
        f"[EARNINGS_IV] {ticker} OPEN_SPREAD — {reason} "
        f"confidence={confidence:.2f}"
    )
    return EarningsIVSignal(
        ticker=ticker,
        signal="OPEN_SPREAD",
        iv_rank=iv_rank,
        iv_current=iv_current,
        days_to_earnings=days_to_earn,
        earnings_date=earn_date,
        confidence=confidence,
        reason=reason,
    )
