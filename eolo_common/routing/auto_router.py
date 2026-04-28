# ============================================================
#  EOLO — Strategy Auto-Router
#
#  Ref: nueva infraestructura v2 (2026-04-27)
#
#  Qué hace:
#    Detecta el régimen de mercado actual usando 4 señales:
#      1. VIX level   → LOW / NORMAL / HIGH / PANIC
#      2. Trend        → BULL / BEAR / NEUTRAL (SMA200 + retorno 20d)
#      3. Momentum     → HOT / COLD (RSI del SPY)
#      4. Volatility   → CONTRACTION / EXPANSION (ATR vs promedio)
#
#    Para cada combinación de régimen, recomienda qué estrategias
#    activar/desactivar. Las recomendaciones se guardan en Firestore:
#      eolo_auto_router / recommendations / {bot_id}
#
#    Los bots leen estas recomendaciones y ajustan sus toggles.
#
#  Reglas de routing (hard-coded, ajustable vía env vars):
#    PANIC  (VIX>40): solo theta_harvest, desactivar todo momentum
#    HIGH   (VIX 25-40): theta_harvest + vix_spike_fade; sin 0DTE
#    NORMAL (VIX 15-25): todo activo, peso normal
#    LOW    (VIX<15):   momentum activo, vol selling reducido
#
#  Uso:
#    from eolo_common.routing import get_strategy_recommendations
#    recs = get_strategy_recommendations(vix=20, spy_df=df, bot_id="v1")
#    # recs = {"ema_gap": True, "theta_harvest": True, "0dte": False, ...}
#
#  Integración: los bots leen el doc de Firestore cada N minutos
#    y aplican los toggles. El auto_router corre en un task separado.
# ============================================================
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ── Config ────────────────────────────────────────────────
VIX_LOW_THRESH    = float(os.environ.get("AR_VIX_LOW",    "15.0"))
VIX_HIGH_THRESH   = float(os.environ.get("AR_VIX_HIGH",   "25.0"))
VIX_PANIC_THRESH  = float(os.environ.get("AR_VIX_PANIC",  "40.0"))
TREND_SMA_WINDOW  = int(os.environ.get("AR_SMA_WINDOW",   "200"))
TREND_RET_DAYS    = int(os.environ.get("AR_RET_DAYS",     "20"))
RSI_HOT_THRESH    = float(os.environ.get("AR_RSI_HOT",    "60.0"))
RSI_COLD_THRESH   = float(os.environ.get("AR_RSI_COLD",   "40.0"))
ATR_WINDOW        = int(os.environ.get("AR_ATR_WINDOW",   "14"))
ATR_EXPANSION_PCT = float(os.environ.get("AR_ATR_EXP",    "20.0"))  # ATR > MA20_ATR * (1 + X/100)
GCP_PROJECT       = os.environ.get("GCP_PROJECT_ID", "eolo-schwab-agent")
FIRESTORE_COL     = "eolo_auto_router"


@dataclass
class MarketRegime:
    vix_regime:  str    # "LOW" | "NORMAL" | "HIGH" | "PANIC"
    trend:       str    # "BULL" | "BEAR" | "NEUTRAL"
    momentum:    str    # "HOT" | "COLD" | "NEUTRAL"
    volatility:  str    # "EXPANSION" | "CONTRACTION" | "NORMAL"
    vix:         float = 0.0
    rsi:         float = 50.0
    spy_price:   float = 0.0
    sma200:      float = 0.0
    timestamp:   str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class StrategyRecommendations:
    bot_id:      str
    regime:      MarketRegime
    toggles:     dict          # {strategy_name: True/False}
    size_mult:   float = 1.0   # multiplicador de tamaño recomendado
    reason:      str = ""
    timestamp:   str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ── Regime detection helpers ──────────────────────────────

def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 2:
        return 50.0
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _compute_atr(df: pd.DataFrame, period: int = ATR_WINDOW) -> tuple[float, float]:
    """Retorna (atr_actual, atr_ma20)."""
    if len(df) < period + 20 or "high" not in df.columns:
        return 0.0, 0.0
    high  = df["high"].values.astype(float)
    low   = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    tr_arr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            abs(high[1:] - close[:-1]),
            abs(low[1:] - close[:-1]),
        )
    )
    tr_s = pd.Series(tr_arr)
    atr_s = tr_s.ewm(span=period, adjust=False).mean()
    atr_now  = float(atr_s.iloc[-1])
    atr_ma20 = float(atr_s.tail(20).mean())
    return atr_now, atr_ma20


