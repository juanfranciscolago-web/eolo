# ============================================================
#  CROP — Theta Harvest Bot (Copia limpia de Eolo v2)
#
#  Especializado en vender spreads de crédito usando Theta Harvest.
#  Mantiene toda la infraestructura de v2 (config, hours, macro, pivots)
#  pero SOLO ejecuta Theta Harvest (sin VRP, 0DTE, Earnings, PutSkew).
#
#  Módulos:
#    1. SchwabStream     → quotes L1 + velas 1min en tiempo real
#    2. OptionChainFetcher → cadena de opciones cada 30s
#    3. OptionsBrain     → Claude API decide spread entry/exit
#    4. OptionsTrader    → ejecuta spreads en Schwab
#    5. Theta Harvest    → monitor y cierre de posiciones
#    6. Pivots           → análisis de pivots y riesgo
#    7. MacroNews        → filtro de noticias macroeconómicas
#
#  Características:
#    - Live P&L (realized + unrealized + credit_total)
#    - Daily P&L tracking
#    - Config modal con risk controls (SL/TP/DLC)
#    - Trading hours configurables
#    - Macro filters
#    - Sheets sync
#    - Telegram alerts
#
#  Auto-close: todas las posiciones se cierran a las 15:27 ET
#
#  Variables de entorno requeridas:
#    ANTHROPIC_API_KEY   : API key de Anthropic
#    GOOGLE_CLOUD_PROJECT: proyecto GCP (para Firestore tokens)
#
#  Uso:
#    python crop_main.py
# ============================================================
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from loguru import logger

# ── Ajustar path ──────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, ".."))        # raíz eolo/
sys.path.insert(0, os.path.join(BASE_DIR, "..", "Bot")) # estrategias v1

# ── Imports de módulos CROP (Theta Harvest only) ────────────────────────────
from stream.options_stream import SchwabStream
from stream.options_chain  import OptionChainFetcher
from analysis.greeks       import enrich_contract
from analysis.iv_surface   import IVSurface
from claude.options_brain  import OptionsBrain
from claude.claude_bot     import ClaudeBotEngine
from execution.options_trader import OptionsTrader, _send_telegram
from theta_harvest import scan_theta_harvest_tranches, ThetaHarvestSignal
from theta_harvest.theta_harvest_strategy import (
    evaluate_open_position,
    should_close_for_eod,
    TARGET_DTES,
    FORCE_CLOSE_HOUR_0DTE,
    FORCE_CLOSE_HOUR_1TO4DTE,
    _determine_spread_type,
    ENTRY_HOUR_ET,
    ENTRY_WINDOW_MINUTES,
)
from theta_harvest.pivot_analysis import (
    analyze_pivots, format_pivot_summary,
    fetch_tick_ad, TickADContext,
)
from theta_harvest.macro_news_filter import is_news_day, log_calendar_status

# ── REMOVED: VRP, 0DTE Gamma Scalp, Earnings IV, Put Skew (CROP es theta-only) ────
# ── Multi-TF compartido (paquete común eolo_common) ───────
# Vive en /eolo_common/ del repo root. Dockerfile lo copia a /app/eolo_common.
import sys as _sys, os as _os
_PARENT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _PARENT not in _sys.path:
    _sys.path.insert(0, _PARENT)
from eolo_common.multi_tf import (
    CandleBuffer, BufferMarketData, ConfluenceFilter, load_multi_tf_config,
)
from eolo_common.multi_tf.normalize import from_schwab_chart_equity
from eolo_common.routing import AutoRouter as _AutoRouter  # Strategy Auto-Router
from eolo_common.trading_hours import (
    DEFAULTS_EQUITY,
    TradingSchedule,
    load_schedule,
    is_within_trading_window,
    is_after_auto_close as _tr_is_after_auto_close,
    now_et,
)

# ── Estrategias Eolo v1 (se importan con lazy-import para no romper
#    si alguna dependencia falta en el entorno de opciones) ──────────
try:
    import bot_strategy           as _strat_ema
    import bot_gap_strategy       as _strat_gap
    import bot_vwap_rsi_strategy  as _strat_vwap
    import bot_bollinger_strategy as _strat_bollinger
    import bot_orb_strategy       as _strat_orb
    import bot_rsi_sma200_strategy as _strat_rsi_sma200
    import bot_supertrend_strategy as _strat_supertrend
    import bot_hh_ll_strategy     as _strat_hh_ll
    import bot_ha_cloud_strategy  as _strat_ha_cloud
    import bot_squeeze_strategy   as _strat_squeeze
    import bot_macd_bb_strategy   as _strat_macd_bb
    import bot_ema_tsi_strategy   as _strat_ema_tsi
    import bot_vela_pivot_strategy as _strat_vela_pivot
    # ── Nivel 1 (trading_strategies_v2.md) ──────────────
    import bot_rvol_breakout_strategy        as _strat_rvol
    import bot_stop_run_strategy             as _strat_stop_run
    import bot_vwap_zscore_strategy          as _strat_vwap_z
    import bot_volume_reversal_bar_strategy  as _strat_vrb
    import bot_anchor_vwap_strategy          as _strat_anchor_vwap
    import bot_obv_strategy                  as _strat_obv
    import bot_tsv_strategy                  as _strat_tsv
    import bot_vw_macd_strategy              as _strat_vw_macd
    import bot_opening_drive_strategy        as _strat_opening_drive
    # ── Nivel 2 (requieren macro feeds) ─────────────────
    import bot_vix_mean_reversion_strategy   as _strat_vix_mr
    import bot_vix_correlation_strategy      as _strat_vix_corr
    import bot_vix_squeeze_strategy          as _strat_vix_sq
    import bot_tick_trin_fade_strategy       as _strat_tt
    import bot_vrp_strategy                  as _strat_vrp
    # ── "EMA 3/8 y MACD" suite (v3) — dispatcher compartido con V1 ──
    import bot_strategies_v3_dispatcher      as _strat_v3
    _STRATEGIES_AVAILABLE = True
    logger.info("✅ Estrategias Eolo v1 + Nivel 1/2 + v3 importadas correctamente")
except ImportError as e:
    _STRATEGIES_AVAILABLE = False
    logger.warning(f"⚠️  Estrategias v1 no disponibles: {e}")

# ── Configuración ──────────────────────────────────────────

TICKERS = ["SPY", "QQQ", "IWM", "TQQQ"]

# Tickers habilitados para Theta Harvest (credit spreads 0-5 DTE)
THETA_HARVEST_TICKERS = ["SPY", "QQQ", "IWM", "TQQQ"]

# ⚠️  PAPER TRADING — cambiar a False para ir live
# En paper mode: órdenes simuladas, CSV log, sin llamadas reales a Schwab
PAPER_TRADING = True

# Intervalo mínimo entre análisis por ticker (segundos)
# Para no spamear Claude API. Subido de 60s → 180s para bajar consumo ~3x
# sin perder capacidad de reacción (las estrategias técnicas corren en cada
# candle de 1min igualmente, esto solo afecta la frecuencia de decisiones Claude).
ANALYSIS_INTERVAL = int(os.environ.get("ANALYSIS_INTERVAL", "180"))

# Hora de auto-close (ET)
AUTO_CLOSE_HOUR   = 15
AUTO_CLOSE_MINUTE = 27

# Máximo de posiciones abiertas simultáneas por ticker
MAX_POSITIONS_PER_TICKER = 2

# Buffer de velas por ticker (para indicadores Eolo v1)
CANDLE_BUFFER_SIZE = 100

# ── Helpers de serialización para Firestore ────────────────
#
# Firestore SOLO acepta: None, bool, int, float, str, bytes, datetime,
# list, dict, GeoPoint, DocumentReference. Todo lo demás (numpy.int64,
# numpy.float64, set, frozenset, Decimal, np.ndarray, etc.) revienta con
# "bad argument type for built-in operation".
#
# _sanitize_for_firestore: convierte recursivamente a tipos nativos.
# _find_unserializable_path: recorre el dict y devuelve el primer camino
# (ej. "claude_bot.history[0].snapshot.spy") cuyo valor Firestore rechaza
# — solo se usa cuando falla el write, para diagnóstico.

_FS_PRIMITIVES = (type(None), bool, int, float, str, bytes)


def _sanitize_for_firestore(obj, _depth=0):
    """Convierte recursivamente un objeto a tipos que Firestore acepta.
    CRITICAL: Firestore rechaza valores NaN/Infinity y dicts muy anidados (>20 niveles).
    """
    import math

    # Límite de profundidad (Firestore tiene ~20 niveles max)
    if _depth > 15:
        return None

    # None y primitivos
    if obj is None:
        return None

    if isinstance(obj, bool):
        return obj

    # float/int: detecta NaN e Infinity
    if isinstance(obj, (int, float)):
        try:
            import numpy as _np
            if isinstance(obj, _np.generic):
                obj = obj.item()
        except ImportError:
            pass

        # Convierte NaN/Infinity a None (Firestore los rechaza)
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
        return obj

    if isinstance(obj, (str, bytes)):
        return obj

    # datetime OK
    from datetime import datetime as _dt, date as _date
    if isinstance(obj, (_dt, _date)):
        return obj

    # numpy arrays → list (recurse)
    try:
        import numpy as _np
        if isinstance(obj, _np.ndarray):
            return [_sanitize_for_firestore(x, _depth+1) for x in obj.tolist()]
    except ImportError:
        pass

    # Decimal → float (pero chequea NaN)
    try:
        from decimal import Decimal as _Dec
        if isinstance(obj, _Dec):
            f = float(obj)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
    except ImportError:
        pass

    # set / frozenset → list
    if isinstance(obj, (set, frozenset)):
        return [_sanitize_for_firestore(x, _depth+1) for x in obj]

    # tuple → list
    if isinstance(obj, tuple):
        return [_sanitize_for_firestore(x, _depth+1) for x in obj]

    # list → recurse
    if isinstance(obj, list):
        return [_sanitize_for_firestore(x, _depth+1) for x in obj]

    # dict — las keys deben ser strings + elimina campos vacíos
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else str(k)
            sanitized = _sanitize_for_firestore(v, _depth+1)
            # Solo agrega si no es None y no es dict vacío
            if sanitized is not None and not (isinstance(sanitized, dict) and not sanitized):
                out[key] = sanitized
        return out if out else None  # Retorna None si dict queda vacío

    # Fallback: para objetos desconocidos, retorna None (no stringify)
    # Esto evita que stats con objetos raros cause problemas
    return None


def _is_firestore_native(obj) -> bool:
    """True si el valor es directamente escribible a Firestore sin conversión."""
    if obj is None or isinstance(obj, (bool, int, float, str, bytes)):
        # Rechazar numpy scalars aunque hereden de int/float
        try:
            import numpy as _np
            if isinstance(obj, _np.generic):
                return False
        except ImportError:
            pass
        return True
    from datetime import datetime as _dt, date as _date
    if isinstance(obj, (_dt, _date)):
        return True
    return False


def _find_unserializable_path(obj, path: str = "root", _depth: int = 0):
    """Devuelve el primer path dentro de `obj` cuyo valor NO es serializable
    directamente por Firestore. Útil para diagnóstico. None si todo OK."""
    if _depth > 12:
        return None  # evitar recursión infinita

    if _is_firestore_native(obj):
        return None

    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = _find_unserializable_path(v, f"{path}.{k}", _depth + 1)
            if sub:
                return sub
        return None

    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            sub = _find_unserializable_path(v, f"{path}[{i}]", _depth + 1)
            if sub:
                return sub
        return None

    # Tipo exótico — reportamos
    return f"{path} = <{type(obj).__name__}>: {repr(obj)[:120]}"


# ── Clase principal ────────────────────────────────────────

