# ============================================================
#  EOLO Crypto — Dashboard Cloud Run
#
#  Lee estado del bot crypto desde Firestore y lo sirve al browser.
#
#  Fuentes de datos (Firestore):
#    eolo-crypto-state/current        ← state writer del bot (cada 10s)
#    eolo-crypto-state/positions      ← posiciones Eolo persistentes (_eolo_positions)
#    eolo-crypto-trades/*             ← histórico de trades (BUY/SELL cerrados)
#    eolo-crypto-claude-decisions/*   ← cada decisión del Claude Bot #14
#
#  Control (escribe a):
#    eolo-crypto-config/commands      ← orden puntual (close_all, set_active, ...)
#    eolo-crypto-config/settings      ← config persistente (budget, max_positions, strategies_enabled)
#
#  El bot (eolo-crypto) debe consumir `eolo-crypto-config/commands` y
#  `eolo-crypto-config/settings` al inicio de cada ciclo y actuar.
#
#  Deploy:
#    gcloud builds submit \
#      --config eolo-crypto-dashboard/cloudbuild.yaml \
#      --project eolo-schwab-agent \
#      eolo-crypto-dashboard/
# ============================================================
import os
import time
import functools
import urllib.parse
import urllib.request
import json as _json
import threading
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, render_template, render_template_string,
    jsonify, request, session, redirect, url_for
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

GCP_PROJECT     = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")

# ── Colecciones Firestore (espejan eolo-crypto/settings.py) ──
STATE_COLL      = "eolo-crypto-state"
STATE_DOC       = "current"
POSITIONS_DOC   = "positions"          # escrito por binance_executor._persist_positions_to_firestore
TRADES_COLL     = "eolo-crypto-trades"
DECISIONS_COLL  = "eolo-crypto-claude-decisions"

CONFIG_COLL     = "eolo-crypto-config"
CONFIG_DOC      = "settings"
COMMANDS_DOC    = "commands"
# Whitelist de strategies conocidas (espejo de eolo-crypto-config/settings)
# Cleanup 2026-05-01: solo estrategias que existen en Firestore.
# Sacadas: gap, base (deprecated), 10 v3 (ema_3_8, ema_8_21, macd_accel,
# volume_breakout, buy_pressure, sell_pressure, vwap_momentum,
# donchian_turtle, bulls_bsp, net_bsv) y 6 combos (combo1-7).
# Agregadas: bollinger_rsi_sensitive, macd_confluence_fase7a,
# momentum_score_fase7a, xom_30m.
# Sincronizado con eolo-crypto/settings.py:STRATEGIES_ENABLED (38 keys).
# Post-Fase 2 (B1+B1.6): 4 efectivas, 34 disabled. Auditoría 19-may-2026.
KNOWN_STRATEGIES = {
    # ── 4 efectivas (enabled en Firestore + code) ──
    "bollinger", "rsi_sma200", "supertrend", "vwap_rsi",
    # ── 5 apagadas en B1.3 (rvol_breakout + 4 nativas TF=4h) ──
    "rvol_breakout", "liquidation_cascade", "funding_rate_carry",
    "weekend_breakout", "btc_lead_lag",
    # ── 24 apagadas en B1.6 por evidencia estadística ──
    # destructoras firmes
    "squeeze", "ema_tsi", "hh_ll", "macd_bb",
    "buy_pressure", "sell_pressure", "net_bsv", "vwap_momentum",
    # borderline / inestables
    "ema_3_8", "macd_accel", "donchian_turtle", "volume_reversal_bar",
    # openers sin SELL eficaz
    "tsv", "vw_macd", "vwap_zscore", "stop_run",
    # equity-portadas sin trades en crypto
    "ha_cloud", "obv_mtf", "orb", "vela_pivot",
    "bollinger_rsi_sensitive", "xom_30m",
    "macd_confluence_fase7a", "momentum_score_fase7a",
    # ── 5 keys que existen sólo en code (no en Firestore, sin override) ──
    "base", "bulls_bsp", "ema_8_21", "gap", "volume_breakout",
}

# Subset operativo (efectivas hoy). Usado por endpoints que solo
# tienen sentido para strategies que realmente operan.
ACTIVE_STRATEGIES = {"bollinger", "rsi_sma200", "supertrend", "vwap_rsi"}


def get_db():
    return firestore.Client(project=GCP_PROJECT)


