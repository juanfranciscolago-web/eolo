# ============================================================
#  EOLO — Dashboard Web (Cloud Run)
#  Lee datos de Firestore en tiempo real
#  URL: https://eolo-dashboard-XXXX-ue.a.run.app
# ============================================================
import os
import functools
import urllib.parse
import pytz
from collections import defaultdict
from datetime import date, datetime, timedelta
from flask import (
    Flask, render_template, render_template_string,
    jsonify, request, session, redirect, url_for
)
from werkzeug.middleware.proxy_fix import ProxyFix
from google.cloud import firestore
import requests as _req

EASTERN = pytz.timezone("America/New_York")

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

GCP_PROJECT       = "eolo-schwab-agent"
TRADES_COLLECTION = "eolo-trades"
CONFIG_COLLECTION = "eolo-config"
CONFIG_DOC        = "strategies"
TICKERS_DOC       = "tickers"
SETTINGS_DOC      = "settings"

BUDGET_MIN =   10      # USD mínimo por trade
BUDGET_MAX = 5000      # USD máximo por trade
BUDGET_DEFAULT = 100

# Tickers por defecto
DEFAULT_TICKERS = {
    # Clásicas (EMA + Gap)
    "SPY":  True,
    "QQQ":  True,
    "AAPL": True,
    "TSLA": True,
    "NVDA": True,
    # Apalancadas (VWAP + Bollinger + ORB)
    "SOXL": True,
    "TSLL": True,
    "NVDL": True,
    "TQQQ": True,
}

TICKER_GROUPS = {
    "clasicas":    ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"],
    "apalancadas": ["SOXL", "TSLL", "NVDL", "TQQQ"],
}

# Tickers que puede operar cada estrategia (para el panel de semáforo)
STRATEGY_TICKERS = {
    "ema_crossover": ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"],
    "gap_fade":      ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"],
    "vwap_rsi":      ["SOXL", "TSLL", "NVDL", "TQQQ"],
    "bollinger":     ["SOXL", "TSLL", "NVDL", "TQQQ"],
    "orb":           ["SOXL", "TSLL", "NVDL", "TQQQ"],
}

# Mapeo: nombre en trades → clave canónica de estrategia.
# Los trades se loggean con el nombre "legacy" de la estrategia que generó la
# señal. Este map normaliza a las 27 canónicas que usa v2/crypto.
TRADE_STRAT_MAP = {
    # Clásicas (legacy → canonical)
    "EMA":               "ema_crossover",
    "GAP":               "gap_fade",
    "RSI_SMA200":        "rsi_sma200",
    "RSI+SMA200":        "rsi_sma200",
    "HH_LL":             "hh_ll",
    "EMA_TSI":           "ema_tsi",
    "EMA+TSI":           "ema_tsi",
    "VWAP+RSI":          "vwap_rsi",
    "VWAP_RSI":          "vwap_rsi",
    "BOLLINGER":         "bollinger",
    "ORB":               "orb",
    "SUPERTREND":        "supertrend",
    "HA_CLOUD":          "ha_cloud",
    "SQUEEZE":           "squeeze",
    "MACD_BB":           "macd_bb",
    "MACD+BB":           "macd_bb",
    "VELA_PIVOT":        "vela_pivot",
    # Nivel 1
    "RVOL_BREAKOUT":     "rvol_breakout",
    "STOP_RUN":          "stop_run",
    "VWAP_ZSCORE":       "vwap_zscore",
    "VOLUME_REVERSAL":   "volume_reversal_bar",
    "ANCHOR_VWAP":       "anchor_vwap",
    "OBV_MTF":           "obv_mtf",
    "TSV":               "tsv",
    "VW_MACD":           "vw_macd",
    "OPENING_DRIVE":     "opening_drive",
    # Nivel 2 (si el bot llegara a escribir con estos nombres)
    "VIX_MEAN_REV":      "vix_mean_rev",
    "VIX_CORRELATION":   "vix_correlation",
    "VIX_SQUEEZE":       "vix_squeeze",
    "TICK_TRIN_FADE":    "tick_trin_fade",
    "VRP_INTRADAY":      "vrp_intraday",
    # Suite "EMA 3/8 y MACD" (v3) — los bots loggean con estos nombres
    "EMA_3_8":           "ema_3_8",
    "EMA_8_21":          "ema_8_21",
    "MACD_ACCEL":        "macd_accel",
    "VOLUME_BREAKOUT":   "volume_breakout",
    "BUY_PRESSURE":      "buy_pressure",
    "SELL_PRESSURE":     "sell_pressure",
    "VWAP_MOMENTUM":     "vwap_momentum",
    "ORB_V3":            "orb_v3",
    "DONCHIAN_TURTLE":   "donchian_turtle",
    "BULLS_BSP":         "bulls_bsp",
    "NET_BSV":           "net_bsv",
    # Combos Ganadores (2026-04)
    "COMBO1_EMA_SCALPER":  "combo1_ema_scalper",
    "COMBO2_RUBBER_BAND":  "combo2_rubber_band",
    "COMBO3_NINO_SQUEEZE": "combo3_nino_squeeze",
    "COMBO4_SLIMRIBBON":   "combo4_slimribbon",
    "COMBO5_BTD":          "combo5_btd",
    "COMBO6_FRACTALCCIX":  "combo6_fractalccix",
    "COMBO7_CAMPBELL":     "combo7_campbell",
}

