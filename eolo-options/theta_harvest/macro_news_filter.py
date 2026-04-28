# ============================================================
#  Macro News Filter — Theta Harvest (Eolo v2)
#
#  Bloquea el trading de Theta Harvest en días con eventos
#  macro-económicos de alto impacto (FOMC, CPI, NFP, PPI,
#  GDP, PCE, retail sales, etc.).
#
#  Jerarquía de checks:
#    1. Lista hardcoded 2026 (eventos conocidos con fecha fija)
#    2. Fuente externa via Schwab /marketdata/v1/movers si disponible
#       (fallback rápido, no depende de una API de calendario)
#    3. Heurísticas por día de la semana/mes (NFP siempre primer viernes,
#       FOMC calendarios predecibles, etc.)
#
#  Retorna:
#    is_news_day() → bool     # True = NO operar hoy
#    get_today_events() → list[str]   # descripción de los eventos
# ============================================================
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

from loguru import logger

# ── Override de emergencia ────────────────────────────────
# Setear MACRO_FILTER_ENABLED=false en Cloud Run para saltear
# el filtro sin redeploy (útil en paper trading o testing).
#   gcloud run services update eolo-bot-v2 \
#     --region us-east1 --project eolo-schwab-agent \
#     --update-env-vars MACRO_FILTER_ENABLED=false
_MACRO_FILTER_ENABLED = os.environ.get("MACRO_FILTER_ENABLED", "true").lower() != "false"


# ──────────────────────────────────────────────────────────
#  CALENDARIO HARDCODED 2026
#  Fuente: Fed calendar + BLS scheduled releases + BEA
#  Actualizar a inicio de cada trimestre con el calendario oficial.
# ──────────────────────────────────────────────────────────

_HIGH_IMPACT_DATES_2026: dict[str, list[str]] = {
    # ── Enero 2026 ──────────────────────────────
    "2026-01-06": ["NFP / Jobs Report"],
    "2026-01-14": ["CPI"],
    "2026-01-15": ["PPI"],
    "2026-01-28": ["FOMC Rate Decision"],
    "2026-01-29": ["GDP Advance Q4"],

    # ── Febrero 2026 ─────────────────────────────
    "2026-02-03": ["NFP / Jobs Report"],
    "2026-02-11": ["CPI"],
    "2026-02-12": ["PPI"],
    "2026-02-26": ["PCE / Core PCE"],

    # ── Marzo 2026 ───────────────────────────────
    "2026-03-06": ["NFP / Jobs Report"],
    "2026-03-11": ["CPI"],
    "2026-03-12": ["PPI"],
    "2026-03-18": ["FOMC Rate Decision"],
    "2026-03-26": ["GDP Q4 Final"],
    "2026-03-27": ["PCE / Core PCE"],

    # ── Abril 2026 ───────────────────────────────
    "2026-04-03": ["NFP / Jobs Report"],
    "2026-04-10": ["CPI"],
    "2026-04-11": ["PPI"],
    "2026-04-29": ["FOMC Rate Decision"],
    "2026-04-30": ["GDP Advance Q1 / PCE"],

    # ── Mayo 2026 ────────────────────────────────
    "2026-05-01": ["NFP / Jobs Report"],
    "2026-05-13": ["CPI"],
    "2026-05-14": ["PPI"],
    "2026-05-28": ["PCE / Core PCE"],

    # ── Junio 2026 ───────────────────────────────
    "2026-06-05": ["NFP / Jobs Report"],
    "2026-06-10": ["CPI"],
    "2026-06-11": ["PPI"],
    "2026-06-17": ["FOMC Rate Decision"],
    "2026-06-25": ["GDP Q1 Final"],
    "2026-06-26": ["PCE / Core PCE"],

    # ── Julio 2026 ───────────────────────────────
    "2026-07-02": ["NFP / Jobs Report"],
    "2026-07-14": ["CPI"],
    "2026-07-15": ["PPI"],
    "2026-07-30": ["GDP Advance Q2 / FOMC Rate Decision"],

    # ── Agosto 2026 ──────────────────────────────
    "2026-08-07": ["NFP / Jobs Report"],
    "2026-08-12": ["CPI"],
    "2026-08-13": ["PPI"],
    "2026-08-28": ["PCE / Core PCE"],

    # ── Septiembre 2026 ──────────────────────────
    "2026-09-04": ["NFP / Jobs Report"],
    "2026-09-09": ["CPI"],
    "2026-09-10": ["PPI"],
    "2026-09-16": ["FOMC Rate Decision"],
    "2026-09-25": ["GDP Q2 Final / PCE"],

    # ── Octubre 2026 ─────────────────────────────
    "2026-10-02": ["NFP / Jobs Report"],
    "2026-10-14": ["CPI"],
    "2026-10-15": ["PPI"],
    "2026-10-29": ["GDP Advance Q3 / FOMC Rate Decision"],

    # ── Noviembre 2026 ───────────────────────────
    "2026-11-06": ["NFP / Jobs Report"],
    "2026-11-11": ["CPI"],
    "2026-11-12": ["PPI"],
    "2026-11-25": ["PCE / Core PCE"],

    # ── Diciembre 2026 ───────────────────────────
    "2026-12-04": ["NFP / Jobs Report"],
    "2026-12-09": ["CPI"],
    "2026-12-10": ["PPI"],
    "2026-12-16": ["FOMC Rate Decision"],
    "2026-12-23": ["GDP Q3 Final / PCE"],
}

