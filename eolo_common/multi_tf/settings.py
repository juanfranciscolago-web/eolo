# ============================================================
#  settings.py — parse de config multi-TF desde Firestore
#
#  Tres campos en el doc settings de cada Eolo:
#    active_timeframes    : list[int]  — TFs activos (minutos)
#    confluence_mode      : bool        — activar filtro multi-TF
#    confluence_min_agree : int         — cuántos TFs deben coincidir
#
#  Defaults: [1,5,15,30,60,240], confluence OFF, min_agree=2
#
#  load_multi_tf_config(settings_dict) devuelve un MultiTFConfig
#  saneado (tipos validados, TFs en el set soportado, etc).
# ============================================================
from dataclasses import dataclass, field
from typing import Any

from .resample import DEFAULT_TIMEFRAMES, SUPPORTED_TIMEFRAMES


@dataclass
class MultiTFConfig:
    active_timeframes:    list[int] = field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    confluence_mode:      bool      = False
    confluence_min_agree: int       = 2

    def as_dict(self) -> dict:
        return {
            "active_timeframes":    list(self.active_timeframes),
            "confluence_mode":      bool(self.confluence_mode),
            "confluence_min_agree": int(self.confluence_min_agree),
        }


def load_multi_tf_config(settings_dict: dict[str, Any] | None) -> MultiTFConfig:
    """
    Recibe el dict raw de Firestore settings y retorna un MultiTFConfig
    con tipos validados. Valores inválidos → default.
    """
    cfg = MultiTFConfig()

    if not settings_dict:
        return cfg

    # ── active_timeframes ─────────────────────────────────
    raw_tfs = settings_dict.get("active_timeframes", cfg.active_timeframes)
    if isinstance(raw_tfs, (list, tuple)) and raw_tfs:
        clean = []
        for t in raw_tfs:
            try:
                ti = int(t)
                if ti in SUPPORTED_TIMEFRAMES:
                    clean.append(ti)
            except (TypeError, ValueError):
                continue
        if clean:
            cfg.active_timeframes = sorted(set(clean))

    # ── confluence_mode ───────────────────────────────────
    cfg.confluence_mode = bool(settings_dict.get("confluence_mode", False))

    # ── confluence_min_agree ──────────────────────────────
    try:
        cfg.confluence_min_agree = max(1, int(
            settings_dict.get("confluence_min_agree", 2)
        ))
    except (TypeError, ValueError):
        cfg.confluence_min_agree = 2

    # Sanity check: min_agree no puede ser mayor al número de TFs activos
    if cfg.confluence_min_agree > len(cfg.active_timeframes):
        cfg.confluence_min_agree = len(cfg.active_timeframes)

    return cfg