# Set canónico (mismo que v2/crypto). Usado por /api/strategy-stats
KNOWN_STRATEGIES = {
    "ema_crossover", "gap_fade", "rsi_sma200", "hh_ll", "ema_tsi",
    "vwap_rsi", "bollinger", "orb", "supertrend", "ha_cloud",
    "squeeze", "macd_bb", "vela_pivot",
    "rvol_breakout", "stop_run", "vwap_zscore", "volume_reversal_bar",
    "anchor_vwap", "obv_mtf", "tsv", "vw_macd", "opening_drive",
    "vix_mean_rev", "vix_correlation", "vix_squeeze",
    "tick_trin_fade", "vrp_intraday",
    # Suite "EMA 3/8 y MACD" (v3) — 11 estrategias
    "ema_3_8", "ema_8_21", "macd_accel", "volume_breakout",
    "buy_pressure", "sell_pressure", "vwap_momentum",
    "orb_v3", "donchian_turtle", "bulls_bsp", "net_bsv",
    # Combos Ganadores (2026-04) — 7 estrategias
    "combo1_ema_scalper", "combo2_rubber_band", "combo3_nino_squeeze",
    "combo4_slimribbon", "combo5_btd", "combo6_fractalccix", "combo7_campbell",
}


def get_db():
    return firestore.Client(project=GCP_PROJECT)


def get_today_trades() -> list:
    today = date.today().strftime("%Y-%m-%d")
    db    = get_db()
    doc   = db.collection(TRADES_COLLECTION).document(today).get()
    if not doc.exists:
        return []
    data   = doc.to_dict() or {}
    trades = list(data.values())
    trades.sort(key=lambda x: x.get("timestamp", ""))
    return trades


def get_strategy_config() -> dict:
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(CONFIG_DOC).get()
    if doc.exists:
        return doc.to_dict() or {}
    return {"ema_crossover": True, "sma200_filter": True,
            "gap_fade": False, "vwap_rsi": False, "bollinger": False, "orb": False}


def get_settings() -> dict:
    """Lee settings globales: budget, bot_active, active_timeframes."""
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC).get()
    defaults = {"budget": BUDGET_DEFAULT, "bot_active": True,
                "active_timeframes": [1]}
    if doc.exists:
        data = doc.to_dict() or {}
        defaults.update(data)
    # Migración: si existe candle_minutes (legado) pero no active_timeframes, convertir
    if "candle_minutes" in defaults and "active_timeframes" not in (doc.to_dict() or {}):
        defaults["active_timeframes"] = [int(defaults["candle_minutes"])]
    return defaults