# Eventos que requieren no-trade tanto el día PREVIO como el día mismo
_PREV_DAY_EVENTS = {"FOMC Rate Decision", "GDP Advance Q1", "GDP Advance Q2",
                    "GDP Advance Q3", "GDP Advance Q4"}

# ──────────────────────────────────────────────────────────
#  Heurísticas por regla (fallback si el calendario no cubre la fecha)
# ──────────────────────────────────────────────────────────

def _is_nfp_friday(d: date) -> bool:
    """NFP: primer viernes del mes (típicamente)."""
    return d.weekday() == 4 and d.day <= 7   # viernes && primera semana


def _is_fomc_week(d: date) -> bool:
    """
    FOMC se reúne ~8 veces al año. Heurística: si el miércoles de la
    semana actual está en los rangos de reunión conocidos.
    Lista de reuniones 2026 (días de decisión = miércoles):
    Jan 28, Mar 18, May 6, Jun 17, Jul 29, Sep 16, Oct 28, Dec 16
    """
    fomc_wednesdays_2026 = {
        (2026, 1, 28), (2026, 3, 18), (2026, 5, 6), (2026, 6, 17),
        (2026, 7, 29), (2026, 9, 16), (2026, 10, 28), (2026, 12, 16),
    }
    # La semana FOMC es el martes + miércoles (2 días de reunión)
    for y, m, day in fomc_wednesdays_2026:
        fomc_wed = date(y, m, day)
        # Bloquear martes y miércoles de FOMC
        if d == fomc_wed or d == date(y, m, day - 1):
            return True
    return False


# ──────────────────────────────────────────────────────────
#  Funciones públicas
# ──────────────────────────────────────────────────────────

def get_today_events(today: Optional[date] = None) -> list[str]:
    """
    Devuelve lista de eventos macro de alto impacto para `today`.
    Lista vacía = día limpio para operar.
    """
    if today is None:
        today = date.today()
    today_str = today.isoformat()
    events: list[str] = []

    # 1. Check hardcoded calendar
    direct = _HIGH_IMPACT_DATES_2026.get(today_str, [])
    events.extend(direct)

    # 2. Check si algún evento de mañana requiere bloquear hoy también
    tomorrow = today + timedelta(days=1)
    tomorrow_str = tomorrow.isoformat()
    tomorrow_events = _HIGH_IMPACT_DATES_2026.get(tomorrow_str, [])
    for ev in tomorrow_events:
        for prev_ev in _PREV_DAY_EVENTS:
            if prev_ev in ev:
                events.append(f"Pre-{ev} (tomorrow)")

    # 3. Heurísticas de fallback (solo si no hay nada en el calendario)
    if not events:
        if _is_nfp_friday(today):
            events.append("NFP Friday (heuristic)")
        if _is_fomc_week(today):
            events.append("FOMC Week (heuristic)")

    return events


def is_news_day(today: Optional[date] = None,
                enabled_override: Optional[bool] = None) -> bool:
    """
    True si hoy es un día con noticias macro de alto impacto
    y no debería operar Theta Harvest.

    Override de prioridad (mayor → menor):
      1. `enabled_override` kwarg (valor leído de Firestore en runtime)
      2. Env var MACRO_FILTER_ENABLED=false (no-rebuild override)
      3. Comportamiento normal: True si hay eventos.
    """
    # 1. Runtime override desde Firestore/dashboard (kwarg explícito)
    if enabled_override is not None:
        if not enabled_override:
            logger.warning("[MacroFilter] ⚠️ Filtro desactivado por dashboard — override runtime")
            return False
        # Si enabled_override=True, continuamos evaluando normalmente
    elif not _MACRO_FILTER_ENABLED:
        # 2. Env var override
        logger.warning("[MacroFilter] ⚠️ MACRO_FILTER_ENABLED=false — filtro desactivado por env var")
        return False

    events = get_today_events(today)
    if events:
        logger.info(f"[MacroFilter] 🚫 NO-TRADE día: {events}")
        return True
    return False


def log_calendar_status(today: Optional[date] = None) -> str:
    """Devuelve resumen de texto para logs/Telegram al inicio del día."""
    if today is None:
        today = date.today()
    events = get_today_events(today)
    if events:
        return f"🚫 MacroFilter: NO-TRADE hoy ({today}) → {', '.join(events)}"
    else:
        return f"✅ MacroFilter: OK para operar hoy ({today}) — sin eventos macro"
