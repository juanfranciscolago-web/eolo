# ============================================================
#  EOLO v2 — Dashboard Cloud Run
#
#  Lee el estado del bot desde Firestore y lo sirve al browser.
#  El bot (corriendo localmente) escribe en:
#    Firestore: eolo-crop-state / current
#
#  Deploy:
#    gcloud builds submit --config dashboard-cloudrun/cloudbuild.yaml \
#      --project eolo-schwab-agent eolo-options/dashboard-cloudrun/
#
#  Local:
#    GOOGLE_CLOUD_PROJECT=eolo-schwab-agent python main.py
# ============================================================
import os
import time
import functools
import urllib.parse
from datetime import timedelta
from flask import (
    Flask, render_template, render_template_string,
    jsonify, request, session, redirect, url_for, abort
)
from werkzeug.middleware.proxy_fix import ProxyFix
from google.cloud import firestore
import requests as _req

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # HTTPS correcto en Cloud Run

# ── Auth — Google OAuth 2.0 ───────────────────────────────
# Variables de entorno requeridas en Cloud Run:
#   GOOGLE_CLIENT_ID     → OAuth 2.0 Client ID (GCP Console)
#   GOOGLE_CLIENT_SECRET → OAuth 2.0 Client Secret
#   FLASK_SECRET_KEY     → clave para firmar cookies (Secret Manager)
#   ALLOWED_EMAIL        → email autorizado (default: juanfranciscolago@gmail.com)
_GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
_GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
_ALLOWED_EMAILS       = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "juanfranciscolago@gmail.com").split(",") if e.strip()}
_GOOGLE_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"
app.secret_key                 = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.permanent_session_lifetime = timedelta(hours=48)

_DENIED_HTML = """
<!doctype html><html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceso denegado</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh}
  .card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:32px 28px;
        width:100%;max-width:380px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.5)}
  h1{font-size:1.1rem;margin-bottom:10px}
  p{font-size:.82rem;color:#8b949e;margin-bottom:18px}
  a{color:#58a6ff;font-size:.82rem;text-decoration:none}
  a:hover{text-decoration:underline}
</style></head>
<body><div class="card">
  <h1>🚫 Acceso denegado</h1>
  <p><strong>{{ email }}</strong> no está autorizado para acceder a este dashboard.</p>
  <a href="/logout">← Intentar con otra cuenta</a>
</div></body></html>
"""