def get_live_positions() -> dict:
    """
    Lee las posiciones reales del bot desde eolo-config/positions.
    Retorna dict con:
      - open_tickers:  {ticker: entry_price}  (LONGs)
      - short_tickers: {ticker: entry_price}  (SHORTs)
      - updated_at:    timestamp
    """
    try:
        db  = get_db()
        doc = db.collection(CONFIG_COLLECTION).document("positions").get()
        if doc.exists:
            data         = doc.to_dict() or {}
            positions    = data.get("positions",    {})
            entry_prices = data.get("entry_prices", {})
            updated_at   = data.get("updated_at",   "")
            open_tickers  = {t: entry_prices.get(t) for t, v in positions.items() if v == "LONG"}
            short_tickers = {t: entry_prices.get(t) for t, v in positions.items() if v == "SHORT"}
            return {"open_tickers": open_tickers, "short_tickers": short_tickers, "updated_at": updated_at}
    except Exception:
        pass
    return {"open_tickers": {}, "short_tickers": {}, "updated_at": ""}


def build_strat_positions(open_tickers: dict, short_tickers: dict, today_trades: list) -> dict:
    """
    Para cada ticker abierto (LONG o SHORT), determina qué estrategia lo abrió
    buscando el último BUY (para LONGs) o SELL_SHORT (para SHORTs) de hoy.
    Retorna {strat_key: {longs: [ticker,...], shorts: [ticker,...]}}
    """
    last_open_strat = {}  # ticker → strat_key (último trade de apertura)
    for t in today_trades:
        action = t.get("action", "")
        if action in ("BUY", "SELL_SHORT"):
            ticker = t.get("ticker", "")
            strat  = t.get("strategy", "")
            if ticker:
                last_open_strat[ticker] = TRADE_STRAT_MAP.get(strat, "")

    by_strategy = {k: {"longs": [], "shorts": []} for k in STRATEGY_TICKERS}

    for ticker in open_tickers:
        strat_key = last_open_strat.get(ticker, "")
        if strat_key and strat_key in by_strategy:
            by_strategy[strat_key]["longs"].append(ticker)
        else:
            for key, tlist in STRATEGY_TICKERS.items():
                if ticker in tlist:
                    by_strategy[key]["longs"].append(ticker)
                    break

    for ticker in short_tickers:
        strat_key = last_open_strat.get(ticker, "")
        if strat_key and strat_key in by_strategy:
            by_strategy[strat_key]["shorts"].append(ticker)
        else:
            for key, tlist in STRATEGY_TICKERS.items():
                if ticker in tlist:
                    by_strategy[key]["shorts"].append(ticker)
                    break

    return by_strategy


def get_ticker_config() -> dict:
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(TICKERS_DOC).get()
    if doc.exists:
        cfg = DEFAULT_TICKERS.copy()
        cfg.update(doc.to_dict() or {})
        return cfg
    return DEFAULT_TICKERS.copy()


def calc_pnl(trades: list) -> dict:
    by_ticker = defaultdict(list)
    for t in trades:
        by_ticker[t["ticker"]].append(t)

    total_pnl  = 0.0
    wins = losses = rounds = 0
    positions  = {}
    ticker_pnl = {}

    for ticker, ticker_trades in by_ticker.items():
        pnl = 0.0
        buy_price = None
        for trade in ticker_trades:
            price = float(trade["price"])
            shares = int(trade.get("shares", 1))
            if trade["action"] == "BUY":
                buy_price = price
                positions[ticker] = {"price": price, "time": trade["timestamp"],
                                     "shares": shares}
            elif trade["action"] == "SELL" and buy_price is not None:
                gain = (price - buy_price) * shares
                pnl += gain
                rounds += 1
                wins   += 1 if gain >= 0 else 0
                losses += 1 if gain <  0 else 0
                buy_price = None
                positions.pop(ticker, None)
        total_pnl          += pnl
        ticker_pnl[ticker]  = round(pnl, 4)

    return {
        "total_pnl":      round(total_pnl, 4),
        "wins":           wins,
        "losses":         losses,
        "rounds":         rounds,
        "win_rate":       round(wins / rounds * 100, 1) if rounds > 0 else 0,
        "open_positions": positions,
        "ticker_pnl":     ticker_pnl,
    }


