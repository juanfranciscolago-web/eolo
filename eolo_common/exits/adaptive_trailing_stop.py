# ============================================================
#  eolo_common.exits.adaptive_trailing_stop
#
#  Ref: trading_strategies_v2.md #19
#
#  Módulo de salida universal: una vez que la posición está en
#  ganancia > 1R, aplicamos un trailing stop basado en ATR con
#  multiplicador variable según régimen de volatilidad.
#
#  Uso típico (pseudocódigo):
#
#      trailer = AdaptiveTrailingStop()
#      # Por cada tick:
#      new_stop = trailer.update(
#          position={
#              "symbol": "SPY",
#              "direction": "LONG",
#              "entry_price": 540.0,
#              "current_stop": 538.0,
#              "unrealized_r": 1.3,   # profit en múltiplos de R
#          },
#          last_close=547.3,
#          atr=1.9,
#          vix=17.2,      # opcional; None si no hay feed
#      )
#      if new_stop is not None:
#          position["current_stop"] = new_stop
#
#  - k=2.0 si VIX<20 (régimen bajo). k=3.0 si VIX≥20 o VIX es None
#    (conservador por defecto).
#  - Para LONG, el stop solo SUBE. Para SHORT, solo BAJA.
#  - Si unrealized_r < 1.0 → devuelve None (no activado aún).
# ============================================================
from dataclasses import dataclass
from typing import Optional


K_LOW_VOL  = 2.0
K_HIGH_VOL = 3.0
VIX_THRESHOLD = 20.0
ACTIVATE_R = 1.0


def _k_for_vix(vix: Optional[float]) -> float:
    """Elige el multiplicador ATR según VIX. None/alto → conservador."""
    if vix is None:
        return K_HIGH_VOL
    try:
        return K_LOW_VOL if float(vix) < VIX_THRESHOLD else K_HIGH_VOL
    except (TypeError, ValueError):
        return K_HIGH_VOL


def compute_trailing_stop(
    direction: str,
    last_close: float,
    atr: float,
    current_stop: float,
    unrealized_r: float,
    vix: Optional[float] = None,
) -> Optional[float]:
    """
    Devuelve el nuevo stop (o None si no aplica todavía).

    direction:     "LONG" o "SHORT"
    last_close:    último precio
    atr:           ATR de referencia (típicamente 14 periodos)
    current_stop:  stop actual de la posición
    unrealized_r:  profit en múltiplos de R
    vix:           VIX actual (opcional)
    """
    if unrealized_r is None or unrealized_r < ACTIVATE_R:
        return None
    if atr is None or atr <= 0 or last_close is None:
        return None

    k = _k_for_vix(vix)
    direction = (direction or "").upper()

    if direction == "LONG":
        candidate = float(last_close) - k * float(atr)
        if current_stop is None:
            return candidate
        return max(float(current_stop), candidate)

    if direction == "SHORT":
        candidate = float(last_close) + k * float(atr)
        if current_stop is None:
            return candidate
        return min(float(current_stop), candidate)

    return None


@dataclass
class AdaptiveTrailingStop:
    """Wrapper con estado mínimo para uso en el orquestador."""
    k_low:         float = K_LOW_VOL
    k_high:        float = K_HIGH_VOL
    vix_threshold: float = VIX_THRESHOLD
    activate_r:    float = ACTIVATE_R

    def k_for(self, vix: Optional[float]) -> float:
        if vix is None:
            return self.k_high
        try:
            return self.k_low if float(vix) < self.vix_threshold else self.k_high
        except (TypeError, ValueError):
            return self.k_high

    def update(
        self,
        position: dict,
        last_close: float,
        atr: float,
        vix: Optional[float] = None,
    ) -> Optional[float]:
        """
        Recibe un dict `position` con al menos:
          - direction:      "LONG" | "SHORT"
          - current_stop:   float | None
          - unrealized_r:   float  (profit actual medido en R múltiplos)
        Devuelve un nuevo stop o None si aún no aplica.
        """
        direction    = position.get("direction")
        current_stop = position.get("current_stop")
        unrealized_r = position.get("unrealized_r")

        if unrealized_r is None or float(unrealized_r) < self.activate_r:
            return None
        if atr is None or atr <= 0 or last_close is None:
            return None

        k = self.k_for(vix)
        direction = (direction or "").upper()

        if direction == "LONG":
            candidate = float(last_close) - k * float(atr)
            return candidate if current_stop is None else max(float(current_stop), candidate)

        if direction == "SHORT":
            candidate = float(last_close) + k * float(atr)
            return candidate if current_stop is None else min(float(current_stop), candidate)

        return None

    def unrealized_r_from_stop(
        self,
        direction: str,
        entry_price: float,
        initial_stop: float,
        current_price: float,
    ) -> Optional[float]:
        """
        Helper para calcular `unrealized_r` a partir del stop inicial.
        Útil si la posición no trae el campo explícito.
        R = (current_price - entry_price) / (entry_price - initial_stop) para LONG.
        """
        if entry_price is None or initial_stop is None or current_price is None:
            return None
        r_unit = abs(float(entry_price) - float(initial_stop))
        if r_unit <= 0:
            return None
        direction = (direction or "").upper()
        if direction == "LONG":
            return (float(current_price) - float(entry_price)) / r_unit
        if direction == "SHORT":
            return (float(entry_price) - float(current_price)) / r_unit
        return None
