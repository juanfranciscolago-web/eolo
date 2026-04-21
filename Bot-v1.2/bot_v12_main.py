# ============================================================
#  EOLO v1.2 — Main Runner (REALTIME via WebSocket)
#
#  Qué cambia vs v1:
#    - Fuente de datos: WebSocket Schwab (CHART_EQUITY + LEVELONE_EQUITIES)
#      en lugar de REST polling. Delay tick a tick < 100ms.
#    - Multi-timeframe simultáneo: 1m, 5m, 15m, 30m, 1h, 4h, 1d
#      resampleados in-memory desde el buffer de velas 1min.
#    - Firestore namespace separado: eolo-config-v12 (no toca v1).
#    - Auto-close 15:27 ET por task async dedicada.
#
#  Qué NO cambia vs v1:
#    - Las 14 estrategias técnicas (bot_*_strategy.py) se usan sin modificar.
#    - bot_trader.py se usa sin modificar (solo cambia el namespace).
#    - helpers.py + auth tokens vía Firestore igual que siempre.
#
#  Deploy:
#    gcloud builds submit --config cloudbuild.yaml --project eolo-schwab-agent .
#
#  Local:
#    export GOOGLE_CLOUD_PROJECT=eolo-schwab-agent
#    python bot_v12_main.py
# ============================================================
import os
import time
import json
import asyncio
import threading
from datetime import datetime, timedelta
from aiohttp import web
import pytz
from loguru import logger
from google.cloud import firestore

from stream import SchwabStream

# ── Multi-TF compartido (paquete común eolo_common) ───────
# El paquete vive en /eolo_common/ en el repo root. El Dockerfile
# lo copia a /app/eolo_common/ durante el build (ver Dockerfile).
import sys, os
_PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
from eolo_common.multi_tf import (
    CandleBuffer, BufferMarketData, ConfluenceFilter, load_multi_tf_config,
)
from eolo_common.multi_tf.normalize import from_schwab_chart_equity

# Estrategias (mismas 14 que v1, sin modificar)
import bot_strategy            as ema_strategy
import bot_gap_strategy        as gap_strategy
import bot_vwap_rsi_strategy   as vwap_strategy
import bot_bollinger_strategy  as bollinger_strategy
import bot_orb_strategy        as orb_strategy
import bot_rsi_sma200_strategy as rsi_sma200_strategy
import bot_supertrend_strategy as supertrend_strategy
import bot_hh_ll_strategy      as hh_ll_strategy
import bot_ha_cloud_strategy   as ha_cloud_strategy
import bot_squeeze_strategy    as squeeze_strategy
import bot_macd_bb_strategy    as macd_bb_strategy
import bot_ema_tsi_strategy    as ema_tsi_strategy
import bot_vela_pivot_strategy as vela_pivot_strategy
import bot_trader              as trader

# Estrategia #14: motor autónomo basado en Claude (Anthropic API)
from claude_bot import ClaudeBotEngine

# ── Constantes ────────────────────────────────────────────
GCP_PROJECT         = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
CONFIG_COLL         = "eolo-config-v12"
CLAUDE_DECISIONS_COLL = "eolo-claude-decisions-v12"   # historial del Claude Bot
EASTERN             = pytz.timezone("America/New_York")

MARKET_OPEN_ET      = (9, 30)
MARKET_CLOSE_ET     = (16, 0)
AUTO_CLOSE_ET       = (15, 27)

TICKERS_EMA_GAP     = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]
TICKERS_LEVERAGED   = ["SOXL", "TSLL", "NVDL", "TQQQ"]
ALL_TICKERS         = TICKERS_EMA_GAP + TICKERS_LEVERAGED

MAX_BUFFER_CANDLES  = 500     # ≈ 1 día completo de candles 1min
STATE_WRITE_EVERY_S = 5       # escribir state cada 5s
COMMAND_POLL_EVERY_S = 5
TOKEN_REFRESH_EVERY_S = 25 * 60   # 25 min
CLAUDE_BOT_EVERY_S  = 60      # Claude decide cada 60s (~390 calls/día en market hours)
CLAUDE_HISTORY_KEEP = 100     # cuántas decisiones guardar en memoria

# ── Strategy groups (por grupo de tickers) ───────────────
CLASSIC_STRATEGIES = {   # corren sobre TICKERS_EMA_GAP
    "ema_crossover":  ema_strategy,
    "gap_fade":       gap_strategy,
    "rsi_sma200":     rsi_sma200_strategy,
    "hh_ll":          hh_ll_strategy,
    "ema_tsi":        ema_tsi_strategy,
}
LEVERAGED_STRATEGIES = {  # corren sobre TICKERS_LEVERAGED
    "vwap_rsi":       vwap_strategy,
    "bollinger":      bollinger_strategy,
    "orb":            orb_strategy,
    "supertrend":     supertrend_strategy,
    "ha_cloud":       ha_cloud_strategy,
    "squeeze":        squeeze_strategy,
    "macd_bb":        macd_bb_strategy,
    "vela_pivot":     vela_pivot_strategy,
}

