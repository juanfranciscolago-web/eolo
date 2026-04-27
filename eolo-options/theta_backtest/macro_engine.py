# ============================================================
#  Theta Harvest Backtester — Macro Engine (offline)
#
#  Wrapper offline del macro_news_filter para el backtester.
#  Lee el mismo calendario hardcoded y las mismas heurísticas
#  que el live strategy, pero sin depender de Schwab API.
# ============================================================
from __future__ import annotations

import sys
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# ── Importar calendario del live strategy ─────────────────
# Añadir el directorio padre al path para poder importar theta_harvest
_PARENT = Path(__file__).parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

try:
    from theta_harvest.macro_news_filter import (
        is_news_day as _live_is_news_day,
        get_today_events as _live_get_events,
    )
    _HAS_LIVE = True
except ImportError:
    _HAS_LIVE = False


# ── Fallback: heurísticas básicas si no se puede importar ──

def _first_friday_of_month(year: int, month: int) -> date:
    """Retorna el primer viernes del mes (NFP)."""
    d = date(year, month, 1)
    while d.weekday() != 4:   # 4 = Friday
        d = date(year, month, d.day + 1)
    return d


# Días FOMC conocidos históricamente (aproximados para 2022-2025)
_FOMC_DATES_APPROX: set[str] = {
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
}

# CPI se publica ~segundo miércoles de cada mes
_CPI_APPROX_DAYS: dict[str, int] = {
    # Año-mes: día del mes
    # 2022
    "2022-01": 12, "2022-02": 10, "2022-03": 10, "2022-04": 12,
    "2022-05": 11, "2022-06": 10, "2022-07": 13, "2022-08": 10,
    "2022-09": 13, "2022-10": 13, "2022-11": 10, "2022-12": 13,
    # 2023
    "2023-01": 12, "2023-02": 14, "2023-03": 14, "2023-04": 12,
    "2023-05": 10, "2023-06": 13, "2023-07": 12, "2023-08": 10,
    "2023-09": 13, "2023-10": 12, "2023-11": 14, "2023-12": 12,
    # 2024
    "2024-01": 11, "2024-02": 13, "2024-03": 12, "2024-04": 10,
    "2024-05": 15, "2024-06": 12, "2024-07": 11, "2024-08": 14,
    "2024-09": 11, "2024-10": 10, "2024-11": 13, "2024-12": 11,
    # 2025
    "2025-01": 15, "2025-02": 12, "2025-03": 12, "2025-04": 10,
    "2025-05": 13, "2025-06": 11, "2025-07": 11, "2025-08": 12,
    "2025-09": 10, "2025-10":  9, "2025-11": 12, "2025-12": 10,
}


def _fallback_is_news_day(d: date) -> tuple[bool, list[str]]:
    """Heurísticas cuando no se puede importar el live filter."""
    events = []

    date_str = d.isoformat()

    # FOMC
    if date_str in _FOMC_DATES_APPROX:
        events.append("FOMC Rate Decision")

    # NFP (primer viernes del mes)
    try:
        first_fri = _first_friday_of_month(d.year, d.month)
        if d == first_fri:
            events.append("NFP / Jobs Report")
    except Exception:
        pass

    # CPI
    ym = f"{d.year}-{d.month:02d}"
    if ym in _CPI_APPROX_DAYS and d.day == _CPI_APPROX_DAYS[ym]:
        events.append("CPI")

    return bool(events), events


# ─────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────

def is_macro_blocked(d: date) -> bool:
    """
    Retorna True si la fecha `d` debe bloquearse por eventos macro.
    Usa el live filter si disponible, heurísticas si no.
    """
    if _HAS_LIVE:
        return _live_is_news_day(d)
    blocked, _ = _fallback_is_news_day(d)
    return blocked


def get_macro_events(d: date) -> list[str]:
    """Lista de eventos macro en la fecha `d`."""
    if _HAS_LIVE:
        return _live_get_events(d)
    _, events = _fallback_is_news_day(d)
    return events


def build_blocked_dates(
    start: str,
    end: str,
) -> set[pd.Timestamp]:
    """
    Pre-computa el conjunto de fechas bloqueadas en el rango.
    Mucho más eficiente que llamar is_macro_blocked() por cada día.
    """
    blocked = set()
    dt = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    while dt <= end_ts:
        if is_macro_blocked(dt.date()):
            blocked.add(dt)
        dt += pd.Timedelta(days=1)

    return blocked