# ── Routes ────────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/data")
@require_auth
def api_data():
    trades    = get_today_trades()
    stats     = calc_pnl(trades)
    config    = get_strategy_config()
    tickers   = get_ticker_config()
    settings  = get_settings()
    live_pos  = get_live_positions()
    now_et    = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")

    # Posiciones por estrategia para el panel semáforo
    strat_pos = build_strat_positions(live_pos["open_tickers"], live_pos["short_tickers"], trades)

    return jsonify({
        "trades":           trades[-50:],
        "stats":            stats,
        "strategies":       config,
        "tickers":          tickers,
        "ticker_groups":    TICKER_GROUPS,
        "settings":         settings,
        "updated_at":       now_et,
        "trade_count":      len(trades),
        "live_positions":   {
            "open":        live_pos["open_tickers"],
            "shorts":      live_pos["short_tickers"],
            "by_strategy": strat_pos,   # {strat_key: {longs:[...], shorts:[...]}}
            "updated_at":  live_pos["updated_at"],
        },
    })


@app.route("/api/toggle-strategy", methods=["POST"])
@require_auth
def toggle_strategy():
    data     = request.get_json()
    strategy = data.get("strategy")
    enabled  = data.get("enabled")
    if not strategy:
        return jsonify({"error": "strategy required"}), 400
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(CONFIG_DOC)
    doc.set({strategy: enabled}, merge=True)
    return jsonify({"ok": True, "strategy": strategy, "enabled": enabled})


@app.route("/api/toggle-ticker", methods=["POST"])
@require_auth
def toggle_ticker():
    """Activa o desactiva un ticker individual en Firestore."""
    data    = request.get_json()
    ticker  = data.get("ticker")
    enabled = data.get("enabled")
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(TICKERS_DOC)
    doc.set({ticker: enabled}, merge=True)
    return jsonify({"ok": True, "ticker": ticker, "enabled": enabled})


@app.route("/api/toggle-ticker-group", methods=["POST"])
@require_auth
def toggle_ticker_group():
    """Activa o desactiva todos los tickers de un grupo."""
    data    = request.get_json()
    group   = data.get("group")
    enabled = data.get("enabled")
    tickers = TICKER_GROUPS.get(group, [])
    if not tickers:
        return jsonify({"error": "group not found"}), 400
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(TICKERS_DOC)
    doc.set({t: enabled for t in tickers}, merge=True)
    return jsonify({"ok": True, "group": group, "tickers": tickers, "enabled": enabled})


@app.route("/api/set-budget", methods=["POST"])
@require_auth
def set_budget():
    """Cambia el budget por trade (USD). Respeta min/max."""
    data   = request.get_json()
    amount = data.get("budget")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    amount = max(BUDGET_MIN, min(BUDGET_MAX, amount))
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
    doc.set({"budget": amount}, merge=True)
    return jsonify({"ok": True, "budget": amount,
                    "budget_min": BUDGET_MIN, "budget_max": BUDGET_MAX})


@app.route("/api/toggle-active", methods=["POST"])
@require_auth
def toggle_active():
    """Pausa o reanuda el bot globalmente (bot_active flag en Firestore)."""
    data    = request.get_json()
    enabled = bool(data.get("enabled", True))
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
    doc.set({"bot_active": enabled}, merge=True)
    return jsonify({"ok": True, "bot_active": enabled})


