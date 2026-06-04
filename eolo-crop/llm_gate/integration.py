"""
Integration helpers — pre-filter rules + decision-to-scan-params converter.

Estos helpers conectan el LLMGateClient con el flujo del bot eolo-crop.
"""
import logging
from datetime import datetime, time
from typing import Optional, Tuple, Dict, Any, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Pre-filter thresholds
# Sprint BUNDLE-v1.5: VIX spike threshold subido 5%→10% per TR-Juan-088
# (trade frequency > selectividad, WAIT solo en eventos extremos).
_VIX_SPIKE_VEL_PCT = 10.0
_MACRO_EVENT_BLOCK_DAYS = 1
# Sprint 15: defaults overridables vía strategy_overrides.
_ENTRY_WINDOW_START_DEFAULT = time(9, 30)
# Sprint INTRADAY-THETA-PIVOT 2026-06-04: full session entries 9:30-15:30 ET.
# Last 30min (15:30-16:00) reservados para exits/monitor, no nuevas entries.
# Era 12:00 — alineado con DTE 0-4 intraday theta harvest puro.
_ENTRY_WINDOW_END_DEFAULT = time(15, 30)

# OPS-1 2026-06-01: LLM scope allowlist. TQQQ excluded (3x leverage requires
# KB additions specific). TODO post-close: hacer config-driven via
# strategy_params.llm_engine.enabled_tickers.
LLM_ENABLED_TICKERS = {"SPY", "QQQ", "IWM"}


def _parse_hhmm(s: str) -> Optional[time]:
    """Sprint 15: parse "HH:MM" → datetime.time. Returns None si inválido."""
    try:
        hh, mm = s.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _get_entry_window(bot_instance=None) -> Tuple[time, time]:
    """Sprint 15: lee override entry_window_{start,end}_et de
    bot_instance._strategy_overrides si existe, sino default 09:30-15:30 ET
    (BUNDLE-v1.5: ventana ampliada per TR-Juan-072 AXIOMA intraday theta).

    Defensivo: cualquier fallo retorna defaults.
    """
    if bot_instance is not None and hasattr(bot_instance, "_strategy_overrides"):
        overrides = getattr(bot_instance, "_strategy_overrides", {}) or {}
        start_str = overrides.get("strategy_params.exits_advanced.entry_window_start_et")
        end_str = overrides.get("strategy_params.exits_advanced.entry_window_end_et")
        start = _parse_hhmm(start_str) if start_str else None
        end = _parse_hhmm(end_str) if end_str else None
        return (
            start if start else _ENTRY_WINDOW_START_DEFAULT,
            end if end else _ENTRY_WINDOW_END_DEFAULT,
        )
    return _ENTRY_WINDOW_START_DEFAULT, _ENTRY_WINDOW_END_DEFAULT


def should_call_llm(
    snapshot: Dict[str, Any],
    tickers_enabled: Dict[str, bool],
    max_positions: int,
    current_positions_count: int,
    now_et: Optional[datetime] = None,
    bot_instance: Optional[Any] = None,
) -> Tuple[bool, str]:
    """
    Pre-filter para decidir si llamar al LLM Engine.

    Returns (should_call, reason).

    Reglas (NO_CALL si alguna aplica):
    0. Ticker not in {SPY, QQQ, IWM} — TQQQ excluded (3x leverage, KB additions pending)
    1. Ticker no enabled en tickers_enabled
    2. Hora fuera de ventana 9:30-15:30 ET (para entries) — permitir para
       exits si has_open_positions (BUNDLE-v1.5)
    3. VIX velocity 30m > 10% (spike violento) — solo bloquea si NO hay positions
    4. Macro event en <=1 dia (FOMC/CPI/NFP)
    5. Bot ya en max positions

    Si pasa, return (True, "ok") y el caller invoca el LLM.
    """
    ticker = snapshot.get("ticker", "?")

    # Rule 0 (OPS-1 2026-06-01): LLM scope expand a SPY/QQQ/IWM.
    # TQQQ excluded — 3x leverage requires KB additions specific (sprint follow-up).
    # TODO post-close: hacer enabled_tickers config-driven via
    # strategy_params.llm_engine.enabled_tickers.
    if ticker.upper() not in LLM_ENABLED_TICKERS:
        return False, (
            f"ticker_outside_llm_scope: {ticker} "
            f"(enabled: {sorted(LLM_ENABLED_TICKERS)})"
        )

    # Rule 1: ticker enabled
    if not tickers_enabled.get(ticker, False):
        return False, f"ticker_disabled: {ticker}"

    now_et = now_et or datetime.now(ET)
    now_time = now_et.time()
    has_positions = snapshot.get("has_open_positions", False)
    # Sprint 15: ventana editable vía strategy_overrides (con fallback a default)
    _window_start, _window_end = _get_entry_window(bot_instance)
    is_entry_window = _window_start <= now_time <= _window_end

    # Rule 2: ventana horaria — permitir fuera de ventana SI hay open positions (eval exits)
    if not is_entry_window and not has_positions:
        return False, f"outside_entry_window_no_positions ({now_time.strftime('%H:%M')} ET)"

    # Rule 3: VIX spike — solo bloquea entries (LLM debe ver spike si hay positions)
    vix_vel = abs(float(snapshot.get("vix_velocity_30m_pct", 0.0)))
    if vix_vel > _VIX_SPIKE_VEL_PCT and not has_positions:
        return False, f"vix_spike_no_positions ({vix_vel:.1f}%)"

    # Rule 4: macro event proximo
    for field in ["days_to_next_fomc", "days_to_next_cpi", "days_to_next_nfp"]:
        days = snapshot.get(field)
        if days is not None and days <= _MACRO_EVENT_BLOCK_DAYS:
            if not has_positions:
                return False, f"macro_event_imminent: {field}={days}d"

    # Rule 5: max positions reached (solo bloquea entries, no exits)
    if current_positions_count >= max_positions and not has_positions:
        return False, f"max_positions_reached ({current_positions_count}/{max_positions})"

    return True, "ok"


