# ============================================================
#  EOLO Crypto — Screener dinámico de small/mid-caps
#
#  Cada SCREENER_INTERVAL_SEC (default 10 min) consulta
#  GET /api/v3/ticker/24hr (devuelve ~2000 pares USDT/otros)
#  y devuelve los top N candidatos que pasan:
#    • quoteVolume en [MIN, MAX] — liquidez suficiente pero no top-cap
#    • priceChangePercent en [MIN, MAX] — momentum sano, no pump suicida
#    • Termina en USDT (ignoramos BUSD/TUSD/BTC-quote)
#    • No están en SCREENER_EXCLUDE ni en CORE_UNIVERSE
#
#  El orquestador usa este output para:
#    stream.add_symbols(nuevos) + stream.remove_symbols(salieron)
# ============================================================
import asyncio
import time

from loguru import logger

import settings
from helpers import binance_get


class BinanceScreener:
    """
    Screener periódico que actualiza un set de candidatos de
    small/mid-caps con momentum. No tradea directamente — solo
    emite una lista que el orquestador usa.
    """

    def __init__(self, interval: int = None, top_n: int = None):
        self.interval    = interval or settings.SCREENER_INTERVAL_SEC
        self.top_n       = top_n    or settings.SCREENER_TOP_N
        self._running    = False
        self._handlers   = []       # fn(active_list, added, removed)
        self._current    = set()    # últimos small-caps activos (excluye core)
        self._last_run   = 0

    # ── API pública ───────────────────────────────────────

    def add_handler(self, fn):
        """fn(active_list: list[str], added: list[str], removed: list[str])."""
        self._handlers.append(fn)

    def get_active(self) -> list[str]:
        return sorted(self._current)

    def stop(self):
        self._running = False
        logger.info("[SCREENER] Deteniendo...")

    # ── Loop principal ────────────────────────────────────

    async def start(self):
        if not settings.SCREENER_ENABLED:
            logger.info("[SCREENER] Deshabilitado en settings — no corre.")
            return

        self._running = True
        logger.info(
            f"[SCREENER] Arrancando | interval={self.interval}s | top_n={self.top_n} "
            f"| vol=[${settings.SCREENER_MIN_VOLUME/1e6:.0f}M, "
            f"${settings.SCREENER_MAX_VOLUME/1e6:.0f}M] "
            f"| change=[{settings.SCREENER_MIN_CHANGE}%, {settings.SCREENER_MAX_CHANGE}%]"
        )

        while self._running:
            try:
                await self._scan()
            except Exception as e:
                import traceback
                logger.error(
                    f"[SCREENER] Error en scan: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )

            # Espera interruptible
            for _ in range(self.interval * 10):
                if not self._running:
                    break
                await asyncio.sleep(0.1)

    async def _scan(self):
        """Ejecuta un scan completo y notifica handlers si hay cambios."""
        loop = asyncio.get_event_loop()
        # El HTTP GET es bloqueante → threadpool
        tickers = await loop.run_in_executor(None, self._fetch_tickers)

        candidates = self._rank(tickers)
        new_set = set(candidates)
        added   = sorted(new_set - self._current)
        removed = sorted(self._current - new_set)

        self._current = new_set
        self._last_run = time.time()

        if added or removed:
            logger.info(
                f"[SCREENER] Universe cambió | "
                f"+{len(added)} {added} | -{len(removed)} {removed} | "
                f"activos ahora: {sorted(new_set)}"
            )
        else:
            logger.debug(
                f"[SCREENER] Sin cambios | activos: {sorted(new_set)}"
            )

        # Notify handlers
        for fn in self._handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(sorted(new_set), added, removed)
                else:
                    fn(sorted(new_set), added, removed)
            except Exception as e:
                logger.error(f"[SCREENER] Handler error: {e}")

    # ── Fetch + filter ────────────────────────────────────

    def _fetch_tickers(self) -> list[dict]:
        """GET /api/v3/ticker/24hr (sin params = TODOS los pares)."""
        # Este endpoint es HEAVY (weight 40 sin symbol), respetar rate limit.
        data = binance_get("/api/v3/ticker/24hr", signed=False, timeout=15)
        return data if isinstance(data, list) else []

    def _rank(self, tickers: list[dict]) -> list[str]:
        """
        Aplica filtros y retorna los top N símbolos por un score compuesto:
          score = priceChangePercent × log10(quoteVolume)
        """
        import math
        core = set(settings.CORE_UNIVERSE)
        excl = settings.SCREENER_EXCLUDE

        candidates = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in core or symbol in excl:
                continue
            # Descarta leveraged tokens (terminan en UPUSDT, DOWNUSDT, BULL, BEAR)
            base = symbol[:-4]  # sin "USDT"
            if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue

            try:
                quote_vol    = float(t.get("quoteVolume", 0))
                change_pct   = float(t.get("priceChangePercent", 0))
                last_price   = float(t.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue

            if quote_vol < settings.SCREENER_MIN_VOLUME:
                continue
            if quote_vol > settings.SCREENER_MAX_VOLUME:
                continue
            if change_pct < settings.SCREENER_MIN_CHANGE:
                continue
            if change_pct > settings.SCREENER_MAX_CHANGE:
                continue
            if last_price <= 0:
                continue

            score = change_pct * math.log10(max(quote_vol, 1.0))
            candidates.append((score, symbol, change_pct, quote_vol))

        candidates.sort(reverse=True)
        top = [s for _, s, _, _ in candidates[: self.top_n]]

        # Log top picks para visibilidad
        if candidates:
            sample = candidates[: self.top_n]
            logger.debug(
                "[SCREENER] Top picks: "
                + ", ".join(f"{s}(chg={c:+.1f}%, vol=${v/1e6:.0f}M)"
                            for _, s, c, v in sample)
            )

        return top
