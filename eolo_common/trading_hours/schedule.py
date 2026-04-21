# ============================================================
#  eolo_common.trading_hours.schedule
#
#  Implementación del módulo. No tiene dependencias externas
#  más allá de la stdlib (zoneinfo en Python 3.9+). Fail-soft:
#  si los settings están malformados, cae a los defaults sin
#  romper el loop principal del bot.
# ============================================================
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Dict, Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — fallback para entornos sin tzdata
    import pytz  # type: ignore
    _ET = pytz.timezone("America/New_York")


# ── Helpers de parseo ──────────────────────────────────────

def _parse_hhmm(value, fallback: time) -> time:
    """
    Parsea 'HH:MM' o 'HH:MM:SS' a `datetime.time`. Tolera:
      - strings tipo "9:30", "09:30", "15:27:00"
      - objetos `time` ya parseados
      - dicts tipo {"hour": 9, "minute": 30}
    Si algo falla devuelve `fallback` (no rompe).
    """
    if value is None:
        return fallback
    if isinstance(value, time):
        return value
    if isinstance(value, dict):
        try:
            return time(int(value.get("hour", 0)), int(value.get("minute", 0)))
        except Exception:
            return fallback
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                return time(h, m)
        except Exception:
            return fallback
    return fallback


def _fmt_hhmm(t: time) -> str:
    """Formatea a 'HH:MM' (zero-padded)."""
    return f"{t.hour:02d}:{t.minute:02d}"


# ── Dataclass ──────────────────────────────────────────────

@dataclass(frozen=True)
class TradingSchedule:
    """
    Ventana de trading configurable.
    - start: desde esta hora (inclusive) el bot puede ABRIR posiciones nuevas.
    - end: a partir de esta hora (exclusive) el bot DEJA de abrir nuevas.
    - auto_close: a esta hora el bot CIERRA todas las posiciones abiertas.
    - enabled: si False, el bot ignora los horarios (trading 24/7 efectivo).

    Semántica: start ≤ now < end → "within window".
              now ≥ auto_close   → "after auto-close" (flatten).
    """
    start: time
    end: time
    auto_close: time
    enabled: bool = True

    def to_dict(self) -> Dict[str, str]:
        return {
            "trading_start_et":     _fmt_hhmm(self.start),
            "trading_end_et":       _fmt_hhmm(self.end),
            "auto_close_et":        _fmt_hhmm(self.auto_close),
            "trading_hours_enabled": self.enabled,
        }


# ── Defaults por tipo de bot ───────────────────────────────

DEFAULTS_EQUITY = TradingSchedule(
    start=time(9, 30),
    end=time(15, 27),
    auto_close=time(15, 27),
    enabled=True,
)

DEFAULTS_CRYPTO = TradingSchedule(
    start=time(0, 0),
    end=time(23, 59),
    auto_close=time(23, 59),
    enabled=True,
)


# ── Loader desde settings dict ─────────────────────────────

def load_schedule(
    settings: Dict,
    defaults: TradingSchedule = DEFAULTS_EQUITY,
) -> TradingSchedule:
    """
    Construye un TradingSchedule desde el dict de settings (típicamente
    el retorno de `get_global_settings()`/`get_runtime_settings()`).

    Tolera keys faltantes, valores mal formados, None, etc. —
    siempre retorna un TradingSchedule válido.
    """
    if not isinstance(settings, dict):
        return defaults

    return TradingSchedule(
        start=_parse_hhmm(settings.get("trading_start_et"), defaults.start),
        end=_parse_hhmm(settings.get("trading_end_et"), defaults.end),
        auto_close=_parse_hhmm(settings.get("auto_close_et"), defaults.auto_close),
        enabled=bool(settings.get("trading_hours_enabled", defaults.enabled)),
    )


# ── Time helpers ───────────────────────────────────────────

def now_et() -> datetime:
    """datetime.now() en America/New_York."""
    return datetime.now(_ET)


def is_within_trading_window(now: datetime, sch: TradingSchedule) -> bool:
    """
    True si `now` (timezone-aware, ET) está dentro del rango
    [start, end) del schedule.

    Si el schedule está deshabilitado (enabled=False) siempre True
    — o sea, el bot opera sin restricción horaria.
    """
    if not sch.enabled:
        return True
    t = now.time().replace(second=0, microsecond=0)
    return sch.start <= t < sch.end


def is_after_auto_close(now: datetime, sch: TradingSchedule) -> bool:
    """
    True si `now` ya pasó el auto_close del día. Se usa para
    disparar el cierre forzado de todas las posiciones abiertas.

    Si el schedule está deshabilitado siempre False (nunca cierra).
    """
    if not sch.enabled:
        return False
    t = now.time().replace(second=0, microsecond=0)
    return t >= sch.auto_close


# ── Payload para dashboards (/api/schedule-status) ─────────

def format_schedule_for_api(sch: TradingSchedule, now: Optional[datetime] = None) -> Dict:
    """
    Serializa el estado actual del schedule para consumo del dashboard.
    Devuelve:
      {
        "now_et": "2026-04-20 14:32",
        "is_within_window": True,
        "is_after_auto_close": False,
        "trading_start_et": "09:30",
        "trading_end_et": "15:27",
        "auto_close_et": "15:27",
        "trading_hours_enabled": True,
        "banner_reason": null   // o "before_start" | "after_end" | "disabled"
      }
    """
    now = now or now_et()
    within = is_within_trading_window(now, sch)
    after_close = is_after_auto_close(now, sch)

    reason = None
    if not sch.enabled:
        reason = "disabled"
    elif not within:
        t = now.time()
        if t < sch.start:
            reason = "before_start"
        else:
            reason = "after_end"

    out = sch.to_dict()
    out.update({
        "now_et":              now.strftime("%Y-%m-%d %H:%M"),
        "is_within_window":    within,
        "is_after_auto_close": after_close,
        "banner_reason":       reason,
    })
    return out
