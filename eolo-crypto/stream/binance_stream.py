# ============================================================
#  EOLO Crypto — Binance WebSocket Stream (combined klines)
#
#  Se conecta a wss://stream.binance.com:9443/stream?streams=...
#  con TODOS los pares suscriptos en una sola conexión
#  (combined stream). Binance permite hasta 1024 streams por
#  conexión, así que no hay límite práctico.
#
#  IMPORTANTE:
#  - Binance cierra el WS cada ~24h por mantenimiento → el
#    loop de reconexión lo maneja.
#  - La suscripción se puede modificar en vivo via mensaje
#    SUBSCRIBE/UNSUBSCRIBE, lo que usa el screener cuando
#    agrega/remueve small-caps al universo activo.
#
#  Docs: https://binance-docs.github.io/apidocs/spot/en/
#        #websocket-market-streams
# ============================================================
import asyncio
import json
import time

from loguru import logger

try:
    import websockets
except ImportError:
    websockets = None

import settings


class BinanceStream:
    """
    Cliente WebSocket para Binance combined streams (market data).
    - Suscribe a kline_1m por cada par del universo
    - Mantiene la conexión con reconexión + backoff
    - Permite agregar/remover pares en runtime (SUBSCRIBE msg)
    - Invoca handlers registrados con add_handler(fn)
    """

    def __init__(self, symbols: list[str], interval: str = None):
        self.symbols    = [s.upper() for s in symbols]
        self.interval   = interval or settings.KLINE_INTERVAL
        self._ws        = None
        self._running   = False
        self._handlers  = []
        self._req_id    = 0
        self._lock      = asyncio.Lock()

    # ── API pública ───────────────────────────────────────

    def add_handler(self, fn):
        """Registrar callback async fn(kline_dict) que recibe cada vela cerrada."""
        self._handlers.append(fn)

    async def add_symbols(self, new_symbols: list[str]):
        """Agregar pares al stream en vivo (sin reconectar)."""
        async with self._lock:
            to_add = [s.upper() for s in new_symbols if s.upper() not in self.symbols]
            if not to_add:
                return
            self.symbols.extend(to_add)
            if self._ws is not None:
                await self._send_subscribe(to_add)
            logger.info(f"[BSTREAM] + {len(to_add)} symbols: {to_add}")

    async def remove_symbols(self, old_symbols: list[str]):
        """Remover pares del stream en vivo (para rotar universe del screener)."""
        async with self._lock:
            to_remove = [s.upper() for s in old_symbols if s.upper() in self.symbols]
            if not to_remove:
                return
            # Proteger core universe: nunca sacar BTC/ETH aunque screener lo pida
            for s in list(to_remove):
                if s in settings.CORE_UNIVERSE:
                    to_remove.remove(s)
            for s in to_remove:
                self.symbols.remove(s)
            if self._ws is not None:
                await self._send_unsubscribe(to_remove)
            logger.info(f"[BSTREAM] - {len(to_remove)} symbols: {to_remove}")

    def get_symbols(self) -> list[str]:
        return list(self.symbols)

    def stop(self):
        self._running = False
        logger.info("[BSTREAM] Deteniendo stream...")

    # ── Loop principal con backoff ────────────────────────

    async def start(self):
        if websockets is None:
            raise ImportError("Falta dependencia: pip install websockets")

        self._running = True
        logger.info(
            f"[BSTREAM] Iniciando combined stream | {len(self.symbols)} pares "
            f"| interval={self.interval} | mode={settings.BINANCE_MODE}"
        )

        backoff = settings.WS_RECONNECT_BACKOFF_MIN
        while self._running:
            t0 = time.time()
            try:
                await self._connect_and_run()
                reason = "cerrada limpiamente"
            except Exception as e:
                reason = f"caída: {e}"

            if not self._running:
                break

            elapsed = time.time() - t0
            if elapsed >= 30:
                backoff = settings.WS_RECONNECT_BACKOFF_MIN
            else:
                backoff = min(backoff * 2, settings.WS_RECONNECT_BACKOFF_MAX)

            logger.warning(
                f"[BSTREAM] Conexión {reason} tras {elapsed:.1f}s — "
                f"reconectando en {backoff}s..."
            )
            await asyncio.sleep(backoff)

    # ── Conexión + loop de mensajes ───────────────────────

    def _streams_url(self) -> str:
        """Construye URL combined: /stream?streams=btcusdt@kline_1m/ethusdt@kline_1m/..."""
        streams = "/".join(f"{s.lower()}@kline_{self.interval}" for s in self.symbols)
        base = settings.get_endpoint("ws_combined")
        return f"{base}?streams={streams}"

    async def _connect_and_run(self):
        url = self._streams_url()
        logger.info(f"[BSTREAM] Conectando ({len(self.symbols)} streams)...")

        async with websockets.connect(
            url,
            ping_interval=180,      # Binance pingeas cada 3 min; cliente responde pong
            ping_timeout=600,       # 10 min para detectar conexión muerta
            max_size=10 * 1024 * 1024,  # 10MB por mensaje (por si llegan burst)
        ) as ws:
            self._ws = ws
            logger.info("[BSTREAM] WebSocket conectado ✅")

            async for raw in ws:
                if not self._running:
                    break
                await self._handle_raw(raw)

        self._ws = None

    async def _handle_raw(self, raw: str | bytes):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[BSTREAM] Mensaje no JSON: {raw[:100]}")
            return

        # Respuestas a SUBSCRIBE/UNSUBSCRIBE: {"result": null, "id": N}
        if "result" in msg and "id" in msg:
            logger.debug(f"[BSTREAM] Control response id={msg['id']}: {msg['result']}")
            return

        # Combined stream format: {"stream": "btcusdt@kline_1m", "data": {...}}
        data = msg.get("data", msg)
        event = data.get("e", "")

        if event == "kline":
            await self._handle_kline(data)
        else:
            logger.trace(f"[BSTREAM] Evento ignorado: {event}")

    async def _handle_kline(self, data: dict):
        """
        Procesa un evento kline. Solo notificamos velas CERRADAS (k.x=true)
        — las velas intermedias son ruido para estrategias OHLCV.
        """
        k = data.get("k", {})
        if not k.get("x"):         # x=true cuando la vela cerró
            return

        candle = {
            "symbol":   k.get("s", ""),
            "interval": k.get("i", ""),
            "open_ms":  k.get("t"),
            "close_ms": k.get("T"),
            "open":     float(k["o"]),
            "high":     float(k["h"]),
            "low":      float(k["l"]),
            "close":    float(k["c"]),
            "volume":   float(k["v"]),
            "trades":   int(k.get("n", 0)),
            "closed":   True,
        }

        for fn in self._handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(candle)
                else:
                    fn(candle)
            except Exception as e:
                import traceback
                logger.error(
                    f"[BSTREAM] Handler error para {candle['symbol']}: "
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                )

    # ── Control del stream (SUBSCRIBE/UNSUBSCRIBE en vivo) ─

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send_subscribe(self, symbols: list[str]):
        msg = {
            "method": "SUBSCRIBE",
            "params": [f"{s.lower()}@kline_{self.interval}" for s in symbols],
            "id":     self._next_id(),
        }
        await self._ws.send(json.dumps(msg))
        logger.debug(f"[BSTREAM] SUBSCRIBE enviado: {symbols}")

    async def _send_unsubscribe(self, symbols: list[str]):
        msg = {
            "method": "UNSUBSCRIBE",
            "params": [f"{s.lower()}@kline_{self.interval}" for s in symbols],
            "id":     self._next_id(),
        }
        await self._ws.send(json.dumps(msg))
        logger.debug(f"[BSTREAM] UNSUBSCRIBE enviado: {symbols}")