@app.route("/api/toggle-timeframe", methods=["POST"])
@require_auth
def toggle_timeframe():
    """
    Activa o desactiva un timeframe individual.
    Múltiples timeframes pueden estar activos simultáneamente.
    candle_minutes: 1 | 5 | 15 | 30 | 60 | 240 | 1440
    enabled: true | false
    El bot lee active_timeframes en cada ciclo y corre estrategias para cada uno.
    """
    data    = request.get_json()
    minutes = data.get("candle_minutes")
    enabled = data.get("enabled")
    valid   = [1, 5, 15, 30, 60, 240, 1440]
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid candle_minutes"}), 400
    if minutes not in valid:
        return jsonify({"error": f"candle_minutes must be one of {valid}"}), 400

    db   = get_db()
    doc  = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
    snap = doc.get()
    current = list(snap.to_dict().get("active_timeframes", [1])) if snap.exists else [1]

    if enabled and minutes not in current:
        current.append(minutes)
    elif not enabled and minutes in current:
        current.remove(minutes)

    # Nunca dejar la lista vacía — mínimo 1m siempre activo
    if not current:
        current = [1]

    current.sort()
    doc.set({"active_timeframes": current}, merge=True)
    labels = {1:"1m", 5:"5m", 15:"15m", 30:"30m", 60:"1h", 240:"4h", 1440:"1d"}
    label_list = [labels[m] for m in current if m in labels]
    return jsonify({"ok": True, "active_timeframes": current,
                    "labels": label_list})


