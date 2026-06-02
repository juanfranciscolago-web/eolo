# ============================================================
#  CROP — Cloud Run entry point (Theta Harvest only)
#
#  Flask sirve /health para que Cloud Run sepa que el servicio
#  está vivo. El bot (asyncio) corre en un thread separado.
#
#  WATCHDOG: si el event loop del bot termina por cualquier
#  excepción, el watchdog lo reinicia automáticamente después
#  de RESTART_DELAY segundos. Esto replica lo que hacía el
#  loop de auto-restart de start_crop.sh cuando corría local.
# ============================================================
import asyncio
import json
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_file
from loguru import logger

import crop_main

# ─── Sprint OBS-1: build/version metadata cached al import ─────────────
import subprocess as _subprocess
import sys as _sys


def _cached_git_field(cmd_args: list, default: str = "unknown") -> str:
    """Lee un campo de git al import. Retorna `default` si el subprocess falla
    (caso típico: container sin .git ni git binary)."""
    try:
        result = _subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode != 0:
            return default
        return (result.stdout or "").strip() or default
    except Exception:
        return default


_GIT_COMMIT: str = (
    os.environ.get("GIT_COMMIT")
    or _cached_git_field(["git", "rev-parse", "--short", "HEAD"])
)
_GIT_BRANCH: str = (
    os.environ.get("GIT_BRANCH")
    or _cached_git_field(["git", "rev-parse", "--abbrev-ref", "HEAD"])
)
_BUILD_TIMESTAMP_EPOCH: float = time.time()
_BUILD_TIMESTAMP_ISO: str = (
    os.environ.get("BUILD_TIMESTAMP")
    or datetime.utcfromtimestamp(_BUILD_TIMESTAMP_EPOCH).strftime("%Y-%m-%dT%H:%M:%SZ")
)
_KB_VERSION: str = "v1.2"
_LLM_ENGINE_URL_DEFAULT: str = "https://llm-engine-service-nmjz4iwcea-uc.a.run.app"


# Sprint S3.A: lock para edits in-memory de _strategy_overrides
# Asegura no race conditions con bot loop (asyncio thread)
_state_edit_lock = threading.Lock()

# Sprint S3.A: allowlist de paths editables vía /api/state/edit
# Excluye campos que viven en /api/config (entry_hour_et, max_positions, daily_loss_cap_pct)
EDITABLE_ALLOWLIST_PREFIXES = [
    "strategy_params.exits_advanced.",        # 14/15 (excluye entry_hour_et)
    "strategy_params.delta_by_risk.",          # 8 fieldIds
    "strategy_params.ticker_config.",          # 20 fieldIds
    "strategy_params.vix_credit_table[",       # 30 fieldIds (excluye vix_ceil)
    "strategy_params.target_dtes.by_weekday.", # 7 per-day arrays
    "strategy_params.position_sizing.",         # Sprint S3.5: 100 paths (5d × 4t × 5dte)
    "strategy_params.llm_engine.",              # Bloque 4 Fase 2: 6 paths LLM feature flag
]
EDITABLE_BLOCKLIST_EXACT = {
    # Overlap con /api/config — readonly UI, rechazo backend
    "strategy_params.exits_advanced.entry_hour_et",
    "config.max_positions",
    "config.daily_loss_cap_pct",
    # vix_ceil readonly (decisión arquitectónica B6)
    # Estos no matchean por prefix pero por si acaso:
}

def _is_allowed_path(path: str) -> bool:
    """Verifica si un fieldId path está en allowlist."""
    if path in EDITABLE_BLOCKLIST_EXACT:
        return False
    for prefix in EDITABLE_ALLOWLIST_PREFIXES:
        if path.startswith(prefix):
            # Check específico para vix_credit_table que excluye vix_ceil
            if "vix_credit_table[" in path and path.endswith(".vix_ceil"):
                return False
            return True
    return False


# Sprint S3.D: range bounds por field exacto (path completo → (min, max, type)).
# Para paths variables (con índices o ticker) la lógica se aplica en
# _validate_value_range según el último segmento del path.
RANGE_BOUNDS_EXACT = {
    "strategy_params.exits_advanced.vix_max_entry":              (10.0, 80.0, float),
    "strategy_params.exits_advanced.vix_spike_delta":            (0.5, 20.0, float),
    "strategy_params.exits_advanced.vvix_panic_threshold":       (80.0, 200.0, float),
    "strategy_params.exits_advanced.delta_drift_max":            (0.1, 0.5, float),
    "strategy_params.exits_advanced.spy_drop_pct_30m":           (0.1, 5.0, float),
    "strategy_params.exits_advanced.stop_loss_mult":             (1.0, 3.0, float),
    "strategy_params.exits_advanced.profit_target_pct":          (0.1, 0.95, float),
    "strategy_params.exits_advanced.entry_window_minutes":       (15, 240, int),
    "strategy_params.exits_advanced.min_minutes_to_exp":         (5, 120, int),
    "strategy_params.exits_advanced.vix_velocity_threshold_up_pct":   (0.005, 0.2, float),
    "strategy_params.exits_advanced.vix_velocity_threshold_down_pct": (-0.2, -0.005, float),
    "strategy_params.exits_advanced.vix_velocity_window_seconds":     (30, 600, int),
    # Sprint 7 (tech debt #22) — LLM Engine numeric keys.
    # Booleans (enabled), strings (url) y dicts (tickers_enabled) no aplican aquí.
    "strategy_params.llm_engine.haiku_threshold":             (1, 10, int),       # confidence 1-10
    "strategy_params.llm_engine.cache_ttl_seconds":           (5.0, 300.0, float), # 5s a 5min
    "strategy_params.llm_engine.spread_override_threshold":   (1, 10, int),       # confidence 1-10
    # Sprint 15 — trading window config editable. Tipo (regex_pattern, str).
    "strategy_params.exits_advanced.entry_window_start_et":   (r"^\d{2}:\d{2}$", str),
    "strategy_params.exits_advanced.entry_window_end_et":     (r"^\d{2}:\d{2}$", str),
    "strategy_params.exits_advanced.auto_close_et":           (r"^\d{2}:\d{2}$", str),
}


