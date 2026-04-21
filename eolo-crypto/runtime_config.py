# ============================================================
#  EOLO Crypto — Runtime config (Firestore overrides + settings.py)
#
#  El dashboard escribe overrides a Firestore en:
#      collection = "eolo-crypto-config"
#      document   = "settings"
#
#  Campos soportados (el dashboard los escribe, el bot los respeta
#  en runtime sin redeploy):
#
#      strategies_enabled       dict  (name → bool)
#      position_size_pct        float
#      max_open_positions       int
#      default_stop_loss_pct    float
#      default_take_profit_pct  float
#      daily_loss_cap_pct       float
#      claude_max_cost_per_day  float
#      claude_bot_enabled       bool
#      active_timeframes        list[int]   (e.g. [1,5,15,30,60,240])
#      confluence_mode          bool        (multi-TF agreement filter)
#      confluence_min_agree     int         (N TFs que deben coincidir)
#
#  Uso típico:
#      from runtime_config import config
#      config.refresh()                       # sync, 1 hit a Firestore
#      if config.claude_bot_enabled: ...
#      if config.is_strategy_enabled("rsi_sma200"): ...
#      size = balance * config.position_size_pct / 100.0
#      tfs = config.multi_tf.active_timeframes
#
#  Fail-soft: si Firestore está caído, conservamos los overrides
#  previos o caemos a los defaults de settings.py — nunca rompemos
#  el ciclo del bot por un problema de red.
# ============================================================
import os
import sys
import time

from loguru import logger

import settings
from helpers import firestore_read

# ── eolo_common import (compartido con v1, v1.2, v2) ─────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.multi_tf.settings import (  # noqa: E402
    MultiTFConfig,
    load_multi_tf_config,
)
from eolo_common.trading_hours import (  # noqa: E402
    DEFAULTS_CRYPTO,
    TradingSchedule,
    load_schedule,
)


CONFIG_COLLECTION = "eolo-crypto-config"
CONFIG_DOC        = "settings"


