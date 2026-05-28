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
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

# ── Ajustar path ──────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, ".."))        # raíz eolo/
sys.path.insert(0, os.path.join(BASE_DIR, "..", "Bot")) # estrategias v1

# ── Imports de módulos CROP (Theta Harvest only) ────────────────────────────
from stream.rest_polling   import SchwabRestPoller
from stream.options_chain  import OptionChainFetcher
from analysis.greeks       import enrich_contract
from analysis.iv_surface   import IVSurface
from execution.options_trader import OptionsTrader, _send_telegram
from theta_harvest import scan_theta_harvest_tranches, ThetaHarvestSignal
from theta_harvest.theta_harvest_strategy import (
    evaluate_open_position,
    TARGET_DTES,
    _determine_spread_type,
    ENTRY_HOUR_ET,
    ENTRY_WINDOW_MINUTES,
    # Sprint S3.1-A: importar para instance var defaults
    STOP_LOSS_MULT,
    TRANCHE_PROFIT_TARGETS,
    # Sprint S3.1-B: thresholds editables via UI
    VIX_SPIKE_DELTA,
    VVIX_PANIC_THRESHOLD,
    DELTA_DRIFT_MAX,
    SPY_DROP_PCT_30M,
    MIN_MINUTES_TO_EXP,
    # Sprint S3.1-C: VIX velocity editables via UI
    VIX_VELOCITY_THRESHOLD_UP_PCT,
    VIX_VELOCITY_THRESHOLD_DOWN_PCT,
    VIX_VELOCITY_WINDOW_SECONDS,
    # Sprint S3.3: per-ticker config editable via UI
    TICKER_CONFIG,
    # Sprint S3.4: VIX credit table editable via UI
    VIX_CREDIT_TABLE,
)
from theta_harvest.pivot_analysis import (
    analyze_pivots, format_pivot_summary,
    fetch_tick_ad, TickADContext,
    # Sprint S3.2: dict default editable via UI
    DELTA_BY_RISK,
)
from theta_harvest.macro_news_filter import is_news_day, log_calendar_status
from helpers import get_access_token

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
from eolo_common.trading_hours import (
    DEFAULTS_EQUITY,
    TradingSchedule,
    load_schedule,
    is_within_trading_window,
    is_after_auto_close as _tr_is_after_auto_close,
    now_et,
)

