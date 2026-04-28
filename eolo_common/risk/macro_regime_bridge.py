# ============================================================
#  eolo_common — Macro Regime Bridge
#
#  Ajusta el tamaño de posición en función del régimen macro
#  actual (VIX). El mismo módulo se usa en v1 (equity) y crypto.
#
#  Lógica:
#    VIX < LOW_THRESHOLD  → multiplier = HIGH_MULT  (e.g. 1.5×)
#    VIX LOW..HIGH        → multiplier = NEUTRAL     (1.0×)
#    VIX > HIGH_THRESHOLD → multiplier = LOW_MULT    (e.g. 0.5×)
#
#  Uso en v1 (bot_main.py):
#      from eolo_common.risk import get_regime_multiplier
#      budget_adj = budget * get_regime_multiplier(macro_feeds)
#
#  Uso en crypto (eolo_crypto_main.py):
#      from eolo_common.risk import MacroRegimeBridge
#      bridge = MacroRegimeBridge()
#      size_pct = base_pct * bridge.multiplier_from_value(vix_current)
#
#  Fail-soft: si macro no está disponible (None, excepción) retorna
#  multiplier = 1.0 (no modifica el sizing).
# ============================================================
import os
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

# Thresholds configurables via env-var o constructor
VIX_LOW_DEFAULT  = float(os.environ.get("MRB_VIX_LOW",   "15.0"))
VIX_HIGH_DEFAULT = float(os.environ.get("MRB_VIX_HIGH",  "25.0"))
MULT_HIGH_DEFAULT = float(os.environ.get("MRB_MULT_HIGH", "1.5"))
MULT_LOW_DEFAULT  = float(os.environ.get("MRB_MULT_LOW",  "0.5"))


@dataclass
class MacroRegimeBridge:
    """
    Calcula un multiplicador de position sizing basado en el régimen VIX.

    Atributos:
        vix_low    : umbral inferior de VIX (default 15)
        vix_high   : umbral superior de VIX (default 25)
        mult_high  : multiplicador cuando VIX < vix_low  (default 1.5)
        mult_neutral: multiplicador en zona neutral       (default 1.0)
        mult_low   : multiplicador cuando VIX > vix_high (default 0.5)
    """
    vix_low:     float = field(default_factory=lambda: VIX_LOW_DEFAULT)
    vix_high:    float = field(default_factory=lambda: VIX_HIGH_DEFAULT)
    mult_high:   float = field(default_factory=lambda: MULT_HIGH_DEFAULT)
    mult_neutral: float = 1.0
    mult_low:    float = field(default_factory=lambda: MULT_LOW_DEFAULT)

    def multiplier_from_value(self, vix: float) -> float:
        """Dado un valor de VIX, retorna el multiplicador."""
        if vix < self.vix_low:
            return self.mult_high
        elif vix > self.vix_high:
            return self.mult_low
        else:
            return self.mult_neutral

    def multiplier_from_macro(self, macro) -> float:
        """
        Lee VIX desde el objeto MacroFeeds y retorna el multiplicador.
        Fail-soft: retorna 1.0 si macro es None o VIX no disponible.
        """
        if macro is None:
            return 1.0
        try:
            vix_val = macro.latest("VIX")
            if vix_val is None:
                return 1.0
            vix = float(vix_val)
            mult = self.multiplier_from_value(vix)
            logger.debug(
                f"[MacroRegimeBridge] VIX={vix:.1f} → mult={mult:.2f}x "
                f"(thresholds: low={self.vix_low}, high={self.vix_high})"
            )
            return mult
        except Exception as e:
            logger.debug(f"[MacroRegimeBridge] VIX read failed: {e} — using 1.0×")
            return 1.0

    def regime_label(self, vix: float) -> str:
        """Etiqueta textual del régimen para logs/dashboards."""
        if vix < self.vix_low:
            return f"CALM (VIX<{self.vix_low:.0f}) → {self.mult_high:.1f}×"
        elif vix > self.vix_high:
            return f"STRESSED (VIX>{self.vix_high:.0f}) → {self.mult_low:.1f}×"
        else:
            return f"NEUTRAL (VIX {self.vix_low:.0f}–{self.vix_high:.0f}) → {self.mult_neutral:.1f}×"


# ── Singleton global para uso directo ─────────────────────
_bridge = MacroRegimeBridge()


def get_regime_multiplier(macro=None, vix_value: Optional[float] = None) -> float:
    """
    Función de conveniencia.

    Pasar macro (MacroFeeds object) O vix_value (float) directamente.
    Si se pasa vix_value, tiene precedencia sobre macro.
    Retorna el multiplicador de position sizing.
    """
    if vix_value is not None:
        return _bridge.multiplier_from_value(float(vix_value))
    return _bridge.multiplier_from_macro(macro)
