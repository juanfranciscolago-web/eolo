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
# ============================================================
import asyncio
import time
from typing import Callable

import requests
from loguru import logger

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from helpers import get_access_token

# ── Constantes ────────────────────────────────────────────
URL_PRICEHISTORY = "https://api.schwabapi.com/marketdata/v1/pricehistory"
URL_QUOTES       = "https://api.schwabapi.com/marketdata/v1/quotes"
POLL_INTERVAL_SEC = 5
TIMEOUT_SEC = 8


class SchwabRestPoller:
    """
    Poller REST drop-in replacement de SchwabStream para CROP.

    Dos loops paralelos a 5s:
      • candles 1-min vía /pricehistory (alimenta CandleBuffer)
      • quotes L1 vía /quotes (alimenta dashboard bid/ask/last/mark)
    """

    def __init__(self, tickers: list[str]):
        self.tickers = [t.upper() for t in tickers]
        self._handlers: list[Callable] = []
        self._running = False
        # Dedup: último ts_ms enviado al handler por símbolo.
        # CandleBuffer.push solo dedupa contra buf[-1], no contra histórico —
        # sin este tracking re-pusheamos ~390 candles del día cada 5s.
        self._last_ts_per_symbol: dict[str, int] = {}

    # ── Compat con SchwabStream ────────────────────────────

    def add_handler(self, fn: Callable) -> None:
        """Registra callback fn(ticker, data_dict)."""
        self._handlers.append(fn)

    def stop(self) -> None:
        self._running = False
        logger.info("[REST_POLLER] Deteniendo polling...")

    # ── Loop principal ─────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        logger.info(
            f"[REST_POLLER] Iniciando para {self.tickers} "
            f"(intervalo {POLL_INTERVAL_SEC}s, candles + quotes)"
        )
        # Candle loop = 1 call por ticker; quote loop = 1 call batched
        # para todos los tickers. Corren en paralelo, mismo cadencia.
        await asyncio.gather(
            self._candle_poll_loop(),
            self._quote_poll_loop(),
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
        try:
            resp = requests.get(
                URL_PRICEHISTORY,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "symbol":                symbol,
                    "periodType":            "day",
                    "period":                1,
                    "frequencyType":         "minute",
                    "frequency":             1,
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
