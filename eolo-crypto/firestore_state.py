# ============================================================
#  EOLO Crypto — Firestore state writer
#
#  Cada N segundos el orquestador llama a StateWriter.push(state)
#  y éste lo escribe en Firestore → colección
#  `eolo-crypto-state`, doc `current`.
#
#  El dashboard (cuando lo hagamos) lee este doc para mostrar:
#    - pares activos (core + screener)
#    - posiciones abiertas
#    - últimas decisiones de Claude
#    - balance y P&L del día
#
#  Mismo patrón que eolo-options/dashboard.
# ============================================================
import asyncio
import time
from datetime import datetime, timezone

from loguru import logger

import settings
from helpers import firestore_write


class StateWriter:
    """
    Thread-safe-ish (singleton per proceso) state writer. El
    orquestador llena el estado interno y, cada WRITE_INTERVAL_SEC,
    lo flushea a Firestore.
    """

    WRITE_INTERVAL_SEC = 10

    def __init__(self):
        self._state: dict = {
            "service":          "eolo-bot-crypto",
            "mode":             settings.BINANCE_MODE,
            "ts_updated":       None,
            "universe":         [],          # core + screener activos
            "core_universe":    list(settings.CORE_UNIVERSE),
            "screener_active":  [],
            "balance_usdt":     0.0,
            "open_positions":   {},
            "last_decisions":   [],          # últimas 20 decisiones Claude
            "last_signals":     [],          # últimas 50 signals de estrategias
            "daily_pnl_usdt":   0.0,
            "daily_cost_usd":   0.0,
            "errors_24h":       0,
            # Multi-TF / confluencia
            "confluence_snapshot": {},        # última foto del filter
            # Trading hours schedule (start/end/auto_close/enabled)
            "schedule": {},                   # payload de format_schedule_for_api
            "market_open": True,              # True si dentro del window (crypto default 24/7)
        }
        self._running = False

    # ── Updaters (llamados desde el orquestador) ──────────

    def set_universe(self, active: list[str], screener_active: list[str] = None):
        self._state["universe"] = sorted(active)
        if screener_active is not None:
            self._state["screener_active"] = sorted(screener_active)

    def set_balance(self, usdt: float):
        self._state["balance_usdt"] = round(usdt, 2)

    def set_positions(self, positions: dict):
        """positions: dict symbol → {qty, entry_price, ts, strategy}"""
        out = {}
        for sym, p in positions.items():
            out[sym] = {
                "qty":         p.get("qty"),
                "entry_price": p.get("entry_price"),
                "strategy":    p.get("strategy"),
                "ts":          p.get("ts"),
            }
        self._state["open_positions"] = out

    def push_decision(self, decision: dict):
        """Agrega una decisión Claude al rolling log."""
        entry = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "action":     decision.get("action"),
            "symbol":     decision.get("symbol"),
            "confidence": decision.get("confidence"),
            "reason":     decision.get("reason"),
        }
        self._state["last_decisions"].insert(0, entry)
        self._state["last_decisions"] = self._state["last_decisions"][:20]

    def push_signal(self, signal: dict):
        """Agrega una signal de estrategia al rolling log (multi-TF aware)."""
        entry = {
            "ts":        datetime.now(timezone.utc).isoformat(),
            "symbol":    signal.get("symbol"),
            "strategy":  signal.get("strategy") or signal.get("name"),
            "signal":    signal.get("signal"),
            "reason":    signal.get("reason"),
            "timeframe": signal.get("timeframe", 1),
        }
        self._state["last_signals"].insert(0, entry)
        self._state["last_signals"] = self._state["last_signals"][:50]

    def get_signals(self, limit: int = 20) -> list[dict]:
        """
        Devuelve las últimas `limit` signals del rolling log (más recientes
        primero). Usado por ClaudeBotCrypto para el gate de costos.
        """
        signals = self._state.get("last_signals", []) or []
        return signals[:limit]

    def set_confluence_snapshot(self, snapshot: dict):
        """Última foto del ConfluenceFilter (para debug/dashboard)."""
        self._state["confluence_snapshot"] = snapshot or {}

    def set_schedule(self, schedule_payload: dict, market_open: bool):
        """
        Setea el payload de trading_hours (format_schedule_for_api) y el flag
        `market_open` (True si dentro del window). El dashboard lee estos dos
        para mostrar el banner "Pause time limit" y los campos del modal Config.
        """
        self._state["schedule"] = schedule_payload or {}
        self._state["market_open"] = bool(market_open)

    def set_daily_pnl(self, pnl_usdt: float):
        self._state["daily_pnl_usdt"] = round(pnl_usdt, 2)

    def set_daily_cost(self, cost_usd: float):
        self._state["daily_cost_usd"] = round(cost_usd, 4)

    def inc_errors(self):
        self._state["errors_24h"] = self._state.get("errors_24h", 0) + 1

    # ── Loop de flush ─────────────────────────────────────

    def stop(self):
        self._running = False

    async def start(self):
        """Corre en background: cada WRITE_INTERVAL_SEC flushea a Firestore."""
        self._running = True
        logger.info(f"[STATE] Writer arrancado | flush cada {self.WRITE_INTERVAL_SEC}s")
        while self._running:
            try:
                await self._flush_once()
            except Exception as e:
                logger.warning(f"[STATE] Flush falló: {e}")
            for _ in range(self.WRITE_INTERVAL_SEC * 10):
                if not self._running:
                    break
                await asyncio.sleep(0.1)

    async def _flush_once(self):
        self._state["ts_updated"] = datetime.now(timezone.utc).isoformat()
        self._state["mode"]       = settings.BINANCE_MODE
        loop = asyncio.get_event_loop()
        # Firestore client es sync → tirarlo a threadpool
        await loop.run_in_executor(
            None,
            firestore_write,
            settings.FIRESTORE_STATE_COLLECTION,
            settings.FIRESTORE_STATE_DOC,
            dict(self._state),
        )

    # ── Accessor ──────────────────────────────────────────

    def snapshot(self) -> dict:
        """Devuelve una copia del estado actual (para debugging)."""
        return dict(self._state)