# ── Readers ───────────────────────────────────────────────

def get_state() -> dict:
    try:
        db  = get_db()
        doc = db.collection(STATE_COLL).document(STATE_DOC).get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception as e:
        return {"error": str(e)}
    return {}


def get_positions_doc() -> dict:
    """
    Posiciones Eolo reales (escritas por binance_executor._persist_positions_to_firestore).
    Schema real (ver trading/binance_executor.py):
      { "open": { "BTCUSDT": {qty, entry_price, strategy, ts, reason, order_id}, ... },
        "updated_at": <unix_ts>,
        "mode": "TESTNET"|"PAPER"|"LIVE" }
    """
    try:
        db  = get_db()
        doc = db.collection(STATE_COLL).document(POSITIONS_DOC).get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception:
        pass
    return {}


def get_recent_trades(limit: int = 200) -> list[dict]:
    """
    Últimos trades (BUY/SELL) ordenados desc por ts.
    Toleramos falta de índice: si la query ordenada falla,
    hacemos fallback sin orderBy.
    """
    try:
        db = get_db()
        q  = (db.collection(TRADES_COLL)
                .order_by("ts", direction=firestore.Query.DESCENDING)
                .limit(limit))
        return [d.to_dict() for d in q.stream()]
    except Exception:
        try:
            db   = get_db()
            docs = list(db.collection(TRADES_COLL).limit(limit * 2).stream())
            rows = [d.to_dict() for d in docs]
            rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
            return rows[:limit]
        except Exception as e:
            return [{"error": str(e)}]


def get_recent_decisions(limit: int = 50) -> list[dict]:
    """
    Últimas decisiones del Claude Bot #14 crypto.
    El campo de tiempo real en FIRESTORE_CLAUDE_COLLECTION es `ts_iso` (string ISO-8601,
    que ordena lex = cronológico).
    """
    try:
        db = get_db()
        q  = (db.collection(DECISIONS_COLL)
                .order_by("ts_iso", direction=firestore.Query.DESCENDING)
                .limit(limit))
        return [d.to_dict() for d in q.stream()]
    except Exception:
        try:
            db   = get_db()
            docs = list(db.collection(DECISIONS_COLL).limit(limit * 2).stream())
            rows = [d.to_dict() for d in docs]
            rows.sort(key=lambda r: r.get("ts_iso", ""), reverse=True)
            return rows[:limit]
        except Exception as e:
            return [{"error": str(e)}]


def get_config() -> dict:
    try:
        db  = get_db()
        doc = db.collection(CONFIG_COLL).document(CONFIG_DOC).get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception:
        pass
    return {}


# ── Writers ───────────────────────────────────────────────