def require_auth(f):
    """Decorator: redirige a /login si no hay sesión activa."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _GOOGLE_CLIENT_ID and not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


@app.route("/login")
def login():
    """Redirige al flujo OAuth de Google (sin PKCE, sin librerías intermedias)."""
    if not _GOOGLE_CLIENT_ID:
        session.permanent = True
        session["authenticated"] = True
        return redirect(request.args.get("next") or "/")
    state = os.urandom(16).hex()
    session["oauth_state"] = state
    if request.args.get("next"):
        session["oauth_next"] = request.args["next"]
    params = {
        "client_id":     _GOOGLE_CLIENT_ID,
        "redirect_uri":  url_for("oauth2callback", _external=True),
        "response_type": "code",
        "scope":         "openid email",
        "access_type":   "offline",
        "state":         state,
        "login_hint":    next(iter(_ALLOWED_EMAILS), ""),
        "prompt":        "select_account",
    }
    return redirect(_GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))


@app.route("/oauth2callback")
def oauth2callback():
    """Callback OAuth: intercambia code → token → email y establece sesión."""
    if not _GOOGLE_CLIENT_ID:
        return redirect("/")
    if request.args.get("state") != session.get("oauth_state"):
        return render_template_string(_DENIED_HTML, email="Error: estado inválido"), 400
    code = request.args.get("code")
    if not code:
        return render_template_string(_DENIED_HTML, email="Error: sin código de autorización"), 400
    try:
        token_r = _req.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     _GOOGLE_CLIENT_ID,
                "client_secret": _GOOGLE_CLIENT_SECRET,
                "redirect_uri":  url_for("oauth2callback", _external=True),
                "grant_type":    "authorization_code",
            },
            timeout=10,
        )
        token_r.raise_for_status()
        access_token = token_r.json().get("access_token", "")
        userinfo = _req.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        ).json()
        email = userinfo.get("email", "").lower()
    except Exception as exc:
        return render_template_string(_DENIED_HTML, email=f"Error: {exc}"), 500

    if email not in _ALLOWED_EMAILS:
        return render_template_string(_DENIED_HTML, email=email), 403

    session.permanent = True
    session["authenticated"] = True
    session["user_email"] = email
    return redirect(session.pop("oauth_next", None) or "/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
# ─────────────────────────────────────────────────────────

GCP_PROJECT      = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
STATE_COLL       = "eolo-crop-state"
STATE_DOC        = "current"
CONFIG_COLL      = "eolo-crop-config"
CONFIG_DOC       = "settings"
COMMANDS_DOC     = "commands"
TRADES_COLL      = "eolo-crop-trades"

# Mismo set canónico que Bot/bot_main.py::DEFAULT_STRATEGIES y v2 EoloV2
# .__init__._strategies_enabled. Whitelist: el endpoint /api/toggle-strategy
# sólo acepta keys acá (evita inyectar keys random a Firestore).
KNOWN_STRATEGIES = {
    # Clásicas
    "ema_crossover", "gap_fade", "rsi_sma200", "hh_ll", "ema_tsi",
    "vwap_rsi", "bollinger", "orb", "supertrend", "ha_cloud",
    "squeeze", "macd_bb", "vela_pivot",
    # Nivel 1
    "rvol_breakout", "stop_run", "vwap_zscore", "volume_reversal_bar",
    "anchor_vwap", "obv_mtf", "tsv", "vw_macd", "opening_drive",
    # Nivel 2 (requieren MacroFeeds)
    "vix_mean_rev", "vix_correlation", "vix_squeeze",
    "tick_trin_fade", "vrp_intraday",
    # Suite "EMA 3/8 y MACD" (v3) — 11 estrategias
    "ema_3_8", "ema_8_21", "macd_accel", "volume_breakout",
    "buy_pressure", "sell_pressure", "vwap_momentum",
    "orb_v3", "donchian_turtle", "bulls_bsp", "net_bsv",
    # Combos Ganadores (2026-04) — 7 estrategias
    "combo1_ema_scalper", "combo2_rubber_band", "combo3_nino_squeeze",
    "combo4_slimribbon", "combo5_btd", "combo6_fractalccix", "combo7_campbell",
    # Theta Harvest — credit spreads 0-5 DTE
    "theta_harvest",
}


def get_db():
    return firestore.Client(project=GCP_PROJECT)


def get_state() -> dict:
    try:
        db  = get_db()
        doc = db.collection(STATE_COLL).document(STATE_DOC).get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception as e:
        return {"error": str(e)}
    return {}


def write_command(payload: dict) -> dict:
    """Escribe un comando pendiente que el bot debe leer y ejecutar."""
    db = get_db()
    payload = dict(payload)
    payload["issued_ts"] = time.time()
    payload["issued_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["consumed"]  = False
    db.collection(CONFIG_COLL).document(COMMANDS_DOC).set(payload, merge=False)
    return payload


def write_config(payload: dict) -> dict:
    """Merge de configuración persistente (budget, etc.)."""
    db = get_db()
    payload = dict(payload)
    payload["updated_ts"] = time.time()
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    db.collection(CONFIG_COLL).document(CONFIG_DOC).set(payload, merge=True)
    return payload


@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/state")
@require_auth
def api_state():
    state = get_state()
    if not state:
        return jsonify({"error": "Bot no corriendo o sin datos en Firestore"}), 503
    return jsonify(state)


@app.route("/api/billing")
@require_auth
def api_billing():
    """
    Estado del circuit breaker de billing de Anthropic (v2).
    El bot lo escribe en `eolo-crop-state/billing` cuando trip-ea el breaker
    (o cuando lo limpia al recuperarse). El dashboard usa esto para prender un
    semáforo visible en el header.
    """
    try:
        db  = get_db()
        doc = db.collection(STATE_COLL).document("billing").get()
        if doc.exists:
            data = doc.to_dict() or {}
            ts   = data.get("updated_at", 0) or data.get("last_ts", 0)
            data["stale_seconds"] = int(time.time() - ts) if ts else None
            return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "anthropic_billing_paused": False}), 200
    # Sin doc todavía → asumimos OK (todavía no hubo billing error)
    return jsonify({
        "anthropic_billing_paused": False,
        "errors_streak": 0,
        "threshold": 5,
        "last_error": "",
        "last_ts": 0,
        "stale_seconds": None,
    })


@app.route("/api/health")
def health():
    state = get_state()
    ts    = state.get("updated_ts", 0)
    stale = (time.time() - ts) > 120 if ts else True
    return jsonify({
        "ok":          bool(state) and not stale,
        "stale":       stale,
        "last_update": state.get("updated_at"),
    })


# ── Control endpoints ──────────────────────────────────────
# NOTA: estos endpoints escriben a Firestore en
#   eolo-crop-config/commands  (orden puntual)
#   eolo-crop-config/settings  (config persistente)
# El bot (eolo_v2_main.py) debe leer estos docs al inicio
# de cada ciclo y actuar en consecuencia.

@app.route("/api/toggle-active", methods=["POST"])
@require_auth
def toggle_active():
    try:
        data   = request.get_json(silent=True) or {}
        active = bool(data.get("active", True))
        cmd    = write_command({"type": "set_active", "active": active})
        return jsonify({"ok": True, "active": active, "command": cmd})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/toggle-claude-options", methods=["POST"])
@require_auth
def toggle_claude_options():
    """Toggle Claude Bot para opciones generales."""
    try:
        data   = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled", True))
        cfg = write_config({"claude_options_enabled": enabled})
        return jsonify({"ok": True, "enabled": enabled, "config": cfg})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/toggle-claude-theta", methods=["POST"])
@require_auth
def toggle_claude_theta():
    """Toggle Claude Bot para Theta Harvest específicamente."""
    try:
        data   = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled", True))
        cfg = write_config({"claude_theta_harvest_enabled": enabled})
        return jsonify({"ok": True, "enabled": enabled, "config": cfg})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/close-all", methods=["POST"])
@require_auth
def close_all():
    try:
        data   = request.get_json(silent=True) or {}
        reason = data.get("reason", "manual_from_dashboard")
        cmd    = write_command({"type": "close_all", "reason": reason})
        return jsonify({"ok": True, "command": cmd})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config", methods=["POST", "GET"])
@require_auth
def api_config():
    try:
        db = get_db()
        if request.method == "GET":
            doc = db.collection(CONFIG_COLL).document(CONFIG_DOC).get()
            return jsonify(doc.to_dict() or {} if doc.exists else {})

        data = request.get_json(silent=True) or {}
        # whitelist de campos editables desde el dashboard (modal config)
        allowed = {}
        if "budget_per_trade" in data:
            try:
                v = float(data["budget_per_trade"])
                if not (10.0 <= v <= 100000.0):
                    raise ValueError("range")
                allowed["budget_per_trade"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "budget_per_trade inválido (10-100000)"}), 400
        if "max_positions" in data:
            try:
                v = int(data["max_positions"])
                if not (1 <= v <= 50):
                    raise ValueError("range")
                allowed["max_positions"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "max_positions inválido (1-50)"}), 400
        if "default_stop_loss_pct" in data:
            try:
                allowed["default_stop_loss_pct"] = float(data["default_stop_loss_pct"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "default_stop_loss_pct inválido"}), 400
        if "default_take_profit_pct" in data:
            try:
                allowed["default_take_profit_pct"] = float(data["default_take_profit_pct"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "default_take_profit_pct inválido"}), 400
        if "daily_loss_cap_pct" in data:
            try:
                v = float(data["daily_loss_cap_pct"])
                if v > 0:
                    raise ValueError("must be <= 0")
                allowed["daily_loss_cap_pct"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "daily_loss_cap_pct inválido (<= 0)"}), 400
        if "claude_bot_budget" in data:
            try:
                allowed["claude_bot_budget"] = float(data["claude_bot_budget"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "claude_bot_budget inválido"}), 400

        # ── Multi-TF confluencia (eolo_common) ─────────────
        if "confluence_mode" in data:
            allowed["confluence_mode"] = bool(data["confluence_mode"])

        if "confluence_min_agree" in data:
            try:
                v = int(data["confluence_min_agree"])
                if not (1 <= v <= 10):
                    raise ValueError("range")
                allowed["confluence_min_agree"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "confluence_min_agree inválido (1-10)"}), 400

        # ── Trading hours (eolo_common.trading_hours) ──────
        def _parse_hhmm(val):
            s = str(val).strip()
            parts = s.split(":")
            if len(parts) < 2:
                raise ValueError("formato HH:MM")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("fuera de rango")
            return f"{h:02d}:{m:02d}"

        for _hkey in ("trading_start_et", "trading_end_et", "auto_close_et"):
            if _hkey in data:
                try:
                    allowed[_hkey] = _parse_hhmm(data[_hkey])
                except (TypeError, ValueError) as e:
                    return jsonify({"ok": False, "error": f"{_hkey} inválido ({e})"}), 400

        if "trading_hours_enabled" in data:
            allowed["trading_hours_enabled"] = bool(data["trading_hours_enabled"])

        # ── Macro news filter override ──────────────────────
        if "macro_filter_enabled" in data:
            allowed["macro_filter_enabled"] = bool(data["macro_filter_enabled"])

        if not allowed:
            return jsonify({"ok": False, "error": "Sin campos válidos"}), 400

        saved = write_config(allowed)
        return jsonify({"ok": True, "config": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/schedule-status")
@require_auth
def api_schedule_status():
    """
    Estado de trading_hours para el bot v2.
    El bot publica el payload en eolo-crop-state/current.schedule;
    si no existe lo re-computamos desde eolo-crop-config/settings
    para que el front siempre tenga algo.
    """
    try:
        state = get_state() or {}
        sch   = state.get("schedule") or {}

        if sch and sch.get("trading_start_et"):
            payload = dict(sch)
            payload.setdefault("market_open", state.get("market_open", True))
            return jsonify(payload)

        # Fallback: computar on-the-fly desde el config doc con stdlib
        # (el build context de este dashboard no incluye eolo_common, así
        # que recalculamos acá con las defaults hardcoded de EQUITY)
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            _now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            _now_et = datetime.utcnow()

        db  = get_db()
        doc = db.collection(CONFIG_COLL).document(CONFIG_DOC).get()
        cfg = (doc.to_dict() or {}) if doc.exists else {}

        start_s = str(cfg.get("trading_start_et") or "09:30")
        end_s   = str(cfg.get("trading_end_et")   or "15:27")
        close_s = str(cfg.get("auto_close_et")    or "15:27")
        enabled = bool(cfg.get("trading_hours_enabled", True))

        def _hhmm_to_minutes(s):
            try:
                h, m = s.split(":"); return int(h) * 60 + int(m)
            except Exception:
                return None

        now_min   = _now_et.hour * 60 + _now_et.minute
        start_min = _hhmm_to_minutes(start_s)
        end_min   = _hhmm_to_minutes(end_s)
        close_min = _hhmm_to_minutes(close_s)
        within = (
            not enabled
            or (start_min is not None and end_min is not None and start_min <= now_min <= end_min)
        )
        after_close = (
            enabled
            and close_min is not None
            and now_min >= close_min
        )
        reason = "disabled" if not enabled else ("within_window" if within else ("before_start" if start_min and now_min < start_min else "after_end"))

        payload = {
            "trading_start_et":     start_s,
            "trading_end_et":       end_s,
            "auto_close_et":        close_s,
            "trading_hours_enabled": enabled,
            "is_within_window":      bool(within),
            "is_after_auto_close":   bool(after_close),
            "reason":                reason,
            "now_et":                _now_et.strftime("%Y-%m-%d %H:%M:%S"),
            "market_open":           bool(within),
        }
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/toggle-strategy", methods=["POST"])
@require_auth
def toggle_strategy():
    """
    Activa/desactiva una estrategia por nombre canónico.
    Body: { "strategy": "rvol_breakout", "enabled": true }

    Persiste en eolo-crop-config/settings.strategies_enabled (merge).
    El bot lo recoge en _poll_settings() cada 5s.
    """
    try:
        data = request.get_json(silent=True) or {}
        strat   = str(data.get("strategy", "")).strip()
        enabled = bool(data.get("enabled", True))
        if not strat:
            return jsonify({"ok": False, "error": "strategy requerido"}), 400
        if strat not in KNOWN_STRATEGIES:
            return jsonify({"ok": False, "error": f"strategy desconocida: {strat}"}), 400

        # Merge preservando las otras estrategias
        db  = get_db()
        doc = db.collection(CONFIG_COLL).document(CONFIG_DOC).get()
        current = (doc.to_dict() or {}).get("strategies_enabled", {}) if doc.exists else {}
        if not isinstance(current, dict):
            current = {}
        current[strat] = enabled

        saved = write_config({"strategies_enabled": current})
        return jsonify({"ok": True, "strategies_enabled": current, "config": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/strategies", methods=["GET"])
@require_auth
def api_strategies():
    """
    Retorna el estado actual de los toggles (para pintar el UI al cargar).
    Combina: default (todas ON) + override desde Firestore.
    """
    try:
        db  = get_db()
        doc = db.collection(CONFIG_COLL).document(CONFIG_DOC).get()
        override = {}
        if doc.exists:
            override = (doc.to_dict() or {}).get("strategies_enabled") or {}
        merged = {k: True for k in KNOWN_STRATEGIES}
        for k, v in (override.items() if isinstance(override, dict) else []):
            if k in merged:
                merged[k] = bool(v)
        return jsonify({"strategies_enabled": merged})
    except Exception as e:
        return jsonify({"error": str(e), "strategies_enabled": {k: True for k in KNOWN_STRATEGIES}}), 200


@app.route("/api/strategy-stats", methods=["GET"])
@require_auth
def api_strategy_stats():
    """
    Agrega stats por estrategia leyendo eolo-crop-trades (daily docs).
    Schema por subfield: {action, strategy, pnl_usd (solo SELL_TO_CLOSE), ...}
    Retorna para cada strategy:
      h24:  {trades, wins, losses, net_pnl, series: [{ts, cum_pnl}, ...]}
      week: {trades, wins, losses, net_pnl}
    El P&L vive sólo en los SELL_TO_CLOSE → esas son las "posiciones cerradas".
    """
    try:
        from datetime import datetime, timedelta
        db  = get_db()
        now = time.time()
        h24_cutoff  = now - 86400
        week_cutoff = now - 7 * 86400

        # Leer los últimos 8 docs diarios (cubre 7 días + hoy UTC)
        today = datetime.utcnow()
        doc_ids = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]

        stats: dict = {
            k: {
                "h24":  {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "series": []},
                "week": {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
            }
            for k in KNOWN_STRATEGIES
        }

        for doc_id in doc_ids:
            doc = db.collection(TRADES_COLL).document(doc_id).get()
            if not doc.exists:
                continue
            data = doc.to_dict() or {}
            for _, trade in data.items():
                if not isinstance(trade, dict):
                    continue
                action = str(trade.get("action", ""))
                # Contar tanto SELL_TO_CLOSE (cierre de long) como
                # BUY_TO_CLOSE_SPREAD (cierre de credit spread theta harvest)
                is_close = "SELL_TO_CLOSE" in action or "BUY_TO_CLOSE_SPREAD" in action
                if not is_close:
                    continue
                strat = str(trade.get("strategy", "")).strip().lower()
                if strat not in stats:
                    continue
                pnl = float(trade.get("pnl_usd") or 0.0)
                ts_str = str(trade.get("timestamp", ""))
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    continue
                if ts < week_cutoff:
                    continue
                is_win = pnl > 0
                stats[strat]["week"]["trades"]  += 1
                stats[strat]["week"]["net_pnl"] += pnl
                if is_win: stats[strat]["week"]["wins"]   += 1
                else:       stats[strat]["week"]["losses"] += 1
                if ts >= h24_cutoff:
                    stats[strat]["h24"]["trades"]  += 1
                    stats[strat]["h24"]["net_pnl"] += pnl
                    if is_win: stats[strat]["h24"]["wins"]   += 1
                    else:       stats[strat]["h24"]["losses"] += 1
                    stats[strat]["h24"]["series"].append({"ts": ts, "pnl": pnl})

        for k in stats:
            s = stats[k]["h24"]["series"]
            s.sort(key=lambda x: x["ts"])
            cum = 0.0
            for p in s:
                cum += p["pnl"]
                p["cum_pnl"] = round(cum, 2)
            stats[k]["h24"]["net_pnl"]  = round(stats[k]["h24"]["net_pnl"], 2)
            stats[k]["week"]["net_pnl"] = round(stats[k]["week"]["net_pnl"], 2)

        return jsonify({"stats": stats, "generated_ts": now})
    except Exception as e:
        return jsonify({"error": str(e), "stats": {}}), 200


@app.route("/api/tickers", methods=["POST"])
@require_auth
def api_tickers():
    """
    Guarda la selección de tickers por estrategia (puts / calls / wheel).
    Body: { "ticker_selection": { "puts": {"SPY": true, ...}, "calls": {...}, "wheel": {...} } }
    """
    try:
        data = request.get_json(silent=True) or {}
        sel  = data.get("ticker_selection")
        if not isinstance(sel, dict):
            return jsonify({"ok": False, "error": "ticker_selection requerido"}), 400

        # Sanitizar: sólo grupos conocidos, valores bool, símbolos uppercase
        clean: dict = {}
        for group in ("puts", "calls", "wheel"):
            src = sel.get(group)
            if not isinstance(src, dict):
                continue
            clean[group] = {
                str(k).upper(): bool(v)
                for k, v in src.items()
                if str(k).isalnum() and len(str(k)) <= 8
            }
        if not clean:
            return jsonify({"ok": False, "error": "Sin grupos válidos"}), 400

        saved = write_config({"ticker_selection": clean})
        return jsonify({"ok": True, "config": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port, debug=False)
