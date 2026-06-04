# ============================================================
#  SchwabRestPoller — REST polling fallback para CROP
#
#  Reemplaza el WebSocket Streamer de Schwab por polling REST
#  cada N segundos al endpoint /marketdata/v1/pricehistory.
#
#  CONTEXTO (tech debt #23): 3 bots eolo (v1, v2, crop) usan
#  las mismas credenciales Schwab. El Streamer solo admite UNA
#  conexión concurrente por OAuth credential — el server cierra
#  limpiamente la anterior cuando un nuevo cliente loguea con
#  las mismas creds. Resultado: CROP uptime real ~8%, CandleBuffer
#  crónicamente vacío. v1/v2 quedan productivos (status quo).
#
#  Interface compatible con SchwabStream:
#    poller = SchwabRestPoller(tickers=[...])
#    poller.add_handler(fn)        # fn(ticker, data_dict)
#    await poller.start()          # corre indefinidamente
#    poller.stop()                 # sync, igual que SchwabStream
#
#  Data dicts emitidos (mismo shape que SchwabStream):
#    candles (CHART_EQUITY parseado):
#      {"type": "candle", "symbol", "time" (ms),
#       "open", "high", "low", "close", "volume"}
#    quotes L1 (LEVELONE_EQUITIES parseado):
#      {"type": "quote", "symbol", "ts" (sec),
#       "bid", "ask", "last", "bid_size", "ask_size",
#       "volume", "open", "high", "low", "close", "mark"}
#
#  Daily backfill (Sprint 6 — tech debt #15):
#    Buffer interno separado `_daily_buffer` con ~252 candles 1-day del
#    último año. Backfill al boot + refresh cada 1h. NO va a handlers
#    externos: el snapshot lo lee directo via `get_daily_buffer()`.
# ============================================================
import asyncio
import time
from typing import Callable

import requests
from loguru import logger

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), ".."))
from helpers import get_access_token
from eolo_common.multi_tf import CandleBuffer
from eolo_common.multi_tf.normalize import from_schwab_chart_equity

# ── Constantes ────────────────────────────────────────────
URL_PRICEHISTORY = "https://api.schwabapi.com/marketdata/v1/pricehistory"
URL_QUOTES       = "https://api.schwabapi.com/marketdata/v1/quotes"
POLL_INTERVAL_SEC = 5
TIMEOUT_SEC = 8

# Sprint 6 — daily backfill (tech debt #15)
DAILY_POLL_INTERVAL_SEC = 3600   # 1 hora — daily candles solo cambian intraday el último
DAILY_BUFFER_SIZE = 300          # ~14 meses de daily candles (cómodo para EMA_200)
DAILY_PARAMS = {
    "periodType":            "year",
    "period":                1,
    "frequencyType":         "daily",
    "frequency":             1,
    "needExtendedHoursData": False,
}

# Sprint 7 — VIX yesterday close para vix_velocity_1d_pct (tech debt #20)
# Símbolo $VIX verificado 2026-04-20 con test_macro_quotes (cuenta bot).
VIX_SYMBOL = "$VIX"
VIX_HISTORY_PARAMS = {
    "periodType":            "month",
    "period":                1,
    "frequencyType":         "daily",
    "frequency":             1,
    "needExtendedHoursData": False,
}


