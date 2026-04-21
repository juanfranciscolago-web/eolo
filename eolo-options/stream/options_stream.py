# ============================================================
#  EOLO v2 — Schwab Streaming WebSocket
#
#  Conecta al Schwab Streaming API y recibe quotes en tiempo
#  real tick a tick para los tickers configurados.
#
#  Schwab Streaming API docs:
#    POST /v1/userPreference → obtiene streamer info
#    WSS wss://streamer-api.schwab.com/ws → stream
#
#  Servicios usados:
#    LEVELONE_EQUITIES : quotes L1 (bid, ask, last, volume)
#    CHART_EQUITY      : velas OHLCV en tiempo real
#
#  Uso:
#    stream = SchwabStream(tickers=["SOXL","TSLL","SPY","QQQ"])
#    await stream.start(on_quote=callback)
# ============================================================
import json
import asyncio
import time
from datetime import datetime
from loguru import logger

try:
    import websockets
except ImportError:
    websockets = None

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from helpers import get_access_token

# ── Constantes ────────────────────────────────────────────
STREAMER_URL     = "wss://streamer-api.schwab.com/ws"
SCHWAB_API_BASE  = "https://api.schwabapi.com"

# Campos L1 Equities que nos interesan:
# 0=Symbol, 1=Bid, 2=Ask, 3=Last, 4=BidSize, 5=AskSize
# 8=Volume, 9=Open, 10=High, 11=Low, 12=Close, 31=Mark
L1_FIELDS = "0,1,2,3,4,5,8,9,10,11,12,31"