class RuntimeConfig:
    """
    Capa de config dinámica. Los módulos consumen sus getters en vez
    de las constantes de `settings.py` para que los toggles del
    dashboard (estrategias on/off, max posiciones, claude on/off,
    budget claude, SL/TP defaults, daily loss cap, position sizing)
    tengan efecto sin redeploy.
    """

    # Mapping Firestore-key → (settings-attr, cast callable)
    _OVERRIDES = {
        "strategies_enabled":      ("STRATEGIES_ENABLED",      dict),
        "position_size_pct":       ("POSITION_SIZE_PCT",       float),
        "max_open_positions":      ("MAX_OPEN_POSITIONS",      int),
        "default_stop_loss_pct":   ("DEFAULT_STOP_LOSS_PCT",   float),
        "default_take_profit_pct": ("DEFAULT_TAKE_PROFIT_PCT", float),
        "daily_loss_cap_pct":      ("DAILY_LOSS_CAP_PCT",      float),
        "claude_max_cost_per_day": ("CLAUDE_MAX_COST_PER_DAY", float),
        "claude_bot_enabled":      ("CLAUDE_BOT_ENABLED",      bool),
    }

    def __init__(self):
        self._overrides: dict = {}
        self._last_refresh_ts: float = 0.0
        self._last_success_ts: float = 0.0
        self._last_error: str | None = None
        # Multi-TF config (saneada via eolo_common.multi_tf.settings)
        self._multi_tf: MultiTFConfig = MultiTFConfig()
        # Trading hours schedule (default: crypto 24/7)
        self._schedule: TradingSchedule = DEFAULTS_CRYPTO

    # ── Carga desde Firestore ─────────────────────────────

    def refresh(self) -> bool:
        """
        Lee el doc de Firestore y re-construye self._overrides. Si falla,
        conserva los overrides previos (fail-soft). Retorna True si leyó.
        """
        self._last_refresh_ts = time.time()
        try:
            data = firestore_read(CONFIG_COLLECTION, CONFIG_DOC) or {}
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            logger.warning(
                f"[CFG] refresh falló ({self._last_error}) — mantengo overrides previos"
            )
            return False

        new_overrides: dict = {}
        for fs_key, (_, cast) in self._OVERRIDES.items():
            if fs_key not in data:
                continue
            raw = data[fs_key]
            try:
                if cast is bool:
                    # bool(str) es siempre True para strings no vacíos.
                    # Aceptamos true/false/"true"/"false"/1/0.
                    if isinstance(raw, str):
                        new_overrides[fs_key] = raw.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        new_overrides[fs_key] = bool(raw)
                elif cast is dict:
                    if not isinstance(raw, dict):
                        raise TypeError(f"esperaba dict, recibí {type(raw).__name__}")
                    new_overrides[fs_key] = dict(raw)
                else:
                    new_overrides[fs_key] = cast(raw)
            except Exception as e:
                logger.warning(
                    f"[CFG] override '{fs_key}'={raw!r} ignorado "
                    f"(cast {cast.__name__} falló: {e})"
                )

        # Log de diffs respecto a la lectura previa
        if new_overrides != self._overrides:
            diffs = []
            for k, v in new_overrides.items():
                if self._overrides.get(k) != v:
                    diffs.append(f"{k}={v!r}")
            for k in self._overrides:
                if k not in new_overrides:
                    diffs.append(f"-{k}")
            if diffs:
                logger.info(f"[CFG] Overrides actualizados: {', '.join(diffs)}")

        self._overrides = new_overrides

        # ── Multi-TF config (active_timeframes + confluence) ──
        # Se parsea de forma independiente porque requiere validación
        # semántica (TFs soportados, min_agree ≤ len(tfs), etc.)
        try:
            self._multi_tf = load_multi_tf_config(data)
        except Exception as e:
            logger.warning(f"[CFG] multi_tf parse falló: {e} — mantengo previa")

        # ── Trading hours schedule (start/end/auto_close/enabled) ──
        # El default de crypto es 24/7 (00:00-23:59). Si el usuario setea
        # overrides desde el dashboard, los respetamos.
        try:
            new_sch = load_schedule(data, defaults=DEFAULTS_CRYPTO)
            if new_sch != self._schedule:
                logger.info(
                    f"[CFG] trading_hours actualizado: "
                    f"{new_sch.start.strftime('%H:%M')}-{new_sch.end.strftime('%H:%M')} "
                    f"(auto_close={new_sch.auto_close.strftime('%H:%M')}, enabled={new_sch.enabled})"
                )
            self._schedule = new_sch
        except Exception as e:
            logger.warning(f"[CFG] trading_hours parse falló: {e} — mantengo previa")

        self._last_success_ts = time.time()
        self._last_error = None
        return True

    # ── Getters (defaults ← settings.py, override ← Firestore) ──

    def _scalar(self, fs_key: str):
        if fs_key in self._overrides:
            return self._overrides[fs_key]
        settings_attr, _ = self._OVERRIDES[fs_key]
        return getattr(settings, settings_attr)

    @property
    def strategies_enabled(self) -> dict:
        """
        Merge per-strategy: defaults ← settings.STRATEGIES_ENABLED,
        override por-key desde Firestore. Rechazamos keys desconocidas
        para que el dashboard no pueda inventar estrategias.
        """
        base = dict(settings.STRATEGIES_ENABLED)
        override = self._overrides.get("strategies_enabled") or {}
        for k, v in override.items():
            if k in base:
                base[k] = bool(v)
        return base

    def is_strategy_enabled(self, name: str) -> bool:
        return bool(self.strategies_enabled.get(name, False))

    @property
    def position_size_pct(self) -> float:
        return float(self._scalar("position_size_pct"))

    @property
    def max_open_positions(self) -> int:
        return int(self._scalar("max_open_positions"))

    @property
    def default_stop_loss_pct(self) -> float:
        return float(self._scalar("default_stop_loss_pct"))

    @property
    def default_take_profit_pct(self) -> float:
        return float(self._scalar("default_take_profit_pct"))

    @property
    def daily_loss_cap_pct(self) -> float:
        return float(self._scalar("daily_loss_cap_pct"))

    @property
    def claude_max_cost_per_day(self) -> float:
        return float(self._scalar("claude_max_cost_per_day"))

    @property
    def claude_bot_enabled(self) -> bool:
        return bool(self._scalar("claude_bot_enabled"))

    # ── Multi-TF ──────────────────────────────────────────

    @property
    def multi_tf(self) -> MultiTFConfig:
        """Config multi-TF saneada (active_timeframes + confluence)."""
        return self._multi_tf

    @property
    def active_timeframes(self) -> list[int]:
        return list(self._multi_tf.active_timeframes)

    @property
    def confluence_mode(self) -> bool:
        return bool(self._multi_tf.confluence_mode)

    @property
    def confluence_min_agree(self) -> int:
        return int(self._multi_tf.confluence_min_agree)

    # ── Trading hours ─────────────────────────────────────

    @property
    def schedule(self) -> TradingSchedule:
        """Trading schedule (start/end/auto_close/enabled). Default crypto 24/7."""
        return self._schedule

    # ── Debug / introspección ─────────────────────────────

    def as_dict(self) -> dict:
        """Snapshot del estado efectivo (defaults + overrides) — útil para logs."""
        return {
            "strategies_enabled":      self.strategies_enabled,
            "position_size_pct":       self.position_size_pct,
            "max_open_positions":      self.max_open_positions,
            "default_stop_loss_pct":   self.default_stop_loss_pct,
            "default_take_profit_pct": self.default_take_profit_pct,
            "daily_loss_cap_pct":      self.daily_loss_cap_pct,
            "claude_max_cost_per_day": self.claude_max_cost_per_day,
            "claude_bot_enabled":      self.claude_bot_enabled,
            "active_timeframes":       self.active_timeframes,
            "confluence_mode":         self.confluence_mode,
            "confluence_min_agree":    self.confluence_min_agree,
            "trading_start_et":        self._schedule.start.strftime("%H:%M"),
            "trading_end_et":          self._schedule.end.strftime("%H:%M"),
            "auto_close_et":           self._schedule.auto_close.strftime("%H:%M"),
            "trading_hours_enabled":   self._schedule.enabled,
            "_overrides_active":       list(self._overrides.keys()),
            "_last_success_ts":        self._last_success_ts,
            "_last_error":             self._last_error,
        }


# ── Singleton por proceso ─────────────────────────────────
# Importar como: `from runtime_config import config`
config = RuntimeConfig()