def detect_regime(
    spy_df: pd.DataFrame,
    vix_current: float,
) -> MarketRegime:
    """
    Detecta el régimen de mercado actual basado en spy_df y vix_current.
    spy_df debe tener columnas: close, high, low (diario, ≥250 días ideal).
    """
    # ── VIX Regime ────────────────────────────────────────
    if vix_current >= VIX_PANIC_THRESH:
        vix_regime = "PANIC"
    elif vix_current >= VIX_HIGH_THRESH:
        vix_regime = "HIGH"
    elif vix_current >= VIX_LOW_THRESH:
        vix_regime = "NORMAL"
    else:
        vix_regime = "LOW"

    closes = spy_df["close"].dropna()
    price  = float(closes.iloc[-1]) if len(closes) > 0 else 0.0

    # ── Trend ─────────────────────────────────────────────
    if len(closes) >= TREND_SMA_WINDOW:
        sma200 = float(closes.tail(TREND_SMA_WINDOW).mean())
    else:
        sma200 = price

    ret_20d = 0.0
    if len(closes) > TREND_RET_DAYS:
        ret_20d = (float(closes.iloc[-1]) - float(closes.iloc[-TREND_RET_DAYS])) / float(closes.iloc[-TREND_RET_DAYS]) * 100

    if price > sma200 and ret_20d > 2.0:
        trend = "BULL"
    elif price < sma200 and ret_20d < -2.0:
        trend = "BEAR"
    else:
        trend = "NEUTRAL"

    # ── Momentum (RSI) ────────────────────────────────────
    rsi = _compute_rsi(closes)
    if rsi > RSI_HOT_THRESH:
        momentum = "HOT"
    elif rsi < RSI_COLD_THRESH:
        momentum = "COLD"
    else:
        momentum = "NEUTRAL"

    # ── Volatility (ATR) ─────────────────────────────────
    atr_now, atr_ma20 = _compute_atr(spy_df)
    if atr_ma20 > 0 and atr_now > atr_ma20 * (1 + ATR_EXPANSION_PCT / 100):
        vol_state = "EXPANSION"
    elif atr_ma20 > 0 and atr_now < atr_ma20 * (1 - ATR_EXPANSION_PCT / 100):
        vol_state = "CONTRACTION"
    else:
        vol_state = "NORMAL"

    logger.debug(
        f"[AUTO_ROUTER] Regime: VIX={vix_regime}({vix_current:.1f}) "
        f"trend={trend} momentum={momentum} vol={vol_state} "
        f"RSI={rsi:.1f} price={price:.2f} SMA200={sma200:.2f}"
    )

    return MarketRegime(
        vix_regime=vix_regime,
        trend=trend,
        momentum=momentum,
        volatility=vol_state,
        vix=vix_current,
        rsi=rsi,
        spy_price=price,
        sma200=sma200,
    )


# ── Routing logic ─────────────────────────────────────────

# Reglas por régimen VIX
_REGIME_RULES: dict[str, dict] = {
    "LOW": {
        # VIX bajo: momentum fuerte, vol selling reducido
        "ema_gap":            True,
        "bollinger_rsi":      True,
        "overnight_drift":    True,
        "vix_spike_fade":     False,   # poca volatilidad, poca señal
        "theta_harvest":      True,
        "vrp_carry":          False,   # VRP estrecho
        "0dte_gamma_scalp":   True,
        "earnings_iv_harvest":True,
        "put_skew":           False,   # skew bajo en mercados tranquilos
        "sector_rrg":         True,
        "spy_qqq_divergence": True,
        "size_mult":          1.5,
    },
    "NORMAL": {
        # VIX normal: todo activo
        "ema_gap":            True,
        "bollinger_rsi":      True,
        "overnight_drift":    True,
        "vix_spike_fade":     True,
        "theta_harvest":      True,
        "vrp_carry":          True,
        "0dte_gamma_scalp":   True,
        "earnings_iv_harvest":True,
        "put_skew":           True,
        "sector_rrg":         True,
        "spy_qqq_divergence": True,
        "size_mult":          1.0,
    },
    "HIGH": {
        # VIX elevado: reducir momentum, aumentar theta/vol
        "ema_gap":            False,
        "bollinger_rsi":      False,
        "overnight_drift":    False,
        "vix_spike_fade":     True,
        "theta_harvest":      True,
        "vrp_carry":          True,
        "0dte_gamma_scalp":   False,   # vol extrema = 0DTE peligroso
        "earnings_iv_harvest":True,
        "put_skew":           True,    # skew alto en vol elevada
        "sector_rrg":         False,
        "spy_qqq_divergence": False,
        "size_mult":          0.7,
    },
    "PANIC": {
        # VIX > 40: solo theta harvest y fade; ningún momentum
        "ema_gap":            False,
        "bollinger_rsi":      False,
        "overnight_drift":    False,
        "vix_spike_fade":     True,
        "theta_harvest":      True,   # seguir vendiendo time value con stops ajustados
        "vrp_carry":          False,  # demasiado riesgo de gap
        "0dte_gamma_scalp":   False,
        "earnings_iv_harvest":False,
        "put_skew":           True,
        "sector_rrg":         False,
        "spy_qqq_divergence": False,
        "size_mult":          0.5,
    },
}