def llm_decision_to_scan_params(
    decision: Dict[str, Any],
    ticker: str,
) -> Optional[Dict[str, Any]]:
    """
    Convierte la Decision del LLM a parametros para scan_theta_harvest_tranches.

    Returns dict con params si verdict es entry-action.
    Returns None si verdict es WAIT / CLOSE_POSITIONS (no entry).

    Output keys son hints — el caller decide si forzarlos en scan o usar
    como referencia.

    Para IRON_CONDOR_SEQUENTIAL ambos strikes/deltas (put+call) se exponen.
    Para SELL_PUT / SELL_CALL solo un short strike/delta.
    """
    verdict = decision.get("verdict", "WAIT")
    if verdict in ("WAIT", "CLOSE_POSITIONS"):
        return None

    spread_type_map = {
        "SELL_PUT": "put_credit_spread",
        "SELL_CALL": "call_credit_spread",
        "IRON_CONDOR_SEQUENTIAL": "iron_condor",
    }
    spread_type = spread_type_map.get(verdict)
    if not spread_type:
        logger.warning(f"[integration] unknown verdict: {verdict}")
        return None

    strikes = decision.get("strikes", {}) or {}
    deltas = decision.get("deltas", {}) or {}

    params: Dict[str, Any] = {
        "ticker": ticker,
        "spread_type": spread_type,
        "dte_preference": int(decision.get("dte_target", 1)),
        "llm_verdict": verdict,
        "llm_confidence": int(decision.get("confidence", 0)),
        "llm_profit_target_pct": int(decision.get("profit_target_pct", 50)),
        "llm_tacit_rules": decision.get("tacit_rules_applied", []),
        "llm_main_reason": decision.get("main_reason", ""),
        "force_entry": True,  # LLM ya decidio, scan solo valida
    }

    if verdict == "IRON_CONDOR_SEQUENTIAL":
        # IC: ambos strikes/deltas relevantes
        params["llm_put_strike"] = strikes.get("put_strike")
        params["llm_call_strike"] = strikes.get("call_strike")
        params["llm_put_delta"] = deltas.get("put_delta")
        params["llm_call_delta"] = deltas.get("call_delta")
    else:
        # SELL_PUT / SELL_CALL: un solo short strike/delta
        params["llm_short_strike"] = strikes.get("put_strike") or strikes.get("call_strike")
        params["llm_target_delta"] = deltas.get("put_delta") or deltas.get("call_delta")

    # ── OPS-3: LLM Risk Arbiter override del NO_TRADE pivot skip ─────────
    # Explicit flag desde decision.meta.force_entry (cuando KB v1.3 con
    # TR-Juan-NEW especifica condiciones para override).
    _meta = decision.get("meta") or {}
    _force_entry_explicit = bool(_meta.get("force_entry", False))

    # Heurística fallback (sin TR-Juan-NEW en KB v1.2 todavía):
    # SELL_* con confidence >= 7 → tratamos como implicit force_entry.
    # Permite override hoy mismo sin esperar KB v1.3.
    _conf = int(decision.get("confidence", 0) or 0)
    _implicit_force = (
        verdict in ("SELL_PUT", "SELL_CALL", "IRON_CONDOR_SEQUENTIAL")
        and _conf >= 7
    )

    params["llm_override_no_trade"] = _force_entry_explicit or _implicit_force

    if params["llm_override_no_trade"]:
        logger.info(
            f"[llm→scan] {ticker} force_entry=True "
            f"(explicit={_force_entry_explicit}, "
            f"implicit_conf={_conf}>=7={_implicit_force})"
        )

    return params


def decision_indicates_exit(decision: Dict[str, Any]) -> bool:
    """Helper rapido: True si verdict es CLOSE_POSITIONS."""
    return decision.get("verdict") == "CLOSE_POSITIONS"