class SchwabRestPoller:
    """
    Poller REST drop-in replacement de SchwabStream para CROP.

    Tres loops async:
      • candles 1-min vía /pricehistory cada 5s (alimenta CandleBuffer externo)
      • quotes L1 vía /quotes cada 5s (alimenta dashboard bid/ask/last/mark)
      • daily candles vía /pricehistory periodType=year cada 1h, backfill
        inmediato al boot (alimenta _daily_buffer interno — tech debt #15)
    """

    def __init__(self, tickers: list[str]):
        self.tickers = [t.upper() for t in tickers]
        self._handlers: list[Callable] = []
        self._running = False
        # Dedup: último ts_ms enviado al handler por símbolo.
        # CandleBuffer.push solo dedupa contra buf[-1], no contra histórico —
        # sin este tracking re-pusheamos ~390 candles del día cada 5s.
        self._last_ts_per_symbol: dict[str, int] = {}
        # Sprint 6: buffer daily separado para indicators (RSI/ATR/EMA/ADR daily).
        # No va a handlers — consumido por snapshot via `get_daily_buffer()`.
        self._daily_buffer = CandleBuffer(max_len=DAILY_BUFFER_SIZE)
        self._last_daily_ts_per_symbol: dict[str, int] = {}
        # Sprint 7: cache de VIX close de ayer para vix_velocity_1d_pct (tech debt #20).
        # Refrescado en el daily loop (cada 1h). None hasta el primer fetch exitoso.
        self._vix_yesterday_close: float | None = None

    # ── Compat con SchwabStream ────────────────────────────

    def add_handler(self, fn: Callable) -> None:
        """Registra callback fn(ticker, data_dict)."""
        self._handlers.append(fn)

    def stop(self) -> None:
        self._running = False
        logger.info("[REST_POLLER] Deteniendo polling...")

    def get_daily_buffer(self) -> CandleBuffer:
        """Acceso al buffer daily — consumido por snapshot.build_market_snapshot_from_crop."""
        return self._daily_buffer

    def get_vix_yesterday_close(self):
        """Sprint 7 (#20): VIX close de ayer — None hasta el primer fetch exitoso."""
        return self._vix_yesterday_close

    # ── Loop principal ─────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        logger.info(
            f"[REST_POLLER] Iniciando para {self.tickers} "
            f"({POLL_INTERVAL_SEC}s candles + quotes, "
            f"{DAILY_POLL_INTERVAL_SEC}s daily)"
        )
        # Loops paralelos: candles 5s + quotes 5s + daily 1h con backfill al boot.
        await asyncio.gather(
            self._candle_poll_loop(),
            self._quote_poll_loop(),
            self._daily_poll_loop(),
            return_exceptions=True,
        )

    async def _candle_poll_loop(self) -> None:
        while self._running:
            t0 = time.time()
            try:
                await asyncio.gather(*(
                    self._poll_ticker_candles(t) for t in self.tickers
                ), return_exceptions=True)
            except Exception as e:
                logger.warning(f"[REST_POLLER] candle cycle err: {e}")
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.5, POLL_INTERVAL_SEC - elapsed))

    async def _quote_poll_loop(self) -> None:
        while self._running:
            t0 = time.time()
            try:
                quotes = await asyncio.to_thread(
                    self._fetch_quotes_sync, self.tickers
                )
                for ticker, qdata in quotes.items():
                    await self._notify(ticker, {"type": "quote", **qdata})
            except Exception as e:
                logger.warning(f"[REST_POLLER] quote cycle err: {e}")
            elapsed = time.time() - t0
            await asyncio.sleep(max(0.5, POLL_INTERVAL_SEC - elapsed))

    async def _poll_ticker_candles(self, symbol: str) -> None:
        candles = await asyncio.to_thread(self._fetch_pricehistory_sync, symbol)
        if not candles:
            return
        new_candles = self._dedup_and_filter(symbol, candles)
        for c in new_candles:
            await self._notify(symbol, {"type": "candle", **c})

    async def _daily_poll_loop(self) -> None:
        """
        Backfill inmediato al boot (1 call por ticker + 1 call para VIX) +
        refresh cada 1h. Daily candles van a `_daily_buffer`; VIX yesterday
        close va a `_vix_yesterday_close`. No notifica handlers — consumidos
        por snapshot vía get_daily_buffer() / get_vix_yesterday_close().
        """
        logger.info(f"[REST_POLLER] daily backfill iniciando para {self.tickers} + VIX...")
        for ticker in self.tickers:
            await self._poll_ticker_daily(ticker)
        await self._refresh_vix_yesterday()
        size = self._daily_buffer.size(self.tickers[0]) if self.tickers else 0
        logger.info(
            f"[REST_POLLER] daily backfill completado — "
            f"{self.tickers[0]} buffer={size} candles, "
            f"VIX yesterday={self._vix_yesterday_close}"
        )
        while self._running:
            await asyncio.sleep(DAILY_POLL_INTERVAL_SEC)
            if not self._running:
                break
            for ticker in self.tickers:
                await self._poll_ticker_daily(ticker)
            await self._refresh_vix_yesterday()

    async def _refresh_vix_yesterday(self) -> None:
        """Sprint 7 (#20): refresh del cache _vix_yesterday_close."""
        vix_close = await asyncio.to_thread(self._fetch_vix_yesterday_sync)
        if vix_close is not None:
            self._vix_yesterday_close = vix_close

    async def _poll_ticker_daily(self, symbol: str) -> None:
        candles = await asyncio.to_thread(self._fetch_daily_history_sync, symbol)
        if not candles:
            return
        # Dedup con `>=` (no `>` como en intraday): el último daily candle
        # del día actual está en formación durante RTH y queremos refrescarlo
        # cada hora. CandleBuffer.push hace replace cuando ts_ms == buf[-1].ts_ms.
        last_ts = self._last_daily_ts_per_symbol.get(symbol, 0)
        new = [c for c in candles if c["time"] >= last_ts]
        if new:
            self._last_daily_ts_per_symbol[symbol] = new[-1]["time"]
        for c in new:
            norm = from_schwab_chart_equity(c)
            if norm is not None:
                self._daily_buffer.push(norm)

    async def _notify(self, ticker: str, data: dict) -> None:
        for fn in self._handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(ticker, data)
                else:
                    fn(ticker, data)
            except Exception as e:
                logger.error(f"[REST_POLLER] Handler error: {e}")

    # ── Fetch + parse ──────────────────────────────────────

    def _fetch_pricehistory_sync(self, symbol: str) -> list[dict]:
        """
        Pega a /pricehistory para `symbol` y devuelve list[candle_dict]
        en el shape esperado por from_schwab_chart_equity (key `time` en ms).
        Retorna [] en cualquier error — el siguiente cycle reintenta.
        """
        token = get_access_token()
        if not token:
            logger.warning(f"[REST_POLLER] sin access_token para {symbol}")
            return []
        # CANDLE-BUFFER-FIX 2026-06-04: period=1 con periodType=day devolvía
        # solo "the most recent complete trading day" = AYER (sesión RTH cerrada).
        # Hoy intraday-in-progress NO entraba. RSI quedaba calculado sobre data
        # de 19h+ vieja (confirmado vía [CANDLE_STALE] log).
        # Fix: usar startDate + endDate explícitos para today's session vía
        # epoch ms params. Schwab acepta startDate/endDate sobreescribiendo
        # period.
        import time as _t
        now_ms = int(_t.time() * 1000)
        # Window de 36h hacia atrás para garantizar today + ayer en buffer
        start_ms = now_ms - 36 * 3600 * 1000
        try:
            resp = requests.get(
                URL_PRICEHISTORY,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "symbol":                symbol,
                    "periodType":            "day",
                    "frequencyType":         "minute",
                    "frequency":             1,
                    "startDate":             start_ms,
                    "endDate":               now_ms,
                    "needExtendedHoursData": False,
                },
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 401:
                # Token loop refresca cada 25min; siguiente poll ya tiene fresh.
                logger.warning(f"[REST_POLLER] 401 {symbol}, retry next cycle")
                return []
            if resp.status_code != 200:
                logger.warning(
                    f"[REST_POLLER] HTTP {resp.status_code} para {symbol}: "
                    f"{resp.text[:120]}"
                )
                return []
            data = resp.json() or {}
        except Exception as e:
            logger.warning(f"[REST_POLLER] fetch failed {symbol}: {e}")
            return []

        raw = data.get("candles") or []
        out = []
        for c in raw:
            ts = c.get("datetime")
            if ts is None:
                continue
            out.append({
                "symbol": symbol,
                "time":   int(ts),
                "open":   c.get("open"),
                "high":   c.get("high"),
                "low":    c.get("low"),
                "close":  c.get("close"),
                "volume": c.get("volume"),
            })
        return out

    def _fetch_daily_history_sync(self, symbol: str) -> list[dict]:
        """
        Pega a /pricehistory con DAILY_PARAMS — retorna ~252 candles 1-day
        del último año. Mismo shape `{symbol, time(ms), open, high, low,
        close, volume}` que `_fetch_pricehistory_sync` para que el normalizer
        `from_schwab_chart_equity` funcione sin cambios.
        """
        token = get_access_token()
        if not token:
            logger.warning(f"[REST_POLLER] daily: sin access_token para {symbol}")
            return []
        try:
            resp = requests.get(
                URL_PRICEHISTORY,
                headers={"Authorization": f"Bearer {token}"},
                params={"symbol": symbol, **DAILY_PARAMS},
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 401:
                logger.warning(f"[REST_POLLER] daily 401 {symbol}, retry next cycle")
                return []
            if resp.status_code != 200:
                logger.warning(
                    f"[REST_POLLER] daily HTTP {resp.status_code} para {symbol}: "
                    f"{resp.text[:120]}"
                )
                return []
            data = resp.json() or {}
        except Exception as e:
            logger.warning(f"[REST_POLLER] daily fetch failed {symbol}: {e}")
            return []

        raw = data.get("candles") or []
        out = []
        for c in raw:
            ts = c.get("datetime")
            if ts is None:
                continue
            out.append({
                "symbol": symbol,
                "time":   int(ts),
                "open":   c.get("open"),
                "high":   c.get("high"),
                "low":    c.get("low"),
                "close":  c.get("close"),
                "volume": c.get("volume"),
            })
        return out

    def _fetch_vix_yesterday_sync(self):
        """
        Pega a /pricehistory para `$VIX` con últimos ~21 daily candles.
        Retorna `close` del PENÚLTIMO candle (= ayer al cierre RTH). El
        último elemento es el día actual en formación durante RTH; sólo
        post-close ese candle está cerrado.

        Retorna None en cualquier error.
        """
        token = get_access_token()
        if not token:
            logger.warning("[REST_POLLER] VIX: sin access_token")
            return None
        try:
            resp = requests.get(
                URL_PRICEHISTORY,
                headers={"Authorization": f"Bearer {token}"},
                params={"symbol": VIX_SYMBOL, **VIX_HISTORY_PARAMS},
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 401:
                logger.warning("[REST_POLLER] VIX 401, retry next cycle")
                return None
            if resp.status_code != 200:
                logger.warning(
                    f"[REST_POLLER] VIX HTTP {resp.status_code}: {resp.text[:120]}"
                )
                return None
            data = resp.json() or {}
        except Exception as e:
            logger.warning(f"[REST_POLLER] VIX fetch failed: {e}")
            return None

        candles = data.get("candles") or []
        if len(candles) < 2:
            logger.warning(
                f"[REST_POLLER] VIX: solo {len(candles)} candles, "
                f"no puedo computar yesterday"
            )
            return None
        # Ordenadas asc por datetime; penúltimo = ayer al cierre.
        try:
            return float(candles[-2].get("close"))
        except (TypeError, ValueError):
            return None

    def _fetch_quotes_sync(self, symbols: list[str]) -> dict[str, dict]:
        """
        Batch call a /quotes para todos los `symbols` en una sola request.
        Devuelve `{ticker: normalized_quote_dict}` con keys idénticas a las
        del L1 stream WS (bid/ask/last/bid_size/ask_size/volume/open/high/
        low/close/mark + ts en segundos). Retorna {} en cualquier error.
        """
        if not symbols:
            return {}
        token = get_access_token()
        if not token:
            logger.warning("[REST_POLLER] sin access_token para quotes")
            return {}
        try:
            resp = requests.get(
                URL_QUOTES,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "symbols":    ",".join(symbols),
                    "fields":     "quote",
                    "indicative": False,
                },
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 401:
                logger.warning("[REST_POLLER] 401 quotes, retry next cycle")
                return {}
            if resp.status_code != 200:
                logger.warning(
                    f"[REST_POLLER] quotes HTTP {resp.status_code}: "
                    f"{resp.text[:120]}"
                )
                return {}
            data = resp.json() or {}
        except Exception as e:
            logger.warning(f"[REST_POLLER] quotes fetch failed: {e}")
            return {}

        now = time.time()
        out: dict[str, dict] = {}
        for ticker in symbols:
            entry = data.get(ticker) or {}
            q = entry.get("quote") or {}
            if not q:
                continue
            out[ticker] = {
                "symbol":   ticker,
                "ts":       now,
                "bid":      q.get("bidPrice"),
                "ask":      q.get("askPrice"),
                "last":     q.get("lastPrice"),
                "bid_size": q.get("bidSize"),
                "ask_size": q.get("askSize"),
                "volume":   q.get("totalVolume"),
                "open":     q.get("openPrice"),
                "high":     q.get("highPrice"),
                "low":      q.get("lowPrice"),
                "close":    q.get("closePrice"),
                "mark":     q.get("mark"),
            }
        return out

    # ── Dedup + partial-candle filter ──────────────────────

    def _dedup_and_filter(self, symbol: str, candles: list[dict]) -> list[dict]:
        """
        Elimina:
          1. La última vela del batch (vela en formación, no cerrada).
          2. Velas con `time` <= último ts ya enviado (dedup vs polls previos).
        Mantiene orden ascendente y actualiza el cursor por símbolo.
        """
        if not candles:
            return []
        # 1) descartar partial last candle
        closed = candles[:-1]
        if not closed:
            return []
        # 2) dedup contra cursor por símbolo
        last_ts = self._last_ts_per_symbol.get(symbol, 0)
        new = [c for c in closed if c["time"] > last_ts]
        if new:
            self._last_ts_per_symbol[symbol] = new[-1]["time"]
        return new