# ── LLM Engine integration (4.B.2) ──────────────────────────────
from llm_gate import LLMGateClient, DecisionCache
from llm_gate.snapshot import build_market_snapshot_from_crop
from llm_gate.trade_logger import TradeLogger  # Sprint 9
from llm_gate.integration import (
    should_call_llm, llm_decision_to_scan_params, decision_indicates_exit,
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
# 4.D HZ-1 / tech debt #16 RESUELTA: bumpeado 100 → 500 para que el LLM snapshot
# tenga suficiente data para calcular indicators 15m (RSI/ATR/EMA period 14
# requiere >=14 candles 15m → ~225 min 1-min = 225+ candles 1-min).
# 500 candles 1-min = 8.3h = sesion NYSE completa. Memory impact: ~200KB total.
CANDLE_BUFFER_SIZE = 500

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
        # Sprint S3.B: overrides in-memory para _strategy_params() merge.
        # Editados via POST /api/state/edit. Sin Firestore (in-memory only).
        # Estructura: dict[path_str, value] donde path es fieldId convention.
        self._strategy_overrides: dict = {}

        # Sprint S3.1-A: instance vars para exits_advanced overrides funcionales.
        # Defaults = constantes module-level. Se actualizan via
        # _apply_strategy_overrides_to_instance_vars() después de cada cambio
        # de _strategy_overrides (POST endpoint o _poll_settings).
        # Strategy functions reciben estos valores como kwargs explícitos.
        self._stop_loss_mult: float = STOP_LOSS_MULT
        self._tranche_profit_targets: list = list(TRANCHE_PROFIT_TARGETS)
        # Sprint S3.1-B: exits_advanced thresholds (defaults = constantes module-level)
        self._vix_spike_delta:      float = VIX_SPIKE_DELTA
        self._vvix_panic_threshold: float = VVIX_PANIC_THRESHOLD
        self._delta_drift_max:      float = DELTA_DRIFT_MAX
        self._spy_drop_pct_30m:     float = SPY_DROP_PCT_30M
        self._min_minutes_to_exp:   int   = MIN_MINUTES_TO_EXP
        # Sprint S3.2: delta_by_risk dict de LISTAS mutables (copia profunda del módulo).
        # Default = comportamiento idéntico al pre-S3.2 (fallback a pivot_result en el scan).
        self._delta_by_risk: dict = {k: list(v) for k, v in DELTA_BY_RISK.items()}
        # Sprint S3.3: per-ticker config editable via UI (copia profunda).
        # Default = TICKER_CONFIG módulo (comportamiento idéntico al pre-S3.3).
        self._ticker_config: dict = {t: dict(cfg) for t, cfg in TICKER_CONFIG.items()}
        # Sprint S3.4: VIX_CREDIT_TABLE editable (list-of-lists; tuples del módulo
        # son inmutables, copiamos a listas mutables). Default = VIX_CREDIT_TABLE.
        # Última fila conserva vix_ceil=float('inf'); ese campo NO es editable.
        self._vix_credit_table: list = [list(row) for row in VIX_CREDIT_TABLE]

        # Tech debt #18: VIX history buffer para velocity_30m (demand-driven)
        self._vix_price_history: list[tuple[float, float]] = []  # (ts_unix, vix_value), cleanup >35min

        # LLM Engine integration (feature flag default OFF)
        self._llm_engine_enabled: bool = False
        self._llm_engine_url: str = os.getenv("LLM_ENGINE_URL", "")
        self._llm_engine_threshold: int = 7
        self._llm_cache_ttl_seconds: float = 30.0
        self._llm_tickers_enabled: dict = {"SPY": True, "QQQ": True, "IWM": True, "TQQQ": True}
        self._llm_max_positions: int = 10
        # 4.C.1: threshold para overridear spread_type del sector
        # (mas conservador que el strike hint threshold del scan = 7)
        self._llm_spread_override_threshold: int = 8
        self._llm_client: Optional[LLMGateClient] = None  # lazy init
        self._llm_cache: Optional[DecisionCache] = None   # lazy init
        # Sprint 9: TradeLogger lazy init en el primer record_open.
        self._trade_logger: Optional[TradeLogger] = None


        # Módulos
        # Sprint 5 Fix B: REST polling reemplaza WS Schwab (tech debt #23).
        # Interface compatible — add_handler/start/stop sin cambios downstream.
        self.stream        = SchwabRestPoller(tickers=TICKERS)
        self.chain_fetcher = OptionChainFetcher(tickers=TICKERS, interval=30)
        # CROP: sin MispricingScanner (solo Theta Harvest)
        self.trader        = OptionsTrader(
            paper=PAPER_TRADING,
            chain_fetcher=self.chain_fetcher,
        )

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
        # Ventana de entrada theta harvest — editable desde el dashboard vía Firestore.
        # Defaults hardcoded como safety net; Firestore los overrides en _poll_settings().
        self._entry_hour_et:        float = ENTRY_HOUR_ET
        self._entry_window_minutes: float = ENTRY_WINDOW_MINUTES
        # VIX entry threshold — editable desde el dashboard. Default safety net.
        self._vix_entry_threshold:  float = 40.0
        # Schedule de trading — editable desde el dashboard. Defaults equity
        # (09:30-15:27 ET con auto-close 15:27). Se refresca en _poll_settings().
        self._schedule: TradingSchedule = DEFAULTS_EQUITY
        # Filtro macro — se puede desactivar desde el dashboard (toggle MACRO ON/OFF)
        self._macro_filter_enabled: bool = True
        # Iron Condor flag — Paso 5 backlog v2.
        # False (default): guard activo, NO se permite PUT+CALL en mismo ticker.
        # True: guard se saltea, permite Iron Condors (eval futura).
        self._iron_condor_enabled: bool = False

        # Paso 7 backlog v2: VIX velocidad ±3% en 120s
        # Default False (conservador). Activación via Firestore: vix_velocity_enabled.
        self._vix_velocity_enabled: bool = False

        # Sprint S3.1-C: thresholds + ventana editables (defaults = constantes module-level)
        self._vix_velocity_threshold_up_pct:   float = VIX_VELOCITY_THRESHOLD_UP_PCT
        self._vix_velocity_threshold_down_pct: float = VIX_VELOCITY_THRESHOLD_DOWN_PCT
        self._vix_velocity_window_seconds:     int   = VIX_VELOCITY_WINDOW_SECONDS
        # samples = sample actual + N históricos × 30s. Para 120s → 5 (actual+4).
        self._vix_velocity_samples: int = max(2, round(self._vix_velocity_window_seconds / 30) + 1)
        self._vix_velocity_buffer: deque = deque(maxlen=self._vix_velocity_samples)

        # Cooldown: 1 disparo por día por dirección (reset en _auto_close_loop)
        self._vix_velocity_up_done: bool = False
        self._vix_velocity_down_done: bool = False

        self._last_command_ts:      float        = 0.0
        self._last_settings_ts:     float        = 0.0
        # Sprint S3.X: timestamp del último override aplicado desde Firestore.
        # Guard para que _load_strategy_overrides_from_firestore sea idempotente.
        self._last_overrides_ts:    float        = 0.0
        self._command_poll_interval = 5          # segundos

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

        # Sprint S3.X: restaurar strategy overrides desde Firestore al boot.
        # Si el doc no existe (primera vez), no-op.
        self._load_strategy_overrides_from_firestore()

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
            self._theta_monitor_loop(),
            self._vix_velocity_loop(),          # Paso 7
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

        # GUARD hardcodeado — defense-in-depth contra trading_hours_enabled=False
        # Bug descubierto 5-may-2026: si self._schedule.enabled=False,
        # is_within_trading_window() retorna True incondicional. Este guard
        # usa .start y .end pero IGNORA deliberadamente .enabled.
        _sch_guard = self._schedule or DEFAULTS_EQUITY
        _now_g = now_et()
        _t_g = _now_g.time().replace(second=0, microsecond=0)
        if _now_g.weekday() >= 5 or not (_sch_guard.start <= _t_g < _sch_guard.end):
            logger.debug(f"[CROP] GUARD: fuera de ventana {_sch_guard.start}-{_sch_guard.end} — skip")
            return

        await self._run_analysis_cycle(ticker, chain)

    # ── Ciclo de análisis ──────────────────────────────────

    async def _run_analysis_cycle(self, ticker: str, chain: dict):
        """
        Ciclo de análisis y entrada para un ticker.
        Setup (señales + IV surface) + gates (max positions + daily loss cap),
        luego delega al scan de Theta Harvest.
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
        self._last_vix  = vix
        self._last_vvix = vvix
        # Tech debt #18: auto-record para velocity_30m
        if vix is not None and vix > 0:
            self._record_vix_history(float(vix))
        return vix, vvix

    def _record_vix_history(self, vix: float) -> None:
        """Append (ts, vix) al buffer + cleanup >35min para velocity_30m (tech debt #18)."""
        now = time.time()
        self._vix_price_history.append((now, vix))
        # Cleanup samples >35min (deja margen para velocity_30m = 1800s)
        cutoff = now - 35 * 60
        self._vix_price_history = [
            (ts, v) for ts, v in self._vix_price_history if ts >= cutoff
        ]

    def _compute_vix_velocity_30m(self) -> float:
        """
        Calcula VIX velocity en ventana 30min vs ahora (tech debt #18).
        Returns 0.0 si no hay suficiente data (buffer recien empezado).
        """
        if len(self._vix_price_history) < 2:
            return 0.0
        now = time.time()
        target_ts = now - 30 * 60  # 30 min ago
        # Buscar el sample mas viejo dentro de la ventana de 30min
        old_samples = [(ts, v) for ts, v in self._vix_price_history if ts >= target_ts]
        if len(old_samples) < 2:
            return 0.0
        _, vix_old = old_samples[0]  # el primero >= 30min ago = mas viejo de la ventana
        _, vix_now = self._vix_price_history[-1]  # ultimo del buffer
        if vix_old == 0:
            return 0.0
        return (vix_now - vix_old) / vix_old * 100

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
        # Paso 6 backlog v2: usar DTEs dinámicos de Paso 5
        for dte in self._compute_theta_dtes():
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
        # GUARD hardcodeado — defense-in-depth contra trading_hours_enabled=False
        # Bug descubierto 5-may-2026: si self._schedule.enabled=False,
        # is_within_trading_window() retorna True incondicional. Este guard
        # usa .start y .end pero IGNORA deliberadamente .enabled.
        _sch_guard = self._schedule or DEFAULTS_EQUITY
        _now_g = now_et()
        _t_g = _now_g.time().replace(second=0, microsecond=0)
        if _now_g.weekday() >= 5 or not (_sch_guard.start <= _t_g < _sch_guard.end):
            logger.debug(f"[CROP][theta] GUARD: fuera de ventana — skip")
            return

        # Mantener también el check original (defensa en doble capa)
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
            top_movers = getattr(pivot_result.sector, "top_movers", []) or []
            movers_str = ", ".join(
                f"{m.get('symbol', '?')}={m.get('pct', 0):+.2f}%"
                for m in top_movers[:3]
            ) if top_movers else "—"
            logger.info(
                f"[ThetaHarvest] {ticker} dirección via sector analysis: "
                f"{spread_type} ({pivot_result.sector.direction} "
                f"{pivot_result.sector.weighted_change_pct:+.2f}%) "
                f"top: {movers_str}"
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
            tick_ctx = fetch_tick_ad(get_access_token())
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

        # ── 4c. Log estructurado de decisión + persistencia Firestore ────
        decision_id  = f"{time.time():.3f}"
        _decision_log = {
            # Identidad
            "decision_id":          decision_id,
            "ticker":               ticker,
            "timestamp_et":         now_et().isoformat(),
            "dte":                  None,
            "weekday":              now_et().strftime("%A"),
            "day_max_positions":    getattr(self, "_max_positions", None),
            # Macro
            "vix_level":            vix,
            "vvix_level":           vvix,
            "spx_level":            None,
            "ticker_price":         (chain.get("underlying") if isinstance(chain, dict) else None),
            "ticker_iv_rank":       None,
            # Pivot Sector
            "consensus_risk":       getattr(pivot_result, "consensus_risk", None) if pivot_result else None,
            "atr_gate_hit":         getattr(pivot_result, "atr_gate_hit", None)   if pivot_result else None,
            "delta_min":            getattr(pivot_result, "delta_min", None)       if pivot_result else None,
            "delta_max":            getattr(pivot_result, "delta_max", None)       if pivot_result else None,
            "sector_spread_type":   spread_type,
            "sector_direction":     pivot_result.sector.direction           if pivot_result and getattr(pivot_result, "sector", None) else None,
            "sector_weighted_pct":  pivot_result.sector.weighted_change_pct if pivot_result and getattr(pivot_result, "sector", None) else None,
            "sector_top_movers":    getattr(pivot_result.sector, "top_movers", [])[:5] if pivot_result and getattr(pivot_result, "sector", None) else [],
            "pivot_pp":             None,
            "pivot_source":         None,
            # TICK/AD
            "tick_value":           getattr(tick_ctx, "tick",         None) if tick_ctx else None,
            "ad_value":             getattr(tick_ctx, "ad",           None) if tick_ctx else None,
            "tick_extreme":         getattr(tick_ctx, "tick_extreme", None) if tick_ctx else None,
            "tick_decision":        getattr(tick_ctx, "confirmation", None) if tick_ctx else None,
            "trin_value":           None,
            # Greeks (no disponibles en evaluación, solo al abrir trade)
            "delta":                None,
            "gamma":                None,
            "theta":                None,
            "vega":                 None,
            # Position state
            "puts_open":            sum(1 for p in self._theta_positions if "put"  in (p.get("spread_type") or "")),
            "calls_open":           sum(1 for p in self._theta_positions if "call" in (p.get("spread_type") or "")),
            "count":                len(self._theta_positions),
            # Decisión final
            "final_decision":       "EVALUATED",
            "decision_layer":       None,
            "rejection_reason":     None,
            "slot_used":            None,
            "already_open_side":    None,
            "decision_source":      "sector_analysis" if pivot_result and getattr(pivot_result, "sector", None) else "internal_signals",
            # Vinculación con trades
            "executed_trades":      [],
        }
        try:
            logger.info(f"[ThetaHarvest][DECISION] {json.dumps(_decision_log, default=str)}")
        except Exception as _log_exc:
            logger.warning(f"[ThetaHarvest][DECISION] log emit failed: {_log_exc}")
        self._log_theta_decision(decision_id, _decision_log)

        # 4.C.1: hint vars para pasar al scan (default None/0 si LLM off o spread mismatch)
        llm_short_strike_hint: Optional[float] = None
        llm_target_delta_hint: Optional[float] = None
        llm_confidence_hint: int = 0

        # ───── LLM Engine wiring (feature flag) ───────────────────────────
        if self._llm_engine_enabled:
            # Tech debt #24: short-circuit non-SPY ANTES de build_market_snapshot.
            # `should_call_llm` Rule 0 (llm_gate/integration.py:50) ya rechaza
            # non-SPY, pero hoy el snapshot se computa primero (RSI/ATR/EMA/MACD/
            # VWAP en 2 timeframes) y se descarta. Save ~5-10ms/ticker × 3 = ~15-30ms/ciclo.
            if ticker != "SPY":
                logger.debug(f"[llm] {ticker} skip: LLM scope = SPY only (rule-based path)")
            # Skip si datos macro o pivot deficientes (snapshot seria engañoso)
            elif pivot_result is None or vix is None:
                logger.debug(
                    f"[llm] {ticker} skip: pivot_result={pivot_result is not None} "
                    f"vix={vix is not None}"
                )
            else:
                # Lazy init
                if self._llm_client is None:
                    self._llm_client = LLMGateClient(
                        service_url=self._llm_engine_url,
                        haiku_confidence_threshold=self._llm_engine_threshold,
                    )
                    self._llm_cache = DecisionCache(ttl_seconds=self._llm_cache_ttl_seconds)
                    logger.info(f"[llm] client + cache initialized (url={self._llm_engine_url})")

                try:
                    positions_summary = self._format_open_positions_summary(ticker)
                    snapshot = build_market_snapshot_from_crop(
                        ticker=ticker,
                        chain=chain,
                        vix_level=vix,
                        pivot_result=pivot_result,
                        candle_buffer=self._candle_buffer,
                        vix_velocity_30m_pct=self._compute_vix_velocity_30m(),
                        # Sprint 7 (#20): si el poller tiene vix_yesterday_close,
                        # snapshot computa el 1d desde ahí; sino, fallback al
                        # default 0.0 vía el arg vix_velocity_1d_pct (no pasado).
                        allowed_dtes=self._compute_theta_dtes(),
                        open_positions_summary=positions_summary,
                        # Sprint 6: daily indicators reales si el poller tiene buffer
                        # (REST polling, no WS). Fallback silencioso a defaults si
                        # `self.stream` es SchwabStream legacy sin get_daily_buffer.
                        daily_buffer=getattr(self.stream, "get_daily_buffer", lambda: None)(),
                        vix_yesterday_close=getattr(
                            self.stream, "get_vix_yesterday_close", lambda: None
                        )(),
                    )

                    should_call, reason = should_call_llm(
                        snapshot,
                        tickers_enabled=self._llm_tickers_enabled,
                        max_positions=self._llm_max_positions,
                        current_positions_count=len(self._theta_positions),
                    )
                    if not should_call:
                        logger.info(f"[llm] {ticker} pre-filter skip: {reason}")
                        return

                    # Sprint 8.B: chain_ts = ahora (handler de chain_update gatilla
                    # este flow apenas llega chain nueva, así que time.time() ≈
                    # arrival time del chain actual). Una llamada al LLM más tarde
                    # con el mismo chain comparte chain_ts ≈ idéntico; cuando llega
                    # un chain refrescado (>30s), invalidación dispara.
                    chain_ts = time.time()
                    cached = self._llm_cache.get(snapshot, current_chain_ts=chain_ts)
                    if cached is not None:
                        decision = cached
                        logger.info(f"[llm] {ticker} cache HIT verdict={decision.get('verdict')}")
                    else:
                        decision = await asyncio.to_thread(self._llm_client.consult, snapshot)
                        self._llm_cache.put(snapshot, decision, chain_ts=chain_ts)

                    verdict = decision.get("verdict", "WAIT")
                    confidence = decision.get("confidence", 0)
                    main_reason = decision.get("main_reason", "")[:200]
                    layered_path = decision.get("layered_path", "?")

                    logger.info(
                        f"[llm] {ticker} verdict={verdict} conf={confidence} "
                        f"path={layered_path} reason={main_reason}"
                    )

                    if verdict == "WAIT":
                        return

                    if decision_indicates_exit(decision):
                        closed = await self._close_theta_positions_for_ticker(
                            ticker, reason=main_reason
                        )
                        logger.info(f"[llm] {ticker} CLOSE_POSITIONS executed: {closed} closed")
                        return

                    llm_params = llm_decision_to_scan_params(decision, ticker)
                    if llm_params is None:
                        return

                    llm_spread_type = llm_params["spread_type"]
                    if llm_spread_type != spread_type:
                        if confidence >= self._llm_spread_override_threshold:
                            logger.warning(
                                f"[llm] {ticker} LLM verdict={verdict} "
                                f"(conf={confidence}>={self._llm_spread_override_threshold}) "
                                f"OVERRIDES sector {spread_type} -> {llm_spread_type}"
                            )
                            spread_type = llm_spread_type
                        else:
                            logger.info(
                                f"[llm] {ticker} LLM verdict={verdict} "
                                f"(conf={confidence}<{self._llm_spread_override_threshold}) "
                                f"REJECTED override, keeping sector spread_type={spread_type}"
                            )
                            # NO override; hint del LLM era para otro spread → no usar
                    # Populate hint vars solo si spread_type final matchea con el LLM
                    if spread_type == llm_spread_type:
                        llm_short_strike_hint = llm_params.get("llm_short_strike")
                        llm_target_delta_hint = llm_params.get("llm_target_delta")
                        llm_confidence_hint = confidence
                except Exception as e:
                    logger.exception(f"[llm] {ticker} wiring exception: {e}")
                    # Continuar con flow normal (rule-based)
        # ───── END LLM Engine wiring ──────────────────────────────────────

        # ── 5. Intentar abrir cada DTE libre ──────────────
        slots = self._theta_slots.get(ticker, {})
        # Slots dinámicos por weekday — Paso 5 backlog v2
        active_dtes = self._compute_theta_dtes()
        for dte in active_dtes:
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

            # ── Cap GLOBAL Theta Harvest — Paso 5 backlog v2 ──
            theta_cap = self._compute_theta_max_positions()
            if len(self._theta_positions) >= theta_cap:
                logger.info(
                    f"[ThetaHarvest] {ticker} DTE={dte} — cap global alcanzado "
                    f"({len(self._theta_positions)}/{theta_cap}), skip"
                )
                continue

            # ── Guard Iron Condor — Paso 5 backlog v2 ──
            # No abrir CALL si hay PUT abierto en mismo ticker (y viceversa).
            # Bypass via Firestore flag iron_condor_enabled (default False).
            if not self._iron_condor_enabled:
                opposite_side = "call" if "put" in (spread_type or "") else "put"
                opposite_open = any(
                    p.get("ticker") == ticker
                    and opposite_side in (p.get("spread_type") or "")
                    for p in self._theta_positions
                )
                if opposite_open:
                    logger.info(
                        f"[ThetaHarvest] {ticker} DTE={dte} — guard Iron Condor: "
                        f"hay {opposite_side} spread abierto en {ticker}, skip {spread_type}"
                    )
                    continue

            # ── Tranche entry: 3 contratos, mismo strike, distintos targets ──
            try:
                signals = scan_theta_harvest_tranches(
                    ticker                = ticker,
                    chain                 = chain,
                    vix                   = vix,
                    vvix                  = vvix,
                    spread_type           = spread_type,
                    dte_preference        = dte,
                    pivot_result          = pivot_result,
                    force_entry           = getattr(self, "_theta_force_entry", False),
                    entry_hour_et         = self._entry_hour_et,
                    entry_window_minutes  = self._entry_window_minutes,
                    vix_max_entry         = self._vix_entry_threshold,
                    # Sprint S3.1-A: pasar instance vars (overrides via _apply_strategy_overrides_to_instance_vars)
                    stop_loss_mult        = self._stop_loss_mult,
                    tranche_profit_targets = self._tranche_profit_targets,
                    # Sprint S3.1-B: thresholds editables propagados al scan
                    vvix_panic_threshold  = self._vvix_panic_threshold,
                    min_minutes_to_exp    = self._min_minutes_to_exp,
                    # Sprint S3.2: delta_by_risk override (dict de LISTAS mutables)
                    delta_by_risk         = self._delta_by_risk,
                    # Sprint S3.3: per-ticker config override
                    ticker_cfg            = self._ticker_config.get(ticker),
                    # Sprint S3.4: VIX_CREDIT_TABLE override (list-of-lists)
                    vix_credit_table      = self._vix_credit_table,
                    # 4.C.1: LLM hints (None si flag OFF o spread mismatch)
                    llm_short_strike      = llm_short_strike_hint,
                    llm_target_delta      = llm_target_delta_hint,
                    llm_confidence        = llm_confidence_hint,
                )
            except Exception as e:
                logger.warning(f"[ThetaHarvest] scan error {ticker} DTE={dte}: {e}")
                continue

            if not signals:
                continue

            # Sprint S3.5: contracts per tranche segun granular sizing (override
            # por weekday × ticker × dte). Default 1 = comportamiento original.
            # qty=0 saltea el signal (skip ticker/dte/weekday combo).
            opened_any = False
            for signal in signals:
                _qty = self._compute_size(signal.ticker, signal.dte)
                if _qty <= 0:
                    logger.info(
                        f"[ThetaHarvest] {signal.ticker} DTE={signal.dte} "
                        f"T{signal.tranche_id} — skip (granular sizing qty=0)"
                    )
                    continue
                decision = signal.to_decision(contracts=_qty)
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
                        # ── Persistencia Firestore — Paso 3 ──────────────
                        trade_id = f"{decision_id}_T{signal.tranche_id}"
                        pos["trade_id"]    = trade_id
                        pos["decision_id"] = decision_id
                        self._log_theta_trade_open(trade_id, decision_id, {
                            "ticker":           ticker,
                            "dte":              dte,
                            "tranche":          signal.tranche_id,
                            "tranche_target":   signal.tranche_target,
                            "side":             signal.spread_type,
                            "short_strike":     signal.short_strike,
                            "long_strike":      signal.long_strike,
                            "strikes":          f"{signal.short_strike}/{signal.long_strike}",
                            "spread_width":     abs(signal.long_strike - signal.short_strike),
                            "entry_credit":     signal.net_credit,
                            "contracts":        pos.get("contracts", 1),
                            "entry_time_et":    now_et().isoformat(),
                            "vix_at_entry":     vix,
                            "vvix_at_entry":    vvix,
                            "spx_at_entry":     None,
                            "slippage_bps":     None,
                            "delta":            pos.get("delta") or signal.short_delta,
                            "gamma":            pos.get("gamma"),
                            "theta":            pos.get("theta"),
                            "vega":             pos.get("vega"),
                        })
                        # Sprint 9: registro estructurado a collection nueva
                        # `eolo-crop-trades`. Aditivo — coexiste con el legacy
                        # `eolo-crop-theta-trades`.
                        try:
                            sprint9_id = self._record_trade_open_sprint9(
                                ticker=ticker,
                                decision=decision,
                                signal=signal,
                                dte=dte,
                                pos=pos,
                                vix=vix,
                                vvix=vvix,
                                decision_id=decision_id,
                                llm_confidence_hint=llm_confidence_hint,
                            )
                            if sprint9_id:
                                pos["sprint9_trade_id"] = sprint9_id
                        except Exception as _s9e:
                            logger.warning(f"[Sprint9] record_trade_open failed: {_s9e}")
                        _decision_log.setdefault("executed_trades", []).append(trade_id)
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
                    # Default: cur_val no válido (marks faltaron o try falló).
                    pos["_cur_val_valid"] = False
                    try:
                        opt_side  = "puts" if "put" in pos.get("spread_type","") else "calls"
                        exp       = pos.get("expiration", "")
                        strikes   = chain.get(opt_side, {}).get(exp, {})
                        short_c   = strikes.get(str(pos.get("short_strike", ""))) or {}
                        long_c    = strikes.get(str(pos.get("long_strike",  ""))) or {}
                        # FIX: distinguir "mark real" de "default 0" para no enmascarar
                        # marks faltantes como cur_val=0 (rompe pricing en STOP_LOSS path).
                        _s_raw = short_c.get("mark")
                        if _s_raw is None:
                            _s_raw = short_c.get("ask")
                        _l_raw = long_c.get("mark")
                        if _l_raw is None:
                            _l_raw = long_c.get("bid")
                        pos["_cur_val_valid"] = (_s_raw is not None and _l_raw is not None)
                        s_mark  = _s_raw or 0
                        l_mark  = _l_raw or 0
                        cur_val = round(abs(s_mark - l_mark), 2)
                        net_credit = pos.get("net_credit", 0)
                        contracts  = pos.get("contracts", 1)
                        pos["unrealized_pnl"] = round(
                            (net_credit - cur_val) * contracts * 100, 2
                        )
                        pos["current_value"] = cur_val
                        # Persistir greeks del short leg para el dashboard
                        pos["delta"] = short_c.get("delta") or 0
                        pos["theta"] = short_c.get("theta") or 0
                        pos["gamma"] = short_c.get("gamma") or 0
                        pos["vega"]  = short_c.get("vega")  or 0
                    except Exception:
                        pass

                    exit_reason = evaluate_open_position(
                        position      = pos,
                        current_chain = chain,
                        vix_current   = vix,
                        vvix_current  = vvix,
                        spy_drop_30m  = spy_drop_30m,
                        auto_close_et = (  # Paso 6
                            self._schedule.auto_close.hour
                            + self._schedule.auto_close.minute / 60.0
                        ),
                        # Sprint S3.1-B: thresholds editables via UI
                        vix_spike_delta      = self._vix_spike_delta,
                        vvix_panic_threshold = self._vvix_panic_threshold,
                        delta_drift_max      = self._delta_drift_max,
                        spy_drop_pct_30m     = self._spy_drop_pct_30m,
                        min_minutes_to_exp   = self._min_minutes_to_exp,
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
                        "strategy":     "theta_harvest",
                        # FIX: usar net_debit de eval-time si los marks eran reales;
                        # None → close_spread re-resuelve (graceful, idéntico a pre-fix).
                        "close_debit":  (pos.get("current_value") if pos.get("_cur_val_valid") else None),
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
                                "strategy": "theta_harvest", "exit_reason": exit_reason,
                                "reason": pos.get("reason", ""), "dte_slot": pos.get("dte_slot"), "tranche_id": pos.get("tranche_id"),
                            })
                        except Exception as _e:
                            logger.warning(f"[ThetaHarvest] closed_positions snapshot failed: {_e}")

                        # ── Persistencia Firestore — Paso 3 ──────────────
                        _trade_id_close = pos.get("trade_id")
                        if _trade_id_close:
                            try:
                                _exit_payload = {
                                    "exit_time_et":       datetime.utcnow().isoformat(),
                                    "trade_duration_min": round(
                                        (time.time() - (pos.get("opened_at") or time.time())) / 60.0, 1
                                    ),
                                    "exit_reason":        exit_reason,
                                    "exit_credit":        _cv,
                                    "pnl":                round(pos.get("unrealized_pnl") or 0, 2),
                                    "pnl_pct":            round((_nc - _cv) / _nc * 100, 1) if _nc else 0,
                                    "vix_at_exit":        vix,
                                    "vvix_at_exit":       vvix,
                                    "spx_at_exit":        None,
                                }
                                self._log_theta_trade_close(_trade_id_close, _exit_payload)
                            except Exception as _fs_e:
                                logger.warning(f"[ThetaHarvest][FS] trade_close prepare failed: {_fs_e}")
                            # Sprint 9: registrar outcome en collection nueva
                            try:
                                _s9_id = pos.get("sprint9_trade_id")
                                if _s9_id and self._trade_logger is not None:
                                    _outcome = {
                                        "pnl_pct":            _exit_payload.get("pnl_pct"),
                                        "pnl_usd":            _exit_payload.get("pnl"),
                                        "exit_reason":        exit_reason,
                                        "hold_time_sec":      int(
                                            time.time() - (pos.get("opened_at") or time.time())
                                        ),
                                        "exit_credit_paid_usd": _exit_payload.get("exit_credit"),
                                        "vix_at_exit":        vix,
                                        "safety_overrides":   [],
                                    }
                                    self._trade_logger.record_trade_close(_s9_id, _outcome)
                            except Exception as _s9ce:
                                logger.warning(f"[Sprint9] record_trade_close failed: {_s9ce}")

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
        strategy = pos.get("strategy", "")
        reason   = pos.get("reason", "")
        if opt_type == "call":
            await self.trader.close_long_call(
                pos["ticker"], pos["expiration"],
                pos["strike"], pos["contracts"],
                strategy=strategy, reason=reason,
            )
        else:
            await self.trader.close_long_put(
                pos["ticker"], pos["expiration"],
                pos["strike"], pos["contracts"],
                strategy=strategy, reason=reason,
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
                    logger.info(f"[CROP] 🕒 AUTO-CLOSE {self._schedule.auto_close.strftime('%H:%M')} ET — cerrando todas las posiciones")
                    try:
                        closed = await self.trader.close_all_positions()
                        logger.info(
                            f"[CROP] Auto-close completado: {len(closed)} órdenes de cierre"
                        )
                        # Paso 6 backlog v2: Daily Theta Harvest — limpiar estado interno
                        # post auto-close para evitar carry-over de posiciones del día anterior.
                        positions_count = len(self._theta_positions)
                        self._theta_positions.clear()
                        self._theta_slots = {}
                        self._save_theta_positions_to_firestore()
                        if positions_count > 0:
                            logger.info(
                                f"[CROP] 🧹 Daily Theta cleanup: {positions_count} posiciones "
                                f"limpias en memoria, slots resetados, Firestore actualizado"
                            )
                        # Paso 7 backlog v2: reset VIX velocity cooldown para mañana
                        self._vix_velocity_up_done = False
                        self._vix_velocity_down_done = False
                        self._vix_velocity_buffer.clear()
                        # ── Daily summary Firestore — Paso 3 ─────────────
                        try:
                            self._write_daily_summary()
                        except Exception as _ds_e:
                            logger.warning(f"[CROP] daily_summary failed: {_ds_e}")
                    except Exception as e:
                        logger.error(f"[CROP] Error en auto-close: {e}")
                    self._auto_close_done_date = today

            await asyncio.sleep(30)

    def _should_auto_close(self) -> bool:
        """
        GUARD: ignora sch.enabled deliberadamente (defense-in-depth).
        Auto-close DEBE disparar independientemente del flag enabled,
        porque es safety net de salida, no restricción de entrada.

        Bug descubierto 5-may-2026 en V1, mismo bug en CROP:
        si trading_hours_enabled=False, las posiciones quedaban abiertas
        overnight con riesgo de gap.
        Theta Harvest tiene safety net propio (TIME_STOP en _theta_monitor_loop).
        """
        now = now_et()

        # Skip weekends — no auto-close fines de semana
        if now.weekday() >= 5:
            return False

        sch = self._schedule or DEFAULTS_EQUITY
        t = now.time().replace(second=0, microsecond=0)

        # GUARD hardcodeado — ignora sch.enabled deliberadamente
        if t < sch.auto_close:
            return False

        # Ceiling: no cerrar después de 16:00 ET (mercado ya cerrado)
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
        # Sprint S3.X: refrescar strategy overrides desde Firestore antes que
        # el guard de settings, para que un POST nuevo se reflecte aún si el
        # doc settings no cambió. Idempotente (guard interno por updated_ts).
        try:
            self._load_strategy_overrides_from_firestore()
        except Exception as _e:
            logger.debug(f"[StrategyOverrides] poll-load error: {_e}")

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
                        # Propagar al trader si tiene el setter
                        for obj in (self.trader,):
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

            # ── Entry window (theta harvest) ──────────────────
            if "entry_hour_et" in cfg:
                try:
                    new_val = float(cfg["entry_hour_et"])
                    if new_val != self._entry_hour_et:
                        old = self._entry_hour_et
                        self._entry_hour_et = new_val
                        changed.append(f"entry_hour_et={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "entry_window_minutes" in cfg:
                try:
                    new_val = float(cfg["entry_window_minutes"])
                    if new_val != self._entry_window_minutes:
                        old = self._entry_window_minutes
                        self._entry_window_minutes = new_val
                        changed.append(f"entry_window_minutes={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if "vix_entry_threshold" in cfg:
                try:
                    new_val = float(cfg["vix_entry_threshold"])
                    if new_val != self._vix_entry_threshold:
                        old = self._vix_entry_threshold
                        self._vix_entry_threshold = new_val
                        changed.append(f"vix_entry_threshold={old}→{new_val}")
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

            # ── Iron Condor (theta harvest) ─────────────────
            if "iron_condor_enabled" in cfg:
                try:
                    new_val = bool(cfg["iron_condor_enabled"])
                    if new_val != self._iron_condor_enabled:
                        old = self._iron_condor_enabled
                        self._iron_condor_enabled = new_val
                        changed.append(f"iron_condor_enabled={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            # ── VIX Velocity (Paso 7 backlog v2) ────────────
            if "vix_velocity_enabled" in cfg:
                try:
                    new_val = bool(cfg["vix_velocity_enabled"])
                    if new_val != self._vix_velocity_enabled:
                        old = self._vix_velocity_enabled
                        self._vix_velocity_enabled = new_val
                        changed.append(f"vix_velocity_enabled={old}→{new_val}")
                except (TypeError, ValueError):
                    pass

            if changed:
                logger.info(f"[COMMANDS] ⚙️  Config actualizada: {', '.join(changed)}")

            # Sprint S3.1-A: future-proof Firestore — si en S3.X se persisten
            # overrides en Firestore y _poll_settings los carga a _strategy_overrides,
            # re-aplicar a instance vars para que strategy fn los reciba.
            try:
                self._apply_strategy_overrides_to_instance_vars()
            except Exception as _e:
                logger.debug(f"[COMMANDS] _apply_strategy_overrides_to_instance_vars: {_e}")

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
          2. Firestore eolo-crop-state/current  (dashboard Cloud Run)
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
                    # Sprint 1 (8-may): exposición de flags al frontend
                    "iron_condor_enabled":     self._iron_condor_enabled,
                    "vix_velocity_enabled":    self._vix_velocity_enabled,
                    "vix_entry_threshold":     self._vix_entry_threshold,
                    "entry_hour_et":           self._entry_hour_et,
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

                # Posiciones abiertas
                "positions":    self._open_positions,

                # Paper trades del día (últimos 50)
                "paper_trades": paper_trades[-50:],

                # P&L calculado — usa theta positions/closed (CROP no usa paper_trades)
                "pnl":          self._calc_theta_pnl_for_dashboard(),

                # Stats del bot
                "stats": {
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

    def _load_strategy_overrides_from_firestore(self):
        """Sprint S3.X: restaura _strategy_overrides desde Firestore (boot + poll)
        y los aplica a instance vars. Guard por updated_ts (idempotente).

        Doc: eolo-crop-config/strategy_overrides
            {overrides: {<path>: <value>, ...}, updated_ts: float}
        Si el doc no existe, no-op (comportamiento idéntico al pre-S3.X).
        """
        try:
            from google.cloud import firestore as _fs
            db = _fs.Client()
            doc = db.collection("eolo-crop-config").document("strategy_overrides").get()
            if not doc.exists:
                return
            data = doc.to_dict() or {}
            updated_ts = float(data.get("updated_ts") or 0)
            if updated_ts <= self._last_overrides_ts:
                return
            self._last_overrides_ts = updated_ts
            overrides = data.get("overrides") or {}
            if isinstance(overrides, dict) and overrides:
                self._strategy_overrides.update(overrides)
                self._apply_strategy_overrides_to_instance_vars()
                logger.info(f"[StrategyOverrides] Restaurados {len(overrides)} overrides desde Firestore")
        except Exception as e:
            logger.warning(f"[StrategyOverrides] Error cargando overrides: {e}")

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

    def _calc_theta_pnl_for_dashboard(self) -> dict:
        """
        Construye el dict state["pnl"] con datos de Theta Harvest para el
        dashboard. Reemplaza el output de _calc_pnl() en CROP porque
        paper_trades está vacío (CROP no usa BUY_TO_OPEN/SELL_TO_CLOSE).

        Schema esperado por el JS del dashboard:
        - open_list: posiciones abiertas con greeks, entry_price, current value
        - closed:    trades cerrados del día (en memoria, no persistente)
        - total_pnl, wins, losses, rounds, win_rate, open_count
        """
        # ── open_list desde _theta_positions ──────────────────────────
        open_list = []
        for pos in self._theta_positions:
            spread_type = pos.get("spread_type", "")
            option_type = "put" if "put" in spread_type else "call"
            opened_at_ts = pos.get("opened_at") or 0
            try:
                entry_ts = datetime.utcfromtimestamp(opened_at_ts).isoformat()
            except (ValueError, OSError):
                entry_ts = ""

            open_list.append({
                "ticker":      pos.get("ticker", ""),
                "option_type": option_type,
                "strike":      pos.get("short_strike", ""),
                "expiration":  pos.get("expiration", ""),
                "symbol":      f"{pos.get('ticker','')} {option_type.upper()} "
                               f"{pos.get('short_strike','')} "
                               f"{pos.get('expiration','')}",
                "entry_price": pos.get("net_credit", 0),
                "exit_price":  pos.get("current_value", 0),
                "qty":         pos.get("contracts", 1),
                "strategy":    "theta_harvest",
                "reason":      pos.get("reason", ""),
                "entry_ts":    entry_ts,
                "dte_slot":    pos.get("dte_slot"),
                "tranche_id":  pos.get("tranche_id"),
                "greeks": {
                    "delta":   pos.get("delta") or 0,
                    "theta":   pos.get("theta") or 0,
                    "gamma":   pos.get("gamma") or 0,
                    "vega":    pos.get("vega") or 0,
                },
            })

        # ── closed desde _theta_closed_positions (últimos 30) ─────────
        closed = list(self._theta_closed_positions[-30:])

        # ── stats agregados ───────────────────────────────────────────
        wins   = sum(1 for c in closed if (c.get("pnl") or 0) > 0)
        losses = sum(1 for c in closed if (c.get("pnl") or 0) < 0)
        rounds = wins + losses
        total_pnl = round(sum(float(c.get("pnl") or 0) for c in closed), 2)
        win_rate  = round(wins / rounds * 100, 1) if rounds > 0 else 0

        return {
            "total_pnl":  total_pnl,
            "wins":       wins,
            "losses":     losses,
            "rounds":     rounds,
            "win_rate":   win_rate,
            "open_count": len(open_list),
            "open_list":  open_list,
            "closed":     closed,
        }

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
                    "strategy": t.get("strategy", "claude_bot"),
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

    # ── Paso 7 backlog v2: VIX Velocity Loop ────────────────────────────────

    async def _vix_velocity_loop(self):
        """
        Loop de monitoreo de velocidad del VIX. Corre cada 30s.

        Detecta movimientos bruscos del VIX en la ventana configurada
        (self._vix_velocity_window_seconds, default 120s):
        - Si VIX sube ≥ threshold_up_pct → cierra todos los PUTs abiertos.
        - Si VIX baja ≤ threshold_down_pct → cierra todos los CALLs abiertos.

        Sprint S3.1-C: thresholds + window editables via UI.

        Cooldown: un disparo por día por dirección. Flags se resetean
        en _auto_close_loop (Paso 6).

        Bypass via Firestore flag vix_velocity_enabled (default False).
        """
        logger.info(f"[VIXVelocity] Loop iniciado (cada 30s, ventana {self._vix_velocity_window_seconds}s)")
        while self._running:
            await asyncio.sleep(30)

            if not self._vix_velocity_enabled:
                continue

            if self._vix_velocity_up_done and self._vix_velocity_down_done:
                continue

            if not self._theta_positions:
                continue

            try:
                vix, _ = self._theta_get_macro_context()
            except Exception as e:
                logger.debug(f"[VIXVelocity] Error obteniendo VIX: {e}")
                continue

            if vix is None or vix <= 0:
                continue

            self._vix_velocity_buffer.append(vix)

            if len(self._vix_velocity_buffer) < self._vix_velocity_samples:
                continue

            oldest_vix = self._vix_velocity_buffer[0]
            newest_vix = self._vix_velocity_buffer[-1]
            delta_pct = (newest_vix / oldest_vix) - 1.0 if oldest_vix > 0 else 0.0

            if delta_pct >= self._vix_velocity_threshold_up_pct and not self._vix_velocity_up_done:
                self._vix_velocity_up_done = True
                puts = [
                    p for p in self._theta_positions
                    if "put" in (p.get("spread_type") or "")
                ]
                logger.warning(
                    f"[VIXVelocity] VIX UP {delta_pct*100:+.2f}% en {self._vix_velocity_window_seconds}s "
                    f"(de {oldest_vix:.2f} a {newest_vix:.2f}) — "
                    f"cerrando {len(puts)} PUT(s)"
                )
                for pos in puts:
                    await self._close_theta_position_by_reason(pos, "VIX_VELOCITY_UP")

            elif delta_pct <= self._vix_velocity_threshold_down_pct and not self._vix_velocity_down_done:
                self._vix_velocity_down_done = True
                calls = [
                    p for p in self._theta_positions
                    if "call" in (p.get("spread_type") or "")
                ]
                logger.warning(
                    f"[VIXVelocity] VIX DOWN {delta_pct*100:+.2f}% en {self._vix_velocity_window_seconds}s "
                    f"(de {oldest_vix:.2f} a {newest_vix:.2f}) — "
                    f"cerrando {len(calls)} CALL(s)"
                )
                for pos in calls:
                    await self._close_theta_position_by_reason(pos, "VIX_VELOCITY_DOWN")

    async def _close_theta_position_by_reason(self, pos: dict, exit_reason: str):
        """
        Cierra una posición theta harvest por exit reason específico.
        Usado por VIX_VELOCITY_UP/DOWN. Paso 7 backlog v2.
        """
        ticker = pos.get("ticker", "?")
        dte_slot = pos.get("dte_slot", "?")
        try:
            await self.trader.close_spread(
                ticker=ticker,
                short_strike=pos.get("short_strike"),
                long_strike=pos.get("long_strike"),
                expiration=pos.get("expiration"),
                spread_type=pos.get("spread_type"),
                contracts=pos.get("contracts", 1),
                reason=f"ThetaHarvest {exit_reason}",
            )
            if pos in self._theta_positions:
                self._theta_positions.remove(pos)
            self._save_theta_positions_to_firestore()
            logger.info(
                f"[VIXVelocity] Cerrado {ticker} DTE={dte_slot} motivo={exit_reason}"
            )
        except Exception as e:
            logger.error(
                f"[VIXVelocity] Error cerrando {ticker} DTE={dte_slot}: {e}"
            )

    # ── LLM Engine helpers (4.B.2) ─────────────────────────────────────────

    async def _close_theta_positions_for_ticker(self, ticker: str, reason: str) -> int:
        """Cierra todas las posiciones theta abiertas del ticker. Returns count cerradas."""
        positions = [p for p in self._theta_positions if p.get("ticker") == ticker]
        if not positions:
            return 0
        count = 0
        for pos in positions:
            try:
                await self._close_theta_position_by_reason(pos, exit_reason=f"LLM_CLOSE: {reason}")
                count += 1
            except Exception as e:
                logger.exception(f"[llm_close] error closing {pos.get('id')}: {e}")
        return count

    def _format_open_positions_summary(self, ticker: str) -> Optional[str]:
        """Formato string de positions abiertas del ticker para el LLM snapshot."""
        open_for_ticker = [p for p in self._theta_positions if p.get("ticker") == ticker]
        if not open_for_ticker:
            return None
        return "; ".join(
            f"{p.get('spread_type', '?')} {p.get('short_strike', '?')}/{p.get('long_strike', '?')} "
            f"{p.get('dte', '?')}DTE T{p.get('tranche_id', '?')}"
            for p in open_for_ticker
        )

    # ── Paso 5: Slots dinámicos + Guard Iron Condor ────────────────────────

    def _compute_theta_dtes(self) -> list[int]:
        """
        DTEs válidos según weekday — opciones expiran lun-vie.
        Lun: [0,1,2,3,4]  Mar: [0,1,2,3]  Mié: [0,1,2]  Jue: [0,1]  Vie: [0]
        Sáb/Dom: [] (no se opera, igual hay guard de fin de semana antes).

        Sprint S3.C: lee _strategy_overrides primero (set via POST /api/state/edit).
        Override key: "strategy_params.target_dtes.by_weekday.<DAY>" (Mon..Sun).
        Validation inline: debe ser list de ints en rango 0..4. Si no, fallback.
        """
        from datetime import datetime, timezone
        wd = datetime.now(timezone.utc).weekday()  # 0=Lun, 6=Dom

        # Sprint S3.C: check _strategy_overrides primero
        _weekday_names = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
        _day_name = _weekday_names.get(wd)
        _override_key = f"strategy_params.target_dtes.by_weekday.{_day_name}"
        _overrides = getattr(self, '_strategy_overrides', {}) or {}
        if _override_key in _overrides:
            _override_value = _overrides[_override_key]
            if isinstance(_override_value, list) and all(
                isinstance(d, int) and 0 <= d <= 4 for d in _override_value
            ):
                return _override_value

        # Fallback hardcoded (preservado del comportamiento original)
        mapping = {
            0: [0, 1, 2, 3, 4],   # Lunes
            1: [0, 1, 2, 3],      # Martes
            2: [0, 1, 2],         # Miércoles
            3: [0, 1],            # Jueves
            4: [0],               # Viernes
        }
        return mapping.get(wd, [])

    def _compute_theta_max_positions(self) -> int:
        """
        Cap global de posiciones theta harvest según weekday.
        Lun: 60  Mar: 48  Mié: 36  Jue: 24  Vie: 12
        Cálculo: len(DTEs válidos) × 4 tickers × 3 tranches
        """
        dtes = self._compute_theta_dtes()
        return len(dtes) * 4 * 3

    def _compute_size(self, ticker: str, dte: int) -> int:
        """Sprint S3.5: contracts per tranche con granular sizing.

        Lee bot_instance._strategy_overrides para granular position sizing
        por (weekday, ticker, dte). Fallback a 1 (comportamiento original).

        Override key format:
            "strategy_params.position_sizing.<DAY>.<TICKER>.dte<N>"
        Donde DAY in {Mon..Fri}, TICKER in {SPY,QQQ,IWM,TQQQ}, N in 0..4.

        Args:
            ticker: Symbol (SPY/QQQ/IWM/TQQQ).
            dte:    Days to expiration (0-4).

        Returns:
            int: contracts per tranche (NOT total spread).
                 0 = skip this ticker/dte/weekday combo.
                 Default fallback: 1.
        """
        from datetime import datetime, timezone

        # Weekday UTC (consistente con _compute_theta_dtes)
        weekday_idx = datetime.now(timezone.utc).weekday()
        weekday_names = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}
        day_name = weekday_names.get(weekday_idx)
        if day_name is None:
            return 1  # Weekend: bot no opera de todas formas

        if ticker not in ('SPY', 'QQQ', 'IWM', 'TQQQ'):
            return 1  # Unknown ticker, default

        if isinstance(dte, bool) or not isinstance(dte, int) or not (0 <= dte <= 4):
            return 1  # Invalid dte, default

        override_key = f"strategy_params.position_sizing.{day_name}.{ticker}.dte{dte}"
        overrides = getattr(self, '_strategy_overrides', {}) or {}
        override_value = overrides.get(override_key)

        # bool antes que int (bool is subclass of int — Python gotcha)
        if isinstance(override_value, int) and not isinstance(override_value, bool):
            if 0 <= override_value <= 50:
                return override_value

        return 1

    def _apply_strategy_overrides_to_instance_vars(self) -> None:
        """Sprint S3.1-A: Aplica overrides activos a instance vars que las
        strategy functions reciben via kwargs explícitos.

        Llamado:
          1. Después de POST /api/state/edit (sincronía con cambio)
          2. En cada _poll_settings cycle (future-proof Firestore)

        Override key formats (bracket notation para arrays):
          - "strategy_params.exits_advanced.stop_loss_mult"
          - "strategy_params.exits_advanced.tranche_profit_targets[0]"
          - "strategy_params.exits_advanced.tranche_profit_targets[1]"
          - "strategy_params.exits_advanced.tranche_profit_targets[2]"

        Validation en /api/state/edit ya filtró tipos inválidos; este helper
        es defensivo igual (TypeError/ValueError → fallback al default actual).
        """
        overrides = getattr(self, '_strategy_overrides', {}) or {}
        if not overrides:
            return

        # STOP_LOSS_MULT — escalar float
        key = "strategy_params.exits_advanced.stop_loss_mult"
        if key in overrides:
            try:
                new_val = float(overrides[key])
                self._stop_loss_mult = new_val
            except (TypeError, ValueError):
                pass

        # TRANCHE_PROFIT_TARGETS — lista de 3 (float | None). Sentinel None = EXP.
        # bracket notation: tranche_profit_targets[0], [1], [2]
        for i in range(3):
            key = f"strategy_params.exits_advanced.tranche_profit_targets[{i}]"
            if key in overrides:
                val = overrides[key]
                if val is None:
                    # None permitido (EXP sentinel)
                    if 0 <= i < len(self._tranche_profit_targets):
                        self._tranche_profit_targets[i] = None
                else:
                    try:
                        if 0 <= i < len(self._tranche_profit_targets):
                            self._tranche_profit_targets[i] = float(val)
                    except (TypeError, ValueError):
                        pass

        # Sprint S3.1-B: exits_advanced thresholds (escalares float/int)
        key = "strategy_params.exits_advanced.vix_spike_delta"
        if key in overrides:
            try:
                self._vix_spike_delta = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.vvix_panic_threshold"
        if key in overrides:
            try:
                self._vvix_panic_threshold = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.delta_drift_max"
        if key in overrides:
            try:
                self._delta_drift_max = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.spy_drop_pct_30m"
        if key in overrides:
            try:
                self._spy_drop_pct_30m = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.min_minutes_to_exp"
        if key in overrides:
            try:
                self._min_minutes_to_exp = int(overrides[key])
            except (TypeError, ValueError):
                pass

        # Sprint S3.1-C: VIX velocity (thresholds + window dinámico)
        key = "strategy_params.exits_advanced.vix_velocity_threshold_up_pct"
        if key in overrides:
            try:
                self._vix_velocity_threshold_up_pct = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.vix_velocity_threshold_down_pct"
        if key in overrides:
            try:
                self._vix_velocity_threshold_down_pct = float(overrides[key])
            except (TypeError, ValueError):
                pass

        key = "strategy_params.exits_advanced.vix_velocity_window_seconds"
        if key in overrides:
            try:
                new_secs = int(overrides[key])
                self._vix_velocity_window_seconds = new_secs
                new_samples = max(2, round(new_secs / 30) + 1)
                if new_samples != self._vix_velocity_samples:
                    self._vix_velocity_samples = new_samples
                    # Recrear deque preservando contenido actual con maxlen nuevo
                    self._vix_velocity_buffer = deque(self._vix_velocity_buffer, maxlen=new_samples)
            except (TypeError, ValueError):
                pass

        # Sprint S3.2: delta_by_risk overrides — bracket notation
        # "strategy_params.delta_by_risk.<LEVEL>[<idx>]" (parse sin regex)
        prefix = "strategy_params.delta_by_risk."
        for k, val in overrides.items():
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix):]              # ej "LOW[0]"
            if "[" not in rest or not rest.endswith("]"):
                continue
            level = rest[:rest.index("[")]
            try:
                idx = int(rest[rest.index("[")+1:-1])
            except (TypeError, ValueError):
                continue
            if level in self._delta_by_risk and 0 <= idx < len(self._delta_by_risk[level]):
                try:
                    self._delta_by_risk[level][idx] = float(val)
                except (TypeError, ValueError):
                    pass

        # Sprint S3.3: ticker_config overrides — dot notation
        # "strategy_params.ticker_config.<TICKER>.<field>" (max_dte = int, resto = float)
        prefix = "strategy_params.ticker_config."
        int_fields = {"max_dte"}
        for k, val in overrides.items():
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix):]              # ej "SPY.spread_width"
            if "." not in rest:
                continue
            tk, field = rest.split(".", 1)
            if tk in self._ticker_config and field in self._ticker_config[tk]:
                try:
                    self._ticker_config[tk][field] = int(val) if field in int_fields else float(val)
                except (TypeError, ValueError):
                    pass

        # Sprint S3.4: vix_credit_table overrides — bracket-row + dot-col
        # "strategy_params.vix_credit_table[<row>].<col>"  (col → índice tupla)
        # vix_ceil (idx 0) NO es editable; UI/allowlist lo excluye.
        prefix = "strategy_params.vix_credit_table["
        col_idx = {"spy": 1, "qqq": 2, "iwm": 3, "tqqq": 4, "payoff_mult": 5}
        for k, val in overrides.items():
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix):]              # ej "3].spy"
            if "]." not in rest:
                continue
            row_str, col = rest.split("].", 1)
            try:
                row = int(row_str)
            except (TypeError, ValueError):
                continue
            if col in col_idx and 0 <= row < len(self._vix_credit_table):
                try:
                    self._vix_credit_table[row][col_idx[col]] = float(val)
                except (TypeError, ValueError):
                    pass

        # LLM Engine overrides
        llm_enabled = overrides.get("strategy_params.llm_engine.enabled")
        if llm_enabled is not None:
            self._llm_engine_enabled = bool(llm_enabled)
        llm_url = overrides.get("strategy_params.llm_engine.url")
        if llm_url is not None:
            self._llm_engine_url = str(llm_url)
        llm_threshold = overrides.get("strategy_params.llm_engine.haiku_threshold")
        if llm_threshold is not None:
            try:
                self._llm_engine_threshold = int(llm_threshold)
            except (TypeError, ValueError):
                pass
        llm_ttl = overrides.get("strategy_params.llm_engine.cache_ttl_seconds")
        if llm_ttl is not None:
            try:
                self._llm_cache_ttl_seconds = float(llm_ttl)
            except (TypeError, ValueError):
                pass
        llm_tickers = overrides.get("strategy_params.llm_engine.tickers_enabled")
        if llm_tickers is not None and isinstance(llm_tickers, dict):
            self._llm_tickers_enabled = dict(llm_tickers)
        # 4.C.1: spread override threshold
        llm_spread_override = overrides.get("strategy_params.llm_engine.spread_override_threshold")
        if llm_spread_override is not None:
            try:
                self._llm_spread_override_threshold = int(llm_spread_override)
            except (TypeError, ValueError):
                pass

    # ── Paso 3: Firestore helpers ──────────────────────────────────────────

    def _log_theta_decision(self, decision_id: str, payload: dict) -> None:
        """Persiste decisión de theta harvest en Firestore. No bloquea flujo."""
        try:
            from google.cloud import firestore as _fs
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            db = _fs.Client()
            db.collection("eolo-crop-theta-decisions") \
              .document(today) \
              .collection("decisions") \
              .document(decision_id) \
              .set({**payload, "recorded_ts": time.time()})
        except Exception as _e:
            logger.warning(f"[ThetaHarvest][FS] decision write failed: {_e}")

    def _log_theta_trade_open(self, trade_id: str, decision_id: str, payload: dict) -> None:
        """Persiste apertura de trade en Firestore con status=OPEN."""
        try:
            from google.cloud import firestore as _fs
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            db = _fs.Client()
            db.collection("eolo-crop-theta-trades") \
              .document(today) \
              .collection("trades") \
              .document(trade_id) \
              .set({
                  **payload,
                  "trade_id":    trade_id,
                  "decision_id": decision_id,
                  "status":      "OPEN",
                  "recorded_ts": time.time(),
              })
        except Exception as _e:
            logger.warning(f"[ThetaHarvest][FS] trade_open write failed: {_e}")

    def _record_trade_open_sprint9(
        self,
        ticker: str,
        decision: dict,
        signal,
        dte: int,
        pos: dict,
        vix,
        vvix,
        decision_id: Optional[str] = None,
        llm_confidence_hint: int = 0,
    ) -> Optional[str]:
        """Sprint 9: lazy-init TradeLogger y registrar OPEN.

        Decision_source heurística: si `llm_confidence_hint > 0` significa
        que el LLM aportó hints al spread → LLM_OVERRIDE/LLM. Si no,
        RULE_BASED. Granularidad HAIKU vs SONNET queda para Sprint 9.1
        cuando el LLM Engine devuelva esa info.
        """
        if self._trade_logger is None:
            try:
                self._trade_logger = TradeLogger(
                    project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent"),
                    bot_revision=os.environ.get("K_REVISION", "unknown"),
                    main_sha=os.environ.get("MAIN_SHA", "unknown"),
                    kb_version="v1.2",
                )
            except Exception as _ie:
                logger.warning(f"[Sprint9] TradeLogger lazy init failed: {_ie}")
                return None
        if llm_confidence_hint and llm_confidence_hint > 0:
            decision_source = "LLM"
        else:
            decision_source = "RULE_BASED"
        decision_meta = {
            "verdict":              decision.get("verdict"),
            "confidence":           decision.get("confidence")
                                    or (llm_confidence_hint if llm_confidence_hint else None),
            "main_reason":          (decision.get("reason") or decision.get("main_reason") or "")[:300],
            "layered_path":         decision.get("layered_path"),
            "tacit_rules_applied": None,    # LLM Engine v0.3 follow-up
            "similar_case_used":   None,    # LLM Engine v0.3 follow-up
            "safety_overrides":    [],
        }
        setup = {
            "dte":                       dte,
            "spread_type":               signal.spread_type,
            "short_strike":              signal.short_strike,
            "long_strike":               signal.long_strike,
            "spread_width":              abs(signal.long_strike - signal.short_strike),
            "short_delta":               signal.short_delta,
            "credit_received_usd":       signal.net_credit,
            "contracts":                 pos.get("contracts", 1),
            "vix_at_open":               vix,
            "vvix_at_open":              vvix,
            "delta":                     pos.get("delta"),
            "gamma":                     pos.get("gamma"),
            "theta":                     pos.get("theta"),
            "vega":                      pos.get("vega"),
            "tranche_id":                signal.tranche_id,
            "tranche_target":            signal.tranche_target,
        }
        return self._trade_logger.record_trade_open(
            ticker=ticker,
            decision_source=decision_source,
            decision_meta=decision_meta,
            setup=setup,
            decision_id=decision_id,
        )

    def _log_theta_trade_close(self, trade_id: str, exit_payload: dict) -> None:
        """Actualiza doc existente del trade con status=CLOSED + campos exit."""
        if not trade_id:
            return
        try:
            from google.cloud import firestore as _fs
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            db = _fs.Client()
            ref = db.collection("eolo-crop-theta-trades") \
                    .document(today) \
                    .collection("trades") \
                    .document(trade_id)
            ref.set({
                **exit_payload,
                "status":              "CLOSED",
                "closed_recorded_ts":  time.time(),
            }, merge=True)
        except Exception as _e:
            logger.warning(f"[ThetaHarvest][FS] trade_close write failed: {_e}")

    def _write_daily_summary(self) -> None:
        """Escribe summary del día en auto-close 15:30."""
        try:
            from google.cloud import firestore as _fs
            today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            weekday  = datetime.now(timezone.utc).strftime("%A")
            stats    = self._theta_stats or {}
            closed_today = self._theta_closed_positions or []

            total_trades = len(closed_today)
            winners  = sum(1 for t in closed_today if (t.get("pnl") or 0) > 0)
            losers   = sum(1 for t in closed_today if (t.get("pnl") or 0) < 0)
            total_pnl = sum((t.get("pnl") or 0) for t in closed_today)

            payload = {
                "date":                     today,
                "weekday":                  weekday,
                "day_max_positions":        getattr(self, "_max_positions", None),
                "total_decisions":          None,
                "decisions_executed":       None,
                "decisions_hold":           None,
                "decisions_rejected":       None,
                "total_trades":             total_trades,
                "trades_winners":           winners,
                "trades_losers":            losers,
                "total_pnl":                round(total_pnl, 2),
                "worst_drawdown_intraday":  None,
                "vix_open":                 None,
                "vix_close":                getattr(self, "_last_vix",  None),
                "vvix_open":                None,
                "vvix_close":               getattr(self, "_last_vvix", None),
                "panic_close_triggered":    False,
                "panic_close_count":        0,
                "recorded_ts":              time.time(),
            }

            db = _fs.Client()
            db.collection("eolo-crop-theta-daily-summary") \
              .document(today) \
              .set(payload)
        except Exception as _e:
            logger.warning(f"[ThetaHarvest][FS] daily_summary write failed: {_e}")

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
