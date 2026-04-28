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
# Backtests (FASE 4/5/7a + análisis cripto) mostraron que en 1m
# prácticamente TODAS las estrategias pierden por ruido + fees 0.2%
# Binance. A 4h solo dejamos las 5 más robustas para crypto 24/7:
#   • supertrend  — trend-following, funciona bien en 4h crypto
#   • rsi_sma200  — régimen (SMA200) + momentum (RSI), filtro fuerte
#   • rvol_breakout — breakout por volumen relativo, ideal en crypto
#   • vwap_rsi    — distancia VWAP + RSI, señales de reversión/continuación
#   • bollinger   — bandas universales, crypto suele respetar desviaciones
#
# El resto se apaga por defecto; se puede reactivar individualmente
# desde el dashboard (Firestore → eolo-crypto-config/settings).
#
# Para activar los FASE-4/5/7a winners de equity en crypto cuando
# se tenga suficiente data real de backtesting, poner True.
STRATEGIES_ENABLED = {
    # ── Core 5 activos para 4h crypto ──────────────────────
    "supertrend":          True,
    "rsi_sma200":          True,
    "rvol_breakout":       True,
    "vwap_rsi":            True,
    "bollinger":           True,
    # ── Crypto-native (2026-04-27) ──────────────────────────
    "liquidation_cascade": True,   # Post-liquidation bounce (4h, drop>3% + vol>2×MA20)
    "funding_rate_carry":  True,   # Contrarian: funding negativo extremo → BUY
    "weekend_breakout":    True,   # BTC/ETH: breakout sobre Friday high el domingo
    "btc_lead_lag":        True,   # Altcoins: entrar cuando BTC movió pero altcoin no siguió
    # ── Resto apagado — requieren más data de validación en crypto
    "macd_bb":             False,
    "orb":                 False,   # equity-only: RTH opening range
    "squeeze":             False,
    "hh_ll":               False,
    "ha_cloud":            False,
    "ema_tsi":             False,
    "vela_pivot":          False,
    "gap":                 False,
    "base":                False,   # utility module, no tiene detect_signal
    "stop_run":            False,
    "vwap_zscore":         False,
    "volume_reversal_bar": False,
    "obv_mtf":             False,
    "tsv":                 False,
    "vw_macd":             False,
    "ema_3_8":             False,
    "ema_8_21":            False,
    "macd_accel":          False,
    "volume_breakout":     False,
    "buy_pressure":        False,
    "sell_pressure":       False,
    "vwap_momentum":       False,
    "donchian_turtle":     False,
    "bulls_bsp":           False,
    "net_bsv":             False,
    # ── FASE 4/5/7a winners — probados en equity, no en crypto 4h
    "bollinger_rsi_sensitive": False,
    "xom_30m":                 False,
    "macd_confluence_fase7a":  False,
    "momentum_score_fase7a":   False,
}

# Mínimo de estrategias que deben coincidir en la misma dirección
# para que se ejecute un trade. Con 5 estrategias activas, >= 2
# significa 40% de acuerdo (evita trades de una sola estrategia).
# Sube a 3 para ser más conservador.
MIN_STRATEGY_CONSENSUS = 1

# Claude Bot #14 — motor con Anthropic API adaptado a crypto
# Haiku 4.5 es ~10-15x más barato que Sonnet 4.6 y alcanza para decisiones
# estructuradas. Override con env CLAUDE_MODEL si querés probar Sonnet puntual.
CLAUDE_BOT_ENABLED        = True
CLAUDE_MODEL              = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
CLAUDE_INTERVAL_SEC       = int(os.environ.get("CLAUDE_INTERVAL_SEC", str(10 * 60)))  # 10 min (antes 5) — crypto es 24/7, no necesita reaccionar tan rápido
CLAUDE_MAX_COST_PER_DAY   = float(os.environ.get("CLAUDE_MAX_COST_PER_DAY", "3.0"))   # USD/día — cortocircuito de seguridad


# ── Timeframes y buffers ──────────────────────────────────
KLINE_INTERVAL      = "1m"                # vela base de streaming
BUFFER_SIZE         = 2000                # 2000 velas 1m (~33h) — suficiente para 30 velas de 1h
HISTORICAL_LOAD     = 1000                # máximo por call REST Binance (/api/v3/klines limit=1000)

# Timeframes activos para evaluación de señales.
# [1, 15, 60]: multi-TF apropiado para crypto volátil (24/7).
#   1m  → reacciona rápido a movimientos, arranca con 250 velas backfill
#   15m → confirma tendencia (necesita 30×15=450 velas ~7.5h de acumulación)
#   60m → contexto horario (necesita 30×60=1800 velas ~30h de acumulación)
# Consensus=3 estrategias filtra ruido: requiere convicción en múltiples TF.
# Override via Firestore sin redeploy.
ACTIVE_TIMEFRAMES   = [1, 15, 60]         # 1m + 15m + 1h (multi-TF crypto)


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
