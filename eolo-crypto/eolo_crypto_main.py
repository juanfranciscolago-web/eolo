# ============================================================
#  EOLO Crypto — Orquestador principal (async)
#
#  Integra:
#    • BinanceStream      (WS combined klines, 24/7)
#    • BinanceScreener    (rota small-caps cada 10 min)
#    • MarketDataBuffer   (rolling OHLCV por símbolo)
#    • StrategyRunner     (13 estrategias Eolo v1 adaptadas)
#    • ClaudeBotCrypto    (motor de decisión con Anthropic API)
#    • BinanceExecutor    (PAPER/TESTNET/LIVE order management)
#    • StateWriter        (flush a Firestore para dashboard)
#
#  Arquitectura: una sola event loop. El stream empuja velas al
#  buffer; cada vela cerrada dispara evaluate_all() sobre ese
#  símbolo; si hay consenso (≥2 BUY/SELL) se ejecuta vía executor.
#  Claude Bot corre en paralelo con su propio cadencia (cada 5 min)
#  y puede override por confidence >= 0.60.
# ============================================================
import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime, timezone

from loguru import logger

import settings
from helpers import binance_get
from buffer_market_data import MarketDataBuffer
from stream.binance_stream import BinanceStream
from stream.binance_screener import BinanceScreener
from strategies import StrategyRunner
from trading.binance_executor import BinanceExecutor
from claude_bot_crypto import ClaudeBotCrypto
from firestore_state import StateWriter
from runtime_config import config as runtime_config

# ── eolo_common (multi-TF + confluencia compartido) ──────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.multi_tf import ConfluenceFilter  # noqa: E402
from eolo_common.trading_hours import (  # noqa: E402
    is_within_trading_window,
    is_after_auto_close,
    now_et,
    format_schedule_for_api,
)
from eolo_common.risk import get_regime_multiplier  # noqa: E402 — Macro Regime Bridge
from eolo_common.routing import AutoRouter as _AutoRouter  # noqa: E402

_auto_router_crypto = _AutoRouter(bot_id="crypto", update_interval_min=30)


# Cada cuánto re-leemos eolo-crypto-config/settings desde Firestore.
# El dashboard modifica el doc en respuesta a toggles; queremos que
# los cambios (estrategias on/off, max posiciones, claude on/off,
# budget claude, SL/TP defaults, daily loss cap, position size) surtan
# efecto rápido pero sin machacar Firestore innecesariamente.
CONFIG_REFRESH_SEC = 20


