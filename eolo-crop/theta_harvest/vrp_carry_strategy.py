# ============================================================
#  EOLO v2 — Estrategia: VRP Carry (Variance Risk Premium)
#
#  Ref: nueva estrategia v2 (2026-04-27)
#
#  Lógica:
#    El VRP (Variance Risk Premium) es la diferencia entre la
#    volatilidad implícita (VIX) y la vol histórica realizada (HV).
#    Históricamente el VIX cotiza sobre la HV (~2-4 pts de media),
#    creando un "carry" al vender vol implícita.
#
#    BUY_SPREAD (señal de apertura de crédito):
#      VIX > HV_30d + VRP_THRESHOLD (ej. > 5 pts)
#      AND mercado no en panic (VIX < PANIC_THRESHOLD)
#      AND no en día de news macro
#    → abrir credit spread (poner/call) en la dirección de la tendencia
#
#    CLOSE_SPREAD:
#      VIX cae a HV_30d + 1pt (VRP normalizado)
#      OR profit target estándar del spread
#
#    Cálculo HV_30d:
#      Usando los últimos 30 días de retornos diarios del subyacente.
#      HV = std(log_returns) * sqrt(252) * 100 → % anualizado
#
#    Se integra en eolo_v2_main.py como señal adicional que puede
#    aumentar el conviction de abrir un tranche extra de theta harvest.
#
#  Universo: SPY, QQQ, IWM
#  Output  : VRPSignal (dataclass)
# ============================================================
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ── Umbrales configurables ────────────────────────────────
VRP_THRESHOLD   = float(os.environ.get("VRP_THRESH",  "5.0"))   # VIX - HV > N pts
PANIC_THRESHOLD = float(os.environ.get("VRP_PANIC",  "40.0"))   # VIX < 40 para operar
HV_WINDOW_DAYS  = int(os.environ.get("VRP_HV_DAYS",  "30"))     # días para HV
MIN_VIX         = float(os.environ.get("VRP_MIN_VIX", "14.0"))  # VIX mínimo para señal


@dataclass
class VRPSignal:
    ticker:      str
    signal:      str          # "OPEN_SPREAD" | "CLOSE_SPREAD" | "HOLD"
    vix:         float = 0.0
    hv_30d:      float = 0.0
    vrp:         float = 0.0  # VIX - HV
    direction:   str = "PUT"  # spread direction: "PUT" | "CALL" | "EITHER"
    confidence:  float = 0.0
    reason:      str = ""


def compute_hv(daily_closes: pd.Series, window: int = HV_WINDOW_DAYS) -> float:
    """
    Calcula la Historical Volatility anualizada (%) de los últimos `window` días.
    HV = std(log_returns, window) * sqrt(252) * 100
    """
    if len(daily_closes) < window + 2:
        return 0.0
    closes = daily_closes.tail(window + 1).values.astype(float)
    closes = closes[closes > 0]
    if len(closes) < 3:
        return 0.0
    log_ret = np.diff(np.log(closes))
    hv = float(np.std(log_ret, ddof=1)) * math.sqrt(252) * 100
    return round(hv, 2)


def scan_vrp_carry(
    ticker: str,
    daily_df: pd.DataFrame,     # OHLCV diario del subyacente (al menos HV_WINDOW_DAYS+5 filas)
    vix_current: float,
    spy_trend: str = "NEUTRAL", # "UP" | "DOWN" | "NEUTRAL" — dirección del mercado
    macro_news_today: bool = False,
) -> VRPSignal:
    """
    Evalúa si hay oportunidad de VRP carry para `ticker`.
    Retorna un VRPSignal con la señal y los parámetros relevantes.

    Parámetros:
        ticker         : ticker del subyacente
        daily_df       : DataFrame con al menos 35 días de datos diarios (columna 'close')
        vix_current    : VIX nivel actual
        spy_trend      : tendencia SPY para dirección del spread
        macro_news_today: si True, no abrir nuevas posiciones
    """
    if daily_df is None or daily_df.empty:
        return VRPSignal(ticker=ticker, signal="HOLD", reason="no daily data")

    hv = compute_hv(daily_df["close"], window=HV_WINDOW_DAYS)
    vrp = vix_current - hv

    logger.debug(
        f"[VRP_CARRY] {ticker} | VIX={vix_current:.1f} "
        f"HV30={hv:.1f} VRP={vrp:+.1f}"
    )

    # ── Gating ────────────────────────────────────────────────
    if macro_news_today:
        return VRPSignal(
            ticker=ticker, signal="HOLD",
            vix=vix_current, hv_30d=hv, vrp=vrp,
            reason="macro_news_day",
        )
    if vix_current < MIN_VIX:
        return VRPSignal(
            ticker=ticker, signal="HOLD",
            vix=vix_current, hv_30d=hv, vrp=vrp,
            reason=f"VIX={vix_current:.1f} < MIN={MIN_VIX}",
        )
    if vix_current > PANIC_THRESHOLD:
        return VRPSignal(
            ticker=ticker, signal="HOLD",
            vix=vix_current, hv_30d=hv, vrp=vrp,
            reason=f"VIX={vix_current:.1f} > PANIC={PANIC_THRESHOLD}",
        )

    # ── Señal de apertura ─────────────────────────────────────
    if vrp >= VRP_THRESHOLD:
        # Dirección según tendencia del mercado
        direction = "PUT" if spy_trend == "UP" else (
            "CALL" if spy_trend == "DOWN" else "EITHER"
        )
        # Confidence escalada: más VRP = más conviction
        confidence = min(1.0, (vrp - VRP_THRESHOLD) / 10.0 + 0.5)
        reason = (
            f"VRP={vrp:+.1f} ≥ {VRP_THRESHOLD} "
            f"(VIX={vix_current:.1f} HV30={hv:.1f})"
        )
        logger.info(
            f"[VRP_CARRY] {ticker} OPEN_SPREAD "
            f"{direction} — {reason} "
            f"confidence={confidence:.2f}"
        )
        return VRPSignal(
            ticker=ticker,
            signal="OPEN_SPREAD",
            vix=vix_current,
            hv_30d=hv,
            vrp=vrp,
            direction=direction,
            confidence=confidence,
            reason=reason,
        )

    # ── Señal de cierre (VRP colapsó) ─────────────────────────
    if vrp < 1.0:
        return VRPSignal(
            ticker=ticker, signal="CLOSE_SPREAD",
            vix=vix_current, hv_30d=hv, vrp=vrp,
            reason=f"VRP normalizado ({vrp:+.1f} < 1.0)",
        )

    return VRPSignal(
        ticker=ticker, signal="HOLD",
        vix=vix_current, hv_30d=hv, vrp=vrp,
        reason=f"VRP={vrp:+.1f} insuficiente (necesita ≥{VRP_THRESHOLD})",
    )