class SchwabStream:
    """
    Cliente WebSocket para Schwab Streaming API.
    Mantiene la conexión activa, reconecta automáticamente
    y distribuye los quotes a los handlers registrados.
    """

    def __init__(self, tickers: list[str]):
        self.tickers      = [t.upper() for t in tickers]
        self._ws          = None
        self._request_id  = 0
        self._running     = False
        self._quotes      = {}   # último quote por ticker
        self._handlers    = []   # callbacks on_quote(ticker, quote_dict)
        self._streamer_info = None

    # ── Auth y streamer info ───────────────────────────────

    def _get_streamer_info(self) -> dict:
        """
        Obtiene la URL y credenciales del streamer via
        GET /v1/userPreference (requiere access token).
        """
        import requests
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}",
                   "Accept": "application/json"}
        url = "https://api.schwabapi.com/trader/v1/userPreference"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        accounts  = data.get("accounts", [{}])
        streamer  = data.get("streamerInfo", [{}])[0]
        return {
            "url":          streamer.get("streamerSocketUrl", STREAMER_URL),
            "customer_id":  streamer.get("schwabClientCustomerId", ""),
            "correl_id":    streamer.get("schwabClientCorrelId", ""),
            "channel":      streamer.get("schwabClientChannel", ""),
            "function_id":  streamer.get("schwabClientFunctionId", ""),
            "account_id":   accounts[0].get("accountNumber", "") if accounts else "",
        }

    # ── Protocolo Streaming ────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _login_request(self, info: dict) -> dict:
        token = get_access_token()
        return {
            "requests": [{
                "service":    "ADMIN",
                "requestid":  str(self._next_id()),
                "command":    "LOGIN",
                "SchwabClientCustomerId": info["customer_id"],
                "SchwabClientCorrelId":   info["correl_id"],
                "parameters": {
                    "Authorization": token,
                    "SchwabClientChannel":    info["channel"],
                    "SchwabClientFunctionId": info["function_id"],
                }
            }]
        }

    def _subscribe_l1(self) -> dict:
        symbols = ",".join(self.tickers)
        return {
            "requests": [{
                "service":    "LEVELONE_EQUITIES",
                "requestid":  str(self._next_id()),
                "command":    "SUBS",
                "SchwabClientCustomerId": self._streamer_info["customer_id"],
                "SchwabClientCorrelId":   self._streamer_info["correl_id"],
                "parameters": {
                    "keys":   symbols,
                    "fields": L1_FIELDS,
                }
            }]
        }

    def _subscribe_chart(self) -> dict:
        """Suscripción a velas de 1 minuto (CHART_EQUITY)."""
        symbols = ",".join(self.tickers)
        return {
            "requests": [{
                "service":    "CHART_EQUITY",
                "requestid":  str(self._next_id()),
                "command":    "SUBS",
                "SchwabClientCustomerId": self._streamer_info["customer_id"],
                "SchwabClientCorrelId":   self._streamer_info["correl_id"],
                "parameters": {
                    "keys":   symbols,
                    "fields": "0,1,2,3,4,5,6,7,8",
                    # 0=key, 1=seq, 2=time, 3=open, 4=high, 5=low, 6=close, 7=vol, 8=chart_day
                }
            }]
        }

    # ── Parser de mensajes ─────────────────────────────────

    def _parse_l1(self, content: list) -> list[dict]:
        """Parsea mensajes LEVELONE_EQUITIES a dicts normalizados."""
        field_map = {
            "0": "symbol", "1": "bid",  "2": "ask",   "3": "last",
            "4": "bid_size","5": "ask_size", "8": "volume",
            "9": "open", "10": "high", "11": "low", "12": "close",
            "31": "mark"
        }
        quotes = []
        for item in content:
            q = {"symbol": item.get("key", ""), "ts": time.time()}
            for k, name in field_map.items():
                if k in item:
                    q[name] = item[k]
            quotes.append(q)
        return quotes

    def _parse_chart(self, content: list) -> list[dict]:
        """Parsea mensajes CHART_EQUITY a velas OHLCV."""
        candles = []
        for item in content:
            candle = {
                "symbol":   item.get("key", ""),
                "time":     item.get("2", 0),
                "open":     item.get("3"),
                "high":     item.get("4"),
                "low":      item.get("5"),
                "close":    item.get("6"),
                "volume":   item.get("7"),
                "chart_day": item.get("8"),
            }
            candles.append(candle)
        return candles

    # ── Loop principal ─────────────────────────────────────

    def add_handler(self, fn):
        """Registra un callback fn(ticker, quote) para recibir quotes."""
        self._handlers.append(fn)

    async def _notify(self, ticker: str, data: dict):
        for fn in self._handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(ticker, data)
                else:
                    fn(ticker, data)
            except Exception as e:
                logger.error(f"[STREAM] Handler error: {e}")

    async def start(self):
        """
        Inicia la conexión WebSocket. Reconecta automáticamente
        si se cae. Corre indefinidamente hasta stop().
        """
        if websockets is None:
            raise ImportError("Instalá: pip install websockets")

        self._running = True
        logger.info(f"[STREAM] Conectando para: {self.tickers}")

        # Backoff exponencial: si el server nos tira repetidamente (ej. otro
        # cliente peleando por el slot), no hammereamos. Se resetea apenas
        # una conexión dura más de MIN_STABLE segundos.
        MIN_STABLE = 30                # segundos mínimos para considerar "conexión estable"
        backoff = 5                    # delay inicial
        MAX_BACKOFF = 120              # tope (2 minutos)

        while self._running:
            t0 = time.time()
            try:
                await self._connect_and_run()
                # Cierre limpio del server (sin excepción). Aún así esperamos
                # antes de reconectar para no hammerear.
                reason = "cerrada limpiamente"
            except Exception as e:
                reason = f"caída: {e}"

            if not self._running:
                break

            elapsed = time.time() - t0
            if elapsed >= MIN_STABLE:
                backoff = 5            # reset: la conexión aguantó bien
            else:
                backoff = min(backoff * 2, MAX_BACKOFF)

            logger.warning(
                f"[STREAM] Conexión {reason} tras {elapsed:.1f}s "
                f"— reconectando en {backoff}s..."
            )
            await asyncio.sleep(backoff)

    async def _connect_and_run(self):
        self._streamer_info = self._get_streamer_info()
        url = self._streamer_info["url"]
        logger.info(f"[STREAM] Conectando a {url}")

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            logger.info("[STREAM] WebSocket conectado ✅")

            # 1. Login
            await ws.send(json.dumps(self._login_request(self._streamer_info)))
            resp = json.loads(await ws.recv())
            if resp.get("response", [{}])[0].get("content", {}).get("code") != 0:
                raise ConnectionError(f"Login fallido: {resp}")
            logger.info("[STREAM] Login OK ✅")

            # 2. Suscribir L1 + Chart
            await ws.send(json.dumps(self._subscribe_l1()))
            await ws.send(json.dumps(self._subscribe_chart()))
            logger.info(f"[STREAM] Suscripto a L1 + Chart para {self.tickers}")

            # 3. Loop de mensajes
            async for message in ws:
                if not self._running:
                    break
                await self._handle_message(json.loads(message))

    async def _handle_message(self, msg: dict):
        # Procesar data frames
        for frame in msg.get("data", []):
            service = frame.get("service", "")
            content = frame.get("content", [])

            if service == "LEVELONE_EQUITIES":
                for q in self._parse_l1(content):
                    ticker = q.get("symbol", "")
                    if ticker:
                        self._quotes[ticker] = q
                        await self._notify(ticker, {"type": "quote", **q})

            elif service == "CHART_EQUITY":
                for candle in self._parse_chart(content):
                    ticker = candle.get("symbol", "")
                    if ticker:
                        await self._notify(ticker, {"type": "candle", **candle})

        # Heartbeat
        if "notify" in msg:
            for notif in msg["notify"]:
                if notif.get("heartbeat"):
                    logger.debug(f"[STREAM] ♥ heartbeat {notif['heartbeat']}")

    def stop(self):
        self._running = False
        logger.info("[STREAM] Deteniendo stream...")

    def get_quote(self, ticker: str) -> dict | None:
        """Retorna el último quote recibido para un ticker."""
        return self._quotes.get(ticker.upper())

    def get_all_quotes(self) -> dict:
        return dict(self._quotes)