# Ajustes adicionales por tendencia
_TREND_OVERRIDES: dict[str, dict] = {
    "BEAR": {
        "0dte_gamma_scalp": False,   # evitar calls en bear
        "overnight_drift":  False,   # overnight drift es alcista
    },
}


def _build_toggles(regime: MarketRegime) -> tuple[dict, float]:
    base = dict(_REGIME_RULES.get(regime.vix_regime, _REGIME_RULES["NORMAL"]))
    size_mult = float(base.pop("size_mult", 1.0))

    # Aplicar overrides de tendencia
    overrides = _TREND_OVERRIDES.get(regime.trend, {})
    for k, v in overrides.items():
        if k in base:
            base[k] = v

    # Ajuste adicional: si ATR en expansión, reducir size
    if regime.volatility == "EXPANSION":
        size_mult = round(size_mult * 0.85, 2)

    return base, size_mult


# ── Main entry point ──────────────────────────────────────

def get_strategy_recommendations(
    vix: float,
    spy_df: pd.DataFrame,
    bot_id: str = "v1",
    save_firestore: bool = True,
) -> dict:
    """
    Detecta régimen y retorna dict de toggles {strategy_name: bool}.
    También guarda las recomendaciones en Firestore si save_firestore=True.

    Uso:
        recs = get_strategy_recommendations(vix=18.5, spy_df=df, bot_id="v1")
        # {"ema_gap": True, "theta_harvest": True, "0dte_gamma_scalp": True, ...}
    """
    regime = detect_regime(spy_df, vix)
    toggles, size_mult = _build_toggles(regime)

    reason = (
        f"VIX={regime.vix_regime}({vix:.1f}) "
        f"trend={regime.trend} "
        f"mom={regime.momentum} "
        f"vol={regime.volatility} "
        f"size_mult={size_mult}"
    )

    recs = StrategyRecommendations(
        bot_id=bot_id,
        regime=regime,
        toggles=toggles,
        size_mult=size_mult,
        reason=reason,
    )

    logger.info(f"[AUTO_ROUTER] {bot_id} — {reason}")
    logger.debug(f"[AUTO_ROUTER] toggles={toggles}")

    if save_firestore:
        _save_recommendations(recs)

    return toggles


class AutoRouter:
    """
    Clase wrapper para integración continua en el bot loop.

    Ejemplo en el orquestador:
        router = AutoRouter(bot_id="v1", update_interval_min=30)
        # En el loop:
        if router.should_update():
            new_toggles = router.update(vix=vix_current, spy_df=spy_df)
            settings["strategies"].update(new_toggles)
    """

    def __init__(self, bot_id: str = "v1", update_interval_min: int = 30):
        self.bot_id = bot_id
        self.update_interval_sec = update_interval_min * 60
        self._last_update: float = 0.0
        self._last_toggles: dict = {}

    def should_update(self) -> bool:
        import time
        return (time.time() - self._last_update) >= self.update_interval_sec

    def update(
        self,
        vix: float,
        spy_df: pd.DataFrame,
        save_firestore: bool = True,
    ) -> dict:
        import time
        toggles = get_strategy_recommendations(
            vix=vix, spy_df=spy_df,
            bot_id=self.bot_id,
            save_firestore=save_firestore,
        )
        self._last_toggles = toggles
        self._last_update = time.time()
        return toggles

    @property
    def last_toggles(self) -> dict:
        return self._last_toggles


# ── Firestore persistence ─────────────────────────────────

def _save_recommendations(recs: StrategyRecommendations) -> None:
    """Guarda recomendaciones en Firestore (fail-soft)."""
    try:
        from google.cloud import firestore as _fs
        db = _fs.Client(project=GCP_PROJECT)
        data = {
            "bot_id":     recs.bot_id,
            "toggles":    recs.toggles,
            "size_mult":  recs.size_mult,
            "reason":     recs.reason,
            "regime":     asdict(recs.regime),
            "timestamp":  recs.timestamp,
        }
        db.collection(FIRESTORE_COL).document("recommendations").set(
            {recs.bot_id: data}, merge=True
        )
        logger.debug(f"[AUTO_ROUTER] Guardado en Firestore: {FIRESTORE_COL}/recommendations/{recs.bot_id}")
    except Exception as e:
        logger.warning(f"[AUTO_ROUTER] No se pudo guardar en Firestore: {e}")
