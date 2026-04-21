# ============================================================
#  eolo_common.macro.feeds
#
#  Polling de feeds macro (VIX / VIX9D / VIX3M / TICK / TRIN)
#  via Schwab quotes. Se consume desde estrategias Nivel 2
#  (#6, #7, #8, #22, #23) y desde el módulo de salida #19.
#
#  Diseño:
#    - MacroFeeds es un singleton liviano por proceso.
#    - Tiene un loop asyncio que cada POLL_SEC llama al quotes
#      fetcher (inyectado como callable) y cachea los últimos
#      N valores con timestamp.
#    - El quotes fetcher recibe una lista de símbolos Schwab y
#      debe devolver un dict {symbol: float}. Se inyecta desde
#      cada bot para no forzar un cliente Schwab acoplado.
#
#  Interfaz pública:
#    - feeds.latest("VIX") → float | None
#    - feeds.series("VIX", minutes=60) → pd.Series
#    - feeds.vrp(iv30_symbol="VIX") → float | None
#    - await feeds.start(quote_fn)
#    - feeds.stop()
#
#  Si Schwab no expone TICK/TRIN (varía por cuenta), los callers
#  caen en modo "None → HOLD" sin romper el resto del sistema.
# ============================================================
import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Deque, Dict, Optional

import numpy as np
import pandas as pd
from loguru import logger

from .symbols import MACRO_SYMBOLS, resolve_schwab


DEFAULT_POLL_SEC = 60          # 1 min suficiente para estrategias intraday
DEFAULT_RING_SIZE = 24 * 60    # 24h de samples a 1 min


@dataclass
class _Ring:
    """Ring buffer de (ts_epoch, value) para un símbolo."""
    maxlen: int
    data: Deque = field(default_factory=deque)

    def push(self, ts: float, value: float):
        self.data.append((ts, value))
        while len(self.data) > self.maxlen:
            self.data.popleft()

    def latest(self) -> Optional[float]:
        if not self.data:
            return None
        return self.data[-1][1]

    def series(self, minutes: int) -> pd.Series:
        if not self.data:
            return pd.Series(dtype=float)
        cutoff = time.time() - minutes * 60
        pts = [(pd.Timestamp(ts, unit="s", tz="UTC"), v) for ts, v in self.data if ts >= cutoff]
        if not pts:
            return pd.Series(dtype=float)
        idx, vals = zip(*pts)
        return pd.Series(list(vals), index=list(idx))


def realized_vol_annualized(prices: pd.Series, bars_per_day: int = 78) -> Optional[float]:
    """
    Vol realizada anualizada en %, usando returns de `prices`.
    bars_per_day: 78 por defecto (5 min × 6.5 h).
    """
    if prices is None or len(prices) < 10:
        return None
    returns = prices.pct_change().dropna()
    if returns.empty:
        return None
    sigma = float(returns.std())
    if sigma <= 0:
        return None
    return sigma * float(np.sqrt(252 * bars_per_day)) * 100.0


def compute_vrp(iv30_pct: float, rv_pct: float) -> Optional[float]:
    """VRP = IV30 - RV (ambos en %)."""
    if iv30_pct is None or rv_pct is None:
        return None
    return float(iv30_pct) - float(rv_pct)


class MacroFeeds:
    """
    Cache + polling loop. Un solo MacroFeeds por proceso.

    Uso:
        feeds = MacroFeeds(poll_sec=60)
        await feeds.start(quote_fn=mi_fetcher)
        v = feeds.latest("VIX")
    """

    def __init__(self, poll_sec: int = DEFAULT_POLL_SEC, ring_size: int = DEFAULT_RING_SIZE):
        self.poll_sec = poll_sec
        self.ring_size = ring_size
        self._rings: Dict[str, _Ring] = {
            name: _Ring(maxlen=ring_size) for name in MACRO_SYMBOLS.keys()
        }
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ── Updates ───────────────────────────────────────────

    def push(self, name: str, value: float, ts: Optional[float] = None):
        name = name.upper()
        if name not in self._rings:
            logger.debug(f"[MACRO] push para símbolo desconocido {name} ignorado")
            return
        if value is None:
            return
        self._rings[name].push(ts if ts is not None else time.time(), float(value))

    # ── Accessors ─────────────────────────────────────────

    def latest(self, name: str) -> Optional[float]:
        name = name.upper()
        ring = self._rings.get(name)
        return ring.latest() if ring else None

    def series(self, name: str, minutes: int = 60) -> pd.Series:
        name = name.upper()
        ring = self._rings.get(name)
        return ring.series(minutes) if ring else pd.Series(dtype=float)

    def snapshot(self) -> dict:
        """Foto de los latest values (útil para dashboard / logs)."""
        return {name: ring.latest() for name, ring in self._rings.items()}

    # ── Utilidades derivadas ──────────────────────────────

    def term_structure_inverted(self) -> Optional[bool]:
        """VIX > VIX3M ⇒ contango invertido ⇒ estrés sostenido."""
        vix = self.latest("VIX")
        vix3m = self.latest("VIX3M")
        if vix is None or vix3m is None:
            return None
        return vix > vix3m

    def vix_zscore(self, lookback_minutes: int = 60 * 8) -> Optional[float]:
        s = self.series("VIX", minutes=lookback_minutes)
        if len(s) < 20:
            return None
        mu = float(s.mean())
        sd = float(s.std())
        if sd <= 0:
            return None
        return (float(s.iloc[-1]) - mu) / sd

    # ── Loop ──────────────────────────────────────────────

    async def start(self, quote_fn: Callable[[list], Awaitable[Dict[str, float]]]):
        """
        quote_fn:  async callable(list[str]) -> dict[str, float]
                   Debe devolver last price por símbolo Schwab.
        """
        if self._running:
            logger.warning("[MACRO] start() llamado dos veces, ignorado")
            return
        self._running = True
        symbols = [resolve_schwab(n) for n in MACRO_SYMBOLS.keys()]
        logger.info(f"[MACRO] Polling cada {self.poll_sec}s | símbolos={symbols}")

        async def _loop():
            while self._running:
                t0 = time.time()
                try:
                    quotes = await quote_fn(symbols)
                    for name, meta in MACRO_SYMBOLS.items():
                        for key in [meta["schwab"], *meta["aliases"]]:
                            if key in quotes and quotes[key] is not None:
                                self.push(name, quotes[key], ts=t0)
                                break
                except Exception as e:
                    logger.warning(f"[MACRO] poll falló: {e}")
                # sleep incremental para respetar stop()
                for _ in range(self.poll_sec * 10):
                    if not self._running:
                        break
                    await asyncio.sleep(0.1)

        self._task = asyncio.create_task(_loop())

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