class EoloCryptoOrchestrator:

    def __init__(self):
        # Universo inicial = core (screener agregará small-caps runtime)
        self.buffer    = MarketDataBuffer()
        self.executor  = BinanceExecutor()
        self.stream    = BinanceStream(symbols=list(settings.CORE_UNIVERSE))
        self.screener  = BinanceScreener()
        self.strategies = StrategyRunner()
        self.state     = StateWriter()
        # State pasado al Claude Bot para que pueda gate-ear llamadas API
        # basándose en signals recientes + posiciones abiertas.
        self.claude    = ClaudeBotCrypto(self.buffer, self.executor, state=self.state)

        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        # Throttle del log "⏸ Pause time limit" para no inundar Cloud Run Logs
        self._last_pause_log_ts: float = 0.0
        # Throttle del auto-close (una pasada por día como máximo)

        # ── BTC Dominance Regime Filter ───────────────────
        # Histórico de lecturas de BTC.D (%) para calcular MA20.
        # Si BTC.D > MA20 → altcoins tienen menor demanda relativa;
        # solo permitimos BUY en BTCUSDT y ETHUSDT (principales).
        # Fuente: CoinGecko /api/v3/global (free, no API key).
        self._btcd_history: list[float] = []   # últimas 20+ lecturas
        self._btcd_restricted: bool     = False  # True = solo BTC/ETH
        self._last_auto_close_date: str | None = None
        # ── 4h candle-close gating ──────────────────────────
        # Trackea por (symbol, tf_min) el número de período TF que ya
        # fue procesado. Si el período actual == el último procesado,
        # el candle TF aún NO cerró → saltamos la evaluación para ese TF.
        # Un período TF = floor(open_ms_1m / (tf_min * 60_000)).
        # La transición (nuevo período) indica que el candle TF anterior
        # acaba de cerrarse: es el momento correcto para evaluar.
        self._last_tf_period: dict[tuple[str, int], int] = {}

        # Wire handlers
        self.stream.add_handler(self._on_candle)
        self.screener.add_handler(self._on_screener_update)
        # Cada decisión Claude alimenta el state doc (tile "Claude calls"
        # del header + tabla del panel Claude leen de last_decisions).
        self.claude.add_decision_handler(self.state.push_decision)

    # ── Setup inicial ─────────────────────────────────────

    def _backfill_all(self):
        """Backfill en paralelo (threadpool) del core universe."""
        logger.info(f"[ORCH] Backfilling {len(settings.CORE_UNIVERSE)} pares del core...")
        for sym in settings.CORE_UNIVERSE:
            self.buffer.backfill(sym, limit=settings.HISTORICAL_LOAD)
        logger.info("[ORCH] Backfill completo")

    def _load_initial_positions(self):
        """Sincroniza posiciones ya existentes (TESTNET/LIVE) al state."""
        positions = self.executor.get_open_positions()
        balance   = self.executor.get_balance_usdt()
        self.state.set_positions(positions)
        self.state.set_balance(balance)
        logger.info(
            f"[ORCH] Estado inicial | balance=${balance:.2f} | "
            f"posiciones abiertas: {len(positions)}"
        )

    # ── Handlers ──────────────────────────────────────────

    async def _on_candle(self, candle: dict):
        """
        Callback por cada vela cerrada. Pushea al buffer y dispara
        evaluación multi-TF con confluencia opcional.

        Flujo:
          1. push al buffer común
          2. para cada TF activo (config Firestore): resample + evaluate_all
          3. registrar señales en ConfluenceFilter
          4. consolidar → ejecutar solo las señales finales (BUY/SELL)

        Cuando confluence_mode=False el comportamiento es compatible
        con el viejo (TF=1 default): cualquier BUY/SELL en cualquier TF
        gatilla acción, con SELL tomando precedencia.
        """
        self.buffer.push(candle)
        symbol = candle["symbol"]

        # Solo evaluamos si ya hay suficiente histórico de 1-min
        if self.buffer.size(symbol) < 60:
            return

        # Config multi-TF actual (leída desde Firestore en el refresher)
        active_tfs = runtime_config.active_timeframes or [1]
        cfilter = ConfluenceFilter(
            mode=runtime_config.confluence_mode,
            min_agree=runtime_config.confluence_min_agree,
        )

        # Aggregamos todas las signals (para push al state doc) y la lista
        # de TFs que contribuyeron a cada (symbol,strategy) para el log/reason.
        all_signals: list[dict] = []
        tf_map: dict[tuple[str, str], list[int]] = {}

        for tf in active_tfs:
            # ── Candle-close gate para TFs > 1m ──────────────────
            # Para TF=1 se evalúa cada vela (comportamiento viejo).
            # Para TF>1 (p.ej. 4h=240) solo evaluamos cuando el período
            # del TF cambia, es decir cuando una vela TF acaba de cerrar.
            # Esto evita que el bot ejecute señales ruidosas en cada vela
            # 1m usando datos de 4h que aún están incompletos.
            if tf > 1:
                open_ms = candle.get("open_ms") or 0
                current_period = int(open_ms) // (tf * 60_000)
                gate_key = (symbol, tf)
                if self._last_tf_period.get(gate_key) == current_period:
                    # Mismo período TF — vela aún no cerró, skip
                    continue
                # Nuevo período → vela TF anterior cerrada ✅
                self._last_tf_period[gate_key] = current_period
                logger.debug(
                    f"[4H-GATE] {symbol}@{tf}m — período {current_period} "
                    f"iniciado, evaluando señales"
                )

            df_tf = (self.buffer.as_dataframe(symbol) if tf == 1
                     else self.buffer.as_dataframe_tf(symbol, tf))
            if df_tf is None or len(df_tf) < 30:
                # No hay suficiente data para este TF todavía
                continue

            try:
                tf_signals = self.strategies.evaluate_all(df_tf, symbol)
            except Exception as e:
                logger.error(
                    f"[ORCH] evaluate_all({symbol}@{tf}m) error: "
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                )
                self.state.inc_errors()
                continue

            for s in tf_signals:
                s["symbol"]    = symbol
                s["timeframe"] = tf
                all_signals.append(s)
                sig = s.get("signal", "HOLD")
                if sig in ("BUY", "SELL"):
                    # Los adapters crypto devuelven la estrategia bajo "strategy";
                    # también aceptamos "name" por compat. Si ninguno está presente,
                    # NO agrupamos bajo "?" porque colapsaba todas las estrategias
                    # en un mismo bucket de confluencia (→ falsos positivos).
                    strat_name = s.get("strategy") or s.get("name")
                    if not strat_name:
                        logger.warning(
                            f"[ORCH] Signal sin nombre de estrategia "
                            f"({symbol}@{tf}m, sig={sig}) — la ignoro para evitar "
                            f"colapso en ConfluenceFilter"
                        )
                        continue
                    cfilter.register(symbol, strat_name, tf, sig)
                    key = (symbol, strat_name)
                    tf_map.setdefault(key, []).append(tf)

        # Push de todas las señales (multi-TF) al state para dashboard
        for s in all_signals:
            self.state.push_signal(s)

        # Consolidar: un dict (symbol,strategy) -> BUY/SELL/HOLD
        consolidated = cfilter.consolidate()
        self.state.set_confluence_snapshot(cfilter.snapshot())

        # Filtrar a sólo las firmas accionables (BUY o SELL) para nuestro symbol
        actionable = [
            (strat, final)
            for (sym, strat), final in consolidated.items()
            if sym == symbol and final in ("BUY", "SELL")
        ]
        if not actionable:
            return

        # Separar en dos grupos — SELL tiene precedencia (cerrar antes de abrir)
        sells = [s for s, sig in actionable if sig == "SELL"]
        buys  = [s for s, sig in actionable if sig == "BUY"]

        # ── Cross-strategy consensus gate ────────────────────
        # Requiere que al menos N estrategias independientes coincidan
        # en la misma dirección antes de ejecutar. Evita trades
        # por señal de una sola estrategia (especialmente en 4h donde
        # cada estrategia vota una sola vez por candle).
        min_consensus = runtime_config.min_strategy_consensus
        if sells and len(sells) < min_consensus:
            logger.debug(
                f"[CONSENSUS] {symbol} SELL insuficiente "
                f"({len(sells)}/{min_consensus}): {sells}"
            )
            sells = []
        if buys and len(buys) < min_consensus:
            logger.debug(
                f"[CONSENSUS] {symbol} BUY insuficiente "
                f"({len(buys)}/{min_consensus}): {buys}"
            )
            buys = []

        if not sells and not buys:
            return

        price = candle["close"]

        # ── Gate trading_hours ──
        # Si estamos FUERA del window configurado, no abrimos posiciones
        # nuevas (BUYs), pero SÍ dejamos que los SELLs cierren posiciones
        # existentes. Defaults crypto = 00:00-23:59 → never blocks.
        sch = runtime_config.schedule
        within_window = is_within_trading_window(now_et(), sch)
        if not within_window and buys:
            import time as _time
            _now_ts = _time.time()
            if _now_ts - self._last_pause_log_ts > 60:
                reason = "before_start" if now_et().time() < sch.start else "after_end"
                logger.info(
                    f"⏸ Pause time limit — {symbol} BUYs ignorados "
                    f"({reason}, window={sch.start.strftime('%H:%M')}-{sch.end.strftime('%H:%M')} ET)"
                )
                self._last_pause_log_ts = _now_ts
            buys = []  # Anulamos los BUYs; SELLs siguen válidos

        # ── SELLs ──
        for strat in sells:
            tfs = sorted(tf_map.get((symbol, strat), []))
            reason = (f"multi-tf consensus | strategy={strat} | "
                      f"tfs={tfs} | mode="
                      f"{'confluence' if runtime_config.confluence_mode else 'any'}")
            self.executor.close_long(
                symbol=symbol,
                price=price,
                strategy=f"consensus:{strat}",
                reason=reason[:200],
            )

        # ── Auto-Router: actualizar toggles de estrategias por régimen ─
        # Se ejecuta cada 30 min usando el candle buffer de BTCUSDT como
        # proxy de mercado (no tenemos SPY en crypto). Fail-soft.
        try:
            if symbol == "BTCUSDT" and _auto_router_crypto.should_update():
                _btc_df = self.buffer.get_candles_resampled("BTCUSDT", 1440)  # daily proxy
                if _btc_df is not None and len(_btc_df) >= 20:
                    # Usar VIX proxy: vol 14d de BTC como nivel de "miedo"
                    import numpy as _np
                    _cl = _btc_df["close"].values.astype(float)
                    _lr = _np.diff(_np.log(_cl[-15:]))
                    _btc_vix_proxy = float(_np.std(_lr, ddof=1)) * _np.sqrt(252) * 100
                    _new_t = _auto_router_crypto.update(
                        vix=_btc_vix_proxy, spy_df=_btc_df, save_firestore=False
                    )
                    logger.debug(f"[AUTO_ROUTER] crypto toggles: vix_proxy={_btc_vix_proxy:.1f} toggles={_new_t}")
        except Exception as _ar_e:
            logger.debug(f"[AUTO_ROUTER] crypto skip: {_ar_e}")

        # ── BTC Dominance gate ───────────────────────────────
        # Si BTC.D > MA20 (risk-off para altcoins), filtramos BUYs
        # en altcoins: solo permitimos BTCUSDT y ETHUSDT.
        _BTCD_SAFE = {"BTCUSDT", "ETHUSDT"}
        if self._btcd_restricted and symbol not in _BTCD_SAFE and buys:
            logger.debug(
                f"[BTCD] {symbol} BUY bloqueado — BTC.D en régimen restrictivo "
                f"(solo {sorted(_BTCD_SAFE)} permitidos)"
            )
            buys = []

        if not sells and not buys:
            return

        # ── BUYs ──
        # Macro Regime Bridge: escala el position size según VIX.
        # En crypto el macro object es None → retorna 1.0× (neutral).
        # Cuando se agregue un feed de VIX a crypto, pasarlo aquí.
        regime_mult = get_regime_multiplier(macro=None)
        for strat in buys:
            tfs = sorted(tf_map.get((symbol, strat), []))
            reason = (f"multi-tf consensus | strategy={strat} | "
                      f"tfs={tfs} | mode="
                      f"{'confluence' if runtime_config.confluence_mode else 'any'}"
                      f" | regime_mult={regime_mult:.2f}x")
            self.executor.open_long(
                symbol=symbol,
                price=price,
                strategy=f"consensus:{strat}",
                reason=reason[:200],
                size_mult=regime_mult,
            )

        # Refrescar state después del posible trade
        self.state.set_positions(self.executor.get_open_positions())
        self.state.set_balance(self.executor.get_balance_usdt())

    async def _on_screener_update(self, active: list[str], added: list[str], removed: list[str]):
        """Rota small-caps: agrega al stream + backfill los nuevos."""
        if added:
            # Backfill en threadpool para no bloquear el event loop
            loop = asyncio.get_event_loop()
            for sym in added:
                await loop.run_in_executor(None, self.buffer.backfill, sym)
            await self.stream.add_symbols(added)
        if removed:
            await self.stream.remove_symbols(removed)
            for sym in removed:
                self.buffer.drop(sym)

        # State con universo actualizado (core + screener activos)
        universe = sorted(set(settings.CORE_UNIVERSE) | set(active))
        self.state.set_universe(universe, screener_active=active)

    # ── State refresher (loop) ────────────────────────────

    async def _state_refresher(self):
        """Cada 30s refresca balance/posiciones desde el executor."""
        while not self._stopping:
            try:
                self.state.set_balance(self.executor.get_balance_usdt())
                self.state.set_positions(self.executor.get_open_positions())
                self.state.set_daily_cost(self.claude._today_cost_usd)
            except Exception as e:
                logger.warning(f"[ORCH] state_refresher: {e}")
                self.state.inc_errors()
            await asyncio.sleep(30)

    # ── Auto-close loop (trading_hours) ───────────────────

    async def _auto_close_loop(self):
        """
        Cada 30s revisa si llegamos a `auto_close_et` del schedule. Si sí, y
        todavía no cerramos hoy, hace flatten de todas las posiciones. También
        publica el schedule al state (market_open + format_schedule_for_api).
        """
        while not self._stopping:
            try:
                sch = runtime_config.schedule
                now = now_et()
                within = is_within_trading_window(now, sch)
                after_close = is_after_auto_close(now, sch)

                # Publicar al state (dashboard lee esto para el banner)
                self.state.set_schedule(
                    format_schedule_for_api(sch, now),
                    market_open=within,
                )

                # Flatten al cruzar auto_close — una vez por día
                today_key = now.strftime("%Y-%m-%d")
                if (sch.enabled
                        and after_close
                        and self._last_auto_close_date != today_key):
                    positions = self.executor.get_open_positions()
                    if positions:
                        logger.warning(
                            f"⏱ Auto-close ({sch.auto_close.strftime('%H:%M')} ET) — "
                            f"cerrando {len(positions)} posiciones"
                        )
                        for sym in list(positions.keys()):
                            try:
                                # precio aproximado — el executor usa last_price
                                self.executor.close_long(
                                    symbol=sym,
                                    price=0.0,
                                    strategy="auto_close",
                                    reason=f"trading_hours auto_close {sch.auto_close.strftime('%H:%M')}",
                                )
                            except Exception as e:
                                logger.warning(f"[AUTO-CLOSE] {sym} falló: {e}")
                        self.state.set_positions(self.executor.get_open_positions())
                        self.state.set_balance(self.executor.get_balance_usdt())
                    self._last_auto_close_date = today_key

            except Exception as e:
                logger.warning(f"[AUTO-CLOSE] loop: {e}")
                self.state.inc_errors()
            await asyncio.sleep(30)

    # ── Reconcile balance real vs _eolo_positions (loop) ──

    async def _reconcile_loop(self):
        """
        Cada 5 min lee /api/v3/account y ajusta _eolo_positions al balance real:
        si Eolo tiene qty > real → elimina (si real≈0) o ajusta (si 0<real<eolo).
        Complemento proactivo al auto-cleanup reactivo de _market_sell (-2010).

        Corre también en TESTNET (donde los desyncs por reset son frecuentes).
        En PAPER mode el método del executor skipea solo.
        """
        # Pequeño delay inicial para que el bot termine de arrancar
        await asyncio.sleep(60)

        while not self._stopping:
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.executor.reconcile_positions_with_binance
                )
                n_rm   = len(result.get("removed", []))
                n_adj  = len(result.get("adjusted", []))
                n_uc   = len(result.get("unchanged", []))
                skipped = result.get("skipped") or result.get("error")
                if skipped:
                    logger.info(f"[RECONCILE] ciclo skipped: {skipped}")
                else:
                    logger.info(
                        f"[RECONCILE] ciclo OK — removed={n_rm} "
                        f"adjusted={n_adj} unchanged={n_uc}"
                    )
                    if n_rm or n_adj:
                        # Refrescar state snapshot para dashboard
                        self.state.set_positions(self.executor.get_open_positions())
            except Exception as e:
                logger.warning(f"[ORCH] reconcile_loop: {e}")
                self.state.inc_errors()
            await asyncio.sleep(300)  # 5 min

    # ── BTC Dominance Regime Filter (loop) ───────────────

    async def _btc_dominance_loop(self):
        """
        Cada 4h obtiene BTC Dominance desde CoinGecko (API pública, sin key).
        Guarda en self._btcd_history (hasta 24 lecturas = 4 días).
        Si BTC.D > MA20 de las últimas 20 lecturas → self._btcd_restricted = True
        → en _on_candle, los BUYs se filtran para permitir solo BTCUSDT / ETHUSDT.

        Fail-soft: si CoinGecko no responde, mantenemos el estado previo.

        Nota: CoinGecko puede geo-bloquear desde us-east1. Si el endpoint falla
        sistemáticamente, cambiar BINANCE_REGION a southamerica-east1 o deshabilitar
        esta feature vía Firestore (se puede agregar 'btcd_filter_enabled' a config).
        """
        import asyncio as _asyncio

        BTCD_INTERVAL  = 4 * 60 * 60      # 4h
        BTCD_MA_PERIOD = 20
        BTCD_URL       = "https://api.coingecko.com/api/v3/global"
        BTCD_SAFE_SYMBOLS = {"BTCUSDT", "ETHUSDT"}

        while not self._stopping:
            try:
                loop = _asyncio.get_event_loop()
                def _fetch():
                    import urllib.request, json as _json
                    req = urllib.request.Request(
                        BTCD_URL,
                        headers={
                            "User-Agent": "eolo-crypto-bot/1.0",
                            "Accept": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return _json.loads(r.read().decode())

                data = await loop.run_in_executor(None, _fetch)
                btcd = float(data["data"]["market_cap_percentage"].get("btc", 0))
                if btcd > 0:
                    self._btcd_history.append(btcd)
                    # Mantener solo las últimas 24 lecturas (4 días a 4h)
                    if len(self._btcd_history) > 24:
                        self._btcd_history = self._btcd_history[-24:]

                    # Calcular régimen
                    if len(self._btcd_history) >= BTCD_MA_PERIOD:
                        ma20 = sum(self._btcd_history[-BTCD_MA_PERIOD:]) / BTCD_MA_PERIOD
                        was_restricted = self._btcd_restricted
                        self._btcd_restricted = btcd > ma20
                        if self._btcd_restricted != was_restricted:
                            if self._btcd_restricted:
                                logger.info(
                                    f"[BTCD] BTC.D={btcd:.1f}% > MA20={ma20:.1f}% "
                                    f"→ RESTRINGIDO: solo {sorted(BTCD_SAFE_SYMBOLS)}"
                                )
                            else:
                                logger.info(
                                    f"[BTCD] BTC.D={btcd:.1f}% ≤ MA20={ma20:.1f}% "
                                    f"→ ABIERTO: todo el universo"
                                )
                    else:
                        logger.debug(
                            f"[BTCD] BTC.D={btcd:.1f}% — "
                            f"acumulando {len(self._btcd_history)}/{BTCD_MA_PERIOD} lecturas"
                        )

            except Exception as e:
                logger.warning(f"[BTCD] fetch falló: {type(e).__name__}: {e} — mantengo estado previo")

            await _asyncio.sleep(BTCD_INTERVAL)

    # ── Config refresher (loop) ───────────────────────────

    async def _config_refresher(self):
        """
        Cada CONFIG_REFRESH_SEC re-lee eolo-crypto-config/settings desde
        Firestore y mergea sobre los defaults de settings.py. Fail-soft:
        si el fetch falla, se mantiene la última config conocida.
        """
        while not self._stopping:
            try:
                runtime_config.refresh()
            except Exception as e:
                logger.warning(f"[ORCH] config_refresher: {e}")
                self.state.inc_errors()
            await asyncio.sleep(CONFIG_REFRESH_SEC)

    # ── Main ──────────────────────────────────────────────

    async def run(self):
        # 0. Primer fetch de config (sync, bloqueante) ANTES del backfill
        # para que todo el arranque ya vea overrides del dashboard.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, runtime_config.refresh)
        logger.info(
            f"[ORCH] Runtime config inicial | "
            f"strategies_enabled={runtime_config.strategies_enabled} | "
            f"max_open_positions={runtime_config.max_open_positions} | "
            f"position_size_pct={runtime_config.position_size_pct} | "
            f"claude_enabled={runtime_config.claude_bot_enabled} | "
            f"claude_budget=${runtime_config.claude_max_cost_per_day}"
        )
        logger.info(
            f"[ORCH] Multi-TF | active={runtime_config.active_timeframes} | "
            f"confluence={runtime_config.confluence_mode} | "
            f"min_agree={runtime_config.confluence_min_agree} | "
            f"min_strategy_consensus={runtime_config.min_strategy_consensus}"
        )

        # 1. Backfill inicial (sync, bloquea unos segundos al arranque)
        await loop.run_in_executor(None, self._backfill_all)
        await loop.run_in_executor(None, self._load_initial_positions)

        # Universo inicial en state
        self.state.set_universe(
            sorted(set(settings.CORE_UNIVERSE)),
            screener_active=[],
        )

        # 2. Arrancar todas las tareas async
        self._tasks = [
            asyncio.create_task(self.stream.start(),           name="stream"),
            asyncio.create_task(self.screener.start(),         name="screener"),
            asyncio.create_task(self.claude.start(),           name="claude"),
            asyncio.create_task(self.state.start(),            name="state"),
            asyncio.create_task(self._state_refresher(),       name="state-refresh"),
            asyncio.create_task(self._config_refresher(),      name="config-refresh"),
            asyncio.create_task(self._auto_close_loop(),       name="auto-close"),
            asyncio.create_task(self._reconcile_loop(),        name="reconcile"),
            asyncio.create_task(self._btc_dominance_loop(),    name="btc-dominance"),
        ]

        logger.info(
            f"[ORCH] EOLO Crypto corriendo | mode={settings.BINANCE_MODE} | "
            f"core={len(settings.CORE_UNIVERSE)} | "
            f"strategies_active={self.strategies.enabled_strategies()} | "
            f"claude={runtime_config.claude_bot_enabled}"
        )

        # 3. Esperar que terminen (en Cloud Run = nunca, salvo SIGTERM)
        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        # Si alguna termina, apagamos todo (el watchdog reinicia el proceso)
        logger.warning(f"[ORCH] Una tarea terminó — parando las demás. done={[t.get_name() for t in done]}")
        await self.stop()

    async def stop(self):
        if self._stopping:
            return
        self._stopping = True
        logger.info("[ORCH] Stop solicitado — apagando módulos...")
        self.stream.stop()
        self.screener.stop()
        self.claude.stop()
        self.state.stop()

        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("[ORCH] Todos los módulos detenidos.")


# ── Entry point async ─────────────────────────────────────

async def main():
    """Llamado desde main.py dentro del watchdog."""
    orch = EoloCryptoOrchestrator()

    # Instalar SIGTERM handler SOLO si corremos en el main thread del proceso.
    # En Cloud Run arrancamos desde un worker thread (gunicorn → watchdog thread),
    # donde signal.signal/add_signal_handler lanzan RuntimeError. En ese caso
    # gunicorn ya gestiona SIGTERM y re-lanza el worker, así que no hace falta.
    import threading
    if threading.current_thread() is threading.main_thread():
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(orch.stop()))
            except (NotImplementedError, RuntimeError, ValueError) as e:
                logger.debug(f"[ORCH] No pude instalar handler para {sig}: {e}")

    await orch.run()


if __name__ == "__main__":
    logger.info("⚡ EOLO Crypto iniciando standalone (sin watchdog)...")
    asyncio.run(main())