def write_command(payload: dict) -> dict:
    """Orden puntual: el bot la lee, la ejecuta y marca consumed=True."""
    db = get_db()
    payload = dict(payload)
    payload["issued_ts"] = time.time()
    payload["issued_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["consumed"]  = False
    db.collection(CONFIG_COLL).document(COMMANDS_DOC).set(payload, merge=False)
    return payload


def write_config(payload: dict) -> dict:
    """Config persistente — merge."""
    db = get_db()
    payload = dict(payload)
    payload["updated_ts"] = time.time()
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    db.collection(CONFIG_COLL).document(CONFIG_DOC).set(payload, merge=True)
    return payload


# ── Helpers de agregación ────────────────────────────────

def _today_utc_bounds() -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end   = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def compute_day_stats(trades: list[dict]) -> dict:
    """
    A partir del listado de trades, calcula stats del día (UTC).
    Sólo consideramos SELLs cerrados con PnL. El campo real es `pnl_usdt`
    (ver binance_executor._log_paper).
    """
    start_ts, end_ts = _today_utc_bounds()

    def _pnl(t: dict) -> float:
        return float(t.get("pnl_usdt", t.get("pnl", 0)) or 0)

    today_closed = [
        t for t in trades
        if (t.get("side", "").upper() == "SELL"
            and (t.get("pnl_usdt") is not None or t.get("pnl") is not None)
            and start_ts <= float(t.get("ts", 0) or 0) < end_ts)
    ]
    wins   = [t for t in today_closed if _pnl(t) > 0]
    losses = [t for t in today_closed if _pnl(t) < 0]
    total_pnl = sum(_pnl(t) for t in today_closed)
    best  = max((_pnl(t) for t in today_closed), default=0.0)
    worst = min((_pnl(t) for t in today_closed), default=0.0)

    return {
        "trades_today":  len(today_closed),
        "wins_today":    len(wins),
        "losses_today": len(losses),
        "win_rate_pct":  (100.0 * len(wins) / len(today_closed)) if today_closed else 0.0,
        "pnl_today":     round(total_pnl, 2),
        "best_trade":    round(best, 2),
        "worst_trade":   round(worst, 2),
    }


# ── B7: Cache de precios Binance (TTL 10s) ────────────────
_price_cache: dict = {}                # symbol -> {"price": float, "ts": float}
_price_cache_lock = threading.Lock()
_PRICE_CACHE_TTL_SEC = 10.0

def _binance_base_url(mode: str) -> str:
    """REST público de Binance según modo del bot."""
    return ("https://testnet.binance.vision"
            if (mode or "").upper() == "TESTNET"
            else "https://api.binance.com")

def _fetch_binance_prices(symbols: list, mode: str = "TESTNET") -> dict:
    """
    Retorna dict {symbol: last_price} para los símbolos pedidos.
    Usa cache local con TTL 10s. Fail-soft: si Binance no responde,
    retorna {} y el caller usa None para distance_to_sl/tp.
    """
    if not symbols:
        return {}
    now = time.time()
    result = {}
    to_fetch = []
    with _price_cache_lock:
        for s in symbols:
            cached = _price_cache.get(s)
            if cached and (now - cached["ts"]) < _PRICE_CACHE_TTL_SEC:
                result[s] = cached["price"]
            else:
                to_fetch.append(s)
    if not to_fetch:
        return result
    base = _binance_base_url(mode)
    try:
        # /api/v3/ticker/price?symbols=["BTCUSDT","ETHUSDT"]
        symbols_param = _json.dumps(to_fetch).replace(" ", "")
        url = f"{base}/api/v3/ticker/price?symbols={symbols_param}"
        req = urllib.request.Request(url, headers={"User-Agent": "eolo-crypto-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read().decode())
        if isinstance(data, list):
            with _price_cache_lock:
                for entry in data:
                    sym = entry.get("symbol")
                    price = float(entry.get("price") or 0)
                    if sym and price > 0:
                        _price_cache[sym] = {"price": price, "ts": now}
                        result[sym] = price
    except Exception:
        # Fail-soft: si falla, los símbolos sin cache se devuelven sin precio.
        pass
    return result


# ── Routes: vistas ───────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return render_template("index.html")


# ── Routes: API ──────────────────────────────────────────

@app.route("/api/state")
@require_auth
def api_state():
    state = get_state()
    if not state:
        return jsonify({"error": "Bot no corriendo o sin datos en Firestore"}), 503

    # Acoplamos positions doc (fuente de verdad del executor: clave "open")
    pos_doc = get_positions_doc()
    open_positions = pos_doc.get("open") if pos_doc else None
    if open_positions:
        state["open_positions"] = open_positions

    # Adjuntamos config actual para que el front refleje toggles
    state["config"] = get_config()
    return jsonify(state)


@app.route("/api/schedule-status")
@require_auth
def api_schedule_status():
    """
    Devuelve el payload de trading_hours publicado por el bot al state doc.
    El front lo usa para renderizar el banner "Pause time limit" y los
    campos del modal Config.
    Shape:
      {
        "enabled": True,
        "market_open": True,
        "banner_reason": None,     # o "before_start" | "after_end" | "disabled"
        "now_et": "YYYY-MM-DD HH:MM",
        "trading_start_et": "HH:MM",
        "trading_end_et":   "HH:MM",
        "auto_close_et":    "HH:MM",
      }
    """
    state = get_state() or {}
    sch = state.get("schedule") or {}
    cfg = get_config() or {}
    return jsonify({
        "enabled":            sch.get("trading_hours_enabled", cfg.get("trading_hours_enabled", True)),
        "market_open":        bool(state.get("market_open", True)),
        "banner_reason":      sch.get("banner_reason"),
        "now_et":             sch.get("now_et"),
        "trading_start_et":   sch.get("trading_start_et", cfg.get("trading_start_et", "00:00")),
        "trading_end_et":     sch.get("trading_end_et",   cfg.get("trading_end_et",   "23:59")),
        "auto_close_et":      sch.get("auto_close_et",    cfg.get("auto_close_et",    "23:59")),
        "is_within_window":   sch.get("is_within_window", True),
        "is_after_auto_close": sch.get("is_after_auto_close", False),
    })


@app.route("/api/positions")
@require_auth
def api_positions():
    """
    B7: posiciones enriquecidas con last_price (Binance), sl_price/tp_price,
    distances, pnl_pct. Si la posición se abrió antes de B3 (sin sl_pct/tp_pct
    persistidos), usa los defaults del config para el cálculo.
    """
    pos_doc = get_positions_doc()
    raw_open = pos_doc.get("open", {}) or {}
    mode = pos_doc.get("mode") or "TESTNET"

    # Defaults para posiciones legacy sin sl_pct/tp_pct persistidos
    cfg = get_config() or {}
    default_sl = float(cfg.get("default_stop_loss_pct") or 5.0)
    default_tp = float(cfg.get("default_take_profit_pct") or 5.0)

    enriched = {}
    if raw_open:
        last_prices = _fetch_binance_prices(list(raw_open.keys()), mode=mode)
        for symbol, pos in raw_open.items():
            entry = float(pos.get("entry_price") or 0)
            sl_pct = float(pos.get("sl_pct") or default_sl)
            tp_pct = float(pos.get("tp_pct") or default_tp)
            last = last_prices.get(symbol)
            # Triggers: SL relativo al entry (bot pricing), no al last
            sl_price = round(entry * (1 - sl_pct / 100.0), 6) if entry > 0 else None
            tp_price = round(entry * (1 + tp_pct / 100.0), 6) if entry > 0 else None
            pnl_pct = None
            distance_to_sl = None
            distance_to_tp = None
            if last and entry > 0:
                pnl_pct = round((last - entry) / entry * 100.0, 2)
                # distance_to_sl: cuánto baja last para gatillar SL (negativo)
                # distance_to_tp: cuánto sube last para gatillar TP (positivo)
                distance_to_sl = round((sl_price - last) / last * 100.0, 2) if sl_price else None
                distance_to_tp = round((tp_price - last) / last * 100.0, 2) if tp_price else None
            enriched[symbol] = {
                **pos,
                "last_price":     last,
                "pnl_pct":        pnl_pct,
                "sl_price":       sl_price,
                "tp_price":       tp_price,
                "distance_to_sl": distance_to_sl,
                "distance_to_tp": distance_to_tp,
                "sl_pct_used":    sl_pct,
                "tp_pct_used":    tp_pct,
            }

    return jsonify({
        "positions":  enriched,
        "updated_at": pos_doc.get("updated_at"),
        "mode":       mode,
    })


@app.route("/api/trades")
@require_auth
def api_trades():
    limit  = min(int(request.args.get("limit", 200)), 500)
    trades = get_recent_trades(limit=limit)
    stats  = compute_day_stats(trades)
    return jsonify({"trades": trades, "stats": stats})


@app.route("/api/strategies")
@require_auth
def api_strategies():
    """
    Estado actual de los toggles en formato canónico. Para crypto, sólo 20 keys
    (13 clásicas + 7 Nivel 1). Las Nivel 2 no aplican pero las devolvemos para
    consistencia visual con v1/v2.
    """
    try:
        override = get_config().get("strategies_enabled") or {}
        if not isinstance(override, dict):
            override = {}
        merged = {k: True for k in KNOWN_STRATEGIES}
        for k, v in override.items():
            if k in merged:
                merged[k] = bool(v)
        return jsonify({"strategies_enabled": merged})
    except Exception as e:
        return jsonify({"error": str(e),
                        "strategies_enabled": {k: True for k in KNOWN_STRATEGIES}}), 200


@app.route("/api/strategy-stats")
@require_auth
def api_strategy_stats():
    """
    Agrega stats por estrategia leyendo eolo-crypto-trades (1 doc por trade).
    Schema: {ts, side (BUY|SELL), strategy, pnl_usdt (solo SELL), ...}
    Retorna para cada strategy canonical:
      h24:  {trades, wins, losses, net_pnl, series}
      week: {trades, wins, losses, net_pnl}
    """
    try:
        now = time.time()
        h24_cutoff  = now - 86400
        week_cutoff = now - 7 * 86400

        # Pido hasta 1000 trades recientes; cubre razonablemente 7 días
        raw = get_recent_trades(limit=1000)

        stats = {
            k: {
                "h24":  {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "series": []},
                "week": {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
            }
            for k in KNOWN_STRATEGIES
        }

        for t in raw:
            if not isinstance(t, dict):
                continue
            if str(t.get("side", "")).upper() != "SELL":
                continue
            strat = str(t.get("strategy", "")).strip().lower()
            if strat not in stats:
                continue
            ts = float(t.get("ts", 0) or 0)
            if ts < week_cutoff:
                continue
            pnl = float(t.get("pnl_usdt") or t.get("pnl") or 0.0)
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


@app.route("/api/decisions")
@require_auth
def api_decisions():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"decisions": get_recent_decisions(limit=limit)})


@app.route("/api/health")
def health():
    state = get_state()
    ts    = state.get("ts_updated")
    stale = True
    if ts:
        try:
            # ts_updated se escribe en ISO (datetime.now(utc).isoformat())
            last  = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - last).total_seconds()
            stale = age_s > 120
        except Exception:
            stale = True
    return jsonify({
        "ok":          bool(state) and not stale,
        "stale":       stale,
        "last_update": state.get("ts_updated"),
    })


@app.route("/api/daily-cap-status")
@require_auth
def api_daily_cap_status():
    """
    B4: estado del daily loss cap publicado por el bot al state writer.

    Shape:
      {
        "pnl_usdt":         8.17,
        "pnl_pct":          0.08,
        "cap_pct":         -5.0,
        "cap_reached":     false,
        "distance_to_cap": -5.08,   # cuánto falta para que el cap se active
        "balance_starting": 10000.0,
        "balance_now":     10008.17,
      }
    """
    state = get_state() or {}
    pnl_usdt = float(state.get("daily_pnl_usdt") or 0)
    pnl_pct  = float(state.get("daily_loss_cap_pnl_pct") or 0)
    cap_pct  = float(state.get("daily_loss_cap_threshold") or -5.0)
    reached  = bool(state.get("daily_loss_cap_reached") or False)
    balance  = float(state.get("balance_usdt") or 0)
    # distance_to_cap: negativo si todavía hay margen, positivo o cero si gatillado
    distance = round(pnl_pct - cap_pct, 2)
    # balance_starting: heurística (no persistido) — balance actual menos pnl del día
    balance_starting = round(balance - pnl_usdt, 2) if balance else None
    return jsonify({
        "pnl_usdt":         round(pnl_usdt, 2),
        "pnl_pct":          round(pnl_pct, 2),
        "cap_pct":          round(cap_pct, 2),
        "cap_reached":      reached,
        "distance_to_cap":  distance,
        "balance_starting": balance_starting,
        "balance_now":      round(balance, 2),
    })


@app.route("/api/sl-tp-events")
@require_auth
def api_sl_tp_events():
    """
    B10: últimos N eventos sl_trigger / tp_trigger del monitor B3.

    Lee de eolo-crypto-trades los SELLs con strategy in {sl_trigger, tp_trigger}.
    Atribución: aunque el closer es safety net, el campo strategy persiste
    el closer name; el opener original se preserva en el log del bot pero
    no en el campo strategy del doc (el bot loguea el opener en el reason).

    Shape:
      {
        "events": [
          {
            "ts": 1779200000.0,
            "ts_iso": "2026-05-19T20:33:20Z",
            "symbol": "LINKUSDT",
            "trigger": "sl_trigger" | "tp_trigger",
            "pnl_usdt": -42.50,
            "qty": 289.77,
            "price": 9.13,
            "reason": "sl_trigger pnl=-5.12% sl=5.0%",
          }, ...
        ]
      }
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
        days_back = min(int(request.args.get("days", 30)), 90)
        cutoff = time.time() - (days_back * 86400)

        db = get_db()
        coll = db.collection("eolo-crypto-trades")
        # Query: SELL con ts >= cutoff. Filter strategy en cliente porque
        # el composite index (ts ASC, side ASC) no incluye strategy.
        docs = coll.where("side", "==", "SELL").where("ts", ">=", cutoff).stream()
        events = []
        for d in docs:
            data = d.to_dict() or {}
            strat = str(data.get("strategy", "")).strip().lower()
            if strat not in ("sl_trigger", "tp_trigger"):
                continue
            ts = float(data.get("ts", 0) or 0)
            events.append({
                "ts":       ts,
                "ts_iso":   datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "symbol":   data.get("symbol"),
                "trigger":  strat,
                "pnl_usdt": round(float(data.get("pnl_usdt") or 0), 2),
                "qty":      float(data.get("qty") or 0),
                "price":    float(data.get("price") or 0),
                "reason":   data.get("reason", ""),
            })
        # Más recientes primero
        events.sort(key=lambda e: e["ts"], reverse=True)
        return jsonify({"events": events[:limit]})
    except Exception as e:
        return jsonify({"error": str(e), "events": []}), 200


# ── Control endpoints ────────────────────────────────────

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


@app.route("/api/close-all", methods=["POST"])
@require_auth
def close_all():
    """
    Cierra TODAS las posiciones Eolo activas (market sell).
    El bot lee este comando, itera _eolo_positions y ejecuta.
    """
    try:
        data   = request.get_json(silent=True) or {}
        reason = data.get("reason", "manual_from_dashboard")
        cmd    = write_command({"type": "close_all", "reason": reason})
        return jsonify({"ok": True, "command": cmd})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/toggle-strategy", methods=["POST"])
@require_auth
def toggle_strategy():
    """
    Body: { "strategy": "rsi_sma200", "enabled": true }
    Persistimos en CONFIG_DOC.strategies_enabled (merge por strategy).
    El bot debe leer settings.strategies_enabled del config doc en cada ciclo.
    """
    try:
        data     = request.get_json(silent=True) or {}
        strategy = str(data.get("strategy", "")).strip()
        enabled  = bool(data.get("enabled", True))
        if strategy not in KNOWN_STRATEGIES:
            return jsonify({"ok": False, "error": f"strategy '{strategy}' desconocida"}), 400

        current = get_config().get("strategies_enabled", {}) or {}
        current[strategy] = enabled
        saved = write_config({"strategies_enabled": current})
        return jsonify({"ok": True, "strategies_enabled": current, "config": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/toggle-claude", methods=["POST"])
@require_auth
def toggle_claude():
    """Enable/disable Claude Bot #14."""
    try:
        data    = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled", True))
        saved   = write_config({"claude_bot_enabled": enabled})
        return jsonify({"ok": True, "claude_bot_enabled": enabled, "config": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config", methods=["POST", "GET"])
@require_auth
def api_config():
    try:
        if request.method == "GET":
            return jsonify(get_config())

        data    = request.get_json(silent=True) or {}
        allowed = {}

        # POSITION_SIZE_PCT  (0.1 .. 100)
        if "position_size_pct" in data:
            try:
                v = float(data["position_size_pct"])
                if not (0.1 <= v <= 100.0):
                    raise ValueError("range")
                allowed["position_size_pct"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "position_size_pct inválido (0.1 a 100)"}), 400

        # MAX_OPEN_POSITIONS  (1 .. 50)
        if "max_open_positions" in data:
            try:
                v = int(data["max_open_positions"])
                if not (1 <= v <= 50):
                    raise ValueError("range")
                allowed["max_open_positions"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "max_open_positions inválido (1 a 50)"}), 400

        # DEFAULT_STOP_LOSS_PCT / DEFAULT_TAKE_PROFIT_PCT
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

        # DAILY_LOSS_CAP_PCT (negativo o 0)
        if "daily_loss_cap_pct" in data:
            try:
                v = float(data["daily_loss_cap_pct"])
                if v > 0:
                    raise ValueError("must be <= 0")
                allowed["daily_loss_cap_pct"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "daily_loss_cap_pct inválido (<= 0)"}), 400

        # CLAUDE_MAX_COST_PER_DAY
        if "claude_max_cost_per_day" in data:
            try:
                allowed["claude_max_cost_per_day"] = float(data["claude_max_cost_per_day"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "claude_max_cost_per_day inválido"}), 400

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

        # ── Trading hours (ET, formato "HH:MM") ────────────
        def _parse_hhmm(val):
            s = str(val).strip()
            parts = s.split(":")
            if len(parts) < 2:
                raise ValueError("formato HH:MM")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("fuera de rango")
            return f"{h:02d}:{m:02d}"

        for key in ("trading_start_et", "trading_end_et", "auto_close_et"):
            if key in data:
                try:
                    allowed[key] = _parse_hhmm(data[key])
                except (TypeError, ValueError) as e:
                    return jsonify({"ok": False, "error": f"{key} inválido ({e})"}), 400

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port, debug=False)
