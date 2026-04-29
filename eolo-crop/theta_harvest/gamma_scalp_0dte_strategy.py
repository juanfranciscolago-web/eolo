# ============================================================
#  EOLO v2 — Estrategia: 0DTE Gamma Scalp
#
#  Ref: nueva estrategia v2 (2026-04-27)
#
#  Lógica:
#    En el día de expiración (0DTE), las opciones near-the-money
#    tienen gamma extremadamente alto. Un movimiento pequeño del
#    subyacente produce grandes cambios de delta → los contratos
#    se mueven mucho más rápido que el subyacente.
#
#    Estrategia: identificar condiciones de alta gamma y tomar
#    una posición direccional en opciones 0DTE cuando hay
#    momentum técnico confirmado.
#
#    Condiciones de entrada (LONG CALL o LONG PUT):
#      1. Es viernes (SPY expiración semanal) O expiración mensual
#      2. VIX < GAMMA_MAX_VIX (sin usar en pánico extremo)
#      3. Señal técnica: RSI > 60 (momentum alcista) → CALL
#                        RSI < 40 (momentum bajista) → PUT
#      4. Precio cerca de strike redondeado ($1 o $5)
#      5. Horario óptimo: 10:00–13:00 ET (no al open ni al cierre)
#
#    Gestión de posición:
#      - Exit: +50% de ganancia sobre la prima ó -30% (stop)
#      - Cierre forzado: 14:00 ET (antes del colapso de theta)
#
#    Output: GammaScalpSignal — se usa como input al OptionsTrader
#
#  Universo: SPY (expiración más líquida en 0DTE)
#  Requiere: opciones chain, datos técnicos del subyacente
# ============================================================
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd
from loguru import logger

# ── Config ────────────────────────────────────────────────
GAMMA_MAX_VIX     = float(os.environ.get("GS_MAX_VIX",    "30.0"))
RSI_CALL_THRESHOLD = float(os.environ.get("GS_RSI_CALL",  "60.0"))
RSI_PUT_THRESHOLD  = float(os.environ.get("GS_RSI_PUT",   "40.0"))
RSI_PERIOD         = int(os.environ.get("GS_RSI_PERIOD",  "14"))
ENTRY_START_HOUR   = int(os.environ.get("GS_START_H",     "10"))
ENTRY_END_HOUR     = int(os.environ.get("GS_END_H",       "13"))
FORCE_CLOSE_HOUR   = int(os.environ.get("GS_CLOSE_H",     "14"))


@dataclass
class GammaScalpSignal:
    ticker:      str
    signal:      str          # "LONG_CALL" | "LONG_PUT" | "CLOSE" | "HOLD"
    direction:   str = "NONE" # "CALL" | "PUT"
    rsi:         float = 50.0
    spy_price:   float = 0.0
    target_strike: Optional[float] = None   # strike más cercano al ATM
    confidence:  float = 0.0
    reason:      str = ""


def _compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    """RSI estándar Wilder."""
    if len(closes) < period + 2:
        return 50.0
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _nearest_round_strike(price: float, width: float = 1.0) -> float:
    """Redondea el precio al strike más cercano (múltiplo de width)."""
    return round(price / width) * width


def _is_expiration_day(today: date) -> bool:
    """True si hoy es viernes (expiración semanal SPY)."""
    return today.weekday() == 4  # viernes


def scan_gamma_scalp(
    ticker: str,
    intraday_df: pd.DataFrame,   # OHLCV intraday 1-5m del subyacente
    vix_current: float,
    now_et: datetime,
    has_open_0dte: bool = False,  # True si ya hay una posición 0DTE abierta
) -> GammaScalpSignal:
    """
    Evalúa condiciones para un gamma scalp 0DTE en `ticker`.

    Parámetros:
        ticker        : subyacente ("SPY")
        intraday_df   : DataFrame con columna 'close' y 'datetime' (1m o 5m)
        vix_current   : nivel VIX actual
        now_et        : datetime con hora ET actual
        has_open_0dte : si ya hay una posición 0DTE abierta (evitar doble entrada)
    """
    today = now_et.date()

    # ── Condición: es día de expiración ──────────────────────
    if not _is_expiration_day(today):
        return GammaScalpSignal(
            ticker=ticker, signal="HOLD",
            reason="not expiration day",
        )

    # ── Condición: horario de entrada ─────────────────────────
    hour = now_et.hour
    if hour < ENTRY_START_HOUR or hour >= ENTRY_END_HOUR:
        return GammaScalpSignal(
            ticker=ticker, signal="HOLD",
            reason=f"outside entry window ({ENTRY_START_HOUR}:00-{ENTRY_END_HOUR}:00 ET)",
        )

    # ── Condición: VIX ────────────────────────────────────────
    if vix_current > GAMMA_MAX_VIX:
        return GammaScalpSignal(
            ticker=ticker, signal="HOLD",
            reason=f"VIX={vix_current:.1f} > {GAMMA_MAX_VIX}",
        )

    if has_open_0dte:
        return GammaScalpSignal(
            ticker=ticker, signal="HOLD",
            reason="ya hay posición 0DTE abierta",
        )

    if intraday_df is None or len(intraday_df) < RSI_PERIOD + 5:
        return GammaScalpSignal(
            ticker=ticker, signal="HOLD",
            reason="insufficient intraday data",
        )

    # ── RSI para dirección ────────────────────────────────────
    rsi = _compute_rsi(intraday_df["close"], period=RSI_PERIOD)
    price = float(intraday_df["close"].iloc[-1])
    atm_strike = _nearest_round_strike(price, width=1.0)

    logger.debug(f"[0DTE_GAMMA] {ticker} RSI={rsi:.1f} price={price:.2f} ATM={atm_strike:.0f}")

    if rsi > RSI_CALL_THRESHOLD:
        logger.info(
            f"[0DTE_GAMMA] {ticker} LONG_CALL — RSI={rsi:.1f} > {RSI_CALL_THRESHOLD}, "
            f"ATM=${atm_strike:.0f}, VIX={vix_current:.1f}"
        )
        return GammaScalpSignal(
            ticker=ticker,
            signal="LONG_CALL",
            direction="CALL",
            rsi=rsi,
            spy_price=price,
            target_strike=atm_strike,
            confidence=min(1.0, (rsi - RSI_CALL_THRESHOLD) / 40.0 + 0.4),
            reason=f"RSI={rsi:.1f}>{RSI_CALL_THRESHOLD} VIX={vix_current:.1f}",
        )

    if rsi < RSI_PUT_THRESHOLD:
        logger.info(
            f"[0DTE_GAMMA] {ticker} LONG_PUT — RSI={rsi:.1f} < {RSI_PUT_THRESHOLD}, "
            f"ATM=${atm_strike:.0f}, VIX={vix_current:.1f}"
        )
        return GammaScalpSignal(
            ticker=ticker,
            signal="LONG_PUT",
            direction="PUT",
            rsi=rsi,
            spy_price=price,
            target_strike=atm_strike,
            confidence=min(1.0, (RSI_PUT_THRESHOLD - rsi) / 40.0 + 0.4),
            reason=f"RSI={rsi:.1f}<{RSI_PUT_THRESHOLD} VIX={vix_current:.1f}",
        )

    return GammaScalpSignal(
        ticker=ticker, signal="HOLD",
        rsi=rsi, spy_price=price,
        reason=f"RSI={rsi:.1f} no extremo",
    )


def should_force_close(now_et: datetime) -> bool:
    """True si es hora de cerrar forzosamente todas las posiciones 0DTE."""
    return now_et.hour >= FORCE_CLOSE_HOUR and now_et.weekday() == 4