@app.route("/api/config", methods=["GET", "POST"])
@require_auth
def api_config():
    """
    GET  → devuelve el doc eolo-config/settings (budget, max_positions,
           default_stop_loss_pct, default_take_profit_pct, daily_loss_cap_pct).
    POST → merge de los campos del modal Config (whitelisted y validados).

    Nota: v1 no tiene Claude Bot, por eso no hay claude_bot_budget.
    El budget aquí es USD por trade (no %), a diferencia de Crypto/v2.
    """
    try:
        db = get_db()
        doc_ref = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)

        if request.method == "GET":
            snap = doc_ref.get()
            return jsonify(snap.to_dict() or {} if snap.exists else {})

        data = request.get_json(silent=True) or {}
        allowed: dict = {}

        if "budget_per_trade" in data:
            try:
                v = float(data["budget_per_trade"])
                if not (BUDGET_MIN <= v <= BUDGET_MAX):
                    raise ValueError("range")
                # v1 usa 'budget' como clave canónica — espejamos ambos
                # para que el bot lea cualquiera de las dos.
                allowed["budget"]           = v
                allowed["budget_per_trade"] = v
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"budget_per_trade inválido ({BUDGET_MIN}-{BUDGET_MAX})"}), 400

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

        import time as _time
        allowed["updated_ts"] = _time.time()
        allowed["updated_at"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        doc_ref.set(allowed, merge=True)
        return jsonify({"ok": True, "config": allowed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/schedule-status")
@require_auth
def api_schedule_status():
    """
    Devuelve el estado de trading_hours calculado on-the-fly desde el doc
    eolo-config/settings (v1 no tiene state writer con el payload).
    El front lo usa para el banner "Pause time limit" y los campos del modal.
    """
    import sys as _sys, os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _parent = _os.path.dirname(_here)
    if _parent not in _sys.path:
        _sys.path.insert(0, _parent)
    try:
        from eolo_common.trading_hours import (
            DEFAULTS_EQUITY,
            load_schedule,
            format_schedule_for_api,
            now_et,
        )
    except Exception as e:
        return jsonify({"error": f"trading_hours module no disponible: {e}"}), 500

    try:
        db = get_db()
        snap = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC).get()
        cfg = snap.to_dict() if snap.exists else {}
    except Exception as e:
        return jsonify({"error": f"firestore read falló: {e}"}), 500

    sch = load_schedule(cfg or {}, defaults=DEFAULTS_EQUITY)
    payload = format_schedule_for_api(sch, now_et())
    payload["market_open"] = payload.get("is_within_window", True)
    return jsonify(payload)


@app.route("/api/close-all-positions", methods=["POST"])
@require_auth
def close_all_positions():
    """
    Escribe un flag en Firestore para que el bot cierre todas las posiciones
    abiertas en el próximo ciclo (5 min máx).
    """
    db  = get_db()
    doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
    doc.set({"close_all": True, "close_all_ts": datetime.now(EASTERN).isoformat()}, merge=True)
    return jsonify({"ok": True, "message": "Close-all flag set — bot will close all positions on next cycle"})


@app.route("/api/performance")
@require_auth
def api_performance():
    """
    Retorna métricas de performance del día actual + historial de los últimos 30 días.
    - Por estrategia: trades, wins, losses, avg_win, avg_loss, pnl
    - Por ticker: pnl, trades
    - Equity curve del día (P&L acumulado por hora)
    - Historial diario: últimos 30 días de P&L total
    - Top trades (best / worst del día)
    """
    db = get_db()

    # ── Día actual ───────────────────────────────────────────
    today        = date.today().strftime("%Y-%m-%d")
    today_trades = get_today_trades()

    # Métricas por estrategia
    strat_stats = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "win_amounts": [], "loss_amounts": [], "pnl": 0.0, "tickers": set()
    })

    # Métricas por ticker
    ticker_stats = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "strategy": ""})

    # Equity curve: P&L acumulado por hora (9:30–16:00)
    equity_by_hour = defaultdict(float)

    # Para calcular trades completos (BUY→SELL) agrupados por ticker+estrategia
    open_buys = {}  # key: (ticker, strategy)

    # Top trades
    all_trades_pnl = []

    for t in today_trades:
        ticker   = t.get("ticker", "?")
        action   = t.get("action", "")
        strategy = t.get("strategy", "EMA")
        price    = float(t.get("price", 0))
        shares   = int(t.get("shares", 1))
        ts       = t.get("timestamp", "")
        hour_key = ts[11:13] + ":00" if len(ts) >= 13 else "??"

        key = (ticker, strategy)
        if action == "BUY":
            open_buys[key] = {"price": price, "shares": shares, "hour": hour_key}
        elif action == "SELL" and key in open_buys:
            buy  = open_buys.pop(key)
            gain = (price - buy["price"]) * buy["shares"]

            s = strat_stats[strategy]
            s["trades"] += 1
            s["pnl"]    += gain
            s["tickers"].add(ticker)
            if gain >= 0:
                s["wins"] += 1
                s["win_amounts"].append(gain)
            else:
                s["losses"] += 1
                s["loss_amounts"].append(gain)

            ticker_stats[ticker]["pnl"]    += gain
            ticker_stats[ticker]["trades"] += 1
            ticker_stats[ticker]["strategy"] = strategy
            equity_by_hour[hour_key]       += gain
            all_trades_pnl.append({"ticker": ticker, "strategy": strategy,
                                    "pnl": round(gain, 2), "hour": hour_key})

    # Formatear estrategias
    strat_result = {}
    for name, s in strat_stats.items():
        strat_result[name] = {
            "trades":   s["trades"],
            "wins":     s["wins"],
            "losses":   s["losses"],
            "win_rate": round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0,
            "avg_win":  round(sum(s["win_amounts"])  / len(s["win_amounts"]),  2) if s["win_amounts"]  else 0,
            "avg_loss": round(sum(s["loss_amounts"]) / len(s["loss_amounts"]), 2) if s["loss_amounts"] else 0,
            "pnl":      round(s["pnl"], 2),
            "tickers":  sorted(s["tickers"]),
        }

    # Formatear ticker P&L para heatmap
    ticker_result = {t: {"pnl": round(v["pnl"], 2), "trades": v["trades"],
                         "strategy": v["strategy"]}
                     for t, v in ticker_stats.items()}

    # Equity curve ordenada
    hours_sorted = sorted(equity_by_hour.keys())
    cumulative, running = [], 0.0
    for h in hours_sorted:
        running += equity_by_hour[h]
        cumulative.append({"hour": h, "pnl": round(running, 2)})

    # Top / worst trades
    all_trades_pnl.sort(key=lambda x: x["pnl"], reverse=True)
    top_trades   = all_trades_pnl[:5]
    worst_trades = sorted(all_trades_pnl, key=lambda x: x["pnl"])[:3]

    # ── Historial últimos 30 días ─────────────────────────────
    history = []
    for i in range(29, -1, -1):
        day_str = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            doc = db.collection(TRADES_COLLECTION).document(day_str).get()
            if doc.exists:
                day_trades = list((doc.to_dict() or {}).values())
                day_pnl    = 0.0
                day_buys   = {}
                for t in sorted(day_trades, key=lambda x: x.get("timestamp", "")):
                    ticker   = t.get("ticker", "?")
                    strategy = t.get("strategy", "EMA")
                    action   = t.get("action", "")
                    price    = float(t.get("price", 0))
                    shares   = int(t.get("shares", 1))
                    k = (ticker, strategy)
                    if action == "BUY":
                        day_buys[k] = {"price": price, "shares": shares}
                    elif action == "SELL" and k in day_buys:
                        b = day_buys.pop(k)
                        day_pnl += (price - b["price"]) * b["shares"]
                history.append({"date": day_str, "pnl": round(day_pnl, 2)})
            else:
                history.append({"date": day_str, "pnl": None})  # sin datos
        except Exception:
            history.append({"date": day_str, "pnl": None})

    return jsonify({
        "date":          today,
        "strategies":    strat_result,
        "ticker_pnl":    ticker_result,
        "equity_curve":  cumulative,
        "top_trades":    top_trades,
        "worst_trades":  worst_trades,
        "history":       history,
    })


