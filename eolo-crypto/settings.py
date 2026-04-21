# ============================================================
#  EOLO Crypto — Settings configurables
#
#  Todos los parámetros editables viven acá. Podés:
#    a) Editar estas constantes y redeployar.
#    b) Override via variable de entorno al crear el servicio
#       Cloud Run (--set-env-vars) — útil para toggles sin rebuild.
#
#  Seguir el mismo patrón que eolo-options lo hace en `claude/`
#  y `eolo_v2_main.py`: las constantes son la fuente de verdad,
#  y env-vars son override solo cuando están seteados.
# ============================================================
import os


# ── Modo de operación ─────────────────────────────────────
# TESTNET: usa testnet.binance.vision (balance ficticio, órdenes reales al testnet)
# PAPER:   simula todo localmente, solo loggea a CSV/Firestore, no manda nada a Binance
# LIVE:    binance.com con API key real ⚠️ DINERO REAL
BINANCE_MODE = os.environ.get("BINANCE_MODE", "TESTNET")  # TESTNET | PAPER | LIVE

# ── Endpoints (Binance global) ────────────────────────────
ENDPOINTS = {
    "TESTNET": {
        "rest": "https://testnet.binance.vision",
        # IMPORTANTE: el WS del testnet está en otro subdominio (stream.testnet...)
        # "testnet.binance.vision" sirve REST, pero "stream.testnet.binance.vision:9443"
        # es el WS. Si mezclás los dominios devuelve HTTP 404 en el handshake.
        "ws":   "wss://stream.testnet.binance.vision:9443/ws",
        "ws_combined": "wss://stream.testnet.binance.vision:9443/stream",
    },
    "PAPER": {
        # En paper leemos datos reales (producción) pero no mandamos órdenes
        "rest": "https://api.binance.com",
        "ws":   "wss://stream.binance.com:9443/ws",
        "ws_combined": "wss://stream.binance.com:9443/stream",
    },
    "LIVE": {
        "rest": "https://api.binance.com",
        "ws":   "wss://stream.binance.com:9443/ws",
        "ws_combined": "wss://stream.binance.com:9443/stream",
    },
}


def get_endpoint(kind: str) -> str:
    """kind: 'rest' | 'ws' | 'ws_combined'"""
    return ENDPOINTS[BINANCE_MODE][kind]


# ── Universo de trading ───────────────────────────────────
# Core fijo: top 10 USDT pairs
CORE_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "TRXUSDT", "LINKUSDT",
]

# Screener dinámico: agrega hasta N small-caps con momentum
SCREENER_ENABLED       = True
SCREENER_INTERVAL_SEC  = 10 * 60          # cada 10 min
SCREENER_TOP_N         = 5                # agregar hasta 5 candidatos al universo
SCREENER_MIN_VOLUME    = 20_000_000       # quoteVolume USDT 24h mínimo
SCREENER_MAX_VOLUME    = 500_000_000      # que no sea top-cap ya incluida
SCREENER_MIN_CHANGE    = 3.0              # % cambio 24h (momentum mínimo)
SCREENER_MAX_CHANGE    = 25.0             # % cambio 24h (evitar pumps suicidas)
SCREENER_EXCLUDE       = {
    # Stablecoins
    "USDTUSDC", "USDCUSDT", "BUSDUSDT", "DAIUSDT", "FDUSDUSDT", "TUSDUSDT",
    # Wrapped
    "WBTCUSDT", "WETHUSDT", "CBETHUSDT",
    # Leveraged tokens (muy volátiles, decay de funding)
    # Binance los suspendió en 2023 pero por las dudas
}

# ── Gestión de capital ────────────────────────────────────
# Tamaño de cada posición. Podés elegir entre modo PERCENT (% del balance)
# o FIXED (USDT fijo por trade).
POSITION_SIZING_MODE   = os.environ.get("POSITION_SIZING_MODE", "PERCENT")  # PERCENT | FIXED
POSITION_SIZE_PCT      = float(os.environ.get("POSITION_SIZE_PCT",   "2.0"))   # % balance USDT por trade
POSITION_SIZE_USDT     = float(os.environ.get("POSITION_SIZE_USDT", "100.0")) # USDT por trade (modo FIXED)

MAX_OPEN_POSITIONS     = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))
MAX_POSITIONS_PER_PAIR = 1                # una sola posición abierta por par

# Stop-loss / take-profit default por si la estrategia no define el suyo
DEFAULT_STOP_LOSS_PCT     = float(os.environ.get("DEFAULT_STOP_LOSS_PCT",   "2.5"))
DEFAULT_TAKE_PROFIT_PCT   = float(os.environ.get("DEFAULT_TAKE_PROFIT_PCT", "5.0"))

