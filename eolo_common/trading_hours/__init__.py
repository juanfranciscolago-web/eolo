# ============================================================
#  eolo_common.trading_hours
#
#  Ventana de trading configurable por Firestore — compartida
#  entre v1 (acciones), v2 (opciones) y crypto. Expone:
#
#    - TradingSchedule (dataclass)
#    - load_schedule(settings, defaults=...)   → TradingSchedule
#    - is_within_trading_window(now_et, sch)   → bool
#    - is_after_auto_close(now_et, sch)        → bool
#    - now_et()                                 → datetime (ET)
#    - DEFAULTS_EQUITY / DEFAULTS_CRYPTO       (constantes de default)
#
#  Keys esperadas en settings (Firestore):
#    trading_start_et   : "HH:MM"  (default equities="09:30", crypto="00:00")
#    trading_end_et     : "HH:MM"  (default equities="15:27", crypto="23:59")
#    auto_close_et      : "HH:MM"  (default equities="15:27", crypto="23:59")
#    trading_hours_enabled : bool  (default True; si False se opera 24/7)
#
#  Todas las horas se interpretan en America/New_York (ET) por
#  consistencia entre los 3 bots. Crypto usa ET también aunque
#  sea 24/7 — por defaults efectivos (00:00-23:59) opera siempre.
# ============================================================
from .schedule import (
    TradingSchedule,
    DEFAULTS_EQUITY,
    DEFAULTS_CRYPTO,
    load_schedule,
    is_within_trading_window,
    is_after_auto_close,
    now_et,
    format_schedule_for_api,
)

__all__ = [
    "TradingSchedule",
    "DEFAULTS_EQUITY",
    "DEFAULTS_CRYPTO",
    "load_schedule",
    "is_within_trading_window",
    "is_after_auto_close",
    "now_et",
    "format_schedule_for_api",
]
