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
#  Data dict emitido (mismo shape que SchwabStream CHART_EQUITY):
#    {"type": "candle", "symbol": str, "time": int (ms),
#     "open": float, "high": float, "low": float,
#     "close": float, "volume": float}
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
POLL_INTERVAL_SEC = 5
TIMEOUT_SEC = 8


class SchwabRestPoller:
    """
    Poller REST de 1-min candles. Drop-in replacement de SchwabStream
    para CROP. NO maneja L1 quotes (dashboard pierde bid/ask en vivo —
    follow-up en sprint posterior si se prioriza).
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
            f"(intervalo {POLL_INTERVAL_SEC}s)"
        )
        while self._running:
            t0 = time.time()
            try:
                await asyncio.gather(*(
                    self._poll_ticker(t) for t in self.tickers
                ), return_exceptions=True)
            except Exception as e:
                logger.warning(f"[REST_POLLER] Error en poll cycle: {e}")
            elapsed = time.time() - t0
            sleep_for = max(0.5, POLL_INTERVAL_SEC - elapsed)
            await asyncio.sleep(sleep_for)

    async def _poll_ticker(self, symbol: str) -> None:
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