DEFAULT_SETTINGS = {
    "bot_active":          True,
    "close_all":           False,
    "budget":              100,

    # ── Multi-timeframe (eolo_common) ─────────────────────
    "active_timeframes":     [1, 5, 15, 30, 60, 240],
    "confluence_mode":       False,   # OFF = passthrough (cada TF ejecuta su señal)
    "confluence_min_agree":  2,       # si confluence_mode=True, se necesitan ≥N TFs coincidiendo

    # ── Claude Bot (estrategia #14) ───────────────────────
    "claude_bot_active":     False,            # OFF por default — activar desde dashboard
    "claude_bot_model":      "claude-sonnet-4-6",
    "claude_bot_budget":     50,               # budget por trade del Claude Bot ($)
    "claude_bot_paper_mode": True,             # True = NO ejecuta, solo registra decisiones
    "claude_bot_min_conf":   0.60,             # umbral de confianza para ejecutar en live
    "claude_bot_every_s":    CLAUDE_BOT_EVERY_S,   # período en segundos
}
DEFAULT_STRATEGIES = {
    "ema_crossover":  True,
    "sma200_filter":  True,
    "gap_fade":       True,
    "vwap_rsi":       True,
    "bollinger":      True,
    "orb":            True,
    "rsi_sma200":     True,
    "supertrend":     True,
    "hh_ll":          True,
    "ha_cloud":       True,
    "squeeze":        True,
    "macd_bb":        True,
    "ema_tsi":        True,
    "vela_pivot":     True,
    "claude_bot":     False,   # estrategia #14 — OFF hasta activar desde dashboard
}


# ── Utilidades ────────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=MARKET_OPEN_ET[0],  minute=MARKET_OPEN_ET[1],  second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE_ET[0], minute=MARKET_CLOSE_ET[1], second=0, microsecond=0)
    return open_t <= now < close_t


def is_auto_close_time() -> bool:
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    ac = now.replace(hour=AUTO_CLOSE_ET[0], minute=AUTO_CLOSE_ET[1], second=0, microsecond=0)
    end = now.replace(hour=MARKET_CLOSE_ET[0], minute=MARKET_CLOSE_ET[1], second=0, microsecond=0)
    return ac <= now < end


# ── EoloV12Bot ────────────────────────────────────────────