def _validate_value_range(path: str, value):
    """Sprint S3.D: validate que value está en rango/tipo esperado.
    Returns (ok: bool, reason: str | None).
    """
    # Strict exact match primero
    if path in RANGE_BOUNDS_EXACT:
        entry = RANGE_BOUNDS_EXACT[path]
        # Sprint 15: tuple-2 (regex_pattern, str) vs tuple-3 (lo, hi, type)
        if len(entry) == 2:
            pattern, expected_type = entry
            if expected_type is str:
                if not isinstance(value, str):
                    return False, f"expected string, got {type(value).__name__}"
                import re as _re
                if not _re.match(pattern, value):
                    return False, f"value {value!r} doesn't match pattern {pattern!r}"
                # Validación semántica HH:MM legal (00:00..23:59)
                try:
                    hh, mm = value.split(":")
                    hh_i, mm_i = int(hh), int(mm)
                    if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                        return False, f"value {value!r} not a valid time (HH 0-23, MM 0-59)"
                except (ValueError, AttributeError):
                    return False, f"value {value!r} not parseable as HH:MM"
                return True, None
            # Tuple-2 sin str type → caer al path no-matchea
            return True, None
        lo, hi, expected_type = entry
        if expected_type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                return False, f"expected int, got {type(value).__name__}"
        elif expected_type is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            value = float(value)
        if not (lo <= value <= hi):
            return False, f"value {value} out of range [{lo}, {hi}]"
        return True, None

    # Tranche profit targets: None permitido (sentinel EXP) en cualquier tranche
    if path.startswith("strategy_params.exits_advanced.tranche_profit_targets["):
        if value is None:
            return True, None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"expected number or null, got {type(value).__name__}"
        v = float(value)
        if not (0.0 <= v <= 0.95):
            return False, f"value {v} out of range [0.0, 0.95]"
        return True, None

    # Delta by Risk: e.g. strategy_params.delta_by_risk.LOW[0]
    if path.startswith("strategy_params.delta_by_risk."):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"expected number, got {type(value).__name__}"
        v = float(value)
        if not (0.0 <= v <= 0.5):
            return False, f"value {v} out of range [0.0, 0.5]"
        return True, None

    # Ticker Config: e.g. strategy_params.ticker_config.SPY.spread_width
    if path.startswith("strategy_params.ticker_config."):
        last = path.rsplit(".", 1)[-1]
        if last == "spread_width":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            v = float(value)
            if not (1.0 <= v <= 20.0):
                return False, f"value {v} out of range [1.0, 20.0]"
            return True, None
        if last in ("delta_min_abs", "delta_max_abs"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            v = float(value)
            if not (0.0 <= v <= 0.5):
                return False, f"value {v} out of range [0.0, 0.5]"
            return True, None
        if last == "min_credit":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            v = float(value)
            if not (0.05 <= v <= 5.0):
                return False, f"value {v} out of range [0.05, 5.0]"
            return True, None
        if last == "max_dte":
            if isinstance(value, bool) or not isinstance(value, int):
                return False, f"expected int, got {type(value).__name__}"
            if not (0 <= value <= 30):
                return False, f"value {value} out of range [0, 30]"
            return True, None

    # VIX Credit Table: e.g. strategy_params.vix_credit_table[0].spy
    if path.startswith("strategy_params.vix_credit_table["):
        last = path.rsplit(".", 1)[-1]
        if last in ("spy", "qqq", "iwm", "tqqq"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            v = float(value)
            if not (0.05 <= v <= 5.0):
                return False, f"value {v} out of range [0.05, 5.0]"
            return True, None
        if last == "payoff_mult":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, f"expected number, got {type(value).__name__}"
            v = float(value)
            if not (0.1 <= v <= 3.0):
                return False, f"value {v} out of range [0.1, 3.0]"
            return True, None

    # DTE Schedule: e.g. strategy_params.target_dtes.by_weekday.Mon
    if path.startswith("strategy_params.target_dtes.by_weekday."):
        if not isinstance(value, list):
            return False, f"expected list, got {type(value).__name__}"
        for i, d in enumerate(value):
            if isinstance(d, bool) or not isinstance(d, int):
                return False, f"element [{i}] expected int, got {type(d).__name__}"
            if not (0 <= d <= 4):
                return False, f"element [{i}] value {d} out of range [0, 4]"
        if len(value) != len(set(value)):
            return False, "duplicate DTE values not allowed"
        return True, None

    # Sprint S3.5: position_sizing paths (int 0-50)
    # e.g. strategy_params.position_sizing.Mon.SPY.dte0
    if path.startswith("strategy_params.position_sizing."):
        if isinstance(value, bool):
            return False, "expected int, got bool"
        if not isinstance(value, int):
            return False, f"expected int, got {type(value).__name__}"
        if not (0 <= value <= 50):
            return False, f"value {value} out of range [0, 50]"
        return True, None

    # Path no matchea ningún pattern — allowlist es authoritative
    return True, None


def _validate_cross_field(payload: dict, current_state: dict) -> dict:
    """Sprint S3.D: validations que cruzan múltiples fields.

    Mergea payload sobre current_state y verifica consistency post-edit.
    Returns dict { path: reason } con errores (vacío si OK).

    Validations:
    - Ticker Config: delta_min_abs < delta_max_abs por ticker
    - Tranches: T0 < T1 (T2 puede ser null/EXP)
    - VIX Credit Table: credits + payoff_mult ascendentes por ticker
      (mayor VIX → mayor credit/payoff)
    """
    import copy
    errors = {}

    # Build merged view: current_state + payload
    merged = copy.deepcopy(current_state)
    for path, value in payload.items():
        if not path.startswith("strategy_params."):
            continue
        try:
            _set_path(merged, path[len("strategy_params."):], value)
        except Exception:
            pass

    # === Ticker Config: delta_min < delta_max ===
    tc = merged.get("ticker_config", {})
    for ticker, cfg in tc.items():
        if not isinstance(cfg, dict):
            continue
        dmin = cfg.get("delta_min_abs")
        dmax = cfg.get("delta_max_abs")
        if dmin is not None and dmax is not None and dmin >= dmax:
            path_min = f"strategy_params.ticker_config.{ticker}.delta_min_abs"
            path_max = f"strategy_params.ticker_config.{ticker}.delta_max_abs"
            attributed = False
            for p in (path_min, path_max):
                if p in payload:
                    errors[p] = f"delta_min_abs ({dmin}) must be < delta_max_abs ({dmax}) for {ticker}"
                    attributed = True
                    break
            if not attributed:
                errors[path_min] = f"delta_min_abs ({dmin}) >= delta_max_abs ({dmax}) for {ticker} (existing state inconsistent)"

    # === Tranches T0 < T1 ===
    ea = merged.get("exits_advanced", {})
    tranches = ea.get("tranche_profit_targets")
    if isinstance(tranches, list) and len(tranches) >= 2:
        t0, t1 = tranches[0], tranches[1]
        if t0 is not None and t1 is not None and t0 >= t1:
            path0 = "strategy_params.exits_advanced.tranche_profit_targets[0]"
            path1 = "strategy_params.exits_advanced.tranche_profit_targets[1]"
            attributed = False
            for p in (path0, path1):
                if p in payload:
                    errors[p] = f"T0 ({t0}) must be < T1 ({t1})"
                    attributed = True
                    break
            if not attributed:
                errors[path0] = f"T0 ({t0}) >= T1 ({t1}) (existing state inconsistent)"

    # === VIX Credit Table: credits + payoff_mult ascending por ticker ===
    vct = merged.get("vix_credit_table", [])
    if isinstance(vct, list) and len(vct) >= 2:
        for col in ("spy", "qqq", "iwm", "tqqq", "payoff_mult"):
            for i in range(1, len(vct)):
                prev = vct[i-1].get(col) if isinstance(vct[i-1], dict) else None
                curr = vct[i].get(col) if isinstance(vct[i], dict) else None
                if prev is None or curr is None:
                    continue
                if curr < prev:
                    path_curr = f"strategy_params.vix_credit_table[{i}].{col}"
                    path_prev = f"strategy_params.vix_credit_table[{i-1}].{col}"
                    if path_curr in payload:
                        errors[path_curr] = f"{col} at row {i} ({curr}) must be >= prev row ({prev}) — credits/payoff ascending by VIX"
                        break
                    if path_prev in payload:
                        errors[path_prev] = f"{col} at row {i-1} ({prev}) > row {i} ({curr}) after edit — credits/payoff must ascend by VIX"
                        break

    # Sprint S3.5: total exposure warning check (NO bloquea, solo log)
    # Si el payload contiene position_sizing edits, calcular max contratos por día
    # y loguear warning si la exposure estimada parece elevada (>$5000 nominal).
    has_sizing_edits = any(
        path.startswith("strategy_params.position_sizing.")
        for path in payload.keys()
    )
    if has_sizing_edits:
        try:
            ps = merged.get("position_sizing", {})
            max_day_total = 0
            for day in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri'):
                day_data = ps.get(day, {})
                if not isinstance(day_data, dict):
                    continue
                day_sum = 0
                for ticker_data in day_data.values():
                    if isinstance(ticker_data, dict):
                        for v in ticker_data.values():
                            if isinstance(v, int) and not isinstance(v, bool):
                                day_sum += v
                if day_sum > max_day_total:
                    max_day_total = day_sum
            # Estimación: max_day_total contracts × 3 tranches × ~$50 credit_estimate
            estimated_exposure = max_day_total * 3 * 50
            if estimated_exposure > 5000:
                logger.warning(
                    f"[S3.5 cross-field] high exposure detected: "
                    f"~${estimated_exposure} max-day estimated "
                    f"(max_contracts_per_slot_sum={max_day_total} × 3 tranches × $50). "
                    f"Verify daily_loss_cap allows this."
                )
        except Exception as _exp_e:
            logger.warning(f"[S3.5 cross-field] exposure check failed: {_exp_e}")

    return errors


from theta_harvest.theta_harvest_strategy import (
    VIX_CREDIT_TABLE,
    TICKER_CONFIG,
    TARGET_DTES,
    DELTA_DRIFT_MAX,
    SPY_DROP_PCT_30M,
    VIX_MAX_ENTRY,
    VIX_SPIKE_DELTA,
    VVIX_PANIC_THRESHOLD,
    STOP_LOSS_MULT,
    PROFIT_TARGET_PCT,
    TRANCHE_PROFIT_TARGETS,
    MIN_MINUTES_TO_EXP,
    ENTRY_HOUR_ET,
    ENTRY_WINDOW_MINUTES,
)
from theta_harvest.pivot_analysis import DELTA_BY_RISK

app = Flask(__name__)

# ── Estado global del bot (para health / status) ─────────
bot_status = {
    "running":     False,
    "starts":      0,
    "restarts":    0,
    "last_start":  None,
    "last_stop":   None,
    "last_error":  None,
    "service":     "eolo-bot-crop",
    "mode":        "PAPER",
}


# ── Watchdog: reinicia el bot si muere ────────────────────

def _bot_watchdog():
    """
    Loop infinito que mantiene el bot CROP vivo. Si `crop_main.main()`
    termina (por error, excepción o vuelta clean del event loop),
    espera RESTART_DELAY y lo arranca de nuevo.
    """
    RESTART_DELAY = 15

    while True:
        bot_status["running"]    = True
        bot_status["starts"]    += 1
        bot_status["last_start"] = datetime.utcnow().isoformat()

        logger.info(
            f"🚀 CROP Theta arrancando (intento #{bot_status['starts']})..."
        )

        try:
            # crop_main.main() es async → usar asyncio.run
            asyncio.run(crop_main.main())
            # Si llega acá, main() terminó limpio (raro salvo KeyboardInterrupt)
            logger.warning(
                "⚠️  crop_main.main() terminó sin excepción — reiniciando..."
            )
        except Exception as e:
            bot_status["last_error"] = f"{type(e).__name__}: {e}"
            bot_status["restarts"]  += 1
            logger.error(
                f"💥 Bot CROP crashed (restart #{bot_status['restarts']}): {e}"
            )

        bot_status["running"]  = False
        bot_status["last_stop"] = datetime.utcnow().isoformat()
        logger.info(f"⏳ Reiniciando CROP Theta en {RESTART_DELAY}s...")
        time.sleep(RESTART_DELAY)


# Arrancar el watchdog al importar el módulo (gunicorn carga `main:app` → aquí)
watchdog_thread = threading.Thread(
    target=_bot_watchdog,
    daemon=True,
    name="eolo-v2-watchdog",
)
watchdog_thread.start()
logger.info("🛡️  CROP Theta Watchdog arrancado")


# ── Health / status endpoints ─────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    """
    Cloud Run usa este endpoint para el health check de cada instancia.
    Devolvemos 200 siempre — el watchdog se encarga de reiniciar el bot
    si cae, así que el servicio está "vivo" aunque el bot esté reiniciando.
    """
    return jsonify({
        "status":     "running" if bot_status["running"] else "restarting",
        "service":    bot_status["service"],
        "mode":       bot_status["mode"],
        "starts":     bot_status["starts"],
        "restarts":   bot_status["restarts"],
        "last_start": bot_status["last_start"],
        "last_stop":  bot_status["last_stop"],
        "last_error": bot_status["last_error"],
    }), 200


@app.route("/status")
def status():
    """Info detallada del estado del bot (debug)."""
    payload = dict(bot_status)
    # Exponer estado del circuit breaker de billing (si el bot ya arrancó)
    bot = getattr(crop_main, "bot_instance", None)
    if bot is not None:
        payload["billing"] = {
            "anthropic_billing_paused": bool(getattr(bot, "_anthropic_billing_paused", False)),
            "errors_streak":            int(getattr(bot, "_anthropic_billing_errors", 0)),
            "threshold":                int(getattr(bot, "_anthropic_billing_threshold", 5)),
            "last_error":               str(getattr(bot, "_anthropic_billing_last_err", ""))[:300],
            "last_ts":                  float(getattr(bot, "_anthropic_billing_last_ts", 0.0)),
            "bot_active":               bool(getattr(bot, "_active", True)),
        }
    return jsonify(payload), 200


@app.route("/billing")
def billing():
    """
    Endpoint dedicado del circuit breaker — lo lee el dashboard para el semáforo.
    """
    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({
            "anthropic_billing_paused": False,
            "errors_streak": 0,
            "threshold": 5,
            "bot_active": False,
            "bot_started": False,
        }), 200
    return jsonify({
        "anthropic_billing_paused": bool(getattr(bot, "_anthropic_billing_paused", False)),
        "errors_streak":            int(getattr(bot, "_anthropic_billing_errors", 0)),
        "threshold":                int(getattr(bot, "_anthropic_billing_threshold", 5)),
        "last_error":               str(getattr(bot, "_anthropic_billing_last_err", ""))[:300],
        "last_ts":                  float(getattr(bot, "_anthropic_billing_last_ts", 0.0)),
        "bot_active":               bool(getattr(bot, "_active", True)),
        "bot_started":              True,
    }), 200


@app.route("/api/config", methods=["POST"])
def api_config():
    """
    Persiste la config del modal Theta Harvest a eolo-crop-config/settings.
    El backend la lee cada ciclo vía _poll_settings().
    """
    import json as _json
    from google.cloud import firestore as _fs
    import time as _time

    try:
        data = request.get_json(force=True) or {}
        allowed = {
            # Existentes
            "budget_per_trade", "max_positions", "daily_loss_cap_pct",
            "macro_filter_enabled", "trading_start_et", "trading_end_et",
            "auto_close_et",
            # Sprint 1 (8-may): Bugs E + Q + R + vix_entry_threshold UI
            "iron_condor_enabled", "vix_velocity_enabled",
            "vix_entry_threshold", "entry_hour_et",
            "default_stop_loss_pct", "default_take_profit_pct",
        }
        payload = {k: v for k, v in data.items() if k in allowed}
        if not payload:
            return jsonify({"error": "No valid fields"}), 400

        payload["updated_ts"] = _time.time()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
        db = _fs.Client(project=project_id)
        db.collection("eolo-crop-config").document("settings").set(
            payload, merge=True
        )
        logger.info(f"[API /config] Config updated: {list(payload.keys())}")
        return jsonify({"ok": True, "updated": list(payload.keys())}), 200
    except Exception as e:
        logger.error(f"[API /config] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard")
def dashboard():
    """Sirve el dashboard HTML estático."""
    try:
        dashboard_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "dashboard-crop.html"
        )
        return send_file(dashboard_path, mimetype='text/html')
    except Exception as e:
        return jsonify({"error": f"Dashboard not found: {e}"}), 404


def _pnl_from_bot(bot):
    """Map bot._theta_positions → state.pnl for dashboard Greeks/Charts/Performance/Trades."""
    return {
        "open_list": [{
            "ticker": p.get("ticker"), "option_type": "put" if "put" in (p.get("spread_type") or "") else "call",
            "strike": p.get("short_strike"), "expiration": p.get("expiration"), "qty": p.get("contracts", 1),
            "entry_ts": datetime.utcfromtimestamp(p.get("opened_at") or 0).isoformat(), "entry_price": p.get("net_credit") or 0,
            "strategy": "THETA_HARVEST", "reason": p.get("reason", ""), "dte_slot": p.get("dte_slot"), "tranche_id": p.get("tranche_id"),
            "greeks": {"delta": p.get("short_delta") or 0, "gamma": 0, "theta": 0, "vega": 0},
        } for p in getattr(bot, "_theta_positions", [])],
        "closed": list(getattr(bot, "_theta_closed_positions", [])),
    }


def _set_path(d: dict, path: str, value):
    """Sprint S3.B helper: set value in dict by dotted/bracketed path.
    Examples:
      _set_path(d, "exits_advanced.stop_loss_mult", 1.5)
      _set_path(d, "delta_by_risk.LOW[0]", 0.15)
      _set_path(d, "vix_credit_table[3].spy", 0.45)
      _set_path(d, "target_dtes.by_weekday.Mon", [0,1,2])
    """
    import re as _re
    parts = []
    for chunk in path.split("."):
        m = _re.match(r'^([^\[]+)((?:\[\d+\])+)$', chunk)
        if m:
            parts.append(m.group(1))
            for idx in _re.findall(r'\[(\d+)\]', m.group(2)):
                parts.append(int(idx))
        else:
            parts.append(chunk)

    cur = d
    for i, part in enumerate(parts[:-1]):
        next_part = parts[i + 1]
        if isinstance(part, int):
            if not isinstance(cur, list):
                return
            while len(cur) <= part:
                cur.append({})
            if cur[part] is None:
                cur[part] = {} if not isinstance(next_part, int) else []
            cur = cur[part]
        else:
            if part not in cur or cur[part] is None:
                cur[part] = {} if not isinstance(next_part, int) else []
            cur = cur[part]

    final_key = parts[-1]
    if isinstance(final_key, int):
        if isinstance(cur, list):
            while len(cur) <= final_key:
                cur.append(None)
            cur[final_key] = value
    else:
        if isinstance(cur, dict):
            cur[final_key] = value


def _apply_overrides(snapshot: dict, overrides: dict) -> dict:
    """Sprint S3.B: aplica overrides flat sobre el snapshot de _strategy_params().

    overrides es flat dict {path: value}. Modifica snapshot in-place y lo devuelve.
    Strips "strategy_params." prefix porque snapshot no incluye ese wrapper.
    Si un path no encaja con la estructura, se ignora silenciosamente.
    """
    for path, value in overrides.items():
        if path.startswith("strategy_params."):
            sub_path = path[len("strategy_params."):]
            try:
                _set_path(snapshot, sub_path, value)
            except Exception:
                pass
    return snapshot


def _strategy_params() -> dict:
    """Snapshot read-only de constantes ThetaHarvest. Para /api/state (Sprint S1).
    Sprint S3.B: aplica _strategy_overrides in-memory si bot_instance los tiene."""
    snapshot = {
        "vix_credit_table": [
            {
                "vix_ceil":    (None if vc == float("inf") else vc),
                "spy":         spy, "qqq": qqq, "iwm": iwm, "tqqq": tqqq,
                "payoff_mult": pm,
            }
            for vc, spy, qqq, iwm, tqqq, pm in VIX_CREDIT_TABLE
        ],
        "delta_by_risk": {
            risk: [dmin, dmax]
            for risk, (dmin, dmax) in DELTA_BY_RISK.items()
        },
        "ticker_config": {
            ticker: {
                "spread_width":  cfg.get("spread_width"),
                "delta_min_abs": cfg.get("delta_min_abs"),
                "delta_max_abs": cfg.get("delta_max_abs"),
                "min_credit":    cfg.get("min_credit"),
                "max_dte":       cfg.get("max_dte"),
            }
            for ticker, cfg in TICKER_CONFIG.items()
        },
        "target_dtes": {
            "module_default": TARGET_DTES,
            "by_weekday": {
                "Mon": [0, 1, 2, 3, 4],
                "Tue": [0, 1, 2, 3],
                "Wed": [0, 1, 2],
                "Thu": [0, 1],
                "Fri": [0],
                "Sat": [],
                "Sun": [],
            },
            "_note": "by_weekday se aplica en crop_main.py:3584 _compute_theta_dtes(). module_default es fallback."
        },
        "exits_advanced": {
            "delta_drift_max":          DELTA_DRIFT_MAX,
            "spy_drop_pct_30m":         SPY_DROP_PCT_30M,
            "vix_max_entry":            VIX_MAX_ENTRY,
            "vix_spike_delta":          VIX_SPIKE_DELTA,
            "vvix_panic_threshold":     VVIX_PANIC_THRESHOLD,
            "stop_loss_mult":           STOP_LOSS_MULT,
            "profit_target_pct":        PROFIT_TARGET_PCT,
            "tranche_profit_targets":   TRANCHE_PROFIT_TARGETS,
            "min_minutes_to_exp":       MIN_MINUTES_TO_EXP,
            "entry_hour_et":            ENTRY_HOUR_ET,
            "entry_window_minutes":     ENTRY_WINDOW_MINUTES,
            "vix_velocity_threshold_up_pct":   0.03,
            "vix_velocity_threshold_down_pct": -0.03,
            "vix_velocity_window_seconds":     120,
            "_note": (
                "auto_close ET unificado en config.auto_close_et (ver /api/state.config). "
                "vix_velocity son magic numbers inline en crop_main.py:3522,3536 (no constantes)."
            ),
        },
        # Sprint S3.5: position sizing matrix default — 5 días × 4 tickers × 5 DTEs.
        # Todos en 1 = comportamiento original. Override via POST /api/state/edit.
        # Key format: strategy_params.position_sizing.<DAY>.<TICKER>.dte<N>
        "position_sizing": {
            day: {
                ticker: {f"dte{dte}": 1 for dte in range(5)}
                for ticker in ('SPY', 'QQQ', 'IWM', 'TQQQ')
            }
            for day in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')
        },
    }

    # Sprint S3.B: aplicar overrides in-memory si bot_instance los tiene
    try:
        from crop_main import bot_instance as _bot
        if _bot is not None and hasattr(_bot, '_strategy_overrides'):
            _ovr = getattr(_bot, '_strategy_overrides', {})
            if _ovr:
                snapshot = _apply_overrides(snapshot, _ovr)
    except Exception:
        pass  # never break /api/state on override failure

    return snapshot


@app.route("/api/state")
def api_state():
    """
    Retorna el estado completo del bot CROP (theta positions, P&L, stats, etc.)
    para que el dashboard lo consuma cada 60s.

    Intenta leer el estado desde el archivo state.json local primero (fallback más rápido),
    luego del bot_instance si está disponible.
    """
    bot = getattr(crop_main, "bot_instance", None)

    try:
        # Fallback 1: Leer state.json local si existe (más rápido que acceder al bot)
        state_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "crop_state.json"
        )
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    state["timestamp"] = datetime.utcnow().isoformat()
                    state["_source"] = "local_state_file"
                    state["strategy_params"] = _strategy_params()
                    if bot is not None:
                        state["pnl"] = _pnl_from_bot(bot)
                        # === R1.A hotfix (2026-06-02): Sprint 8.B + Sprint 11 inject ===
                        # Fallback 3 tiene los mismos 2 try/except. Sin esto,
                        # llm_cache + llm_metrics quedaban ausentes cuando
                        # crop_state.json existía. Ver docs/FINDING_R1_LLM_METRICS_REGRESSION.md
                        try:
                            llm_cache = getattr(bot, "_llm_cache", None)
                            if llm_cache is not None and hasattr(llm_cache, "stats"):
                                state.setdefault("stats", {})["llm_cache"] = llm_cache.stats()
                        except Exception as ce:
                            logger.debug(f"[API /state] Could not read llm_cache stats (fallback 1): {ce}")
                        try:
                            llm_metrics = getattr(bot, "_llm_metrics", None)
                            if llm_metrics is not None and hasattr(llm_metrics, "stats"):
                                state.setdefault("stats", {})["llm_metrics"] = llm_metrics.stats()
                        except Exception as me:
                            logger.debug(f"[API /state] Could not read llm_metrics stats (fallback 1): {me}")
                    return jsonify(state), 200
            except Exception as e:
                logger.debug(f"[API /state] Could not read state file: {e}")

        # Fallback 2: Si bot no está disponible, devolver estado vacío
        if bot is None:
            return jsonify({
                "timestamp": datetime.utcnow().isoformat(),
                "status": "bot_not_started",
                "theta": {
                    "positions": [],
                    "stats": {},
                    "enabled": True,
                    "pnl_today": {},
                    "pnl_history": [],
                    "macro": {},
                    "pivots": {},
                },
                "strategy_params": _strategy_params(),
            }), 200

        # Fallback 3: Construir estado desde bot_instance
        state = {
            "timestamp": datetime.utcnow().isoformat(),
            "bot_status": {
                "active": getattr(bot, "_active", True),
                "mode": "PAPER",
                "service": "eolo-bot-crop",
            },
            "theta": {
                "positions": list(getattr(bot, "_theta_positions", [])),
                "stats": {
                    k: v for k, v in getattr(bot, "_theta_stats", {}).items()
                    if not k.startswith("_")
                },
                "enabled": getattr(bot, "_theta_harvest_enabled", True),
                "pnl_today": bot._calc_theta_pnl_today() if hasattr(bot, "_calc_theta_pnl_today") else {},
                "pnl_history": getattr(bot, "_theta_pnl_history", [])[-80:],
                "macro": getattr(bot, "_theta_macro_status", {}),
                "pivots": {},
            },
            "_source": "bot_instance"
        }

        # Intentar agregar pivots si existen
        try:
            pivot_cache = getattr(bot, "_theta_pivot_cache", {})
            state["theta"]["pivots"] = {
                t: {
                    "consensus_risk": getattr(r, "consensus_risk", None),
                    "delta_min": getattr(r, "delta_min", None),
                    "delta_max": getattr(r, "delta_max", None),
                    "price": getattr(r, "price", None),
                }
                for t, r in pivot_cache.items()
            }
        except Exception as pe:
            logger.debug(f"[API /state] Could not build pivots: {pe}")

        state["pnl"] = _pnl_from_bot(bot)
        state["strategy_params"] = _strategy_params()

        # Sprint 8.B: LLM cache stats para observabilidad post-deploy.
        try:
            llm_cache = getattr(bot, "_llm_cache", None)
            if llm_cache is not None and hasattr(llm_cache, "stats"):
                state.setdefault("stats", {})["llm_cache"] = llm_cache.stats()
        except Exception as ce:
            logger.debug(f"[API /state] Could not read llm_cache stats: {ce}")

        # Sprint 11: LLM operational metrics. Eager-init en CropBotTheta.__init__
        # → siempre presente con counters en 0 si todavía no hubo actividad.
        try:
            llm_metrics = getattr(bot, "_llm_metrics", None)
            if llm_metrics is not None and hasattr(llm_metrics, "stats"):
                state.setdefault("stats", {})["llm_metrics"] = llm_metrics.stats()
        except Exception as me:
            logger.debug(f"[API /state] Could not read llm_metrics stats: {me}")

        return jsonify(state), 200

    except Exception as e:
        logger.error(f"[API /state] Error: {e}")
        return jsonify({
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e),
            "status": "error",
            "theta": {
                "positions": [],
                "stats": {},
                "enabled": True,
                "pnl_today": {},
                "pnl_history": [],
                "macro": {},
                "pivots": {},
            }
        }), 200  # Devolver 200 siempre para no romper el dashboard


# ══════════════════════════════════════════════════════════════════════
#  Sprint OBS-1 — GET /api/version (build/version metadata)
# ══════════════════════════════════════════════════════════════════════
# ── Module-level cache para /api/version engine health proxy (fix #90) ──
_ENGINE_HEALTH_TTL: int = int(os.environ.get("ENGINE_HEALTH_CACHE_TTL", "300"))
_ENGINE_HEALTH_TIMEOUT: int = int(os.environ.get("ENGINE_HEALTH_TIMEOUT", "5"))
_engine_health_cache: dict = {}  # {engine_url: {"ts": float, "result": dict}}


def _fetch_engine_health(engine_url: str) -> dict:
    """Best-effort proxy de /health del LLM Engine con cache TTL.

    Fix #90: el handler /api/version hacia una llamada fresh cada hit
    con timeout=3s, lo que producia 'unreachable' frecuente durante
    cold starts del engine. Ahora:
      - Cache TTL 5 min (env ENGINE_HEALTH_CACHE_TTL).
      - Timeout 5s (env ENGINE_HEALTH_TIMEOUT) cuando hay que refetchear.
      - Si el refetch falla y hay cache stale -> retornar stale con
        status='stale' + last_check_age_seconds.
    """
    now = time.time()
    cached = _engine_health_cache.get(engine_url)
    if cached and (now - cached["ts"]) < _ENGINE_HEALTH_TTL:
        return cached["result"]

    try:
        from google.auth.transport import requests as _ga_requests
        from google.oauth2 import id_token as _id_token
        token = _id_token.fetch_id_token(_ga_requests.Request(), engine_url)
    except Exception as _e:
        return {"status": "auth_unavailable", "model": "unknown", "error": str(_e)[:200]}

    try:
        import urllib.request as _ur
        req = _ur.Request(
            f"{engine_url.rstrip('/')}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        with _ur.urlopen(req, timeout=_ENGINE_HEALTH_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        result = json.loads(body)
        _engine_health_cache[engine_url] = {"ts": now, "result": result}
        return result
    except Exception as _e:
        if cached:
            stale_result = dict(cached["result"])
            stale_result["status"] = "stale"
            stale_result["last_check_age_seconds"] = round(now - cached["ts"], 1)
            stale_result["last_error"] = str(_e)[:200]
            return stale_result
        return {"status": "unreachable", "error": str(_e)[:200]}



@app.route("/api/version")
def api_version():
    """Sprint OBS-1: build/version metadata para live debugging."""
    engine_url = os.environ.get("LLM_ENGINE_URL", _LLM_ENGINE_URL_DEFAULT)
    return jsonify({
        "git_commit":         _GIT_COMMIT,
        "git_branch":         _GIT_BRANCH,
        "build_timestamp":    _BUILD_TIMESTAMP_ISO,
        "kb_version":         _KB_VERSION,
        "llm_engine_url":     engine_url,
        "llm_engine_health":  _fetch_engine_health(engine_url),
        "python_version":     _sys.version.split()[0],
        "bot_uptime_seconds": round(time.time() - _BUILD_TIMESTAMP_EPOCH, 1),
    }), 200


# ══════════════════════════════════════════════════════════════════════
#  Sprint OBS-2 (backend) — GET /api/trades?date&limit&ticker&...
# ══════════════════════════════════════════════════════════════════════
import re as _re
_UUID_RE = _re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@app.route("/api/trades")
def api_trades():
    """Sprint OBS-2 backend: trades UUID del día con full decision_meta.

    Filtra automáticamente el day-doc legacy (sólo IDs con shape UUID).
    Rango temporal por timestamp_open en zona ET (DST-aware via ZoneInfo).
    """
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    # ── Parse params ──
    date_str = (request.args.get("date") or "").strip()
    try:
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = datetime.now(_ET).date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    except (ValueError, TypeError):
        return jsonify({"error": "limit must be integer"}), 400

    ticker_filter = ((request.args.get("ticker") or "").strip().upper()) or None
    include_closed = (request.args.get("include_closed", "true").lower() != "false")
    include_open   = (request.args.get("include_open",   "true").lower() != "false")

    # ── Firestore query ──
    try:
        from google.cloud import firestore as _fs
        from llm_gate.trade_logger import FIRESTORE_COLLECTION
    except ImportError as _e:
        return jsonify({"error": f"firestore_import_failed: {_e}"}), 500

    start_dt = datetime(target_date.year, target_date.month, target_date.day,
                        0, 0, 0, tzinfo=_ET)
    end_dt   = datetime(target_date.year, target_date.month, target_date.day,
                        23, 59, 59, tzinfo=_ET)
    start_iso = start_dt.isoformat()
    end_iso   = end_dt.isoformat()
    date_prefix = target_date.isoformat()

    try:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
        db = _fs.Client(project=project)
        coll = db.collection(FIRESTORE_COLLECTION)
        query = (
            coll
            .where(filter=_fs.FieldFilter("timestamp_open", ">=", start_iso))
            .where(filter=_fs.FieldFilter("timestamp_open", "<=", end_iso))
            .order_by("timestamp_open", direction=_fs.Query.DESCENDING)
            .limit(limit * 3)  # buffer para filtrar legacy day-doc
        )

        trades: list = []
        for doc in query.stream():
            if not _UUID_RE.match(doc.id):
                continue  # skip day-doc legacy
            data = doc.to_dict() or {}
            data.setdefault("trade_id", doc.id)
            if ticker_filter and (data.get("ticker") or "").upper() != ticker_filter:
                continue
            has_close = data.get("timestamp_close") is not None
            if has_close and not include_closed:
                continue
            if (not has_close) and not include_open:
                continue
            trades.append(data)
            if len(trades) >= limit:
                break
    except Exception as _e:
        logger.error(f"[api_trades] query failed: {_e}")
        return jsonify({"error": f"query_failed: {str(_e)[:200]}"}), 500

    return jsonify({
        "date":   date_prefix,
        "count":  len(trades),
        "limit":  limit,
        "trades": trades,
    }), 200


@app.route("/daily-open-reset", methods=["GET", "POST"])
def daily_open_reset():
    """
    Disparado por Cloud Scheduler a las 9:30am ET (lunes–viernes).
    1. Cierra todas las posiciones abiertas (theta harvest + otras)
    2. Limpia _theta_positions y _theta_slots → bot re-entra fresh
    3. Limpia el doc de trades de ayer en Firestore (P&L del día vuelve a $0)
    4. Loguea el reset para auditoría

    Idempotente: si no hay posiciones abiertas, no hace nada dañino.
    """
    import asyncio as _asyncio
    from datetime import datetime, timedelta, timezone
    from google.cloud import firestore as _fs
    import os as _os

    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({"ok": False, "error": "bot_instance no disponible aún"}), 503

    results = {}

    # ── 1. Cerrar todas las posiciones abiertas ───────────────
    try:
        loop = getattr(bot, "_loop", None)
        if loop and loop.is_running():
            future = _asyncio.run_coroutine_threadsafe(
                bot._execute_close_all(reason="daily-open-reset"), loop
            )
            future.result(timeout=30)
            results["close_all"] = "ok"
        else:
            results["close_all"] = "loop no disponible — skip"
    except TimeoutError:
        results["close_all"] = "timeout 30s — coroutine sigue en background"
    except Exception as e:
        results["close_all"] = f"error: {e!r}"

    # ── 2. Limpiar estado de theta harvest ────────────────────
    try:
        bot._theta_positions.clear()
        bot._theta_slots.clear()
        bot._theta_stats = {"credit_total": 0, "closed_pnl": 0}
        bot._theta_closed_positions = []
        results["theta_reset"] = "ok"
    except Exception as e:
        results["theta_reset"] = f"error: {e}"

    # ── 3. Limpiar P&L de ayer en Firestore (archive antes de borrar) ─────
    try:
        project_id = _os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
        db = _fs.Client(project=project_id)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        src_ref = db.collection("eolo-crop-trades").document(yesterday)
        snap = src_ref.get()
        if snap.exists:
            db.collection("eolo-crop-trades-archive").document(yesterday).set(
                snap.to_dict(), merge=False
            )
            src_ref.delete()
            results["firestore_cleanup"] = f"archivado eolo-crop-trades/{yesterday} → eolo-crop-trades-archive/{yesterday}"
        else:
            results["firestore_cleanup"] = f"skip — no existe eolo-crop-trades/{yesterday}"
    except Exception as e:
        results["firestore_cleanup"] = f"error: {e}"

    # ── 4. Resetear daily loss cap status ─────────────────────
    try:
        bot._daily_loss_cap_status = {}
        bot._daily_loss_cap_log_ts = 0.0
        results["daily_cap_reset"] = "ok"
    except Exception as e:
        results["daily_cap_reset"] = f"error: {e}"

    logger.warning(
        f"[DAILY_OPEN_RESET] ✅ Reset completado a las 9:30am ET | {results}"
    )
    return jsonify({"ok": True, "results": results}), 200


# ── Entry point local (dev) ───────────────────────────────
# En Cloud Run se arranca con gunicorn; este if solo sirve para correr
# `python main.py` en el Mac como alternativa a start_eolo.sh.

@app.route("/api/state/edit", methods=["POST"])
def api_state_edit():
    """Sprint S3.A — POST endpoint para editar strategy_params in-memory.

    Body JSON: { "path.to.field": value, ... }

    Behavior:
    - Validation all-or-nothing: si algún path falla, rechaza todo
    - Allowlist estricta (excluye campos de /api/config)
    - Aplica a bot_instance._strategy_overrides + persiste a Firestore (Sprint S3.X)
    - threading.Lock para concurrencia con bot loop

    Returns:
    - 200 { "ok": True, "applied": N, "overrides": {...} } si éxito
    - 422 { "ok": False, "errors": {path: reason, ...} } si validation falla
    - 503 si bot_instance no inicializado todavía
    """
    from crop_main import bot_instance

    if bot_instance is None:
        return jsonify({"ok": False, "error": "bot_instance not initialized"}), 503

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as e:
        return jsonify({"ok": False, "error": f"invalid JSON: {e}"}), 400

    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "expected dict {path: value}"}), 400

    errors = {}
    accepted = {}

    # Capa 1 — Validación de paths (allowlist + tipo base)
    for path, value in payload.items():
        if not isinstance(path, str):
            errors[str(path)] = "path must be string"
            continue
        if not _is_allowed_path(path):
            errors[path] = "path not in allowlist (likely a /api/config field, configure there)"
            continue
        if not isinstance(value, (int, float, list, str, bool, type(None))):
            errors[path] = f"unsupported value type: {type(value).__name__}"
            continue
        accepted[path] = value

    # Capa 2 — Sprint S3.D: validation de rango/tipo por field
    for path, value in list(accepted.items()):
        ok, reason = _validate_value_range(path, value)
        if not ok:
            errors[path] = reason
            del accepted[path]

    # Capa 3 — Sprint S3.D: cross-field validation (solo si capas 1+2 limpias)
    if not errors:
        try:
            current_state = _strategy_params()
            cross_errors = _validate_cross_field(accepted, current_state)
            errors.update(cross_errors)
        except Exception as _cross_e:
            logger.warning(f"[API /state/edit] cross-field validation skipped: {_cross_e}")

    # All-or-nothing: si hay errors, rechazar todo
    if errors:
        return jsonify({"ok": False, "errors": errors, "accepted_count": len(accepted)}), 422

    # Aplicar al state in-memory bajo lock
    with _state_edit_lock:
        if not hasattr(bot_instance, '_strategy_overrides'):
            bot_instance._strategy_overrides = {}

        for path, value in accepted.items():
            bot_instance._strategy_overrides[path] = value

        # Sprint S3.1-A: sync instance vars que las strategy fn leen via kwargs
        # (stop_loss_mult, tranche_profit_targets). Safe-call: si el método no
        # existe (revisión vieja del bot), seguimos sin error.
        _apply_fn = getattr(bot_instance, '_apply_strategy_overrides_to_instance_vars', None)
        if callable(_apply_fn):
            try:
                _apply_fn()
            except Exception as _e:
                logger.warning(f"[API /state/edit] _apply_strategy_overrides_to_instance_vars failed: {_e}")

        # Snapshot del state actual para response
        current_overrides = dict(bot_instance._strategy_overrides)

        # Sprint S3.X: espejar overrides a Firestore para que sobrevivan restart.
        # Safe-call: si Firestore falla, el in-memory ya quedó aplicado.
        try:
            from google.cloud import firestore as _fs
            import time as _t
            _db = _fs.Client()
            _db.collection("eolo-crop-config").document("strategy_overrides").set({
                "overrides": dict(bot_instance._strategy_overrides),
                "updated_ts": _t.time(),
            })
        except Exception as _e:
            logger.warning(f"[API /state/edit] persist overrides Firestore falló: {_e}")

    return jsonify({
        "ok": True,
        "applied": len(accepted),
        "overrides": current_overrides,
        "note": "persisted to Firestore (eolo-crop-config/strategy_overrides); restored at boot + poll."
    }), 200


# ===========================================================================
# Sprint T3 (Master Plan v2.1 sec 9.2): Manual close endpoints
# ===========================================================================
import time as _t3_time
import asyncio as _t3_asyncio
from backup.firestore_writer import log_system_event as _t3_log_event

_pending_close_all_tokens: dict[str, float] = {}  # token → expiry_ts


def _t3_validate_confirm_token(token: str) -> bool:
    """One-shot validator: token must exist and not be expired (TTL set at issue)."""
    expiry = _pending_close_all_tokens.pop(token, None)
    if expiry is None:
        return False
    if _t3_time.time() > expiry:
        return False
    return True


def _t3_position_id(pos: dict) -> str:
    """Stable identifier for a position dict (uses 'symbol' which is the OCC code)."""
    return str(pos.get("symbol") or pos.get("id") or "")


def _t3_dte_for(pos: dict) -> int:
    """DTE for a position from its expiration string (best-effort, returns 999 if N/A)."""
    exp = pos.get("expiration")
    if not exp:
        return 999
    try:
        from datetime import datetime as _dt, timezone as _tz
        exp_dt = _dt.strptime(str(exp)[:10], "%Y-%m-%d").replace(tzinfo=_tz.utc)
        return max(0, (exp_dt - _dt.now(_tz.utc)).days)
    except Exception:
        return 999


def _t3_close_position_sync(bot, pos: dict, reason: str) -> dict:
    """Close one position via bot._loop. Returns dict status."""
    try:
        pos_with_reason = {**pos, "reason": reason}
        loop = getattr(bot, "_loop", None)
        if not (loop and loop.is_running()):
            return {"ok": False, "error": "bot loop not running"}
        future = _t3_asyncio.run_coroutine_threadsafe(
            bot._close_position(pos_with_reason), loop
        )
        future.result(timeout=30)
        return {"ok": True, "symbol": _t3_position_id(pos)}
    except TimeoutError:
        return {"ok": False, "error": "timeout 30s — coroutine sigue en background"}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


@app.route("/positions/close_one", methods=["POST"])
def positions_close_one():
    """Close single position by position_id (OCC symbol). Requires reason."""
    body = request.get_json(force=True, silent=True) or {}
    position_id = (body.get("position_id") or "").strip()
    reason = (body.get("reason") or "").strip()
    force = bool(body.get("force", False))
    if not position_id or not reason:
        return jsonify({"error": "position_id and reason required"}), 400

    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({"error": "bot_instance not initialized"}), 503

    positions = list(getattr(bot, "_open_positions", []) or [])
    target = next((p for p in positions if _t3_position_id(p) == position_id), None)
    if target is None:
        return jsonify({"error": f"position_id {position_id} not found", "open_count": len(positions)}), 404

    result = _t3_close_position_sync(bot, target, reason)
    _t3_log_event("MANUAL_CLOSE_ONE", {
        "position_id": position_id,
        "reason": reason,
        "force": force,
        "result": result,
    })
    status_code = 200 if result.get("ok") else 500
    return jsonify({"closed": result.get("ok", False), "position_id": position_id, "result": result}), status_code


@app.route("/positions/close_all/request", methods=["POST"])
def positions_close_all_request():
    """Step 1 of close_all: request a confirm_token valid for 30s."""
    body = request.get_json(force=True, silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason required"}), 400
    token = f"CLOSE_ALL_{int(_t3_time.time() * 1000)}"
    _pending_close_all_tokens[token] = _t3_time.time() + 30
    return jsonify({"confirm_token": token, "expires_in_sec": 30}), 200


@app.route("/positions/close_all", methods=["POST"])
def positions_close_all():
    """Step 2 of close_all: requires confirm_token + reason. Closes ALL open positions."""
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("confirm_token") or ""
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason required"}), 400
    if not token or not _t3_validate_confirm_token(token):
        return jsonify({"error": "invalid or expired confirm_token"}), 403

    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({"error": "bot_instance not initialized"}), 503

    positions = list(getattr(bot, "_open_positions", []) or [])
    results = []
    for pos in positions:
        res = _t3_close_position_sync(bot, pos, f"close_all: {reason}")
        results.append({"position_id": _t3_position_id(pos), "result": res})
    _t3_log_event("MANUAL_CLOSE_ALL", {
        "reason": reason,
        "positions_closed": len(results),
        "results": results,
    })
    return jsonify({"closed_count": len(results), "results": results}), 200


@app.route("/positions/close_filter", methods=["POST"])
def positions_close_filter():
    """Close positions matching filter (ticker, dte_max). Requires reason."""
    body = request.get_json(force=True, silent=True) or {}
    flt = body.get("filter") or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason required"}), 400

    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({"error": "bot_instance not initialized"}), 503

    positions = list(getattr(bot, "_open_positions", []) or [])
    ticker_f = flt.get("ticker")
    dte_max = flt.get("dte_max")

    matched = []
    for pos in positions:
        if ticker_f and pos.get("ticker") != ticker_f:
            continue
        if dte_max is not None and _t3_dte_for(pos) > int(dte_max):
            continue
        matched.append(pos)

    results = []
    for pos in matched:
        res = _t3_close_position_sync(bot, pos, f"close_filter: {reason}")
        results.append({"position_id": _t3_position_id(pos), "result": res})
    _t3_log_event("MANUAL_CLOSE_FILTER", {
        "filter": flt,
        "reason": reason,
        "matched_count": len(matched),
        "results": results,
    })
    return jsonify({"matched_count": len(matched), "results": results}), 200


# ===========================================================================
# Sprint T5/F5 (Master Plan v2.1 sec 9.3): /juan/suggest endpoint
# ===========================================================================
@app.route("/juan/suggest", methods=["POST"])
def juan_suggest():
    """Juan ↔ LLM bidirectional channel.

    Body:
    {
      "suggestion_type": "ENTRY" | "EXIT" | "SIZE_DEBATE" | "MANUAL_TRADE_LOG",
      "ticker": "SPY",
      "your_proposal": {...},
      "your_reasoning": "...",
      "request_llm_opinion": true
    }

    Response per Master Plan sec 9.3 schema.
    """
    body = request.get_json(force=True, silent=True) or {}

    suggestion_type = (body.get("suggestion_type") or "").upper()
    valid_types = {"ENTRY", "EXIT", "SIZE_DEBATE", "MANUAL_TRADE_LOG"}
    if suggestion_type not in valid_types:
        return jsonify({"error": f"suggestion_type must be one of {sorted(valid_types)}"}), 400

    ticker = (body.get("ticker") or "").upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    proposal = body.get("your_proposal") or {}
    reasoning = body.get("your_reasoning", "")

    # Log antes de cualquier llamada al LLM (audit trail).
    # Reusa el alias _t3_log_event ya importado arriba (línea ~1244) en lugar
    # de re-importar; un nuevo alias _t5_log_event sería redundante.
    _t3_log_event("JUAN_SUGGEST", {
        "suggestion_type": suggestion_type,
        "ticker": ticker,
        "proposal": proposal,
        "reasoning": reasoning[:500],
    })

    # Phase 1: skeleton response — full LLM integration en F5 follow-up
    # (deferred: requires prompt_builder.build_juan_suggestion_prompt() — F5.B).
    return jsonify({
        "suggestion_id": f"SUG_{int(time.time() * 1000)}",
        "received": {
            "suggestion_type": suggestion_type,
            "ticker": ticker,
            "proposal": proposal,
        },
        "llm_verdict": "PENDING_F5B_INTEGRATION",
        "confidence_in_juans_call": 0,
        "rules_supporting_juan": [],
        "rules_questioning_juan": [],
        "alternative_proposal": None,
        "final_recommendation": "PENDING_INTEGRATION",
        "would_lead_to_case": None,
        "note": "Phase 1 stub. Full LLM evaluation in F5.B with build_juan_suggestion_prompt().",
    }), 200


# ===========================================================================
# Sprint T8/F6 (Master Plan v2.1 sec 12.12): Mode toggle PAPER↔LIVE doble auth
# ===========================================================================
import secrets as _t8_secrets


_pending_mode_requests: dict = {}  # request_id → {target_mode, reason, expires_ts, confirmation_code}


def _t8_generate_confirmation_code() -> str:
    """Generate 6-digit confirmation code (would be emailed in real impl)."""
    return f"{_t8_secrets.randbelow(1000000):06d}"


@app.route("/system/trading_mode/request", methods=["POST"])
def trading_mode_request():
    """Step 1: request mode change. Returns request_id valid 5 min."""
    body = request.get_json(force=True, silent=True) or {}
    target = (body.get("target_mode") or "").upper()
    reason = (body.get("reason") or "").strip()
    if target not in {"PAPER", "LIVE"}:
        return jsonify({"error": "target_mode must be PAPER or LIVE"}), 400
    if not reason:
        return jsonify({"error": "reason required"}), 400

    req_id = f"REQ_{int(_t3_time.time() * 1000)}_{_t8_secrets.token_hex(4)}"
    code = _t8_generate_confirmation_code()
    _pending_mode_requests[req_id] = {
        "target_mode": target,
        "reason": reason,
        "expires_ts": _t3_time.time() + 300,  # 5 min
        "confirmation_code": code,
    }
    _t3_log_event("MODE_REQUEST", {
        "request_id": req_id,
        "target_mode": target,
        "reason": reason,
        "code_sent_via": "stub_logged_only",
    })

    # Phase 1: code returned in response (in real impl, email-only)
    return jsonify({
        "request_id": req_id,
        "expires_in_seconds": 300,
        "confirmation_code_preview": code,  # STUB: remove in real deploy
        "note": "Phase 1 stub: code returned. F-follow-up sends via email only.",
    }), 200


@app.route("/system/trading_mode/confirm", methods=["POST"])
def trading_mode_confirm():
    """Step 2: confirm with request_id + code (received separately)."""
    body = request.get_json(force=True, silent=True) or {}
    req_id = body.get("request_id")
    code = body.get("confirmation_code")

    if not req_id or not code:
        return jsonify({"error": "request_id and confirmation_code required"}), 400

    pending = _pending_mode_requests.get(req_id)
    if pending is None:
        return jsonify({"error": "request_id not found or expired"}), 404

    if _t3_time.time() > pending["expires_ts"]:
        _pending_mode_requests.pop(req_id, None)
        return jsonify({"error": "request expired (>5 min)"}), 410

    if pending["confirmation_code"] != code:
        return jsonify({"error": "invalid confirmation_code"}), 403

    # One-shot
    _pending_mode_requests.pop(req_id, None)
    target = pending["target_mode"]

    _t3_log_event("MODE_SWITCHED", {
        "request_id": req_id,
        "target_mode": target,
        "switched_by": "juan",  # in real impl: extract from auth
    })

    # Real impl: persist target_mode to Firestore + signal bot to reload config
    return jsonify({
        "mode": target,
        "switched_at": datetime.utcnow().isoformat() + "Z",
        "switched_by": "juan",
        "note": "Phase 1 stub: mode not actually switched. F-follow-up persists + reloads bot.",
    }), 200


@app.route("/system/trading_mode/revert_to_paper", methods=["POST"])
def trading_mode_revert():
    """Emergency revert to PAPER — one-click, no confirmation needed."""
    _t3_log_event("MODE_REVERTED_TO_PAPER", {
        "at": datetime.utcnow().isoformat() + "Z",
        "by": "juan",
    })
    return jsonify({
        "mode": "PAPER",
        "switched_at": datetime.utcnow().isoformat() + "Z",
        "note": "One-click revert. Always available.",
    }), 200


# ===========================================================================
# Sprint T9 (Master Plan v2.1 sec 11.4): Chat feedback nocturno endpoints
# ===========================================================================
@app.route("/journal/chat/start", methods=["POST"])
def journal_chat_start():
    """Open new feedback session. Returns session_id."""
    from learning.feedback_chat.session_manager import open_feedback_session
    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get("date")
    session_id = open_feedback_session(date_str)
    return jsonify({"session_id": session_id, "status": "OPEN"}), 200


@app.route("/journal/chat/message", methods=["POST"])
def journal_chat_message():
    """Append message turn to feedback session."""
    from learning.feedback_chat.session_manager import add_message_to_session
    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    role = (body.get("role") or "user").lower()
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    ok = add_message_to_session(date_str, role, content)
    return jsonify({"appended": ok, "date": date_str}), 200 if ok else 500


@app.route("/journal/chat/close", methods=["POST"])
def journal_chat_close():
    """Close feedback session."""
    from learning.feedback_chat.session_manager import close_session
    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    summary = close_session(date_str)
    return jsonify(summary), 200


@app.route("/journal/today", methods=["GET"])
def journal_today():
    """Generate nightly journal for today on-demand.

    Bot CROP container NO incluye el package llm_engine (deploys separados:
    eolo-crop vs llm-engine-service). Para obtener la lista de rule_ids:
      1. Si el package está disponible localmente (dev/test), úsalo
      2. Si no, intentá fetch al engine HTTP /kb_stats — pero ese endpoint
         devuelve solo counts, no ids. Por ahora, fallback a empty list:
         el journal pierde la lista de "unused_rules_30d" pero el resto
         (trades, P/L, win_rate) sigue funcionando.
    """
    from learning.nightly_journal import run_nightly_journal
    all_rule_ids = []
    try:
        import glob
        _sys.path.insert(0, "llm_engine_eolo")
        try:
            from llm_engine.kb_loader import KBLoader
            kbs = sorted(glob.glob("llm_engine_eolo/kb/EOLO_ThetaHarvest_v*.xlsx"))
            if kbs:
                kb = KBLoader(kbs[-1])
                all_rule_ids = [r.rule_id for r in kb.rules]
        finally:
            try:
                _sys.path.remove("llm_engine_eolo")
            except ValueError:
                pass
    except ImportError:
        logger.debug("[journal_today] llm_engine package no disponible en container, rule_ids=[]")

    journal = run_nightly_journal(all_rule_ids)
    return jsonify(journal), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