@app.route("/api/strategies")
@require_auth
def api_strategies():
    """
    Retorna el estado actual de los toggles en formato canónico (27 keys).
    El doc en Firestore puede contener solo algunas keys; las faltantes default True.
    """
    try:
        db  = get_db()
        doc = db.collection(CONFIG_COLLECTION).document(CONFIG_DOC).get()
        override = (doc.to_dict() or {}) if doc.exists else {}
        merged   = {k: True for k in KNOWN_STRATEGIES}
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
    Agrega stats por estrategia canónica leyendo eolo-trades (daily docs).
    Normaliza nombres legacy (EMA, GAP, VWAP_RSI, ...) → canonical via
    TRADE_STRAT_MAP. Usa pnl_usd del SELL si está presente; si no, matchea
    BUY↔SELL FIFO por (ticker, strategy).

    Retorna para cada strategy canonical:
      h24:  {trades, wins, losses, net_pnl, series: [{ts, pnl, cum_pnl}]}
      week: {trades, wins, losses, net_pnl}
    """
    try:
        import time as _time
        db  = get_db()
        now = _time.time()
        h24_cutoff  = now - 86400
        week_cutoff = now - 7 * 86400

        stats = {
            k: {
                "h24":  {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "series": []},
                "week": {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
            }
            for k in KNOWN_STRATEGIES
        }

        # Leer los últimos 8 docs diarios (7d + hoy)
        doc_ids = [
            (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(8)
        ]

        # Matching FIFO: agrupar por (ticker, canonical_strat)
        open_buys: dict = {}

        # Ordenar trades por timestamp para FIFO correcto
        all_trades: list = []
        for doc_id in doc_ids:
            doc = db.collection(TRADES_COLLECTION).document(doc_id).get()
            if not doc.exists:
                continue
            data = doc.to_dict() or {}
            for _, trade in data.items():
                if isinstance(trade, dict):
                    all_trades.append(trade)
        all_trades.sort(key=lambda t: t.get("timestamp", ""))

        for trade in all_trades:
            action = str(trade.get("action", ""))
            raw_strat = str(trade.get("strategy", "")).strip()
            canon = TRADE_STRAT_MAP.get(raw_strat, raw_strat.lower())
            if canon not in stats:
                continue
            ticker = str(trade.get("ticker", ""))
            shares = int(trade.get("shares", 1) or 1)
            price  = float(trade.get("price", 0) or 0)
            ts_str = str(trade.get("timestamp", ""))
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                continue

            if action == "BUY":
                open_buys.setdefault((ticker, canon), []).append(
                    {"price": price, "shares": shares, "ts": ts}
                )
                continue

            if action != "SELL":
                continue

            # SELL: preferimos pnl_usd si está persistido; si no, FIFO match
            pnl = trade.get("pnl_usd")
            if pnl is None:
                q = open_buys.get((ticker, canon), [])
                if not q:
                    continue
                b = q.pop(0)
                pnl = round((price - b["price"]) * b["shares"], 2)
            else:
                pnl = float(pnl)
                # pop matching BUY para no double-contar si queda colgado
                q = open_buys.get((ticker, canon), [])
                if q:
                    q.pop(0)

            if ts < week_cutoff:
                continue
            is_win = pnl > 0
            stats[canon]["week"]["trades"]  += 1
            stats[canon]["week"]["net_pnl"] += pnl
            if is_win: stats[canon]["week"]["wins"]   += 1
            else:       stats[canon]["week"]["losses"] += 1
            if ts >= h24_cutoff:
                stats[canon]["h24"]["trades"]  += 1
                stats[canon]["h24"]["net_pnl"] += pnl
                if is_win: stats[canon]["h24"]["wins"]   += 1
                else:       stats[canon]["h24"]["losses"] += 1
                stats[canon]["h24"]["series"].append({"ts": ts, "pnl": pnl})

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


@app.route("/daily-open-reset", methods=["GET", "POST"])
def daily_open_reset():
    """
    Disparado por Cloud Scheduler a las 9:30am ET (lunes–viernes).
    1. Setea close_all flag en Firestore (el bot lo ejecuta en el próximo ciclo)
    2. Limpia el doc de trades de ayer en Firestore (P&L del día vuelve a $0)
    3. Resetea daily loss cap en Firestore para que el bot pueda tradear de nuevo
    4. Loguea el reset para auditoría

    Idempotente: si no hay posiciones abiertas, el bot ignora el flag sin daño.
    """
    import time as _time
    results = {}
    db = get_db()

    # ── 1. Setear close_all flag en Firestore ─────────────────
    try:
        doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
        doc.set({
            "close_all":    True,
            "close_all_ts": datetime.now(EASTERN).isoformat(),
        }, merge=True)
        results["close_all_flag"] = "ok — bot ejecutará en próximo ciclo"
    except Exception as e:
        results["close_all_flag"] = f"error: {e}"

    # ── 2. Limpiar P&L de ayer en Firestore ──────────────────
    try:
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        db.collection(TRADES_COLLECTION).document(yesterday).delete()
        results["firestore_cleanup"] = f"eliminado eolo-trades/{yesterday}"
    except Exception as e:
        results["firestore_cleanup"] = f"error: {e}"

    # ── 3. Resetear daily loss cap en Firestore ───────────────
    try:
        doc = db.collection(CONFIG_COLLECTION).document(SETTINGS_DOC)
        doc.set({
            "daily_loss_cap_triggered": False,
            "daily_reset_ts":           _time.time(),
            "daily_reset_at":           datetime.now(EASTERN).isoformat(),
        }, merge=True)
        results["daily_cap_reset"] = "ok"
    except Exception as e:
        results["daily_cap_reset"] = f"error: {e}"

    from loguru import logger as _logger
    _logger.warning(
        f"[DAILY_OPEN_RESET] ✅ V1 Reset completado a las 9:30am ET | {results}"
    )
    return jsonify({"ok": True, "results": results}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