class CropBotTheta:

    def __init__(self):
        # Módulos
        self.stream        = SchwabStream(tickers=TICKERS)
        self.chain_fetcher = OptionChainFetcher(tickers=TICKERS, interval=30)
        # CROP: sin MispricingScanner (solo Theta Harvest)
        self.brain         = OptionsBrain()
        self.trader        = OptionsTrader(paper=PAPER_TRADING)
        # Auto-Router — actualiza toggles de estrategias según régimen cada 30 min
        self._auto_router  = _AutoRouter(bot_id="v2", update_interval_min=30)

        # Claude Bot — estrategia #14 sobre acciones del underlying.
        # Corre en paralelo con OptionsBrain (que opera opciones).
        # Intenta instanciarse, pero si falla la API key no rompe el bot.
        try:
            self.claude_bot = ClaudeBotEngine(paper_mode=True)
            logger.info("[CLAUDE_BOT_V2] Engine inicializado ✅")
        except Exception as e:
            self.claude_bot = None
            logger.warning(f"[CLAUDE_BOT_V2] Engine NO disponible: {e}")

        mode = "📄 PAPER TRADING" if PAPER_TRADING else "💰 LIVE TRADING"
        logger.info(f"[CROP] Modo: {mode}")

        # Estado interno
        # Buffer 1-min compartido (eolo_common.CandleBuffer)
        self._candle_buffer = CandleBuffer(max_len=CANDLE_BUFFER_SIZE)
        # Quote buffer separado (antes estaba mezclado en candle_buffers con prefijo _quote_)
        self._quote_buffers: dict[str, dict] = {}
        # Multi-TF settings (se pueblan en _poll_settings)
        self._active_timeframes:    list[int] = [1, 5, 15, 30, 60, 240]
        self._confluence_mode:      bool      = False
        self._confluence_min_agree: int       = 2
        self._confluence_snapshot:  dict      = {}
        # Macro feeds (VIX/VIX9D/VIX3M/TICK/TRIN) para estrategias Nivel 2.
        # Se instancia acá y se arranca en start() como una de las tasks del gather.
        # Mientras no haya samples, las estrategias Nivel 2 devuelven HOLD silenciosamente.
        try:
            from macro_poll import make_macro_feeds
            self._macro_feeds = make_macro_feeds(poll_sec=60)
        except Exception as e:
            logger.warning(f"[MACRO-v2] no se pudo crear MacroFeeds: {e} — Nivel 2 quedará en HOLD")
            self._macro_feeds = None
        # Inyectar MacroFeeds al trader para enrichment (VIX snapshot en cada trade).
        try:
            if hasattr(self.trader, "set_macro_feeds"):
                self.trader.set_macro_feeds(self._macro_feeds)
        except Exception as e:
            logger.debug(f"[MACRO-v2] trader.set_macro_feeds falló: {e}")
        # Gate de costos Claude — no llamar a OptionsBrain/ClaudeBotEngine si
        # no hay nada accionable (señales técnicas, mispricing, posiciones).
        # Reduce ~80-90% el consumo Anthropic en mercados laterales.
        self._claude_gate_enabled:  bool      = os.environ.get("CLAUDE_GATE", "1") != "0"
        self._last_analysis:  dict[str, float] = {}
        self._open_positions: list[dict] = []
        self._auto_close_done_date: str | None = None
        self._running = False

        # ── Theta Harvest state (v2 — Always-In Multi-DTE) ──
        # Lista de credit spreads abiertos por el theta harvest strategy.
        # Monitoreados independientemente del ciclo de análisis principal.
        self._theta_positions: list[dict] = []
        # Tracking multi-DTE: ticker → dte → fecha YYYY-MM-DD (o None si slot libre)
        # Permite tener 1 spread por cada DTE (0,1,2,3,4) por ticker simultáneamente.
        # Ejemplo: {"SPY": {0: "2026-04-27", 1: "2026-04-27", 2: None, ...}}
        self._theta_slots: dict[str, dict[int, str | None]] = {}
        # Caché de pivot analysis por ticker (se actualiza 1 vez por día al abrir)
        self._theta_pivot_cache: dict[str, object] = {}    # ticker → PivotAnalysisResult
        self._theta_pivot_date:  dict[str, str]    = {}    # ticker → fecha del cache
        # SPY price history para calcular caída 30 min
        self._spy_price_history: list[tuple[float, float]] = []  # (timestamp, price)
        # Habilitado/deshabilitado desde Firestore (toggle en dashboard)
        self._theta_harvest_enabled: bool = True
        # Flag force_entry (set por comando Firestore, permite saltear ventana horaria)
        self._theta_force_entry: bool = False
        # Último chain recibido por ticker (para force_theta_entry)
        self._last_chain: dict[str, dict] = {}
        # Dashboard: historial intraday de P&L [{time, datasets:[{label,pnl}], vix}]
        self._theta_pnl_history: list[dict] = []
        self._theta_last_history_ts: float = 0.0   # último snapshot (cada 5 min)
        self._theta_closed_positions: list[dict] = []
        # Stats acumuladas del día
        self._theta_stats: dict = {
            "pnl_today": 0.0, "win_rate": 0.0,
            "credit_total": 0.0, "trades_closed": 0,
            "_wins": 0, "_losses": 0,
        }
        # Estado macro para dashboard (is_news_day, events)
        self._theta_macro_status: dict = {}
        # Último Tick/A-D leído (para dashboard pill)
        self._theta_tick_ad: dict = {}

        # ── Dashboard state ───────────────────────────────
        self._quotes:          dict[str, dict] = {}
        self._iv_surfaces:     dict[str, dict] = {}
        self._mispricing_data: dict[str, list] = {}
        self._signals_data:    dict[str, dict] = {}
        self._claude_decisions:dict[str, dict] = {}
        self._claude_history:  list[dict]      = []   # últimas 50 decisiones
        self._state_file = os.path.join(BASE_DIR, "crop_state.json")

        # ── Token auto-refresh ────────────────────────────
        # Refresca el access_token de Schwab cada 25 min (expira a los 30)
        self._token_refresh_interval = 25 * 60  # segundos

        # ── Control remoto desde el dashboard ─────────────
        # El dashboard Cloud Run escribe en:
        #   eolo-crop-config/commands → órdenes puntuales (toggle, close_all)
        #   eolo-crop-config/settings → config persistente (budget, max_positions)
        # Este bot las lee en _command_watcher_loop() cada 5s.
        self._active:               bool         = True
        self._budget_per_trade:     float | None = None
        self._max_positions:        int          = MAX_POSITIONS_PER_TICKER
        # Defaults de SL/TP y daily-loss-cap — editables desde el dashboard
        # (modal Config). El bot los consulta al abrir/gestionar posiciones.
        self._default_stop_loss_pct:   float = 25.0   # % por debajo del precio de entrada
        self._default_take_profit_pct: float = 50.0   # % por encima del precio de entrada
        self._daily_loss_cap_pct:      float = -5.0   # cap diario (negativo, % sobre equity)
        # Snapshot del último cálculo del daily loss cap — lo publicamos en el
        # state doc para que el dashboard muestre pnl real vs cap. Poblado por
        # _is_daily_loss_cap_hit() en cada ticker (cheap: solo suma trades del
        # día del CSV + open positions). Log dedup: una línea por cycle.
        self._daily_loss_cap_status: dict = {}
        self._daily_loss_cap_log_ts: float = 0.0      # last WARN emit (epoch s)
        self._daily_loss_cap_log_dedup_s: float = 60.0
        # Schedule de trading — editable desde el dashboard. Defaults equity
        # (09:30-15:27 ET con auto-close 15:27). Se refresca en _poll_settings().
        self._schedule: TradingSchedule = DEFAULTS_EQUITY
        # Filtro macro — se puede desactivar desde el dashboard (toggle MACRO ON/OFF)
        self._macro_filter_enabled: bool = True
        self._last_command_ts:      float        = 0.0
        self._last_settings_ts:     float        = 0.0
        self._command_poll_interval = 5          # segundos

        # ── Claude Bot state ─────────────────────────────────
        # Corre en su propio loop (independiente de _on_chain_update).
        # Settings se cargan desde Firestore vía _poll_settings() bajo las
        # claves `claude_options_enabled`, `claude_theta_harvest_enabled`, `claude_bot_interval`, `claude_bot_paper_mode`.
        self._claude_options_enabled:        bool = True
        self._claude_theta_harvest_enabled:  bool = True
        self._claude_bot_interval:   int  = 60           # segundos entre ticks
        self._claude_bot_last_tick:  float = 0.0
        self._claude_bot_decision:   dict | None = None   # última decisión
        self._claude_bot_history:    list[dict]  = []     # últimas 50
        # Budget "virtual" para el prompt (en shares el budget es por posición)
        self._claude_bot_budget:     float = 500.0
        # Paper mode por default — no hay stock trader en v2 todavía.
        # Cuando se implemente, se podrá pasar a live desde Firestore.
        # Posiciones virtuales que el bot abrió en paper (no hay trader real).
        self._claude_bot_positions:  list[dict] = []

        # ── Strategy selection (dashboard toggles) ─────────
        # Keys canónicos (matchean los de Bot/bot_main.py DEFAULT_STRATEGIES).
        # El bot consulta este dict en _run() antes de llamar a cada analyzer.
        # Defaults = todos ON. Overrides vienen de Firestore:
        #   eolo-crop-config/settings.strategies_enabled
        self._strategies_enabled: dict[str, bool] = {
            # Clásicas
            "ema_crossover": True, "gap_fade": True, "rsi_sma200": True,
            "hh_ll": True, "ema_tsi": True, "vwap_rsi": True,
            "bollinger": True, "orb": True, "supertrend": True,
            "ha_cloud": True, "squeeze": True, "macd_bb": True,
            "vela_pivot": True,
            # Nivel 1
            "rvol_breakout": True, "stop_run": True, "vwap_zscore": True,
            "volume_reversal_bar": True, "anchor_vwap": True,
            "obv_mtf": True, "tsv": True, "vw_macd": True,
            "opening_drive": True,
            # Nivel 2 (dependen de MacroFeeds — devuelven HOLD si macro=None)
            "vix_mean_rev": True, "vix_correlation": True,
            "vix_squeeze": True, "tick_trin_fade": True,
            "vrp_intraday": True,
            # Suite "EMA 3/8 y MACD" (v3)
            "ema_3_8": True, "ema_8_21": True, "macd_accel": True,
            "volume_breakout": True, "buy_pressure": True,
            "sell_pressure": True, "vwap_momentum": True,
            "orb_v3": True, "donchian_turtle": True,
            "bulls_bsp": True, "net_bsv": True,
            # Combos Ganadores (2026-04)
            "combo1_ema_scalper": True, "combo2_rubber_band": True,
            "combo3_nino_squeeze": True, "combo4_slimribbon": True,
            "combo5_btd": True, "combo6_fractalccix": True,
            "combo7_campbell": True,
        }

        # ── Circuit breaker: Anthropic billing ──────────────
        # Si Claude devuelve N errores 400 seguidos por "credit balance is too low"
        # (o insuficiencia de créditos), el bot se auto-pausa (self._active = False)
        # y manda Telegram + escribe flag en Firestore para que el dashboard prenda
        # un semáforo rojo. Se reanuda al redeploy, al toggle manual desde el
        # dashboard, o al siguiente éxito de OptionsBrain (que resetea el contador).
        self._anthropic_billing_errors:    int  = 0
        self._anthropic_billing_threshold: int  = int(os.environ.get("ANTHROPIC_BILLING_THRESHOLD", "5"))
        self._anthropic_billing_paused:    bool = False
        self._anthropic_billing_last_err:  str  = ""
        self._anthropic_billing_last_ts:   float = 0.0
        self._anthropic_billing_notified:  bool = False  # anti-spam de Telegram

    # ── Arranque ───────────────────────────────────────────

    async def start(self):
        """Inicia todos los módulos en paralelo."""
        self._running = True
        self._loop = asyncio.get_running_loop()   # ref para schedular desde threads
        logger.info("🚀 EOLO v2 iniciando...")

        # ── Theta Harvest v2: log macro calendar al inicio ─
        try:
            cal_status = log_calendar_status()
            logger.info(f"[ThetaHarvest] {cal_status}")
            _send_telegram(f"[ThetaHarvest] {cal_status}")
        except Exception as _e:
            logger.debug(f"[ThetaHarvest] log_calendar_status error: {_e}")

        # ── Restaurar posiciones theta desde Firestore (sobrevive reinicios) ─
        self._load_theta_positions_from_firestore()

        # Registrar handlers de eventos
        self.stream.add_handler(self._on_market_event)
        self.chain_fetcher.add_handler(self._on_chain_update)

        # MacroFeeds polling task — alimenta las estrategias Nivel 2.
        # Si _macro_feeds es None (falló en __init__), omitimos la task.
        macro_task = None
        if self._macro_feeds is not None:
            try:
                from macro_poll import start_macro_loop
                macro_task = start_macro_loop(self._macro_feeds)
            except Exception as e:
                logger.warning(f"[MACRO-v2] no se pudo armar start_macro_loop: {e}")
                macro_task = None

        # Iniciar tareas en paralelo
        gather_tasks = [
            self.stream.start(),
            self.chain_fetcher.start(),
            self._position_monitor_loop(),
            self._auto_close_loop(),
            self._token_refresh_loop(),
            self._state_writer_loop(),
            self._command_watcher_loop(),
            self._claude_bot_loop(),
            self._theta_monitor_loop(),
        ]
        if macro_task is not None:
            gather_tasks.append(macro_task)
        await asyncio.gather(*gather_tasks)

    def stop(self):
        self._running = False
        self.stream.stop()
        self.chain_fetcher.stop()
        logger.info("[CROP] Detenido.")

    # ── Circuit breaker: Anthropic billing ─────────────────

    def _is_anthropic_billing_error(self, err: Exception) -> bool:
        """
        Detecta si `err` es un error 400 de billing de Anthropic.
        Firmas posibles (según SDK anthropic / http):
          - "credit balance is too low"
          - "insufficient_credits" / "insufficient credits"
          - BillingError / BillingException (nombre de clase)
          - status_code/http 400 + mensaje con "billing"/"credit"
        """
        msg = str(err).lower()
        cls = type(err).__name__.lower()
        if "credit balance is too low" in msg:
            return True
        if "insufficient_credits" in msg or "insufficient credits" in msg:
            return True
        if "billing" in cls:
            return True
        # Fallback: 400 + palabras clave de crédito/billing
        if ("400" in msg or "bad request" in msg) and (
            "credit" in msg or "billing" in msg or "quota" in msg
        ):
            return True
        return False

    def _check_anthropic_billing_error(self, err: Exception) -> None:
        """
        Inspecciona `err`. Si es de billing, incrementa contador.
        Al alcanzar `_anthropic_billing_threshold`, dispara el breaker:
          - self._active = False       (bloquea análisis + execs)
          - self._anthropic_billing_paused = True
          - notifica Telegram + Firestore (una sola vez)
        Si el error NO es de billing, NO resetea el contador (un ValueError
        ruidoso en medio no debe enmascarar la racha de 400s).
        El reset viene por _on_anthropic_success() cuando vuelve a funcionar.
        """
        if not self._is_anthropic_billing_error(err):
            return

        self._anthropic_billing_errors  += 1
        self._anthropic_billing_last_err = str(err)[:300]
        self._anthropic_billing_last_ts  = time.time()

        logger.warning(
            f"[BILLING-BREAKER] Error {self._anthropic_billing_errors}/"
            f"{self._anthropic_billing_threshold}: {self._anthropic_billing_last_err[:140]}"
        )

        if (
            self._anthropic_billing_errors >= self._anthropic_billing_threshold
            and not self._anthropic_billing_paused
        ):
            self._anthropic_billing_paused = True
            self._active = False
            logger.error(
                f"[BILLING-BREAKER] 🚨 TRIPPED. Pausando v2. "
                f"Cargá créditos en console.anthropic.com/settings/billing."
            )
            self._notify_billing_breaker()

    def _on_anthropic_success(self) -> None:
        """
        Llamado cuando una request a Anthropic devuelve OK. Resetea el contador
        de la racha de billing. Si el breaker estaba disparado y el user re-activó
        manualmente el bot (self._active=True vía dashboard), también limpiamos
        el flag — Claude vuelve a estar operativo.
        """
        if self._anthropic_billing_errors > 0:
            logger.info(
                f"[BILLING-BREAKER] reset: Anthropic respondió OK "
                f"(contador estaba en {self._anthropic_billing_errors})"
            )
        self._anthropic_billing_errors  = 0
        if self._anthropic_billing_paused and self._active:
            self._anthropic_billing_paused = False
            self._anthropic_billing_notified = False
            self._persist_billing_status_to_firestore(paused=False)
            logger.info("[BILLING-BREAKER] breaker clear: Anthropic OK y bot activo")

    def _notify_billing_breaker(self) -> None:
        """
        Telegram + Firestore flag. Anti-spam: sólo manda Telegram una vez
        hasta que el breaker se resetee.
        """
        if self._anthropic_billing_notified:
            return
        self._anthropic_billing_notified = True

        msg = (
            "🚨 <b>EOLO v2 PAUSADO — Anthropic sin créditos</b>\n\n"
            f"Racha: {self._anthropic_billing_errors} errores 400 billing seguidos.\n"
            f"Último error: <code>{self._anthropic_billing_last_err[:200]}</code>\n\n"
            "👉 Cargar créditos: console.anthropic.com/settings/billing\n"
            "Al redeployar v2 o re-activarlo desde el dashboard, el breaker se libera."
        )
        try:
            _send_telegram(msg)
        except Exception as e:
            logger.warning(f"[BILLING-BREAKER] Telegram falló: {e}")

        self._persist_billing_status_to_firestore(paused=True)

    def _persist_billing_status_to_firestore(self, paused: bool) -> None:
        """
        Escribe el estado del breaker en Firestore para que el dashboard prenda
        el semáforo. Path: eolo-options-state/billing
        """
        try:
            from google.cloud import firestore as _fs
            _db = _fs.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent"))
            _db.collection("eolo-crop-state").document("billing").set({
                "anthropic_billing_paused": paused,
                "errors_streak": self._anthropic_billing_errors,
                "threshold":     self._anthropic_billing_threshold,
                "last_error":    self._anthropic_billing_last_err[:300],
                "last_ts":       self._anthropic_billing_last_ts,
                "updated_at":    time.time(),
                "service":       "eolo-bot-crop",
            }, merge=True)
        except Exception as e:
            logger.warning(f"[BILLING-BREAKER] Firestore write falló: {e}")

    # ── Handlers de eventos ────────────────────────────────

    async def _on_market_event(self, ticker: str, data: dict):
        """Recibe cada tick del WebSocket."""
        event_type = data.get("type")

        if event_type == "candle":
            # Acumular en CandleBuffer común (shape normalizado)
            norm = from_schwab_chart_equity(data)
            if norm is not None:
                self._candle_buffer.push(norm)

        elif event_type == "quote":
            # Quote L1: merge con estado previo (Schwab manda partial updates)
            prev = self._quote_buffers.get(ticker) or {}
            prev.update(data)
            self._quote_buffers[ticker] = prev
            # Dashboard state
            self._quotes[ticker] = {
                "bid": data.get("bid"), "ask": data.get("ask"),
                "last": data.get("last"), "volume": data.get("volume"),
                "open": data.get("open"), "high": data.get("high"),
                "low": data.get("low"), "mark": data.get("mark"),
                "ts": data.get("ts", time.time()),
            }

    async def _on_chain_update(self, ticker: str, chain: dict):
        """
        Se llama cada vez que llega una nueva cadena de opciones.
        Aquí se dispara el ciclo de análisis completo.
        """
        # Guardar último chain para comandos force_theta_entry
        self._last_chain[ticker] = chain

        now = time.time()
        last = self._last_analysis.get(ticker, 0)

        # Throttle: no analizar más de una vez por ANALYSIS_INTERVAL
        if now - last < ANALYSIS_INTERVAL:
            return

        self._last_analysis[ticker] = now

        # ── Auto-Router: actualizar toggles cada 30 min (solo en SPY) ─
        if ticker == "SPY":
            try:
                if self._auto_router.should_update():
                    _vix_val, _ = self._theta_get_macro_context()
                    if _vix_val is not None:
                        # Obtener daily_df de SPY desde los candle buffers
                        _spy_df = self._candle_buffer.as_df_1min("SPY")
                        if _spy_df is not None and len(_spy_df) >= 20:
                            _toggles = self._auto_router.update(
                                vix=float(_vix_val), spy_df=_spy_df, save_firestore=False
                            )
                            # CROP: solo Theta Harvest (sin VRP, 0DTE, Earnings, PutSkew)
                            logger.debug(f"[AUTO_ROUTER] CROP (theta-only): {_toggles}")
            except Exception as _ar_e:
                logger.debug(f"[AUTO_ROUTER] v2 skip: {_ar_e}")

        # Bot pausado desde el dashboard → skip
        if not self._active:
            logger.debug(f"[CROP] Bot pausado (dashboard) — skip {ticker}")
            return

        if ticker not in THETA_HARVEST_TICKERS:
            return

        # No operar después de 15:27 ET
        if self._is_after_close_time():
            logger.debug(f"[CROP] Mercado cerca del cierre — sin nuevas órdenes")
            return

        await self._run_analysis_cycle(ticker, chain)

    # ── Ciclo de análisis ──────────────────────────────────

    async def _run_analysis_cycle(self, ticker: str, chain: dict):
        """
        Ciclo completo de análisis para un ticker:
        1. Obtener señales Eolo v1
        2. Construir IV surface
        3. Escanear mispricing
        4. Consultar Claude
        5. Ejecutar decisión
        """
        logger.info(f"[CROP] ── Ciclo de análisis: {ticker} ──")

        # 1. Señales técnicas Eolo v1
        signals = self._get_eolo_signals(ticker)
        self._signals_data[ticker] = signals

        # 2. IV Surface
        surface = IVSurface.from_chain(chain)
        # Guardar datos de IV para dashboard
        self._iv_surfaces[ticker] = {
            "atm_iv":      round(surface.atm_iv * 100, 2) if surface.atm_iv else None,
            "skew_index":  round(surface.skew_index * 100, 2) if surface.skew_index is not None else None,
            "term_slope":  surface.term_slope,
            "term_structure": surface.get_term_structure(),
            "skew":        surface.get_skew(),
            "underlying":  surface.underlying_price,
            "ts":          surface.ts,
        }

        # 3. CROP: Sin mispricing scanning (solo Theta Harvest)
        alerts = []

        # 4. Quote del subyacente
        quote = self._quote_buffers.get(ticker) or chain.get("underlying", {})

        # 5. Posiciones abiertas (actualizadas)
        ticker_positions = [
            p for p in self._open_positions
            if p.get("ticker") == ticker
        ]

        # No abrir más posiciones si ya tenemos el máximo
        if len(ticker_positions) >= self._max_positions:
            logger.info(
                f"[CROP] {ticker} — máx posiciones alcanzado "
                f"({len(ticker_positions)}/{self._max_positions}), no abrir nuevas"
            )
            return

        # Daily loss cap — freezea nuevas aperturas cuando el P&L del día
        # cruza el umbral. El cap se configura desde el modal Config del
        # dashboard (valor negativo, % sobre capital nocional desplegable).
        # El log rico se emite UNA sola vez cada _daily_loss_cap_log_dedup_s;
        # el skip por-ticker queda silencioso para no spamear.
        if self._is_daily_loss_cap_hit():
            st = self._daily_loss_cap_status or {}
            now_ts = time.time()
            if (now_ts - self._daily_loss_cap_log_ts) >= self._daily_loss_cap_log_dedup_s:
                self._daily_loss_cap_log_ts = now_ts
                logger.warning(
                    f"[CROP] 🛑 Daily loss cap HIT — "
                    f"pnl_pct={st.get('pnl_pct', 0):+.2f}% <= cap={st.get('cap', 0):.2f}% "
                    f"(realized=${st.get('realized', 0):+.2f}, "
                    f"unrealized=${st.get('unrealized', 0):+.2f}, "
                    f"nominal=${st.get('nominal_equity', 0):.0f}, "
                    f"open={st.get('n_open', 0)}, trades_today={st.get('n_trades_today', 0)}). "
                    f"Skipeo aperturas hasta próximo cycle / reset 8am."
                )
            return

        # 5b. Gate de costos Claude — solo llamar a OptionsBrain si hay algo
        # accionable (señal técnica BUY/SELL, alerta de mispricing, o posición
        # abierta que pueda querer cerrarse). Sin esto, con confluence_mode=OFF
        # y mercado lateral, cada ticker dispara 1 llamada Claude cada 60s
        # aunque ninguna estrategia emita señal. Esto reduce ~80-90% las
        # llamadas en sesiones tranquilas. Override: set _claude_gate_enabled=False
        # para deshabilitar (o CLAUDE_GATE=0 en env al arrancar).
        if getattr(self, "_claude_gate_enabled", True):
            now_h      = now_et()
            hour_frac  = now_h.hour + now_h.minute / 60.0
            window_end = ENTRY_HOUR_ET + ENTRY_WINDOW_MINUTES / 60.0
            if not (ENTRY_HOUR_ET <= hour_frac <= window_end):
                logger.debug(
                    f"[CROP] {ticker} — gate: fuera de ventana {ENTRY_HOUR_ET:.2f}-{window_end:.2f} ET, "
                    f"skip Claude (ahorro API)"
                )
                return

        # 6. Claude decide
        try:
            decision = await self.brain.analyze(
                ticker         = ticker,
                quote          = quote,
                chain          = chain,
                surface        = surface,
                mispricing_alerts = alerts,
                open_positions = ticker_positions,
            )
            # Éxito → resetear contador del circuit breaker de billing
            self._on_anthropic_success()
        except Exception as e:
            logger.error(f"[CROP] Error en OptionsBrain para {ticker}: {e}")
            # Circuit breaker: si es un error 400 billing, incrementa contador
            # y eventualmente pausa el bot + Telegram + Firestore flag.
            self._check_anthropic_billing_error(e)
            return

        # 7. Guardar decisión de Claude para dashboard
        decision_record = {
            **decision,
            "ts": time.time(),
            "ts_str": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "ticker": ticker,
            # Guardar inputs que usó Claude (para trazabilidad)
            "inputs": {
                "mispricing_count": len(alerts),
                "atm_iv": self._iv_surfaces.get(ticker, {}).get("atm_iv"),
                "skew_index": self._iv_surfaces.get(ticker, {}).get("skew_index"),
            },
        }
        self._claude_decisions[ticker] = decision_record
        self._claude_history.insert(0, decision_record)
        self._claude_history = self._claude_history[:50]  # últimas 50

        # 7b. Persistir decisión en Firestore para auditoría + dashboard cross-bot
        # Path: eolo-claude-decisions-v2/{YYYY-MM-DD}/decisions/{ts}
        try:
            from google.cloud import firestore as _fs
            _db = _fs.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent"))
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tsid  = f"{time.time():.3f}"
            _db.collection("eolo-claude-decisions-v2") \
               .document(today).collection("decisions").document(tsid) \
               .set({**decision_record, "recorded_ts": time.time()})
        except Exception as _e:
            logger.warning(f"[CROP] Firestore write de decisión falló: {_e}")

        # 8. Ejecutar si hay señal
        action = decision.get("action", "HOLD")
        if action in ("BUY", "SELL_TO_CLOSE"):
            await self._execute_decision(decision)
        else:
            logger.info(
                f"[CROP] {ticker} → HOLD | {decision.get('reason','')[:100]}"
            )

        # 9. Escribir estado al disco para el dashboard
        self._write_state()

        # 10. Theta Harvest scan — independiente de Claude
        if ticker in THETA_HARVEST_TICKERS and self._theta_harvest_enabled:
            await self._run_theta_harvest(ticker, chain)

    # ── Theta Harvest v2 — Always-In Multi-DTE ────────────

    def _theta_get_macro_context(self) -> tuple[float | None, float | None]:
        """Retorna (vix, vvix) desde macro_feeds si están disponibles."""
        vix = vvix = None
        try:
            macro = getattr(self, "_macro_feeds", None)
            if macro:
                if hasattr(macro, "latest"):      # MacroFeeds object
                    vix  = macro.latest("VIX")
                    vvix = macro.latest("VVIX")
                elif isinstance(macro, dict):     # fallback dict
                    vix  = macro.get("vix")  or macro.get("VIX")
                    vvix = macro.get("vvix") or macro.get("VVIX")
        except Exception:
            pass
        return vix, vvix

    def _theta_spy_drop_30m(self) -> float:
        """
        Calcula la caída de SPY en los últimos 30 minutos como %.
        Retorna 0.0 si no hay datos suficientes.
        Positivo = caída (ej. 0.9 = cayó 0.9%).
        """
        import time as _time
        now_ts  = _time.time()
        cutoff  = now_ts - 30 * 60  # últimos 30 min
        # Mantener solo los últimos 60 min de history
        self._spy_price_history = [
            (ts, px) for ts, px in self._spy_price_history
            if ts >= now_ts - 3600
        ]
        # Actualizar con precio actual de SPY
        spy_quote = self._quotes.get("SPY", {})
        spy_price = spy_quote.get("mark") or spy_quote.get("last") or 0.0
        if spy_price > 0:
            self._spy_price_history.append((now_ts, spy_price))

        # Buscar precio hace 30 min
        older = [(ts, px) for ts, px in self._spy_price_history if ts <= cutoff]
        if not older or spy_price <= 0:
            return 0.0
        price_30m_ago = older[-1][1]
        if price_30m_ago <= 0:
            return 0.0
        drop_pct = (price_30m_ago - spy_price) / price_30m_ago * 100
        return max(0.0, drop_pct)  # positivo = caída, negativo no nos interesa

    def _theta_init_slots(self, ticker: str, today: str):
        """Inicializa o resetea los slots del ticker para el día de hoy."""
        slots = self._theta_slots.get(ticker, {})
        # Si algún slot tiene fecha de ayer (o antes), liberarlo
        new_slots = {}
        for dte in TARGET_DTES:
            slot_date = slots.get(dte)
            if slot_date == today:
                new_slots[dte] = today      # spread de hoy sigue activo
            else:
                new_slots[dte] = None       # slot libre para hoy
        self._theta_slots[ticker] = new_slots

    def _theta_free_slot(self, ticker: str, dte: int):
        """Libera un slot DTE para re-entry."""
        if ticker in self._theta_slots:
            self._theta_slots[ticker][dte] = None

    def _theta_get_pivot(self, ticker: str, current_price: float, today: str):
        """
        Retorna (cached) PivotAnalysisResult para el ticker.
        Actualiza el cache si es un día nuevo o si no existe.
        """
        if (self._theta_pivot_date.get(ticker) != today or
                ticker not in self._theta_pivot_cache):
            try:
                result = analyze_pivots(ticker=ticker, current_price=current_price)
                if result:
                    self._theta_pivot_cache[ticker] = result
                    self._theta_pivot_date[ticker]  = today
                    logger.info(format_pivot_summary(result))
            except Exception as e:
                logger.warning(f"[ThetaHarvest] pivot_analysis falló {ticker}: {e}")
        return self._theta_pivot_cache.get(ticker)

    async def _run_theta_harvest(self, ticker: str, chain: dict):
        """
        Always-In Multi-DTE: intenta abrir spreads en todos los DTEs libres.
        Por cada DTE en TARGET_DTES (0,1,2,3,4) que no tenga spread hoy:
          1. Chequea macro news day → skip todo si hay noticias
          2. Obtiene pivot analysis → determina risk level y delta range
          3. Determina dirección (PUT/CALL) desde señales técnicas
          4. Llama a scan_theta_harvest para ese DTE específico
          5. Si hay señal válida → abre el spread y ocupa el slot
        Solo corre en la ventana de entrada (ENTRY_HOUR_ET ± ENTRY_WINDOW_MINUTES).
        """
        from datetime import date as _date
        today = _date.today().isoformat()

        # ── 0. Gate horario — solo operar dentro de sesión NYSE (9:30–16:00 ET)
        #       Bloquea entradas en pre/post market y fines de semana.
        #       El check de ventana interna (9:45–12:00) lo hace scan_theta_harvest;
        #       este gate evita intentos fuera de sesión completamente.
        if not self._is_within_trading_window():
            logger.debug(f"[ThetaHarvest] {ticker} — fuera de sesión NYSE, skip")
            return

        # ── 0b. Inicializar / resetear slots ────────────────
        self._theta_init_slots(ticker, today)

        # ── 1. Macro news filter ───────────────────────────
        news_day = is_news_day(enabled_override=self._macro_filter_enabled)
        # Actualizar estado macro para dashboard
        try:
            from theta_harvest.macro_news_filter import get_today_events
            events_today = get_today_events()
        except Exception:
            events_today = []
        self._theta_macro_status = {"is_news_day": news_day, "events": events_today}
        if news_day:
            logger.info(f"[ThetaHarvest] {ticker} — día de noticias macro, skip")
            return

        # ── 2. Macro context ───────────────────────────────
        vix, vvix = self._theta_get_macro_context()

        # ── 3. Pivot analysis (1 vez por día por ticker) ───
        underlying_price = (chain.get("underlying") or {}).get("mark") or 0.0
        pivot_result = None
        if underlying_price > 0:
            pivot_result = self._theta_get_pivot(ticker, underlying_price, today)

        # ── 4. Dirección (prioridad: sector analysis > señales internas) ─
        # 1° opción: dirección del sector analysis del pivot_result
        #   (feed de 11 ETFs sectoriales ponderados, exacto al spreadsheet de Juan)
        # 2° fallback: señales técnicas internas Eolo v1
        if pivot_result and getattr(pivot_result, "sector", None):
            spread_type = pivot_result.sector.spread_type
            logger.info(
                f"[ThetaHarvest] {ticker} dirección via sector analysis: "
                f"{spread_type} ({pivot_result.sector.direction} "
                f"{pivot_result.sector.weighted_change_pct:+.2f}%)"
            )
        else:
            signals     = self._signals_data.get(ticker, {})
            macro_feeds = getattr(self, "_macro_feeds", None)
            spread_type = _determine_spread_type(signals, macro_feeds)
            logger.debug(f"[ThetaHarvest] {ticker} dirección via señales internas: {spread_type}")

        # ── 4b. Tick / A-D — confirmación intraday de dirección ──
        # Se fetcha en tiempo real en cada intento de entrada.
        # block  → no entrar ningún spread este ciclo (TICK extremo)
        # skip   → Tick contradice la dirección → esperar próximo ciclo
        # neutral/confirm → proceder
        tick_ctx: Optional[TickADContext] = None
        try:
            tick_ctx = fetch_tick_ad(self._schwab.access_token)
            if tick_ctx is not None:
                # Guardar para dashboard pill + flow gate
                _tick_conf = tick_ctx.evaluate(spread_type) if spread_type else "neutral"
                self._theta_tick_ad = {
                    "tick":         getattr(tick_ctx, "tick", None),
                    "ad":           getattr(tick_ctx, "ad",   None),
                    "confirmation": _tick_conf,
                }
        except Exception as e:
            logger.debug(f"[ThetaHarvest] {ticker} Tick/AD fetch error: {e} — omitido")

        if tick_ctx is not None:
            tick_confirmation = tick_ctx.evaluate(spread_type)
            if tick_confirmation == "block":
                logger.warning(
                    f"[ThetaHarvest] {ticker} — Tick/AD BLOCK "
                    f"(TICK={tick_ctx.tick:+.0f} extremo), skip todas las entradas"
                )
                return
            if tick_confirmation == "skip":
                logger.info(
                    f"[ThetaHarvest] {ticker} — Tick/AD SKIP "
                    f"(TICK={tick_ctx.tick:+.0f} AD={tick_ctx.ad:+.0f} contradicen {spread_type})"
                )
                return
            logger.debug(
                f"[ThetaHarvest] {ticker} Tick/AD → {tick_confirmation} "
                f"(TICK={tick_ctx.tick:+.0f} AD={tick_ctx.ad:+.0f})"
            )

        # ── 5. Intentar abrir cada DTE libre ──────────────
        slots = self._theta_slots.get(ticker, {})
        for dte in TARGET_DTES:
            if slots.get(dte) == today:
                continue    # ya tenemos spread para este DTE hoy

            # Verificar que no haya ya una posición activa para este DTE
            already_open = any(
                p.get("ticker") == ticker and p.get("dte") == dte
                for p in self._theta_positions
            )
            if already_open:
                self._theta_slots.setdefault(ticker, {})[dte] = today
                continue

            # ── Tranche entry: 3 contratos, mismo strike, distintos targets ──
            try:
                signals = scan_theta_harvest_tranches(
                    ticker         = ticker,
                    chain          = chain,
                    vix            = vix,
                    vvix           = vvix,
                    spread_type    = spread_type,
                    dte_preference = dte,
                    pivot_result   = pivot_result,
                    force_entry    = getattr(self, "_theta_force_entry", False),
                )
            except Exception as e:
                logger.warning(f"[ThetaHarvest] scan error {ticker} DTE={dte}: {e}")
                continue

            if not signals:
                continue

            # Abrir un contrato por cada tranche (T0=35%, T1=65%, T2=EXPIRY)
            opened_any = False
            for signal in signals:
                decision = signal.to_decision(contracts=1)
                try:
                    order_id = await self.trader.execute_decision(decision)
                    if order_id:
                        pos = {
                            **decision,
                            "order_id":  order_id,
                            "opened_at": time.time(),
                            "dte_slot":  dte,
                        }
                        self._theta_positions.append(pos)
                        self._save_theta_positions_to_firestore()
                        opened_any = True
                        # Sumar crédito total del día
                        self._theta_stats["credit_total"] = round(
                            self._theta_stats.get("credit_total", 0)
                            + signal.net_credit * pos.get("contracts", 1) * 100, 2
                        )
                        t_label = (
                            f"T{signal.tranche_id}={int(signal.tranche_target*100)}%"
                            if signal.tranche_target is not None
                            else f"T{signal.tranche_id}=EXPIRY"
                        )
                        logger.info(
                            f"[ThetaHarvest] ✅ {ticker} DTE={dte} [{t_label}] "
                            f"K={signal.short_strike}/{signal.long_strike} "
                            f"credit=${signal.net_credit:.2f} target=${signal.profit_target:.2f} | "
                            f"order_id={order_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"[ThetaHarvest] Error abriendo T{signal.tranche_id} "
                        f"{ticker} DTE={dte}: {e}"
                    )

            if opened_any:
                self._theta_slots.setdefault(ticker, {})[dte] = today
                s0 = signals[0]  # datos del spread (todos los tranches tienen el mismo strike)
                targets_str = " / ".join(
                    f"T{s.tranche_id}→${s.profit_target:.2f}" for s in signals
                )
                _send_telegram(
                    f"🎯 ThetaHarvest {ticker} {'PUT' if 'put' in spread_type else 'CALL'} "
                    f"{dte}DTE K={s0.short_strike}/{s0.long_strike} ($5) | "
                    f"Δ={s0.short_delta:.2f} [{s0.risk_level}] | "
                    f"credit=${s0.net_credit:.2f} SL=${s0.stop_loss:.2f} | "
                    f"targets: {targets_str} | "
                    f"exp={s0.expiration}"
                )

    async def _theta_monitor_loop(self):
        """
        Loop de monitoreo de posiciones theta harvest v2 (Always-In).
        Corre cada 60 segundos.

        Evalúa cada posición con exits inteligentes (per-tranche):
          1. VVIX_PANIC  → cerrar todos los tranches
          2. STOP_LOSS   → spread ≥ 125% crédito (aplica a todos los tranches)
          3. VIX_SPIKE   → VIX subió > 3 pts desde entrada
          4. DELTA_DRIFT → short delta > 0.35
          5. SPY_DROP    → SPY cayó > 0.8% en 30 min (PUT spreads)
          6. PROFIT      → per-tranche: T0≤65%crédito / T1≤35%crédito / T2=sin target
          7. TIME_STOP   → 14:30 ET (0DTE) | 15:15 ET (1-2 DTE)

        Al cerrar un spread, libera el DTE slot para re-entry.
        EOD 15:30 ET: cierra TODOS los spreads sin excepción.
        """
        logger.info("[ThetaHarvest] Monitor loop v2 iniciado (Always-In)")
        while self._running:
            await asyncio.sleep(60)    # chequear cada 60 segundos (era 120)

            if not self._theta_positions:
                continue

            # Obtener contexto de mercado actual
            vix, vvix      = self._theta_get_macro_context()
            spy_drop_30m   = self._theta_spy_drop_30m()

            to_close = []
            for pos in list(self._theta_positions):
                pos_ticker = pos.get("ticker", "")
                try:
                    chain = self.chain_fetcher._chains.get(pos_ticker, {})
                    if not chain:
                        continue

                    # ── Actualizar unrealized_pnl en el dict de posición ──
                    try:
                        opt_side  = "puts" if "put" in pos.get("spread_type","") else "calls"
                        exp       = pos.get("expiration", "")
                        strikes   = chain.get(opt_side, {}).get(exp, {})
                        short_c   = strikes.get(str(pos.get("short_strike", ""))) or {}
                        long_c    = strikes.get(str(pos.get("long_strike",  ""))) or {}
                        s_mark    = short_c.get("mark") or short_c.get("ask") or 0
                        l_mark    = long_c.get("mark")  or long_c.get("bid")  or 0
                        cur_val   = round(abs(s_mark - l_mark), 2)
                        net_credit = pos.get("net_credit", 0)
                        contracts  = pos.get("contracts", 1)
                        pos["unrealized_pnl"] = round(
                            (net_credit - cur_val) * contracts * 100, 2
                        )
                        pos["current_value"] = cur_val
                    except Exception:
                        pass

                    exit_reason = evaluate_open_position(
                        position      = pos,
                        current_chain = chain,
                        vix_current   = vix,
                        vvix_current  = vvix,
                        spy_drop_30m  = spy_drop_30m,
                    )
                    if exit_reason:
                        to_close.append((pos, exit_reason))
                except Exception as e:
                    logger.warning(f"[ThetaHarvest] Error evaluando posición {pos_ticker}: {e}")

            # ── Snapshot intraday de P&L cada 5 min ──────────────
            now_ts = time.time()
            if now_ts - self._theta_last_history_ts >= 300 and self._theta_positions:
                try:
                    from zoneinfo import ZoneInfo
                    hhmm = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M")
                    datasets = []
                    # Agrupar por ticker+spread_type+dte_slot como label único
                    for p in self._theta_positions:
                        isPut  = "put" in p.get("spread_type", "")
                        label  = f"{p.get('ticker','?')} {'PUT' if isPut else 'CALL'} {p.get('dte_slot', p.get('dte','?'))}DTE T{p.get('tranche_id',0)}"
                        pnl    = p.get("unrealized_pnl", 0) or 0
                        price  = p.get("current_value",  p.get("net_credit", 0)) or 0
                        datasets.append({"label": label, "pnl": pnl, "price": price})
                    self._theta_pnl_history.append({
                        "time":     hhmm,
                        "datasets": datasets,
                        "vix":      vix,
                    })
                    # Mantener solo últimos 80 puntos (6h+ a 5min)
                    if len(self._theta_pnl_history) > 80:
                        self._theta_pnl_history = self._theta_pnl_history[-80:]
                    self._theta_last_history_ts = now_ts
                except Exception:
                    pass

            for pos, exit_reason in to_close:
                try:
                    pos_ticker  = pos.get("ticker", "?") or "?"
                    dte_slot    = (pos.get("dte_slot") or pos.get("dte") or "?")
                    spread_type = pos.get("spread_type", "") or ""
                    exit_reason = exit_reason or "UNKNOWN"
                    close_decision = {
                        "action":       "CLOSE_SPREAD",
                        "ticker":       pos_ticker,
                        "expiration":   pos.get("expiration"),
                        "spread_type":  spread_type,
                        "short_strike": pos.get("short_strike"),
                        "long_strike":  pos.get("long_strike"),
                        "contracts":    pos.get("contracts", 1),
                        "reason":       f"ThetaHarvest {exit_reason or '?'}",
                    }
                    order_id = await self.trader.execute_decision(close_decision)
                    if order_id:
                        self._theta_positions.remove(pos)
                        self._save_theta_positions_to_firestore()
                        try:
                            _nc = pos.get("net_credit") or None
                            _cv = pos.get("current_value") or 0
                            self._theta_closed_positions.append({
                                "ticker": pos.get("ticker"), "option_type": "put" if "put" in (pos.get("spread_type") or "") else "call",
                                "strike": pos.get("short_strike"), "expiration": pos.get("expiration"), "qty": pos.get("contracts", 1),
                                "entry_ts": datetime.utcfromtimestamp(pos.get("opened_at") or 0).isoformat(),
                                "exit_ts": datetime.utcnow().isoformat(), "entry_price": _nc or 0, "exit_price": _cv,
                                "pnl": round(pos.get("unrealized_pnl") or 0, 2),
                                "pnl_pct": round((_nc - _cv) / _nc * 100, 1) if _nc else 0,
                                "strategy": "THETA_HARVEST", "exit_reason": exit_reason,
                                "reason": pos.get("reason", ""), "dte_slot": pos.get("dte_slot"), "tranche_id": pos.get("tranche_id"),
                            })
                        except Exception as _e:
                            logger.warning(f"[ThetaHarvest] closed_positions snapshot failed: {_e}")

                        # Liberar el DTE slot solo cuando TODOS los tranches del
                        # mismo DTE están cerrados. T0 cierra antes (35% target)
                        # pero T1 y T2 siguen abiertos — no re-abrir hasta que cierren.
                        remaining_tranches = [
                            p for p in self._theta_positions
                            if p.get("ticker") == pos_ticker
                            and p.get("dte_slot") == dte_slot
                        ]
                        if not remaining_tranches and dte_slot is not None:
                            self._theta_free_slot(pos_ticker, dte_slot)

                        tranche_id  = pos.get("tranche_id") or "?"
                        t_target    = pos.get("tranche_target")
                        try:
                            t_label     = (
                                f"T{tranche_id}={int(t_target*100)}%"
                                if t_target is not None else f"T{tranche_id}=EXPIRY"
                            )
                        except (TypeError, ValueError):
                            t_label = f"T{tranche_id}=?"
                        is_win = (exit_reason or "") in ("PROFIT", "EXPIRY")
                        emoji  = "✅" if is_win else ("⏱️" if "TIME" in (exit_reason or "") else "🛑")
                        still_open = len(remaining_tranches)

                        # ── Actualizar stats del día ──────────────────────
                        realized = pos.get("unrealized_pnl", 0) or 0
                        self._theta_stats["pnl_today"]    = round(
                            self._theta_stats.get("pnl_today", 0) + realized, 2
                        )
                        self._theta_stats["trades_closed"] = (
                            self._theta_stats.get("trades_closed", 0) + 1
                        )
                        if is_win:
                            self._theta_stats["_wins"] = self._theta_stats.get("_wins", 0) + 1
                        else:
                            self._theta_stats["_losses"] = self._theta_stats.get("_losses", 0) + 1
                        total = self._theta_stats.get("_wins", 0) + self._theta_stats.get("_losses", 0)
                        self._theta_stats["win_rate"] = round(
                            (self._theta_stats.get("_wins", 0) / total * 100) if total > 0 else 0, 1
                        )
                        try:
                            logger.info(
                                f"[ThetaHarvest] Spread cerrado {pos_ticker} DTE={dte_slot} "
                                f"[{t_label}] — motivo: {exit_reason or '?'} | "
                                f"tranches restantes: {still_open} | order_id={order_id or '?'}"
                            )
                        except Exception as log_err:
                            logger.warning(f"[ThetaHarvest] Error logging close: {log_err}")
                        short_k = pos.get('short_strike') or "?"
                        long_k  = pos.get('long_strike') or "?"
                        try:
                            msg = (
                                f"{emoji} ThetaHarvest {pos_ticker} DTE={dte_slot} [{t_label}] "
                                f"cerrado ({exit_reason or '?'}) "
                                f"K={short_k}/{long_k}"
                                + (f" | {still_open} tranches aún abiertos" if still_open else " | DTE slot libre")
                            )
                            _send_telegram(msg)
                        except Exception as telegram_err:
                            logger.warning(f"[ThetaHarvest] Error sending telegram: {telegram_err}")
                except Exception as e:
                    logger.error(f"[ThetaHarvest] Error cerrando spread {pos_ticker}: {e}")

    # ── Ejecución de orden ─────────────────────────────────

    async def _execute_decision(self, decision: dict):
        """Ejecuta la decisión de Claude via OptionsTrader."""
        ticker    = decision.get("ticker", "")
        action    = decision.get("action")
        opt_type  = decision.get("option_type", "")
        exp       = decision.get("expiration", "")
        strike    = decision.get("strike", 0)
        contracts = decision.get("contracts", 1)
        limit     = decision.get("limit_price")
        reason    = decision.get("reason", "")

        logger.info(
            f"[CROP] EJECUTANDO: {action} {contracts}x "
            f"{ticker} {opt_type} K={strike} exp={exp} "
            f"limit=${limit} | {reason[:80]}"
        )

        try:
            order_id = await self.trader.execute_decision(decision)
            if order_id:
                logger.info(f"[CROP] Orden ejecutada ✅ order_id={order_id}")
            else:
                logger.warning(f"[CROP] Orden no ejecutada para {ticker}")
        except Exception as e:
            logger.error(f"[CROP] Error ejecutando orden: {e}")

    # ── Señales Eolo v1 ────────────────────────────────────

    def _get_eolo_signals(self, ticker: str) -> dict:
        """
        Corre las 13 estrategias de Eolo v1 sobre el buffer 1-min del WebSocket
        resampleando a cada TF en `self._active_timeframes`. Si confluence_mode
        está activo, reduce las señales multi-TF a una señal unificada por
        estrategia (exige ≥N TFs coincidentes). Si está OFF, passthrough.

        Retorna dict {strategy_name: {signal, price, tfs}} o {} si faltan datos.
        """
        if not _STRATEGIES_AVAILABLE:
            return {}

        if self._candle_buffer.size(ticker) < 20:
            logger.debug(
                f"[CROP] {ticker} — insuficientes velas "
                f"({self._candle_buffer.size(ticker)}/20) para señales v1"
            )
            return {}

        CLASSIC = {"SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA"}
        is_classic   = ticker in CLASSIC
        is_leveraged = not is_classic

        def _qs(sym: str):
            return self._quote_buffers.get(sym) or None

        cf = ConfluenceFilter(mode=self._confluence_mode,
                              min_agree=self._confluence_min_agree)
        # results_by_tf[strategy][tf] = result (dict)
        results_by_tf: dict[str, dict[int, dict]] = {}

        # Map: nombre interno que usa _run() → key canónico del toggle
        # (el mismo que usa Bot/bot_main.py y el dashboard v1).
        _INTERNAL_TO_CANONICAL = {
            "EMA":               "ema_crossover",
            "GAP":               "gap_fade",
            "RSI_SMA200":        "rsi_sma200",
            "HH_LL":             "hh_ll",
            "EMA_TSI":           "ema_tsi",
            "VWAP_RSI":          "vwap_rsi",
            "BOLLINGER":         "bollinger",
            "ORB":               "orb",
            "SUPERTREND":        "supertrend",
            "HA_CLOUD":          "ha_cloud",
            "SQUEEZE":           "squeeze",
            "MACD_BB":           "macd_bb",
            "VELA_PIVOT":        "vela_pivot",
            "RVOL_BREAKOUT":     "rvol_breakout",
            "STOP_RUN":          "stop_run",
            "VWAP_ZSCORE":       "vwap_zscore",
            "VOL_REVERSAL_BAR":  "volume_reversal_bar",
            "ANCHOR_VWAP":       "anchor_vwap",
            "OBV_MTF":           "obv_mtf",
            "TSV":               "tsv",
            "VW_MACD":           "vw_macd",
            "OPENING_DRIVE":     "opening_drive",
            "VIX_MEAN_REV":      "vix_mean_rev",
            "VIX_CORRELATION":   "vix_correlation",
            "VIX_SQUEEZE":       "vix_squeeze",
            "TICK_TRIN_FADE":    "tick_trin_fade",
            "VRP_INTRADAY":      "vrp_intraday",
            # Suite "EMA 3/8 y MACD" (v3)
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

        def _run(name: str, fn, md, *args, **kwargs):
            # Gating: si el toggle del dashboard la apagó, skip.
            canonical = _INTERNAL_TO_CANONICAL.get(name)
            if canonical is not None and not self._strategies_enabled.get(canonical, True):
                return
            tf = md.frequency
            try:
                result = fn(md, ticker, *args, **kwargs)
                if result and result.get("signal") not in (None, "ERROR"):
                    cf.register(ticker, name, tf, result.get("signal"))
                    results_by_tf.setdefault(name, {})[tf] = result
            except Exception as e:
                logger.debug(f"[SIGNALS] {ticker} {name}/{tf}m error: {e}")

        for tf in self._active_timeframes:
            md = BufferMarketData(self._candle_buffer, frequency=tf,
                                  quote_source=_qs)

            if is_classic:
                _run("EMA",        _strat_ema.analyze,        md, use_sma200_filter=True)
                _run("GAP",        _strat_gap.analyze,        md)
                _run("RSI_SMA200", _strat_rsi_sma200.analyze, md)
                _run("HH_LL",      _strat_hh_ll.analyze,      md)
                _run("EMA_TSI",    _strat_ema_tsi.analyze,    md)

            if is_leveraged:
                _run("VWAP_RSI",   _strat_vwap.analyze,       md)
                _run("BOLLINGER",  _strat_bollinger.analyze,  md)
                entry = (self.trader.entry_prices.get(ticker)
                         if hasattr(self.trader, "entry_prices") else None)
                _run("ORB",        _strat_orb.analyze,        md, entry)
                _run("SUPERTREND", _strat_supertrend.analyze, md)
                _run("HA_CLOUD",   _strat_ha_cloud.analyze,   md)
                _run("SQUEEZE",    _strat_squeeze.analyze,    md)
                _run("MACD_BB",    _strat_macd_bb.analyze,    md)
                _run("VELA_PIVOT", _strat_vela_pivot.analyze, md)

            if ticker in ("SPY", "QQQ"):
                _run("VWAP_RSI",   _strat_vwap.analyze,       md)
                _run("SUPERTREND", _strat_supertrend.analyze, md)

            # ───────────────────────────────────────────────
            #  Nivel 1 (trading_strategies_v2.md) — universal
            # ───────────────────────────────────────────────
            entry = (self.trader.entry_prices.get(ticker)
                     if hasattr(self.trader, "entry_prices") else None)
            _run("RVOL_BREAKOUT",    _strat_rvol.analyze,          md, entry)
            _run("STOP_RUN",         _strat_stop_run.analyze,      md, entry)
            _run("VWAP_ZSCORE",      _strat_vwap_z.analyze,        md, entry)
            _run("VOL_REVERSAL_BAR", _strat_vrb.analyze,           md, entry)
            _run("ANCHOR_VWAP",      _strat_anchor_vwap.analyze,   md, entry)
            _run("OBV_MTF",          _strat_obv.analyze,           md, entry)
            _run("TSV",              _strat_tsv.analyze,           md, entry)
            _run("VW_MACD",          _strat_vw_macd.analyze,       md, entry)

            # Opening Drive solo sobre ETFs apalancadas
            if is_leveraged:
                _run("OPENING_DRIVE", _strat_opening_drive.analyze, md, entry)

            # ───────────────────────────────────────────────
            #  Nivel 2 (macro) — solo SPY/QQQ/TQQQ.
            #  macro=self._macro_feeds (None hasta el wiring)
            # ───────────────────────────────────────────────
            if ticker in ("SPY", "QQQ", "TQQQ"):
                _run("VIX_MEAN_REV",    _strat_vix_mr.analyze,   md,
                     macro=self._macro_feeds, entry_price=entry)
                _run("VIX_CORRELATION", _strat_vix_corr.analyze, md,
                     macro=self._macro_feeds, entry_price=entry)
                _run("VIX_SQUEEZE",     _strat_vix_sq.analyze,   md,
                     macro=self._macro_feeds, entry_price=entry)
                _run("TICK_TRIN_FADE",  _strat_tt.analyze,       md,
                     macro=self._macro_feeds, entry_price=entry)
                _run("VRP_INTRADAY",    _strat_vrp.analyze,      md,
                     macro=self._macro_feeds, entry_price=entry)

            # ───────────────────────────────────────────────
            #  Suite "EMA 3/8 y MACD" (v3) — universal
            # ───────────────────────────────────────────────
            _run("EMA_3_8",         _strat_v3.analyze_ema_3_8,         md)
            _run("EMA_8_21",        _strat_v3.analyze_ema_8_21,        md)
            _run("MACD_ACCEL",      _strat_v3.analyze_macd_accel,      md)
            _run("VOLUME_BREAKOUT", _strat_v3.analyze_volume_breakout, md)
            _run("BUY_PRESSURE",    _strat_v3.analyze_buy_pressure,    md)
            _run("SELL_PRESSURE",   _strat_v3.analyze_sell_pressure,   md)
            _run("VWAP_MOMENTUM",   _strat_v3.analyze_vwap_momentum,   md)
            # ORB_V3 es equity-only por diseño (requiere RTH equity)
            _run("ORB_V3",          _strat_v3.analyze_orb_v3,          md)
            _run("DONCHIAN_TURTLE", _strat_v3.analyze_donchian_turtle, md)
            _run("BULLS_BSP",       _strat_v3.analyze_bulls_bsp,       md)
            _run("NET_BSV",         _strat_v3.analyze_net_bsv,         md)

            # ───────────────────────────────────────────────
            #  Combos Ganadores (2026-04) — 7 estrategias
            # ───────────────────────────────────────────────
            _run("COMBO1_EMA_SCALPER",  _strat_v3.analyze_combo1_ema_scalper,  md)
            _run("COMBO2_RUBBER_BAND",  _strat_v3.analyze_combo2_rubber_band,  md)
            _run("COMBO3_NINO_SQUEEZE", _strat_v3.analyze_combo3_nino_squeeze, md)
            _run("COMBO4_SLIMRIBBON",   _strat_v3.analyze_combo4_slimribbon,   md)
            _run("COMBO5_BTD",          _strat_v3.analyze_combo5_btd,          md)
            _run("COMBO6_FRACTALCCIX",  _strat_v3.analyze_combo6_fractalccix,  md)
            _run("COMBO7_CAMPBELL",     _strat_v3.analyze_combo7_campbell,     md)

        # Consolidar multi-TF → una señal por estrategia
        self._confluence_snapshot = cf.snapshot()
        consolidated = cf.consolidate()
        signals: dict = {}
        for (t, sname), final_sig in consolidated.items():
            if t != ticker:
                continue
            # Elegir el result del TF más bajo que coincida con la señal final
            tf_results = results_by_tf.get(sname, {})
            rep = None
            for tf in sorted(tf_results.keys()):
                r = tf_results[tf]
                if final_sig == "HOLD" or r.get("signal") == final_sig:
                    rep = r
                    break
            if rep is None:
                continue
            signals[sname] = {
                "signal": final_sig,
                "price":  rep.get("price"),
                "tfs":    sorted(tf_results.keys()),
            }

        buy_count  = sum(1 for s in signals.values() if s["signal"] == "BUY")
        sell_count = sum(1 for s in signals.values() if s["signal"] == "SELL")
        logger.info(
            f"[SIGNALS] {ticker} — {len(signals)} estrategias (multi-TF={self._active_timeframes}) | "
            f"BUY={buy_count} SELL={sell_count} HOLD={len(signals)-buy_count-sell_count} "
            f"| confluence={self._confluence_mode}/{self._confluence_min_agree}"
        )

        return signals

    # ── Claude Bot (estrategia #14 sobre acciones) ─────────

    async def _claude_bot_loop(self):
        """
        Loop del Claude Bot para acciones del underlying.
        Corre cada `_claude_bot_interval` segundos (por default 60s).
        En paper mode: solo registra decisiones en Firestore, no ejecuta.
        """
        # Espera inicial: darle tiempo al stream a llenar buffers
        await asyncio.sleep(45)
        logger.info(
            f"[CLAUDE_BOT_V2] Loop iniciado — interval={self._claude_bot_interval}s "
            f"options_enabled={self._claude_options_enabled} "
            f"theta_enabled={self._claude_theta_harvest_enabled}"
        )

        while self._running:
            try:
                if (
                    self._active
                    and self._should_run_claude_bot()
                    and self.claude_bot is not None
                    and not self._is_after_close_time()
                ):
                    await self._claude_bot_tick()
            except Exception as e:
                import traceback
                logger.error(
                    f"[CLAUDE_BOT_V2] Error en tick: {e}\n{traceback.format_exc()}"
                )
                # Circuit breaker: si Claude-bot #14 también tira 400 billing,
                # cuenta hacia el mismo umbral compartido con OptionsBrain.
                self._check_anthropic_billing_error(e)

            await asyncio.sleep(max(10, int(self._claude_bot_interval)))

    def _should_run_claude_bot(self) -> bool:
        """Determine if Claude Bot should run based on strategy toggles.

        - If theta_harvest is enabled → check self._claude_theta_harvest_enabled
        - If other strategies are enabled → check self._claude_options_enabled
        - If both are enabled → Claude runs if either is enabled (OR logic)
        """
        theta_harvest_active = self._strategies_enabled.get("theta_harvest", False)
        other_strategies_active = any(
            v for k, v in self._strategies_enabled.items() if k != "theta_harvest"
        )

        should_run = False
        if theta_harvest_active and self._claude_theta_harvest_enabled:
            should_run = True
        if other_strategies_active and self._claude_options_enabled:
            should_run = True

        return should_run

    async def _claude_bot_tick(self):
        """Construye snapshot, llama a Claude y persiste la decisión."""
        snapshot = self._build_claude_bot_snapshot()

        prices = snapshot.get("prices") or {}
        if not any(prices.values()):
            logger.warning(
                "[CLAUDE_BOT_V2] No hay datos de precios para ningún ticker — "
                "skip este tick"
            )
            return

        # Gate de costos: sin señales técnicas BUY/SELL y sin posiciones
        # abiertas, no hay nada útil que decidir → saltar la llamada Claude.
        # Con ANALYSIS_INTERVAL=180s y gate activo, en sesiones laterales
        # consumimos prácticamente 0 tokens. Override: CLAUDE_GATE=0 en env.
        if getattr(self, "_claude_gate_enabled", True):
            recent = snapshot.get("recent_signals") or []
            has_sig = any(
                (s or {}).get("signal") in ("BUY", "SELL") for s in recent
            )
            has_pos = bool(snapshot.get("open_positions"))
            if not (has_sig or has_pos):
                logger.debug(
                    "[CLAUDE_BOT_V2] Gate: sin signals BUY/SELL recientes "
                    "ni posiciones abiertas — skip tick (ahorro API)"
                )
                return

        # [CLAUDE_BOT_SNAP] — visibilidad sobre qué recibe Claude
        sample = next(iter(self._quotes.values()), None) if self._quotes else None
        candles_summary = snapshot.get("candles_summary", {})
        sample_candle = None
        for t in snapshot.get("tickers", []):
            buf = self._candle_buffer.raw_candles(t)
            if buf:
                sample_candle = buf[-1]
                break
        logger.info(
            f"[CLAUDE_BOT_SNAP] prices={prices} | "
            f"sample_quote={sample} | "
            f"candles_summary={candles_summary} | "
            f"raw_last_candle={sample_candle}"
        )

        decision = await self.claude_bot.decide(snapshot)
        # Éxito Anthropic → resetear contador del billing breaker
        self._on_anthropic_success()
        self._claude_bot_last_tick = time.time()
        self._claude_bot_decision  = decision

        decision_record = {
            **decision,
            "ts":     time.time(),
            "ts_str": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        }
        self._claude_bot_history.insert(0, decision_record)
        self._claude_bot_history = self._claude_bot_history[:50]

        logger.info(
            f"[CLAUDE_BOT_V2] Decisión: {decision.get('ticker')} "
            f"→ {decision.get('signal')} @ ${decision.get('price')} "
            f"| conf={decision.get('confidence')} "
            f"| strat={decision.get('strategy_used')} "
            f"| reasoning={decision.get('reasoning','')[:500]}"
        )

        # Persistir en Firestore (namespace separado de OptionsBrain)
        # Path: eolo-claude-bot-decisions-v2/{YYYY-MM-DD}/decisions/{ts}
        try:
            from google.cloud import firestore as _fs
            _db = _fs.Client(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
            )
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tsid  = f"{time.time():.3f}"
            _db.collection("eolo-claude-bot-decisions-v2") \
               .document(today).collection("decisions").document(tsid) \
               .set({**decision_record, "recorded_ts": time.time()})
        except Exception as _e:
            logger.warning(f"[CLAUDE_BOT_V2] Firestore write falló: {_e}")

        # Ejecución en paper: registrar posición virtual pero sin broker real.
        # Hay un TODO de integrar stock trader en v2 — hasta entonces,
        # mantenemos tracking de paper positions en memoria.
        if decision.get("signal") in ("BUY", "SELL") and not self.claude_bot.paper_mode:
            logger.warning(
                "[CLAUDE_BOT_V2] ⚠️  Modo LIVE solicitado pero no hay stock trader "
                "en v2 — la decisión se loggea pero NO se ejecuta"
            )
        elif decision.get("signal") == "BUY":
            self._claude_bot_positions.append({
                "ticker":    decision.get("ticker"),
                "entry":     decision.get("price"),
                "entry_ts":  time.time(),
                "strategy":  decision.get("strategy_used"),
                "reasoning": decision.get("reasoning"),
                "stop_loss_pct":   decision.get("stop_loss_pct"),
                "take_profit_pct": decision.get("take_profit_pct"),
                "paper":     True,
            })
        elif decision.get("signal") == "SELL":
            tkr = decision.get("ticker")
            self._claude_bot_positions = [
                p for p in self._claude_bot_positions if p.get("ticker") != tkr
            ]

    def _build_claude_bot_snapshot(self) -> dict:
        """
        Arma el dict de entrada para ClaudeBotEngine.decide() usando
        los buffers del stream (velas 1m + quotes L1).
        """
        from zoneinfo import ZoneInfo
        et_now = datetime.now(ZoneInfo("America/New_York"))

        tickers = sorted(TICKERS)

        # prices: mark > last > mid(bid,ask) por ticker
        prices: dict[str, float | None] = {}
        candles_summary: dict[str, dict] = {}

        for t in tickers:
            q = self._quotes.get(t) or {}
            p = q.get("mark") or q.get("last")
            if not p:
                bid, ask = q.get("bid"), q.get("ask")
                if bid and ask:
                    p = (bid + ask) / 2
            prices[t] = p

            # Resumen de la última vela + trend aproximada de 1m/5m/15m
            buf = self._candle_buffer.raw_candles(t)
            if buf:
                last = buf[-1]
                o = last.get("open"); h = last.get("high")
                l = last.get("low");  cl = last.get("close")
                vol = last.get("volume")
                change_pct = None
                if o and cl:
                    try:
                        change_pct = round((float(cl) - float(o)) / float(o) * 100, 3)
                    except (TypeError, ValueError, ZeroDivisionError):
                        change_pct = None

                # Trends: dirección del close sobre N velas pasadas
                def _trend(n: int) -> str:
                    if len(buf) < n + 1:
                        return "n/a"
                    try:
                        past  = float(buf[-n-1].get("close") or 0)
                        now_c = float(buf[-1].get("close") or 0)
                        if not past:
                            return "n/a"
                        d = (now_c - past) / past * 100
                        if d > 0.15:
                            return "up"
                        if d < -0.15:
                            return "down"
                        return "flat"
                    except (TypeError, ValueError):
                        return "n/a"

                candles_summary[t] = {
                    "open":  o, "high": h, "low": l, "close": cl,
                    "volume": vol,
                    "change_pct_open": change_pct,
                    "trend_1m":  _trend(1),
                    "trend_5m":  _trend(5),
                    "trend_15m": _trend(15),
                }

        # recent_signals: usamos las señales técnicas que ya calculan las
        # estrategias v1 y que guardamos en _signals_data.
        recent_signals: list[dict] = []
        for tkr, strat_map in (self._signals_data or {}).items():
            for strat_name, sig in (strat_map or {}).items():
                if sig.get("signal") in ("BUY", "SELL"):
                    recent_signals.append({
                        "ts":       et_now.strftime("%H:%M:%S"),
                        "strategy": strat_name,
                        "ticker":   tkr,
                        "signal":   sig.get("signal"),
                        "price":    sig.get("price"),
                    })

        # open_positions: paper positions del propio Claude Bot
        open_positions = []
        for p in self._claude_bot_positions:
            tkr = p.get("ticker")
            cur = prices.get(tkr)
            entry = p.get("entry")
            pnl_pct = None
            if cur and entry:
                try:
                    pnl_pct = (float(cur) - float(entry)) / float(entry) * 100
                except (TypeError, ValueError, ZeroDivisionError):
                    pnl_pct = None
            open_positions.append({
                "ticker":  tkr,
                "entry":   entry,
                "current": cur,
                "unrealized_pnl_pct": pnl_pct,
            })

        # stats del día — usamos lo que tenemos a mano
        stats_today = {
            "trades_today":  len(self._claude_bot_history),
            "signals_today": sum(
                1 for d in self._claude_bot_history
                if d.get("signal") in ("BUY", "SELL")
            ),
            "total_pnl":     0,   # sin broker real todavía en paper v2
            "win_rate":      "n/a",
        }

        return {
            "tickers":         list(tickers),
            "prices":          prices,
            "candles_summary": candles_summary,
            "recent_signals":  recent_signals,
            "open_positions":  open_positions,
            "budget":          self._claude_bot_budget,
            "market_time_et":  et_now.strftime("%Y-%m-%d %H:%M ET"),
            "stats_today":     stats_today,
        }

    # ── Monitor de posiciones ──────────────────────────────

    async def _position_monitor_loop(self):
        """
        Actualiza la lista de posiciones abiertas cada 60 segundos.
        También ejecuta stop-loss / take-profit automáticos.
        """
        while self._running:
            try:
                self._open_positions = await self.trader.get_positions()
                logger.debug(
                    f"[CROP] Posiciones abiertas: {len(self._open_positions)}"
                )

                # Verificar stop-loss y take-profit
                await self._check_exit_conditions()

            except Exception as e:
                logger.error(f"[CROP] Error en monitor de posiciones: {e}")

            await asyncio.sleep(60)

    def _is_daily_loss_cap_hit(self) -> bool:
        """
        Evalúa el daily loss cap y cachea el resultado detallado en
        self._daily_loss_cap_status. Retorna True cuando el P&L realizado +
        no-realizado del día (sobre capital nocional = budget_per_trade ×
        max_positions) es <= self._daily_loss_cap_pct. Fail-soft: ante error,
        False.

        El cap se setea desde el modal Config (negativo, p.ej. -5 → frena
        cuando perdés 5% del capital nocional del día).

        Side-effect: escribe en self._daily_loss_cap_status un dict con:
            hit, pnl_pct, realized, unrealized, cap, nominal_equity,
            n_open, n_trades_today, ts
        Esto lo consume el state publisher (dashboard-visible) y el caller
        que loguea con contexto rico.
        """
        status = {
            "hit":            False,
            "pnl_pct":        0.0,
            "realized":       0.0,
            "unrealized":     0.0,
            "cap":            float(self._daily_loss_cap_pct),
            "nominal_equity": 0.0,
            "n_open":         0,
            "n_trades_today": 0,
            "ts":             time.time(),
            "error":          None,
        }
        try:
            cap = float(self._daily_loss_cap_pct)
            if cap >= 0:
                # No configurado o valor inválido → sin cap
                self._daily_loss_cap_status = status
                return False

            budget = float(self._budget_per_trade or 0) or 200.0
            nominal_equity = budget * max(1, int(self._max_positions))
            status["nominal_equity"] = nominal_equity
            if nominal_equity <= 0:
                self._daily_loss_cap_status = status
                return False

            # Realized: desde paper_trades del día
            trades = self._read_paper_trades()
            status["n_trades_today"] = len(trades)
            realized = self._calc_pnl(trades).get("total_pnl", 0.0)

            # Unrealized: posiciones abiertas con current/entry
            # (En paper mode el current_price no se refresca post-open; en live
            # viene de Schwab vía options_trader.get_positions.)
            unrealized = 0.0
            n_open = 0
            for pos in self._open_positions:
                n_open += 1
                entry   = float(pos.get("entry_price", 0) or 0)
                current = float(pos.get("current_price", 0) or 0)
                qty     = int(pos.get("contracts", pos.get("qty", 1)) or 1)
                if entry > 0 and current > 0:
                    unrealized += (current - entry) * qty * 100

            pnl_total = realized + unrealized
            pnl_pct   = pnl_total / nominal_equity * 100.0

            status.update({
                "hit":        pnl_pct <= cap,
                "pnl_pct":    pnl_pct,
                "realized":   realized,
                "unrealized": unrealized,
                "n_open":     n_open,
            })
            self._daily_loss_cap_status = status
            return status["hit"]
        except Exception as e:
            logger.debug(f"[CROP] _is_daily_loss_cap_hit error: {e}")
            status["error"] = str(e)
            self._daily_loss_cap_status = status
            return False

    async def _check_exit_conditions(self):
        """
        Para cada posición, verifica si alcanzó el stop-loss o take-profit
        y la cierra automáticamente.

        Umbrales:
        - Si la posición trae `stop_loss_pct` / `take_profit_pct` propios
          (p.ej. los que metió Claude o una estrategia), se respetan.
        - Si no, se usan los defaults globales editables desde el dashboard
          (modal Config → Firestore → self._default_stop_loss_pct / _take_profit_pct).
        """
        sl_default = float(self._default_stop_loss_pct   or 25.0)
        tp_default = float(self._default_take_profit_pct or 50.0)

        for pos in self._open_positions:
            entry   = pos.get("entry_price", 0)
            current = pos.get("current_price", 0)

            if not entry or not current:
                continue

            pnl_pct = (current - entry) / entry * 100

            sl_pct = float(pos.get("stop_loss_pct")   or sl_default)
            tp_pct = float(pos.get("take_profit_pct") or tp_default)
            # SL es umbral negativo; TP es positivo. Aseguramos signos.
            sl_threshold = -abs(sl_pct)
            tp_threshold =  abs(tp_pct)

            if pnl_pct <= sl_threshold:
                logger.warning(
                    f"[CROP] STOP LOSS {pos['symbol']}: "
                    f"P&L={pnl_pct:.1f}% ≤ {sl_threshold:.1f}% — cerrando posición"
                )
                await self._close_position(pos)

            elif pnl_pct >= tp_threshold:
                logger.info(
                    f"[CROP] TAKE PROFIT {pos['symbol']}: "
                    f"P&L={pnl_pct:.1f}% ≥ {tp_threshold:.1f}% — cerrando posición 🎯"
                )
                await self._close_position(pos)

    async def _close_position(self, pos: dict):
        """Cierra una posición específica."""
        opt_type = pos.get("option_type", "call")
        if opt_type == "call":
            await self.trader.close_long_call(
                pos["ticker"], pos["expiration"],
                pos["strike"], pos["contracts"]
            )
        else:
            await self.trader.close_long_put(
                pos["ticker"], pos["expiration"],
                pos["strike"], pos["contracts"]
            )

    # ── Auto-close 15:27 ET ────────────────────────────────

    async def _auto_close_loop(self):
        """
        Cierra todas las posiciones a las 15:27 ET todos los días.
        Mismo comportamiento que Eolo v1.
        """
        while self._running:
            if self._should_auto_close():
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if self._auto_close_done_date != today:
                    logger.info("[CROP] 🕒 AUTO-CLOSE 15:27 ET — cerrando todas las posiciones")
                    try:
                        closed = await self.trader.close_all_positions()
                        logger.info(
                            f"[CROP] Auto-close completado: {len(closed)} órdenes de cierre"
                        )
                    except Exception as e:
                        logger.error(f"[CROP] Error en auto-close: {e}")
                    self._auto_close_done_date = today

            await asyncio.sleep(30)

    def _should_auto_close(self) -> bool:
        """
        True si ya pasó el `auto_close_et` configurado y todavía estamos
        dentro del día de mercado (antes de 16:00 ET). Usa self._schedule,
        que se refresca cada ciclo desde Firestore vía _poll_settings().
        """
        now = now_et()
        sch = self._schedule or DEFAULTS_EQUITY
        if not sch.enabled:
            return False
        if not _tr_is_after_auto_close(now, sch):
            return False
        # Ceiling 16:00 ET — no cerramos fuera del día
        return now.hour < 16

    def _is_after_close_time(self) -> bool:
        """
        True si ya NO se permiten nuevas órdenes — equivale a "fuera del
        rango de trading" (antes del start o desde end en adelante).
        """
        sch = self._schedule or DEFAULTS_EQUITY
        return not is_within_trading_window(now_et(), sch)

    def _is_within_trading_window(self) -> bool:
        """True si estamos dentro del rango [start, end) configurado."""
        sch = self._schedule or DEFAULTS_EQUITY
        return is_within_trading_window(now_et(), sch)

    # ── State writer loop ──────────────────────────────────

    async def _state_writer_loop(self):
        """Escribe el estado al disco y Firestore cada 30s, siempre."""
        await asyncio.sleep(10)  # espera inicial
        while self._running:
            self._write_state()
            await asyncio.sleep(30)

    # ── Token auto-refresh ─────────────────────────────────

    async def _token_refresh_loop(self):
        """
        Refresca el Schwab access_token cada 25 minutos.
        El token expira a los 30 min — esto lo renueva antes de que expire.
        Usa la misma lógica que refresh_token_local.py.
        """
        # Espera inicial: deja que el bot arranque antes de refrescar
        await asyncio.sleep(60)

        while self._running:
            try:
                loop = asyncio.get_event_loop()
                ok = await loop.run_in_executor(None, self._do_token_refresh)
                if ok:
                    logger.info("[TOKEN] ✅ Access token refrescado automáticamente")
                else:
                    logger.warning("[TOKEN] ⚠️  Refresh falló — el token puede expirar pronto")
            except Exception as e:
                logger.error(f"[TOKEN] Error en auto-refresh: {e}")

            await asyncio.sleep(self._token_refresh_interval)

    def _do_token_refresh(self) -> bool:
        """Refresca el token de Schwab usando el refresh_token de Firestore."""
        import base64
        import requests as _req
        try:
            sys.path.insert(0, os.path.join(BASE_DIR, ".."))
            from helpers import (
                retrieve_google_secret_dict,
                retrieve_firestore_value,
                store_firestore_value,
            )

            creds = retrieve_google_secret_dict(
                gcp_id="eolo-schwab-agent", secret_id="cs-app-key"
            )
            app_key    = creds["app-key"]
            app_secret = creds["app-secret"]

            refresh_token = retrieve_firestore_value(
                collection_id="schwab-tokens",
                document_id="schwab-tokens-auth",
                key="refresh_token",
            )
            if not refresh_token:
                logger.error("[TOKEN] refresh_token no encontrado en Firestore")
                return False

            b64 = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
            headers = {
                "Authorization": f"Basic {b64}",
                "Content-Type":  "application/x-www-form-urlencoded",
            }
            payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}

            resp = _req.post(
                "https://api.schwabapi.com/v1/oauth/token",
                headers=headers, data=payload, timeout=15
            )
            resp.raise_for_status()
            tokens = resp.json()

            if "access_token" not in tokens:
                logger.error(f"[TOKEN] Respuesta inesperada: {tokens}")
                return False

            # Preservar campos existentes y actualizar
            existing = {}
            for key in ["access_token", "refresh_token", "expires_in",
                        "token_type", "scope", "id_token"]:
                val = retrieve_firestore_value("schwab-tokens", "schwab-tokens-auth", key)
                if val is not None:
                    existing[key] = val
            existing.update({k: v for k, v in tokens.items() if v is not None})

            store_firestore_value(
                project_id="eolo-schwab-agent",
                collection_id="schwab-tokens",
                document_id="schwab-tokens-auth",
                value=existing,
            )
            return True

        except Exception as e:
            logger.error(f"[TOKEN] Error en refresh: {e}")
            return False

    # ── Command watcher (control remoto desde el dashboard) ─

    async def _command_watcher_loop(self):
        """
        Lee periódicamente los docs de Firestore que el dashboard usa para
        controlar el bot:
          • eolo-crop-config/commands  → órdenes puntuales (set_active, close_all)
          • eolo-crop-config/settings  → config persistente (budget, max_positions)

        Los cambios se aplican en caliente (no hace falta reiniciar el bot).
        """
        # Espera inicial para que Firestore esté listo
        await asyncio.sleep(5)

        # Cargar settings iniciales
        await self._load_settings()

        logger.info("[COMMANDS] Watcher activo — escuchando comandos del dashboard")

        while self._running:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._poll_commands)
                await loop.run_in_executor(None, self._poll_settings)
            except Exception as e:
                logger.debug(f"[COMMANDS] Error en poll: {e}")

            await asyncio.sleep(self._command_poll_interval)

    async def _load_settings(self):
        """Carga la config persistente al arrancar el bot."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._poll_settings)
        except Exception as e:
            logger.debug(f"[COMMANDS] Error cargando settings iniciales: {e}")

    def _poll_commands(self):
        """Lee el doc de comandos y ejecuta si hay uno pendiente."""
        try:
            from google.cloud import firestore as _fs
            db  = _fs.Client()
            doc = db.collection("eolo-crop-config").document("commands").get()
            if not doc.exists:
                return
            cmd = doc.to_dict() or {}

            issued_ts = float(cmd.get("issued_ts") or 0)
            consumed  = bool(cmd.get("consumed", False))

            # Skip si ya fue consumido o es más viejo que el último visto
            if consumed or issued_ts <= self._last_command_ts:
                return

            cmd_type = cmd.get("type")
            logger.info(f"[COMMANDS] 📩 Comando recibido: {cmd_type} (issued {cmd.get('issued_at')})")

            if cmd_type == "set_active":
                new_state = bool(cmd.get("active", True))
                old_state = self._active
                self._active = new_state
                logger.warning(
                    f"[COMMANDS] Bot {'REANUDADO' if new_state else 'PAUSADO'} "
                    f"(era {'activo' if old_state else 'pausado'})"
                )
                # Si el user reactiva, le damos al breaker una pista limpia:
                # reseteamos el contador de billing errors, el flag paused y el
                # anti-spam de Telegram. Asumimos que el user cargó créditos — si
                # aún siguen fallando, el breaker volverá a tripar desde 0 y
                # Telegram volverá a mandar (no silencio).
                if new_state and (
                    self._anthropic_billing_paused
                    or self._anthropic_billing_errors > 0
                ):
                    logger.warning(
                        "[BILLING-BREAKER] manual reset: user reactivó bot. "
                        f"(errors era {self._anthropic_billing_errors}, "
                        f"paused era {self._anthropic_billing_paused})"
                    )
                    self._anthropic_billing_errors   = 0
                    self._anthropic_billing_paused   = False
                    self._anthropic_billing_notified = False
                    self._persist_billing_status_to_firestore(paused=False)

            elif cmd_type == "close_all":
                reason = cmd.get("reason", "dashboard")
                logger.warning(f"[COMMANDS] 🚨 CLOSE ALL recibido — razón: {reason}")
                # Programar el cierre asíncrono usando el loop guardado en start()
                loop = getattr(self, "_loop", None)
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._execute_close_all(reason), loop
                    )
                else:
                    logger.error("[COMMANDS] Loop no disponible para close_all")

            elif cmd_type == "force_theta_entry":
                # Fuerza una entrada inmediata ignorando la ventana horaria.
                # Útil para testing fuera de la ventana 10:00-10:45.
                tickers = cmd.get("tickers") or THETA_TICKERS
                logger.warning(f"[COMMANDS] FORCE_THETA_ENTRY para {tickers}")
                self._theta_force_entry = True
                loop = getattr(self, "_loop", None)
                if loop is not None:
                    async def _force_entries():
                        try:
                            for t in tickers:
                                chain = self._last_chain.get(t)
                                if chain:
                                    logger.info(f"[COMMANDS] force_theta_entry: scan {t}")
                                    await self._run_theta_harvest(t, chain)
                                else:
                                    logger.warning(f"[COMMANDS] sin chain para {t} — skip")
                        finally:
                            self._theta_force_entry = False   # reset DESPUÉS de correr
                    asyncio.run_coroutine_threadsafe(_force_entries(), loop)

            else:
                logger.warning(f"[COMMANDS] Tipo desconocido: {cmd_type}")

            # Marcar consumido y actualizar timestamp
            self._last_command_ts = issued_ts
            db.collection("eolo-crop-config").document("commands").update({
                "consumed":     True,
                "consumed_ts":  time.time(),
                "consumed_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
            })

        except Exception as e:
            logger.debug(f"[COMMANDS] poll_commands error: {e}")

    def _poll_settings(self):
        """Lee la config persistente (budget, max_positions) y la aplica."""
        try:
            from google.cloud import firestore as _fs
            db  = _fs.Client()
            doc = db.collection("eolo-crop-config").document("settings").get()
            if not doc.exists:
                return
            cfg = doc.to_dict() or {}

            updated_ts = float(cfg.get("updated_ts") or 0)
            if updated_ts <= self._last_settings_ts:
                return
            self._last_settings_ts = updated_ts

            changed = []

            if "budget_per_trade" in cfg:
                try:
                    new_val = float(cfg["budget_per_trade"])
                    if new_val != self._budget_per_trade:
                        old = self._budget_per_trade
                        self._budget_per_trade = new_val
                        # Propagar al brain y al trader si tienen el setter
                        for obj in (self.brain, self.trader):
                            if hasattr(obj, "set_budget"):
                                try:
                                    obj.set_budget(new_val)
                                except Exception:
                                    pass
                            elif hasattr(obj, "budget_per_trade"):
                                try:
                                    obj.budget_per_trade = new_val
                                except Exception:
                                    pass
                        changed.append(f"budget={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "max_positions" in cfg:
                try:
                    new_val = int(cfg["max_positions"])
                    if new_val != self._max_positions:
                        old = self._max_positions
                        self._max_positions = new_val
                        changed.append(f"max_positions={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            # ── Risk defaults (SL / TP / Daily Loss Cap) ──
            if "default_stop_loss_pct" in cfg:
                try:
                    new_val = float(cfg["default_stop_loss_pct"])
                    if new_val != self._default_stop_loss_pct:
                        old = self._default_stop_loss_pct
                        self._default_stop_loss_pct = new_val
                        changed.append(f"default_sl_pct={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "default_take_profit_pct" in cfg:
                try:
                    new_val = float(cfg["default_take_profit_pct"])
                    if new_val != self._default_take_profit_pct:
                        old = self._default_take_profit_pct
                        self._default_take_profit_pct = new_val
                        changed.append(f"default_tp_pct={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "daily_loss_cap_pct" in cfg:
                try:
                    new_val = float(cfg["daily_loss_cap_pct"])
                    if new_val != self._daily_loss_cap_pct:
                        old = self._daily_loss_cap_pct
                        self._daily_loss_cap_pct = new_val
                        changed.append(f"daily_loss_cap_pct={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            # ── Trading schedule (start/end/auto-close) ───
            # Keys: trading_start_et, trading_end_et, auto_close_et,
            # trading_hours_enabled. Defaults equity (09:30/15:27/15:27).
            new_schedule = load_schedule(cfg, defaults=DEFAULTS_EQUITY)
            if new_schedule != self._schedule:
                old = self._schedule
                self._schedule = new_schedule
                changed.append(
                    f"schedule={old.start.strftime('%H:%M')}-{old.end.strftime('%H:%M')}"
                    f"→{new_schedule.start.strftime('%H:%M')}-{new_schedule.end.strftime('%H:%M')}"
                    f" ac={new_schedule.auto_close.strftime('%H:%M')}"
                    f" enabled={new_schedule.enabled}"
                )

            # ── Multi-TF + confluencia (eolo_common) ────
            mtf = load_multi_tf_config(cfg)
            if mtf.active_timeframes != self._active_timeframes:
                old = self._active_timeframes
                self._active_timeframes = mtf.active_timeframes
                changed.append(f"active_timeframes={old}→{mtf.active_timeframes}")
            if mtf.confluence_mode != self._confluence_mode:
                old = self._confluence_mode
                self._confluence_mode = mtf.confluence_mode
                changed.append(f"confluence_mode={old}→{mtf.confluence_mode}")
            if mtf.confluence_min_agree != self._confluence_min_agree:
                old = self._confluence_min_agree
                self._confluence_min_agree = mtf.confluence_min_agree
                changed.append(f"confluence_min_agree={old}→{mtf.confluence_min_agree}")

            # ── Claude Bot settings (Options + Theta Harvest separados) ─────────────────────
            if "claude_options_enabled" in cfg:
                try:
                    new_val = bool(cfg["claude_options_enabled"])
                    if new_val != self._claude_options_enabled:
                        old = self._claude_options_enabled
                        self._claude_options_enabled = new_val
                        changed.append(f"claude_options_enabled={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "claude_theta_harvest_enabled" in cfg:
                try:
                    new_val = bool(cfg["claude_theta_harvest_enabled"])
                    if new_val != self._claude_theta_harvest_enabled:
                        old = self._claude_theta_harvest_enabled
                        self._claude_theta_harvest_enabled = new_val
                        changed.append(f"claude_theta_harvest_enabled={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "claude_bot_interval" in cfg:
                try:
                    new_val = int(cfg["claude_bot_interval"])
                    if new_val >= 10 and new_val != self._claude_bot_interval:
                        old = self._claude_bot_interval
                        self._claude_bot_interval = new_val
                        changed.append(f"claude_bot_interval={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "claude_bot_budget" in cfg:
                try:
                    new_val = float(cfg["claude_bot_budget"])
                    if new_val != self._claude_bot_budget:
                        old = self._claude_bot_budget
                        self._claude_bot_budget = new_val
                        changed.append(f"claude_bot_budget={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "claude_bot_paper_mode" in cfg and self.claude_bot is not None:
                try:
                    new_val = bool(cfg["claude_bot_paper_mode"])
                    if new_val != self.claude_bot.paper_mode:
                        old = self.claude_bot.paper_mode
                        self.claude_bot.paper_mode = new_val
                        changed.append(f"claude_bot_paper_mode={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            # ── Strategy selection (toggles del dashboard) ──
            # Dashboard escribe cfg["strategies_enabled"] = {canonical_key: bool}.
            # Merge con los defaults para no perder keys; overrides explícitos
            # ganan. Fail-soft: tipos inválidos se ignoran.
            if "strategies_enabled" in cfg and isinstance(cfg["strategies_enabled"], dict):
                remote_strats = cfg["strategies_enabled"]
                strat_changes = []
                for k, v in remote_strats.items():
                    k = str(k)
                    if k in self._strategies_enabled:
                        new_val = bool(v)
                        if self._strategies_enabled[k] != new_val:
                            strat_changes.append(f"{k}={new_val}")
                            self._strategies_enabled[k] = new_val
                if strat_changes:
                    changed.append(f"strategies=[{', '.join(strat_changes)}]")

            # ── Macro filter enabled (dashboard toggle) ────
            if "macro_filter_enabled" in cfg:
                try:
                    new_val = bool(cfg["macro_filter_enabled"])
                    if new_val != self._macro_filter_enabled:
                        old = self._macro_filter_enabled
                        self._macro_filter_enabled = new_val
                        changed.append(f"macro_filter_enabled={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if changed:
                logger.info(f"[COMMANDS] ⚙️  Config actualizada: {', '.join(changed)}")

            # Propagar defaults SL/TP al OptionsBrain para que los use como
            # fallback cuando Claude no mande valores propios.
            try:
                if hasattr(self, "brain") and hasattr(self.brain, "set_risk_defaults"):
                    self.brain.set_risk_defaults(
                        self._default_stop_loss_pct,
                        self._default_take_profit_pct,
                    )
            except Exception as e:
                logger.debug(f"[COMMANDS] no pude propagar defaults al brain: {e}")

        except Exception as e:
            logger.debug(f"[COMMANDS] poll_settings error: {e}")

    async def _execute_close_all(self, reason: str = "dashboard"):
        """Cierra todas las posiciones abiertas (invocado por close_all)."""
        try:
            closed = await self.trader.close_all_positions()
            logger.warning(
                f"[COMMANDS] ✅ Close all completado: {len(closed)} órdenes "
                f"(razón: {reason})"
            )
        except Exception as e:
            logger.error(f"[COMMANDS] Error en close_all: {e}")

    # ── Dashboard state writer ─────────────────────────────

    def _write_state(self):
        """
        Escribe el estado completo del bot en:
          1. crop_state.json  (dashboard local)
          2. Firestore eolo-options-state/current  (dashboard Cloud Run)
        """
        try:
            from zoneinfo import ZoneInfo
            et_now = datetime.now(ZoneInfo("America/New_York"))

            # P&L desde CSV de paper trades
            paper_trades = self._read_paper_trades()

            # Refrescar snapshot del daily loss cap antes de publicar
            # (side-effect: actualiza self._daily_loss_cap_status).
            try:
                self._is_daily_loss_cap_hit()
            except Exception as e:
                logger.debug(f"[CROP] dlc refresh en state publish falló: {e}")

            # Schedule status — dashboard usa esto para mostrar el banner
            # "Pause time limit" cuando is_within_window=False.
            from eolo_common.trading_hours import format_schedule_for_api
            schedule_status = format_schedule_for_api(self._schedule, now_et())

            # VIX / VVIX — para KPIs de Theta Harvest en el dashboard
            _vix_snap, _vvix_snap = self._theta_get_macro_context()

            state = {
                "updated_at":   et_now.strftime("%Y-%m-%d %H:%M:%S ET"),
                "updated_ts":   time.time(),
                "mode":         "PAPER" if PAPER_TRADING else "LIVE",
                "tickers":      TICKERS,
                "market_open":  self._is_within_trading_window(),
                "schedule":     schedule_status,
                "vix":          _vix_snap,
                "vvix":         _vvix_snap,

                # Control state (leído desde Firestore eolo-crop-config)
                "active":       self._active,
                "config": {
                    "budget_per_trade":        self._budget_per_trade,
                    "max_positions":           self._max_positions,
                    "default_stop_loss_pct":   self._default_stop_loss_pct,
                    "default_take_profit_pct": self._default_take_profit_pct,
                    "daily_loss_cap_pct":      self._daily_loss_cap_pct,
                    # Schedule (editable desde el dashboard)
                    "trading_start_et":        self._schedule.start.strftime("%H:%M"),
                    "trading_end_et":          self._schedule.end.strftime("%H:%M"),
                    "auto_close_et":           self._schedule.auto_close.strftime("%H:%M"),
                    "trading_hours_enabled":   self._schedule.enabled,
                    "macro_filter_enabled":    self._macro_filter_enabled,
                },
                # Daily loss cap snapshot — dashboard lo usa para mostrar
                # pnl real vs cap + estado (HIT / armed / disabled).
                # Poblado en cada _is_daily_loss_cap_hit() call.
                "daily_loss_cap": self._daily_loss_cap_status or {},
                "active_tickers": sorted(TICKERS),

                # Quotes en tiempo real
                "quotes":       self._quotes,

                # IV Surfaces por ticker
                "iv_surfaces":  self._iv_surfaces,

                # Alertas de mispricing por ticker (top 10 por ticker para no exceder Firestore)
                # CROP: Sin mispricing data (solo Theta Harvest)

                # Señales Eolo v1 por ticker
                "signals":      self._signals_data,

                # Última decisión de Claude por ticker
                "claude_last":  self._claude_decisions,

                # Historial de decisiones (últimas 20 para Firestore)
                "claude_history": self._claude_history[:20],

                # Posiciones abiertas
                "positions":    self._open_positions,

                # Paper trades del día (últimos 50)
                "paper_trades": paper_trades[-50:],

                # P&L calculado
                "pnl":          self._calc_pnl(paper_trades),

                # Stats del bot
                "stats": {
                    "claude_calls":    self.brain.call_count,
                    "analysis_count":  len(self._last_analysis),
                    "candle_buffers":  {
                        t: self._candle_buffer.size(t)
                        for t in self._candle_buffer.symbols()
                    },
                    "multi_tf": {
                        "active_timeframes":    self._active_timeframes,
                        "confluence_mode":      self._confluence_mode,
                        "confluence_min_agree": self._confluence_min_agree,
                        "confluence_snapshot":  getattr(self, "_confluence_snapshot", {}),
                    },
                },

                # Toggles activos para el dashboard (source of truth live)
                "strategies_enabled": dict(self._strategies_enabled),

                # Claude Bot (Options + Theta Harvest separados)
                "claude_bot": {
                    "options_enabled":       self._claude_options_enabled,
                    "theta_harvest_enabled": self._claude_theta_harvest_enabled,
                    "interval":      self._claude_bot_interval,
                    "paper_mode":    (
                        self.claude_bot.paper_mode if self.claude_bot else True
                    ),
                    "budget":        self._claude_bot_budget,
                    "model":         (
                        self.claude_bot.model if self.claude_bot else None
                    ),
                    "call_count":    (
                        self.claude_bot.call_count if self.claude_bot else 0
                    ),
                    "last_tick_ts":  self._claude_bot_last_tick,
                    "last_tick_str": (
                        datetime.fromtimestamp(
                            self._claude_bot_last_tick,
                            tz=ZoneInfo("America/New_York"),
                        ).strftime("%H:%M:%S")
                        if self._claude_bot_last_tick else None
                    ),
                    "last_decision": self._claude_bot_decision,
                    "history":       self._claude_bot_history[:30],
                    "paper_positions": self._claude_bot_positions,
                    "available":     self.claude_bot is not None,
                },

                # ── Theta Harvest state ───────────────────────────
                "theta": {
                    "positions":    self._theta_positions,
                    "slots": {
                        t: {str(k): v for k, v in s.items()}
                        for t, s in self._theta_slots.items()
                    },
                    "stats":        {
                        k: v for k, v in self._theta_stats.items()
                        if not k.startswith("_")   # ocultar _wins/_losses del dashboard
                    },
                    "pnl_today":    self._calc_theta_pnl_today(),  # Live P&L: realized + unrealized + credit_total
                    "pnl_history":  self._theta_pnl_history[-80:],
                    "macro":        self._theta_macro_status,
                    "pivots": {
                        t: {
                            "consensus_risk": getattr(r, "consensus_risk", None),
                            "delta_min":      getattr(r, "delta_min", None),
                            "delta_max":      getattr(r, "delta_max", None),
                            "atr_gate_hit":   getattr(r, "atr_gate_hit", None),
                            "price":          getattr(r, "price", None),
                            # Para el pivot gauge del dashboard
                            "avg_pp":         (getattr(r, "details", None) or {}).get("avg_pp"),
                            "dist_pp_pct":    (getattr(r, "details", None) or {}).get("dist_pp_pct"),
                            "delta_range":    f"{getattr(r,'delta_min',0):.2f}–{getattr(r,'delta_max',0):.2f}"
                                              if getattr(r, "delta_min", None) else "",
                        }
                        for t, r in self._theta_pivot_cache.items()
                    },
                    # sector: dirección agregada del análisis sectorial (para el chart del dashboard)
                    "sector": {
                        t: {
                            "direction":           getattr(getattr(r, "sector", None), "direction", None),
                            "weighted_change_pct": getattr(getattr(r, "sector", None), "weighted_change_pct", None),
                            "spread_type":         getattr(getattr(r, "sector", None), "spread_type", None),
                            "top_movers":          getattr(getattr(r, "sector", None), "top_movers", []),
                        }
                        for t, r in self._theta_pivot_cache.items()
                        if getattr(r, "sector", None) is not None
                    },
                    "tick_ad":      self._theta_tick_ad,
                    "enabled":      self._theta_harvest_enabled,
                },
            }

            # 1. JSON local
            with open(self._state_file, "w") as f:
                json.dump(state, f, default=str)

            # 2. Firestore (async en thread para no bloquear el loop)
            import threading
            threading.Thread(
                target=self._write_firestore,
                args=(state,),
                daemon=True,
            ).start()

        except Exception as e:
            logger.debug(f"[DASHBOARD] Error escribiendo state: {e}")

    def _write_firestore(self, state: dict):
        """Escribe el estado en Firestore (corre en thread separado).

        CRITICAL FIX (2026-04-24):
        - Detecta y elimina valores NaN/Infinity (Firestore los rechaza)
        - Elimina campos stats si causa problemas
        - Maneja dicts anidados profundos que Firestore rechaza
        """
        try:
            from google.cloud import firestore as _fs
            clean = _sanitize_for_firestore(state)

            # Si clean quedó None o vacío, no hay nada que escribir
            if not clean or (isinstance(clean, dict) and not clean):
                logger.debug("[DASHBOARD] State vacío después de sanitización, skip write")
                return

            db  = _fs.Client()
            doc = db.collection("eolo-crop-state").document("current")
            doc.set(clean)
            logger.debug("[DASHBOARD] State escrito en Firestore ✅")

        except Exception as e:
            import traceback
            error_str = str(e)

            # Si el error menciona 'stats', reintenta sin ese campo
            if "stats" in error_str.lower() and isinstance(state, dict):
                try:
                    logger.warning("[DASHBOARD] Error con stats field, reintentando sin él...")
                    state_no_stats = {k: v for k, v in state.items() if k != "stats"}
                    clean = _sanitize_for_firestore(state_no_stats)
                    if clean:
                        db  = _fs.Client()
                        doc = db.collection("eolo-crop-state").document("current")
                        doc.set(clean)
                        logger.info("[DASHBOARD] State escrito sin stats field ✅")
                        return
                except Exception as e2:
                    logger.error(f"[DASHBOARD] Reintento también falló: {e2}")

            # Logging completo del error original
            bad = _find_unserializable_path(state)
            logger.error(
                f"[DASHBOARD] Firestore write error: {type(e).__name__}: {e} "
                f"— primer campo no-serializable: {bad}\n{traceback.format_exc()}"
            )

    # ── Persistencia de theta positions (sobrevive reinicios) ─────────────

    _THETA_POS_COLL = "eolo-crop-state"
    _THETA_POS_DOC  = "theta-positions"

    def _load_theta_positions_from_firestore(self):
        """
        Restaura self._theta_positions desde Firestore al arrancar.
        Solo carga posiciones del día actual (evita cargar spreads vencidos).
        """
        try:
            from google.cloud import firestore as _fs
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
            db  = _fs.Client()
            doc = db.collection(self._THETA_POS_COLL).document(self._THETA_POS_DOC).get()
            if not doc.exists:
                logger.info("[ThetaHarvest] No hay posiciones guardadas en Firestore")
                return
            data = doc.to_dict() or {}
            saved_date = data.get("date", "")
            if saved_date != today:
                logger.info(
                    f"[ThetaHarvest] Posiciones guardadas son de {saved_date}, "
                    f"hoy es {today} — descartando (slots liberados)"
                )
                return
            positions = data.get("positions", [])
            if positions:
                self._theta_positions = positions
                # Reconstruir slots desde las posiciones cargadas
                for pos in positions:
                    ticker  = pos.get("ticker", "")
                    dte     = pos.get("dte_slot", pos.get("dte"))
                    if ticker and dte is not None:
                        self._theta_slots.setdefault(ticker, {})[dte] = today
                logger.info(
                    f"[ThetaHarvest] ✅ Restauradas {len(positions)} posiciones desde Firestore"
                )
                _send_telegram(
                    f"♻️ ThetaHarvest: {len(positions)} posiciones restauradas tras reinicio"
                )
        except Exception as e:
            logger.warning(f"[ThetaHarvest] Error cargando posiciones desde Firestore: {e}")

    def _save_theta_positions_to_firestore(self):
        """
        Persiste self._theta_positions en Firestore (en thread separado).
        Llamado tras cada apertura/cierre de posición.
        """
        import threading
        def _do_save():
            try:
                from google.cloud import firestore as _fs
                from zoneinfo import ZoneInfo
                today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                db  = _fs.Client()
                doc = db.collection(self._THETA_POS_COLL).document(self._THETA_POS_DOC)
                # Serializar: convertir floats nan/inf a None
                safe_pos = []
                for p in self._theta_positions:
                    safe_pos.append({
                        k: (None if isinstance(v, float) and (v != v or abs(v) == float("inf")) else v)
                        for k, v in p.items()
                    })
                doc.set({"date": today, "positions": safe_pos, "updated_at": time.time()})
            except Exception as e:
                logger.debug(f"[ThetaHarvest] Error guardando posiciones: {e}")
        threading.Thread(target=_do_save, daemon=True).start()

    def _read_paper_trades(self) -> list[dict]:
        """
        Lee paper trades del día — HYBRID Firestore (autoritativo) + CSV local (fallback).

        El CSV `paper_trades_log.csv` vive en el fs efímero de Cloud Run: se
        borra en cada redeploy. Antes de este fix, tras un redeploy el cap
        diario "se olvidaba" del P&L realizado acumulado porque _calc_pnl
        leía solo CSV. Ahora:
          1. Primero lee eolo-options-trades/{YYYY-MM-DD} de Firestore
             (todos los trades del día, sobreviven redeploys).
          2. Luego merge con CSV local (para trades que todavía no hicieron
             round-trip a Firestore — rare, pero cubre el gap).
          3. Dedup por order_id. Si el order_id no está, usa key compuesta.
          4. Sort por timestamp ascendente.
          5. Normaliza strike a `str(float(...))` para que el tuple key de
             _calc_pnl empareje BUY/SELL aunque vengan de fuentes distintas.
        Ante error en cualquiera de las dos fuentes, fallback a la otra.
        """
        def _norm_strike(v) -> str:
            try:
                return str(float(v)) if v is not None and v != "" else ""
            except (ValueError, TypeError):
                return str(v) if v is not None else ""

        def _normalize(t: dict) -> dict:
            return {
                "timestamp":   str(t.get("timestamp") or ""),
                "order_id":    str(t.get("order_id") or ""),
                "action":      str(t.get("action") or ""),
                "ticker":      str(t.get("ticker") or ""),
                "option_type": str(t.get("option_type") or ""),
                "expiration":  str(t.get("expiration") or ""),
                "strike":      _norm_strike(t.get("strike")),
                "contracts":   t.get("contracts", 0) or 0,
                "limit_price": t.get("limit_price") if t.get("limit_price") not in (None, "") else "MARKET",
                "symbol":      str(t.get("symbol") or ""),
                "strategy":    str(t.get("strategy") or ""),
                "reason":      str(t.get("reason") or ""),
                "pnl_usd":     t.get("pnl_usd"),
                "pnl_pct":     t.get("pnl_pct"),
            }

        trades_by_key: dict[str, dict] = {}

        # 1. Firestore (autoritativo — sobrevive redeploys)
        try:
            from google.cloud import firestore as _fs
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
            db = _fs.Client(project=project_id)
            today = datetime.now().strftime("%Y-%m-%d")
            doc = db.collection("eolo-crop-trades").document(today).get()
            if doc.exists:
                for doc_key, t in (doc.to_dict() or {}).items():
                    if not isinstance(t, dict):
                        continue
                    normalized = _normalize(t)
                    # Dedup key: order_id si existe; si no, la clave del doc
                    # Firestore ({ts}_{ticker}_{action})
                    dedup_key = normalized["order_id"] or doc_key
                    trades_by_key[dedup_key] = normalized
        except Exception as e:
            logger.debug(f"[CROP] _read_paper_trades Firestore read error: {e}")

        # 2. CSV local (fallback para gap post-write local / pre-write FS)
        csv_path = os.path.join(BASE_DIR, "paper_trades_log.csv")
        if os.path.exists(csv_path):
            try:
                import csv as _csv
                with open(csv_path, newline="") as f:
                    reader = _csv.DictReader(f)
                    for row in reader:
                        normalized = _normalize(dict(row))
                        dedup_key = (
                            normalized["order_id"]
                            or f"{normalized['timestamp']}_{normalized['ticker']}_{normalized['action']}"
                        )
                        # Solo agregar si Firestore no lo tiene (Firestore gana)
                        if dedup_key not in trades_by_key:
                            trades_by_key[dedup_key] = normalized
            except Exception as e:
                logger.debug(f"[CROP] _read_paper_trades CSV read error: {e}")

        trades = list(trades_by_key.values())
        trades.sort(key=lambda t: t.get("timestamp", ""))
        return trades

    def _calc_pnl(self, trades: list[dict]) -> dict:
        """Calcula P&L de paper trades (BUY → SELL_TO_CLOSE pairs)."""
        open_buys = {}
        closed = []
        total_pnl = 0.0
        wins = losses = 0

        for t in trades:
            key = (t.get("ticker"), t.get("expiration"), t.get("strike"), t.get("option_type",""))
            action = t.get("action", "")
            try:
                price_raw = t.get("limit_price", 0)
                price = float(price_raw) if price_raw != "MARKET" else 0
                qty   = int(t.get("contracts", 1))
            except (ValueError, TypeError):
                continue

            if action == "BUY_TO_OPEN":
                open_buys[key] = {
                    "price":    price,
                    "qty":      qty,
                    "ts":       t.get("timestamp", ""),
                    "strategy": t.get("strategy", "CLAUDE"),
                    "reason":   t.get("reason", ""),
                    "ticker":   t.get("ticker", ""),
                    "option_type": t.get("option_type", ""),
                    "strike":   t.get("strike", ""),
                    "expiration": t.get("expiration", ""),
                    "symbol":   t.get("symbol", ""),
                }
            elif action == "SELL_TO_CLOSE" and key in open_buys:
                entry = open_buys.pop(key)
                cost  = entry["price"] * entry["qty"] * 100
                pnl   = (price - entry["price"]) * entry["qty"] * 100
                pnl_pct = (pnl / cost * 100) if cost else 0
                total_pnl += pnl
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                closed.append({
                    "ticker":      entry["ticker"],
                    "option_type": entry["option_type"],
                    "strike":      entry["strike"],
                    "expiration":  entry["expiration"],
                    "symbol":      entry["symbol"],
                    "entry_price": entry["price"],
                    "exit_price":  price,
                    "qty":         entry["qty"],
                    "pnl":         round(pnl, 2),
                    "pnl_pct":     round(pnl_pct, 2),
                    "entry_ts":    entry["ts"],
                    "exit_ts":     t.get("timestamp", ""),
                    "strategy":    entry["strategy"],
                    "exit_reason": t.get("strategy", "SELL_TO_CLOSE"),
                    "reason":      entry["reason"],
                })

        rounds = wins + losses

        # Posiciones abiertas con datos completos
        open_list = [
            {
                "ticker":      v["ticker"],
                "option_type": v["option_type"],
                "strike":      v["strike"],
                "expiration":  v["expiration"],
                "symbol":      v["symbol"],
                "entry_price": v["price"],
                "qty":         v["qty"],
                "strategy":    v["strategy"],
                "reason":      v["reason"],
                "entry_ts":    v["ts"],
            }
            for v in open_buys.values()
        ]

        return {
            "total_pnl":  round(total_pnl, 2),
            "wins":       wins,
            "losses":     losses,
            "rounds":     rounds,
            "win_rate":   round(wins / rounds * 100, 1) if rounds > 0 else 0,
            "open_count": len(open_buys),
            "open_list":  open_list,
            "closed":     closed[-30:],
        }

    def _calc_theta_pnl_today(self) -> dict:
        """
        Calcula P&L LIVE de Theta Harvest: Realized (hoy) + Unrealized (abiertos)
        + Crédito Total de posiciones abiertas.

        Returns:
            {
                "realized_today":    float (P&L de spreads cerrados HOY),
                "unrealized_today":  float (P&L de spreads abiertos),
                "total_pnl_today":   float (realized + unrealized),
                "credit_total":      float (crédito total de abiertos),
                "positions_open":    int (count de spreads abiertos),
                "positions_closed_today": int (spreads cerrados HOY),
            }
        """
        try:
            # 1. Realized: desde paper_trades cerrados HOY (CLOSE_SPREAD actions)
            trades = self._read_paper_trades()
            realized_today = 0.0
            closed_count = 0

            for t in trades:
                if t.get("strategy") == "ThetaHarvest":
                    action = t.get("action", "")
                    if action == "BUY_TO_CLOSE_SPREAD":
                        pnl = float(t.get("pnl_usd", 0) or 0)
                        realized_today += pnl
                        closed_count += 1

            # 2. Unrealized: suma de unrealized_pnl desde posiciones abiertas
            unrealized_today = 0.0
            for pos in self._theta_positions:
                unrealized = pos.get("unrealized_pnl", 0)
                if unrealized:
                    unrealized_today += float(unrealized)

            # 3. Credit Total: suma de (net_credit × contracts × 100) de posiciones abiertas
            credit_total = 0.0
            for pos in self._theta_positions:
                net_credit = pos.get("net_credit", 0) or 0
                contracts = pos.get("contracts", 1) or 1
                credit_total += float(net_credit) * int(contracts) * 100

            total_pnl_today = realized_today + unrealized_today

            return {
                "realized_today":    round(realized_today, 2),
                "unrealized_today":  round(unrealized_today, 2),
                "total_pnl_today":   round(total_pnl_today, 2),
                "credit_total":      round(credit_total, 2),
                "positions_open":    len(self._theta_positions),
                "positions_closed_today": closed_count,
            }
        except Exception as e:
            logger.warning(f"[ThetaHarvest] Error calculating pnl_today: {e}")
            return {
                "realized_today":    0.0,
                "unrealized_today":  0.0,
                "total_pnl_today":   0.0,
                "credit_total":      0.0,
                "positions_open":    0,
                "positions_closed_today": 0,
            }


# ── Entry point ────────────────────────────────────────────

# Instancia global del bot (leída por main.py en /status para exponer
# el estado del circuit breaker de billing al dashboard / curl).
bot_instance: "CropBotTheta | None" = None


async def main():
    global bot_instance
    bot = CropBotTheta()
    bot_instance = bot
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Interrupción manual — deteniendo EOLO v2...")
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