class EoloV12Bot:
    def __init__(self):
        self._db               = firestore.Client(project=GCP_PROJECT)
        self._tickers          = list(ALL_TICKERS)
        self._stream           = SchwabStream(self._tickers)

        # Buffer compartido multi-TF (eolo_common.CandleBuffer)
        self._candle_buffer    = CandleBuffer(max_len=MAX_BUFFER_CANDLES)
        self._quote_buffers    = {t: {} for t in self._tickers}

        # Estado dinámico (leído de Firestore en cada ciclo)
        self._active              = True
        self._close_all_flag      = False
        self._budget              = 100.0
        self._active_timeframes   = [1, 5, 15, 30, 60, 240]
        self._confluence_mode     = False
        self._confluence_min_agree = 2
        self._active_strategies   = DEFAULT_STRATEGIES.copy()
        self._active_tickers      = {"classic": list(TICKERS_EMA_GAP),
                                     "leveraged": list(TICKERS_LEVERAGED)}

        # Per-TF bookkeeping: última vela cerrada por (ticker, tf)
        self._last_analyzed_key   = {}   # (ticker, tf) → timestamp del último DF analizado

        # Stats y tracking
        self._signals_today       = 0
        self._last_signal_ts      = 0.0
        self._startup_ts          = time.time()
        self._auto_close_done     = None   # date
        self._last_state_write    = 0.0

        # Claude Bot (estrategia #14) — lazy init en _poll_commands_and_settings
        self._claude_engine          = None       # ClaudeBotEngine | None
        self._claude_bot_active      = False
        self._claude_bot_paper_mode  = True
        self._claude_bot_budget      = 50.0
        self._claude_bot_min_conf    = 0.60
        self._claude_bot_model       = "claude-sonnet-4-6"
        self._claude_bot_every_s     = CLAUDE_BOT_EVERY_S
        self._claude_last_call_ts    = 0.0
        self._claude_decisions_today = 0
        self._claude_buys_today      = 0
        self._claude_sells_today     = 0
        self._claude_holds_today     = 0
        self._claude_history         = []         # últimas N decisiones (en memoria)
        self._claude_last_decision   = None
        self._recent_signals_log     = []         # buffer de señales de las 13 estrategias para context

    # ── Firestore helpers ─────────────────────────────────

    def _read_settings(self) -> dict:
        try:
            doc = self._db.collection(CONFIG_COLL).document("settings").get()
            if doc.exists:
                s = doc.to_dict() or {}
                merged = {**DEFAULT_SETTINGS, **s}
                tfs = merged.get("active_timeframes", [1])
                if not isinstance(tfs, list) or not tfs:
                    tfs = [1]
                merged["active_timeframes"] = sorted({int(t) for t in tfs})
                return merged
        except Exception as e:
            logger.warning(f"[SETTINGS] Read failed: {e}")
        return DEFAULT_SETTINGS.copy()

    def _read_strategies(self) -> dict:
        try:
            doc = self._db.collection(CONFIG_COLL).document("strategies").get()
            if doc.exists:
                s = doc.to_dict() or {}
                return {**DEFAULT_STRATEGIES, **s}
        except Exception as e:
            logger.warning(f"[STRATEGIES] Read failed: {e}")
        return DEFAULT_STRATEGIES.copy()

    def _read_tickers(self) -> dict:
        """
        Lee eolo-config-v12/tickers. Formato esperado:
        { "SPY": true, "QQQ": true, "SOXL": false, ... }
        Si falla, todos activos.
        """
        try:
            doc = self._db.collection(CONFIG_COLL).document("tickers").get()
            if doc.exists:
                cfg = doc.to_dict() or {}
                classic   = [t for t in TICKERS_EMA_GAP   if cfg.get(t, True)]
                leveraged = [t for t in TICKERS_LEVERAGED if cfg.get(t, True)]
                return {
                    "classic":   classic   or list(TICKERS_EMA_GAP),
                    "leveraged": leveraged or list(TICKERS_LEVERAGED),
                }
        except Exception as e:
            logger.warning(f"[TICKERS] Read failed: {e}")
        return {"classic": list(TICKERS_EMA_GAP),
                "leveraged": list(TICKERS_LEVERAGED)}

    def _init_defaults_in_firestore(self):
        """Crea docs iniciales en eolo-config-v12 si no existen."""
        try:
            for doc_id, data in [
                ("settings",   DEFAULT_SETTINGS),
                ("strategies", DEFAULT_STRATEGIES),
                ("tickers",    {t: True for t in ALL_TICKERS}),
            ]:
                ref  = self._db.collection(CONFIG_COLL).document(doc_id)
                snap = ref.get()
                if not snap.exists:
                    ref.set(data)
                    logger.info(f"[FIRESTORE] Doc inicial creado: {CONFIG_COLL}/{doc_id}")
                else:
                    # Merge keys que faltan
                    existing = snap.to_dict() or {}
                    missing  = {k: v for k, v in data.items() if k not in existing}
                    if missing:
                        ref.set(missing, merge=True)
                        logger.info(f"[FIRESTORE] Claves faltantes agregadas a {doc_id}: {list(missing.keys())}")
        except Exception as e:
            logger.warning(f"[FIRESTORE] Init defaults falló: {e}")

    def _clear_close_all_flag(self):
        try:
            self._db.collection(CONFIG_COLL).document("settings").set(
                {"close_all": False}, merge=True
            )
        except Exception as e:
            logger.warning(f"[CMD] Clear close_all falló: {e}")

    # ── Buffer management ────────────────────────────────

    def _on_stream_event(self, ticker: str, data: dict):
        """Callback registrado en SchwabStream. Sync — se ejecuta desde el WS loop."""
        if data.get("type") == "candle":
            # Normalizar al shape estándar del CandleBuffer
            norm = from_schwab_chart_equity(data)
            if norm is not None:
                self._candle_buffer.push(norm)
        elif data.get("type") == "quote":
            # Schwab manda partial updates: cada mensaje trae SOLO los
            # campos que cambiaron. Si reemplazamos el dict perdemos el
            # resto. Merge preservando los campos previos.
            prev = self._quote_buffers.get(ticker) or {}
            prev.update(data)
            self._quote_buffers[ticker] = prev

    # ── Análisis realtime multi-TF ───────────────────────

    async def _analysis_watcher_loop(self):
        """
        Cada ~5s chequea si hay nuevas velas 1min cerradas en el buffer.
        Por cada nueva vela, re-evalúa todos los TFs activos.
        Si el TF tiene un frontier nuevo (ej: vela de 5m recién cerrada),
        dispara las estrategias asociadas a ese grupo de tickers.
        """
        await asyncio.sleep(15)   # dar tiempo al stream a llenar el buffer
        while True:
            try:
                if self._active and is_market_open() and not is_auto_close_time():
                    await self._run_analysis_pass()
            except Exception as e:
                logger.error(f"[ANALYSIS] Error: {e}")
            await asyncio.sleep(5)

    async def _run_analysis_pass(self):
        """
        Una pasada completa multi-TF:
          - Para cada TF activo corremos todas las estrategias sobre cada ticker.
          - Si confluence_mode está activo, las señales se acumulan en un
            ConfluenceFilter y sólo se ejecutan las que tienen consenso.
          - Si confluence_mode está OFF, cada TF ejecuta sus señales (passthrough).
        """
        classic   = self._active_tickers["classic"]
        leveraged = self._active_tickers["leveraged"]

        # Quote-source que lee del dict de quotes L1 del stream
        def _quote_source(sym: str):
            return self._quote_buffers.get(sym) or None

        # ConfluenceFilter opcional por estrategia+ticker
        cf = ConfluenceFilter(
            mode=self._confluence_mode,
            min_agree=self._confluence_min_agree,
        )

        # Capturamos los results por (strategy, tf, ticker) para reconstruir al ejecutar
        results_idx: dict[tuple[str, str, int], dict] = {}

        for tf in self._active_timeframes:
            md = BufferMarketData(self._candle_buffer, frequency=tf,
                                  quote_source=_quote_source)

            # ── Classic strategies (sobre TICKERS_EMA_GAP) ──
            for ticker in classic:
                df = md.get_price_history(ticker, candles=250, frequency=tf)
                if df is None or len(df) < 10:
                    continue
                last_ts = str(df["datetime"].iloc[-1])
                seen_key = (ticker, tf, "classic")
                if self._last_analyzed_key.get(seen_key) == last_ts:
                    continue
                self._last_analyzed_key[seen_key] = last_ts

                for sname, smod in CLASSIC_STRATEGIES.items():
                    if not self._active_strategies.get(sname, True):
                        continue
                    try:
                        if sname == "ema_crossover":
                            use_sma = self._active_strategies.get("sma200_filter", True)
                            result = smod.analyze(md, ticker, use_sma200_filter=use_sma)
                        else:
                            result = smod.analyze(md, ticker)
                        if result:
                            cf.register(ticker, sname, tf, result.get("signal"))
                            results_idx[(ticker, sname, tf)] = result
                    except Exception as e:
                        logger.error(f"[{sname}/{tf}m] {ticker} error: {e}")

            # ── Leveraged strategies (sobre TICKERS_LEVERAGED) ──
            for ticker in leveraged:
                df = md.get_price_history(ticker, candles=250, frequency=tf)
                if df is None or len(df) < 10:
                    continue
                last_ts = str(df["datetime"].iloc[-1])
                seen_key = (ticker, tf, "leveraged")
                if self._last_analyzed_key.get(seen_key) == last_ts:
                    continue
                self._last_analyzed_key[seen_key] = last_ts

                for sname, smod in LEVERAGED_STRATEGIES.items():
                    if not self._active_strategies.get(sname, True):
                        continue
                    try:
                        if sname == "orb":
                            entry = trader.entry_prices.get(ticker)
                            result = smod.analyze(md, ticker, entry)
                        else:
                            result = smod.analyze(md, ticker)
                        if result:
                            cf.register(ticker, sname, tf, result.get("signal"))
                            results_idx[(ticker, sname, tf)] = result
                    except Exception as e:
                        logger.error(f"[{sname}/{tf}m] {ticker} error: {e}")

        # Guardar snapshot para state.json
        self._confluence_snapshot = cf.snapshot()

        # Consolidar y ejecutar
        consolidated = cf.consolidate()
        for (ticker, sname), final_sig in consolidated.items():
            if final_sig == "HOLD":
                continue
            # Elegir un result representativo — el del TF más bajo que coincida
            rep = None
            for (t, s, tf), r in results_idx.items():
                if t == ticker and s == sname and r.get("signal") == final_sig:
                    if rep is None or tf < rep["_tf"]:
                        rep = {**r, "_tf": tf}
            if rep is None:
                continue
            tf = rep.pop("_tf")
            # Marcar la señal final con la etiqueta correcta
            if self._confluence_mode:
                rep["strategy"] = f"{sname.upper()}_CONF"
            self._handle_result(rep, sname, tf)

    def _handle_result(self, result: dict, strategy_name: str, tf: int):
        if not result:
            return
        result["strategy"] = f"{strategy_name.upper()}_{tf}m"
        result["_budget"]  = float(self._budget)
        trader.print_status(result)
        sig = result.get("signal")
        if sig in ("BUY", "SELL"):
            self._signals_today   += 1
            self._last_signal_ts   = time.time()
            trader.execute(result)
        # Log de señal (incluso HOLD si viniera) para que Claude Bot lo vea como context
        if sig in ("BUY", "SELL", "HOLD") and sig != "HOLD":
            self._recent_signals_log.append({
                "ts":        datetime.now(EASTERN).strftime("%H:%M:%S"),
                "ts_epoch":  time.time(),
                "strategy":  result["strategy"],
                "ticker":    result.get("ticker"),
                "signal":    sig,
                "price":     result.get("price"),
            })
            # Trim ventana reciente (última hora)
            cutoff = time.time() - 3600
            self._recent_signals_log = [
                s for s in self._recent_signals_log if s["ts_epoch"] >= cutoff
            ][-200:]

    # ── State writer ─────────────────────────────────────

    async def _state_writer_loop(self):
        import traceback
        while True:
            try:
                if time.time() - self._last_state_write >= STATE_WRITE_EVERY_S:
                    await asyncio.to_thread(self._write_state)
                    self._last_state_write = time.time()
            except Exception as e:
                # Loguear stack completo para diagnosticar causa real
                logger.error(f"[STATE] Write error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(STATE_WRITE_EVERY_S)

    def _write_state(self):
        now       = datetime.now(EASTERN)
        now_str   = now.strftime("%Y-%m-%d %H:%M:%S ET")

        # Positions (defensivo: entry_prices puede tener valores inesperados)
        positions = []
        try:
            for t, v in trader.positions.items():
                if v == "LONG":
                    ep = trader.entry_prices.get(t)
                    try:
                        ep = float(ep) if ep is not None else None
                    except (TypeError, ValueError):
                        ep = None
                    positions.append({"ticker": t, "entry": ep})
        except Exception as e:
            logger.warning(f"[STATE] positions build failed: {e}")

        # Prices (defensivo: cualquier quote puede traer strings, None, NaN)
        prices = {}
        for t in self._tickers:
            try:
                q = self._quote_buffers.get(t) or {}
                p = q.get("last") or q.get("mark") or q.get("close")
                if p is None or p == "":
                    continue
                p_f = float(p)
                # NaN check: NaN != NaN
                if p_f == p_f:
                    prices[t] = p_f
            except (TypeError, ValueError):
                continue

        candle_counts = {t: self._candle_buffer.size(t) for t in self._tickers}

        state = {
            "updated_at":       now_str,
            "updated_ts":       time.time(),
            "bot":              "eolo-v1.2",
            "active":           self._active,
            "market_open":      is_market_open(),
            "auto_close_now":   is_auto_close_time(),
            "tickers":          self._tickers,
            "active_tickers":   self._active_tickers,
            "active_timeframes":     self._active_timeframes,
            "confluence_mode":       self._confluence_mode,
            "confluence_min_agree":  self._confluence_min_agree,
            "confluence_snapshot":   getattr(self, "_confluence_snapshot", {}),
            "active_strategies": self._active_strategies,
            "budget":           self._budget,
            "positions":        positions,
            "prices":           prices,
            "candle_counts":    candle_counts,
            "signals_today":    self._signals_today,
            "last_signal_ts":   self._last_signal_ts,
            "uptime_s":         round(time.time() - self._startup_ts),

            # ── Claude Bot ─────────────────────────────
            "claude_bot": {
                "active":             self._claude_bot_active,
                "paper_mode":         self._claude_bot_paper_mode,
                "model":              self._claude_bot_model,
                "budget":             self._claude_bot_budget,
                "min_conf":           self._claude_bot_min_conf,
                "every_s":            self._claude_bot_every_s,
                "decisions_today":    self._claude_decisions_today,
                "buys_today":         self._claude_buys_today,
                "sells_today":        self._claude_sells_today,
                "holds_today":        self._claude_holds_today,
                "last_call_ts":       self._claude_last_call_ts,
                "last_decision":      self._claude_last_decision,
                "history":            self._claude_history[:20],   # últimas 20 para el dashboard
            },
        }

        # Firestore
        try:
            self._db.collection(CONFIG_COLL).document("state").set(state)
        except Exception as e:
            logger.warning(f"[STATE] Firestore write failed: {e}")

        # JSON local (opcional, útil para debug)
        try:
            with open("eolo_v12_state.json", "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception:
            pass

    # ── Command watcher (close_all, toggle_active) ───────

    async def _command_watcher_loop(self):
        while True:
            try:
                await asyncio.to_thread(self._poll_commands_and_settings)
            except Exception as e:
                logger.error(f"[CMD] Poll error: {e}")
            await asyncio.sleep(COMMAND_POLL_EVERY_S)

    def _poll_commands_and_settings(self):
        # Settings (bot_active, budget, active_timeframes, close_all, confluence_*)
        s = self._read_settings()
        self._active              = bool(s.get("bot_active", True))
        self._budget              = float(s.get("budget", 100))

        # Multi-TF + confluencia via eolo_common.load_multi_tf_config
        mtf = load_multi_tf_config(s)
        if mtf.active_timeframes != self._active_timeframes:
            logger.info(f"[CFG] Timeframes activos: {self._active_timeframes} → {mtf.active_timeframes}")
            self._active_timeframes = mtf.active_timeframes
        if (mtf.confluence_mode != self._confluence_mode
                or mtf.confluence_min_agree != self._confluence_min_agree):
            logger.info(
                f"[CFG] Confluencia: mode={self._confluence_mode}→{mtf.confluence_mode} "
                f"min_agree={self._confluence_min_agree}→{mtf.confluence_min_agree}"
            )
            self._confluence_mode      = mtf.confluence_mode
            self._confluence_min_agree = mtf.confluence_min_agree

        # Strategies
        self._active_strategies = self._read_strategies()

        # Tickers
        new_tickers = self._read_tickers()
        if new_tickers != self._active_tickers:
            logger.info(f"[CFG] Tickers activos: {new_tickers}")
            self._active_tickers = new_tickers

        # ── Claude Bot (estrategia #14) ─────────────────
        prev_active = self._claude_bot_active
        # Habilitado si: claude_bot_active=True en settings Y claude_bot=True en strategies
        self._claude_bot_active     = bool(s.get("claude_bot_active", False)) \
                                      and bool(self._active_strategies.get("claude_bot", False))
        self._claude_bot_paper_mode = bool(s.get("claude_bot_paper_mode", True))
        self._claude_bot_budget     = float(s.get("claude_bot_budget", 50))
        try:
            self._claude_bot_min_conf = float(s.get("claude_bot_min_conf", 0.60))
        except (TypeError, ValueError):
            self._claude_bot_min_conf = 0.60
        try:
            self._claude_bot_every_s = max(15, int(s.get("claude_bot_every_s", CLAUDE_BOT_EVERY_S)))
        except (TypeError, ValueError):
            self._claude_bot_every_s = CLAUDE_BOT_EVERY_S
        new_model = str(s.get("claude_bot_model", "claude-sonnet-4-6")).strip() or "claude-sonnet-4-6"
        if new_model != self._claude_bot_model:
            logger.info(f"[CLAUDE_BOT] Modelo: {self._claude_bot_model} → {new_model}")
            self._claude_bot_model = new_model
            if self._claude_engine is not None:
                self._claude_engine.model = new_model

        # Lazy init: solo construir el engine cuando se active la primera vez
        if self._claude_bot_active and self._claude_engine is None:
            try:
                self._claude_engine = ClaudeBotEngine(
                    model      = self._claude_bot_model,
                    paper_mode = self._claude_bot_paper_mode,
                )
                logger.info(f"[CLAUDE_BOT] Engine inicializado (model={self._claude_bot_model}, "
                            f"paper_mode={self._claude_bot_paper_mode})")
            except Exception as e:
                logger.error(f"[CLAUDE_BOT] No pude inicializar engine: {e}")
                self._claude_bot_active = False
        # Sincronizar paper_mode si ya está el engine
        if self._claude_engine is not None:
            self._claude_engine.paper_mode = self._claude_bot_paper_mode

        if prev_active != self._claude_bot_active:
            logger.info(f"[CLAUDE_BOT] active: {prev_active} → {self._claude_bot_active}")

        # Close all flag
        if s.get("close_all"):
            logger.warning("[CMD] close_all=true detectado — cerrando todas las posiciones")
            try:
                self._execute_close_all()
            finally:
                self._clear_close_all_flag()

    def _execute_close_all(self):
        def _qs(sym: str):
            return self._quote_buffers.get(sym) or None
        md = BufferMarketData(self._candle_buffer, frequency=1, quote_source=_qs)
        closed = 0
        for ticker, state in list(trader.positions.items()):
            if state == "LONG":
                price = md.get_quote(ticker)
                if price and price > 0:
                    trader.execute({
                        "ticker":   ticker,
                        "signal":   "SELL",
                        "price":    price,
                        "strategy": "CLOSE_ALL",
                        "_budget":  self._budget,
                    })
                    closed += 1
        logger.info(f"[CMD] CLOSE_ALL — {closed} posiciones cerradas")

    # ── Auto-close diario 15:27 ET ───────────────────────

    async def _auto_close_loop(self):
        while True:
            try:
                today = datetime.now(EASTERN).date()
                if is_auto_close_time() and self._auto_close_done != today:
                    logger.warning("[AUTO_CLOSE] 15:27 ET — cerrando posiciones del día")
                    try:
                        await asyncio.to_thread(self._execute_close_all)
                    finally:
                        self._auto_close_done = today
            except Exception as e:
                logger.error(f"[AUTO_CLOSE] Error: {e}")
            await asyncio.sleep(30)

    # ── Claude Bot (estrategia #14) ──────────────────────

    async def _claude_bot_loop(self):
        """Ejecuta al Claude Bot cada `claude_bot_every_s` segundos.
        Solo corre si: market_open, bot_active, claude_bot_active, engine inicializado."""
        await asyncio.sleep(30)   # esperar a que el stream llene buffers
        while True:
            try:
                if (
                    self._active
                    and self._claude_bot_active
                    and self._claude_engine is not None
                    and is_market_open()
                    and not is_auto_close_time()
                    and (time.time() - self._claude_last_call_ts) >= self._claude_bot_every_s
                ):
                    await self._claude_bot_tick()
                    self._claude_last_call_ts = time.time()
            except Exception as e:
                import traceback
                logger.error(f"[CLAUDE_BOT] Loop error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(5)   # chequeo ligero cada 5s, tick real según every_s

    async def _claude_bot_tick(self):
        """Un ciclo de decisión: snapshot → Claude → registrar → (opcional) ejecutar."""
        snapshot = self._build_claude_snapshot()
        # [DEBUG] — dumpear lo que Claude realmente está viendo
        try:
            sample_ticker = (snapshot.get("tickers") or ["?"])[0]
            raw_q = self._quote_buffers.get(sample_ticker) or {}
            logger.info(
                f"[CLAUDE_SNAP] prices={snapshot.get('prices')} "
                f"| candles_keys={list((snapshot.get('candles_summary') or {}).keys())}"
            )
            logger.info(f"[CLAUDE_SNAP] sample_quote[{sample_ticker}]={raw_q}")
            cs_sample = (snapshot.get("candles_summary") or {}).get(sample_ticker)
            if cs_sample:
                logger.info(f"[CLAUDE_SNAP] sample_candle_summary[{sample_ticker}]={cs_sample}")
            raw_candles = self._candle_buffer.raw_candles(sample_ticker)
            if raw_candles:
                logger.info(f"[CLAUDE_SNAP] raw_last_candle[{sample_ticker}]={raw_candles[-1]}")
        except Exception as _e:
            logger.warning(f"[CLAUDE_SNAP] log error: {_e}")
        decision = await self._claude_engine.decide(snapshot)
        decision["_paper_mode"] = self._claude_bot_paper_mode

        # Registrar en memoria y Firestore
        self._claude_history.insert(0, decision)
        self._claude_history = self._claude_history[:CLAUDE_HISTORY_KEEP]
        self._claude_last_decision = decision
        self._claude_decisions_today += 1
        sig = decision.get("signal", "HOLD")
        if sig == "BUY":   self._claude_buys_today  += 1
        elif sig == "SELL": self._claude_sells_today += 1
        else:               self._claude_holds_today += 1

        await asyncio.to_thread(self._record_claude_decision_firestore, decision)

        logger.info(
            f"[CLAUDE_BOT] {sig} {decision.get('ticker')} "
            f"@ ${decision.get('price')} "
            f"conf={decision.get('confidence'):.2f} "
            f"| {decision.get('reasoning', '')[:500]}"
        )

        # En paper mode NO ejecutamos
        if self._claude_bot_paper_mode:
            return

        # En live: ejecutar si BUY/SELL con confianza suficiente
        if sig in ("BUY", "SELL") \
                and decision.get("confidence", 0) >= self._claude_bot_min_conf \
                and decision.get("ticker") in self._tickers \
                and decision.get("price", 0) > 0:
            result = {
                "ticker":    decision["ticker"],
                "signal":    sig,
                "price":     decision["price"],
                "strategy":  "CLAUDE_BOT",
                "reasoning": decision.get("reasoning", ""),
                "_budget":   float(self._claude_bot_budget),
            }
            trader.print_status(result)
            self._signals_today  += 1
            self._last_signal_ts  = time.time()
            trader.execute(result)

    def _build_claude_snapshot(self) -> dict:
        """Arma el contexto que se le pasa a Claude."""
        now = datetime.now(EASTERN)
        tickers_active = []
        for group in ("classic", "leveraged"):
            tickers_active.extend(self._active_tickers.get(group, []))
        tickers_active = list(dict.fromkeys(tickers_active))  # dedup conservando orden

        # Precios actuales + mini resumen por ticker
        prices = {}
        candles_summary = {}
        for t in tickers_active:
            try:
                q = self._quote_buffers.get(t) or {}
                p = q.get("last") or q.get("mark") or q.get("close")
                if p is not None:
                    p_f = float(p)
                    if p_f == p_f:   # NaN check
                        prices[t] = round(p_f, 2)
            except (TypeError, ValueError):
                pass

            # Resumen básico de velas: open del día, high, low, close, trend
            candles = self._candle_buffer.raw_candles(t)
            if len(candles) >= 2:
                try:
                    first = candles[0]; last = candles[-1]
                    o = float(first.get("open") or first.get("close") or 0)
                    cl = float(last.get("close") or 0)
                    highs = [float(c.get("high", 0) or 0) for c in candles]
                    lows  = [float(c.get("low", 0) or 0)  for c in candles if (c.get("low") or 0) > 0]
                    vol   = sum(int(c.get("volume", 0) or 0) for c in candles)
                    pct   = round((cl - o) / o * 100, 2) if o > 0 else None

                    # Trends: comparar close vs close N velas atrás
                    def trend_label(n):
                        if len(candles) <= n:
                            return "n/a"
                        past_close = float(candles[-n].get("close") or 0)
                        if past_close <= 0:
                            return "n/a"
                        diff = (cl - past_close) / past_close * 100
                        if diff > 0.15:  return f"UP {diff:+.2f}%"
                        if diff < -0.15: return f"DOWN {diff:+.2f}%"
                        return f"FLAT {diff:+.2f}%"

                    candles_summary[t] = {
                        "open":   round(o, 2),
                        "high":   round(max(highs), 2) if highs else None,
                        "low":    round(min(lows), 2) if lows else None,
                        "close":  round(cl, 2),
                        "volume": vol,
                        "change_pct_open": pct,
                        "trend_1m":   trend_label(1),
                        "trend_5m":   trend_label(5),
                        "trend_15m":  trend_label(15),
                    }
                except Exception:
                    pass

        # Posiciones abiertas con P&L no realizado
        open_positions = []
        for t, v in trader.positions.items():
            if v == "LONG":
                ep_raw = trader.entry_prices.get(t)
                try:
                    ep = float(ep_raw) if ep_raw is not None else None
                except (TypeError, ValueError):
                    ep = None
                cur = prices.get(t)
                pnl = None
                if ep and cur:
                    pnl = round((cur - ep) / ep * 100, 2)
                open_positions.append({
                    "ticker":              t,
                    "entry":               ep,
                    "current":             cur,
                    "unrealized_pnl_pct":  pnl,
                })

        # Señales recientes de las 13 estrategias (última hora)
        recent_signals = list(self._recent_signals_log)

        # Stats del día
        stats_today = {
            "trades_today":   len(open_positions),
            "signals_today":  self._signals_today,
            "claude_decisions_today": self._claude_decisions_today,
        }

        return {
            "tickers":          tickers_active,
            "prices":           prices,
            "candles_summary":  candles_summary,
            "recent_signals":   recent_signals,
            "open_positions":   open_positions,
            "budget":           self._claude_bot_budget,
            "market_time_et":   now.strftime("%Y-%m-%d %H:%M:%S ET"),
            "stats_today":      stats_today,
        }

    def _record_claude_decision_firestore(self, decision: dict):
        """Persiste la decisión en eolo-claude-decisions-v12/{YYYY-MM-DD}/decisions/{ts}."""
        try:
            today = datetime.now(EASTERN).strftime("%Y-%m-%d")
            ts    = f"{time.time():.3f}"
            # Path: /eolo-claude-decisions-v12/{today}/decisions/{ts}
            self._db.collection(CLAUDE_DECISIONS_COLL) \
                    .document(today) \
                    .collection("decisions") \
                    .document(ts) \
                    .set({**decision, "recorded_ts": time.time()})
        except Exception as e:
            logger.warning(f"[CLAUDE_BOT] Firestore write falló: {e}")

    # ── Boot ─────────────────────────────────────────────

    async def start(self):
        logger.info("🚀 EOLO v1.2 arrancando (REALTIME via WebSocket)...")
        logger.info(f"   Tickers clásicos   : {TICKERS_EMA_GAP}")
        logger.info(f"   Tickers apalancados: {TICKERS_LEVERAGED}")
        logger.info(f"   GCP project        : {GCP_PROJECT}")
        logger.info(f"   Firestore namespace: {CONFIG_COLL}")

        # Restaurar posiciones persistidas
        trader.load_positions()

        # Inicializar docs por defecto en Firestore
        self._init_defaults_in_firestore()

        # Cargar settings actuales
        self._poll_commands_and_settings()

        # Registrar handler en el stream
        self._stream.add_handler(self._on_stream_event)

        # Lanzar todas las tasks en paralelo
        await asyncio.gather(
            self._stream.start(),
            self._analysis_watcher_loop(),
            self._state_writer_loop(),
            self._command_watcher_loop(),
            self._auto_close_loop(),
            self._claude_bot_loop(),
            return_exceptions=False,
        )


# ── HTTP health server (Cloud Run necesita bind a $PORT) ─

async def _health_server(bot: EoloV12Bot):
    async def status(req):
        return web.json_response({
            "service":      "eolo-v1.2",
            "running":      True,
            "active":       bot._active,
            "market_open":  is_market_open(),
            "uptime_s":     round(time.time() - bot._startup_ts),
            "tickers":      bot._tickers,
            "timeframes":   bot._active_timeframes,
            "signals_today": bot._signals_today,
        })

    app = web.Application()
    app.router.add_get("/",       status)
    app.router.add_get("/health", status)
    app.router.add_get("/status", status)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"[HTTP] Health server en puerto {port}")


async def main():
    bot = EoloV12Bot()
    await _health_server(bot)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