# Daily loss cap — si el P&L del día baja de esto, el bot se detiene hasta el día siguiente
DAILY_LOSS_CAP_PCT        = float(os.environ.get("DAILY_LOSS_CAP_PCT", "-5.0"))


# ── Estrategias ───────────────────────────────────────────
# Las 13 técnicas Eolo v1 (imports de ../Bot/) — toggle individual.
STRATEGIES_ENABLED = {
    "rsi_sma200":    True,
    "bollinger":     True,
    "macd_bb":       True,
    "supertrend":    True,
    "vwap_rsi":      True,
    "orb":           True,    # adaptado a 24/7 (ver strategies/crypto_adapters.py)
    "squeeze":       True,
    "hh_ll":         True,
    "ha_cloud":      True,
    "ema_tsi":       True,
    "vela_pivot":    True,
    "gap":           True,    # adaptado: vela 00:00 UTC vs 23:59 anterior
    "base":          False,   # utility module de Eolo v1 — no tiene detect_signal
    # ── Nivel 1 (trading_strategies_v2.md) — aplicables a crypto
    # (las 7 que NO dependen de sesión US / VIX / TICK / TRIN).
    # Decisión Juan 2026-04-20: todas ON por default.
    "rvol_breakout":       True,
    "stop_run":            True,
    "vwap_zscore":         True,   # filtro 11:30 ET deja crypto acotado a ~5h/día
    "volume_reversal_bar": True,
    "obv_mtf":             True,
    "tsv":                 True,
    "vw_macd":             True,
    # ── Suite "EMA 3/8 y MACD" (v3) — 10 estrategias ──────
    # ORB_V3 NO se incluye en crypto (es equity-only: requiere RTH).
    # tod_filter se fuerza a False en el adapter — 24/7 UTC.
    "ema_3_8":             True,
    "ema_8_21":            True,
    "macd_accel":          True,
    "volume_breakout":     True,
    "buy_pressure":        True,
    "sell_pressure":       True,
    "vwap_momentum":       True,
    "donchian_turtle":     True,
    "bulls_bsp":           True,   # breadth columns ausentes → degrade a True
    "net_bsv":             True,
}

# Claude Bot #14 — motor con Anthropic API adaptado a crypto
# Haiku 4.5 es ~10-15x más barato que Sonnet 4.6 y alcanza para decisiones
# estructuradas. Override con env CLAUDE_MODEL si querés probar Sonnet puntual.
CLAUDE_BOT_ENABLED        = True
CLAUDE_MODEL              = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
CLAUDE_INTERVAL_SEC       = int(os.environ.get("CLAUDE_INTERVAL_SEC", str(10 * 60)))  # 10 min (antes 5) — crypto es 24/7, no necesita reaccionar tan rápido
CLAUDE_MAX_COST_PER_DAY   = float(os.environ.get("CLAUDE_MAX_COST_PER_DAY", "3.0"))   # USD/día — cortocircuito de seguridad


# ── Timeframes y buffers ──────────────────────────────────
KLINE_INTERVAL      = "1m"                # vela base de streaming (ajustable: 1m|5m|15m)
BUFFER_SIZE         = 300                 # velas en memoria por par (suficiente para SMA200)
HISTORICAL_LOAD     = 250                 # velas a backfill al arrancar (REST /klines)


# ── Logging y observabilidad ──────────────────────────────
LOG_LEVEL                = os.environ.get("LOG_LEVEL", "INFO")
FIRESTORE_STATE_DOC      = "current"
FIRESTORE_STATE_COLLECTION = "eolo-crypto-state"
FIRESTORE_TRADES_COLLECTION = "eolo-crypto-trades"
FIRESTORE_CLAUDE_COLLECTION = "eolo-crypto-claude-decisions"

# ── GCP ──────────────────────────────────────────────────
GCP_PROJECT_ID           = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
SECRET_BINANCE_KEY_NAME  = "binance-testnet-api-key"      # Secret Manager name
SECRET_BINANCE_SEC_NAME  = "binance-testnet-api-secret"   # Secret Manager name
SECRET_ANTHROPIC_NAME    = "ANTHROPIC_API_KEY"            # Reusa el mismo secret que v2 (mayúsculas + underscore)


# ── Exchange info cache (tick/step size por símbolo) ─────
# Refrescamos cada N min porque Binance a veces actualiza filters
EXCHANGE_INFO_TTL_SEC    = 60 * 60        # 1 hora


# ── Watchdog / health ────────────────────────────────────
WATCHDOG_RESTART_DELAY   = 15             # seg antes de reiniciar el bot si crashea
WS_RECONNECT_BACKOFF_MIN = 5
WS_RECONNECT_BACKOFF_MAX = 120
