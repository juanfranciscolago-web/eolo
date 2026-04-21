# ============================================================
#  EOLO Crypto — Executor de órdenes
#
#  Abstrae PAPER vs TESTNET vs LIVE:
#    PAPER   → no toca Binance, solo loggea a CSV/Firestore.
#    TESTNET → manda órdenes reales a testnet.binance.vision
#              (balance ficticio pero el flujo es 100% real).
#    LIVE    → binance.com con dinero real. ⚠️ DOBLE CONFIRMACIÓN.
#
#  Respeta filters de Binance por símbolo:
#    LOT_SIZE         → stepSize (múltiplo de la qty)
#    PRICE_FILTER     → tickSize (múltiplo del precio)
#    MIN_NOTIONAL     → qty × price mínimo (típicamente $10)
# ============================================================
import csv
import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

from loguru import logger

import settings
from helpers import binance_get, binance_post, binance_delete, firestore_write, firestore_read
from runtime_config import config as runtime_config


# ── Exchange info cache ───────────────────────────────────

_EXCHANGE_INFO_CACHE: dict = {
    "ts":      0.0,
    "symbols": {},   # symbol → filters dict
}


def _load_exchange_info(symbols: list[str] | None = None):
    """Carga /api/v3/exchangeInfo y cachea filters por símbolo."""
    params = {}
    if symbols:
        import json
        params["symbols"] = json.dumps(symbols)

    try:
        data = binance_get("/api/v3/exchangeInfo", params=params, signed=False, timeout=15)
    except Exception as e:
        logger.warning(f"[EXEC] No pude cargar exchangeInfo: {e}")
        return

    for s in data.get("symbols", []):
        symbol = s["symbol"]
        filters = {f["filterType"]: f for f in s.get("filters", [])}
        _EXCHANGE_INFO_CACHE["symbols"][symbol] = {
            "status":       s.get("status"),
            "baseAsset":    s.get("baseAsset"),
            "quoteAsset":   s.get("quoteAsset"),
            "stepSize":     float(filters.get("LOT_SIZE", {}).get("stepSize", 0)),
            "tickSize":     float(filters.get("PRICE_FILTER", {}).get("tickSize", 0)),
            "minQty":       float(filters.get("LOT_SIZE", {}).get("minQty", 0)),
            "minNotional":  float(
                filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
                .get("minNotional", 10.0)
            ),
        }
    _EXCHANGE_INFO_CACHE["ts"] = time.time()
    logger.info(f"[EXEC] exchangeInfo cargado para {len(_EXCHANGE_INFO_CACHE['symbols'])} símbolos")


def _get_symbol_filters(symbol: str) -> dict | None:
    age = time.time() - _EXCHANGE_INFO_CACHE["ts"]
    if age > settings.EXCHANGE_INFO_TTL_SEC or symbol not in _EXCHANGE_INFO_CACHE["symbols"]:
        _load_exchange_info()
    return _EXCHANGE_INFO_CACHE["symbols"].get(symbol)


def _round_step(value: float, step: float) -> float:
    """Redondea value al múltiplo más cercano de step (floor)."""
    if step <= 0:
        return value
    # Usar Decimal para evitar imprecisiones float
    d_value = Decimal(str(value))
    d_step  = Decimal(str(step))
    return float((d_value // d_step) * d_step)


# ── Executor ──────────────────────────────────────────────

class BinanceExecutor:
    """
    Único punto de ejecución de trades. Los strategy outputs
    llegan acá y este módulo decide si mandar a Binance o
    loggear paper según settings.BINANCE_MODE.
    """

    def __init__(self, paper_log_path: str = "paper_trades_crypto.csv"):
        self.mode = settings.BINANCE_MODE
        self.paper_log_path = paper_log_path
        # _eolo_positions: ÚNICA fuente de verdad para posiciones abiertas.
        # Clave: symbol uppercase. Valor: {qty, entry_price, ts, strategy, reason}.
        # NO se infiere del wallet balance porque el testnet regala 450+ assets
        # ficticios (10k unidades de cada uno). Si usáramos balances crudos
        # como fuente de "posiciones abiertas", toda señal SELL vendería esos
        # regalos — comportamiento catastrófico en LIVE.
        self._eolo_positions: dict[str, dict] = {}
        self._paper_balance_usdt = 10_000.0          # balance simulado inicial
        self._init_paper_log()
        self._hydrate_positions_from_firestore()
        logger.info(
            f"[EXEC] Executor arrancado en modo {self.mode} | "
            f"posiciones Eolo activas: {len(self._eolo_positions)}"
        )

    def _init_paper_log(self):
        if self.mode != "PAPER":
            return
        if not os.path.exists(self.paper_log_path):
            with open(self.paper_log_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ts_utc", "symbol", "side", "qty", "price",
                    "notional_usdt", "strategy", "pnl_usdt", "reason"
                ])

    # ── Persistencia de posiciones Eolo ───────────────────

    def _hydrate_positions_from_firestore(self):
        """
        Restaura el dict de posiciones Eolo al arrancar. Si el bot crashea
        en medio de un trade o Cloud Run lo reinicia, sin esto perderíamos
        entry_price y no podríamos calcular PnL.
        """
        try:
            data = firestore_read(settings.FIRESTORE_STATE_COLLECTION, "positions")
        except Exception as e:
            logger.warning(f"[EXEC] No pude hidratar posiciones desde Firestore: {e}")
            return

        if not data or "open" not in data:
            return

        restored = {
            sym.upper(): pos
            for sym, pos in (data.get("open") or {}).items()
            if isinstance(pos, dict) and float(pos.get("qty", 0)) > 0
        }
        if restored:
            self._eolo_positions = restored
            logger.info(
                f"[EXEC] Hidratadas {len(restored)} posiciones desde Firestore: "
                f"{list(restored.keys())}"
            )

    def _persist_positions_to_firestore(self):
        """Escribe snapshot del dict de posiciones — best-effort."""
        try:
            firestore_write(
                settings.FIRESTORE_STATE_COLLECTION,
                "positions",
                {
                    "open":       self._eolo_positions,
                    "updated_at": time.time(),
                    "mode":       self.mode,
                },
            )
        except Exception as e:
            logger.warning(f"[EXEC] No pude persistir posiciones a Firestore: {e}")

    # ── API pública ───────────────────────────────────────

    def get_balance_usdt(self) -> float:
        """USDT disponible para tradear. En paper es el simulado."""
        if self.mode == "PAPER":
            return self._paper_balance_usdt

        try:
            data = binance_get("/api/v3/account", signed=True, timeout=10)
            for b in data.get("balances", []):
                if b["asset"] == "USDT":
                    return float(b["free"])
        except Exception as e:
            logger.error(f"[EXEC] No pude leer balance: {e}")
        return 0.0

    def get_position(self, symbol: str) -> dict | None:
        """
        Posición abierta **por Eolo** para el símbolo — NO wallet balance.
        Si el usuario tiene ese asset por otra razón (regalo de testnet,
        compra externa, airdrop, etc.), Eolo lo ignora.
        """
        return self._eolo_positions.get(symbol.upper())

    def get_open_positions(self) -> dict[str, dict]:
        """
        Dict symbol → position dict — solo posiciones abiertas por Eolo.
        Se usa para:
          - Chequear MAX_OPEN_POSITIONS antes de BUY.
          - Saber qué cerrar cuando strategies dicen SELL.
        NO refleja el wallet total de Binance (eso está en get_balance_usdt
        + exploración manual, no es rol del executor).
        """
        return dict(self._eolo_positions)

    # ── Sizing ────────────────────────────────────────────

    def _size_notional(self, current_price: float, balance_usdt: float) -> float:
        """
        Calcula el notional USDT de la próxima orden. El MODE (PERCENT|FIXED)
        y el USDT fijo siguen viniendo de settings.py (no son editables desde
        el dashboard), pero el % efectivo viene de runtime_config para que
        el slider del dashboard tenga efecto sin redeploy.
        """
        if settings.POSITION_SIZING_MODE == "FIXED":
            return settings.POSITION_SIZE_USDT
        # PERCENT
        return balance_usdt * (runtime_config.position_size_pct / 100.0)

    def _quantize(self, symbol: str, qty: float, price: float) -> tuple[float, float, float]:
        """Ajusta qty/price a stepSize/tickSize. Retorna (qty, price, notional)."""
        filters = _get_symbol_filters(symbol)
        if filters is None:
            logger.warning(f"[EXEC] Sin filters para {symbol} — uso qty/price sin ajuste")
            return qty, price, qty * price

        step = filters["stepSize"]
        tick = filters["tickSize"]
        q_qty   = _round_step(qty, step)    if step > 0 else qty
        q_price = _round_step(price, tick)  if tick > 0 else price

        notional = q_qty * q_price
        if notional < filters["minNotional"]:
            logger.warning(
                f"[EXEC] {symbol} notional ${notional:.2f} < min ${filters['minNotional']:.2f} — "
                f"orden rechazada"
            )
            return 0.0, q_price, 0.0

        return q_qty, q_price, notional

    # ── Entrada / salida ──────────────────────────────────

    def open_long(self, symbol: str, price: float, strategy: str,
                  reason: str = "") -> dict | None:
        """
        Abre una posición long (BUY market) del tamaño configurado.
        Retorna el dict de la orden ejecutada o None si falló.
        """
        symbol = symbol.upper()

        # Chequear max posiciones
        positions = self.get_open_positions()
        if symbol in positions:
            logger.debug(f"[EXEC] {symbol} ya tiene posición abierta — skip")
            return None
        max_pos = runtime_config.max_open_positions
        if len(positions) >= max_pos:
            logger.debug(
                f"[EXEC] Max posiciones ({max_pos}) alcanzado — "
                f"{symbol} descartado"
            )
            return None

        balance = self.get_balance_usdt()
        notional = self._size_notional(price, balance)
        if notional > balance:
            logger.warning(
                f"[EXEC] {symbol} notional ${notional:.2f} > balance ${balance:.2f}"
            )
            return None

        raw_qty = notional / price if price > 0 else 0
        qty, q_price, q_notional = self._quantize(symbol, raw_qty, price)
        if qty <= 0:
            return None

        if self.mode == "PAPER":
            return self._paper_buy(symbol, qty, q_price, q_notional, strategy, reason)
        else:
            return self._market_buy(symbol, qty, q_price, q_notional, strategy, reason)

    def close_long(self, symbol: str, price: float, strategy: str,
                   reason: str = "") -> dict | None:
        """Cierra la posición long existente con SELL market."""
        symbol = symbol.upper()
        position = self.get_position(symbol)
        if not position:
            logger.debug(f"[EXEC] No hay posición en {symbol} — nada que cerrar")
            return None

        qty = position["qty"]
        # Quantize por si el asset acumula dust
        filters = _get_symbol_filters(symbol)
        if filters and filters["stepSize"] > 0:
            qty = _round_step(qty, filters["stepSize"])
        if qty <= 0:
            return None

        if self.mode == "PAPER":
            return self._paper_sell(symbol, qty, price, strategy, reason)
        else:
            return self._market_sell(symbol, qty, price, strategy, reason)

    # ── PAPER ─────────────────────────────────────────────

    def _paper_buy(self, symbol, qty, price, notional, strategy, reason):
        self._eolo_positions[symbol] = {
            "qty":         qty,
            "entry_price": price,
            "ts":          time.time(),
            "strategy":    strategy,
            "reason":      reason,
        }
        self._paper_balance_usdt -= notional
        self._persist_positions_to_firestore()
        self._log_paper(symbol, "BUY", qty, price, notional, strategy, 0.0, reason)
        logger.info(
            f"[PAPER] 🟢 BUY {symbol} qty={qty} @ ${price:.4f} "
            f"(${notional:.2f}) strategy={strategy} — {reason}"
        )
        return {"symbol": symbol, "side": "BUY", "qty": qty, "price": price, "paper": True}

    def _paper_sell(self, symbol, qty, price, strategy, reason):
        position = self._eolo_positions.pop(symbol, None)
        if not position:
            return None
        notional = qty * price
        pnl = notional - (position["qty"] * position["entry_price"])
        self._paper_balance_usdt += notional
        self._persist_positions_to_firestore()
        self._log_paper(symbol, "SELL", qty, price, notional, strategy, pnl, reason)
        logger.info(
            f"[PAPER] 🔴 SELL {symbol} qty={qty} @ ${price:.4f} "
            f"(${notional:.2f}) pnl={pnl:+.2f} strategy={strategy} — {reason}"
        )
        return {"symbol": symbol, "side": "SELL", "qty": qty, "price": price,
                "pnl": pnl, "paper": True}

    def _log_paper(self, symbol, side, qty, price, notional, strategy, pnl, reason):
        row = [
            datetime.now(timezone.utc).isoformat(),
            symbol, side, qty, price, notional, strategy, pnl, reason,
        ]
        with open(self.paper_log_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        # También a Firestore para dashboard
        try:
            firestore_write(
                settings.FIRESTORE_TRADES_COLLECTION,
                f"{symbol}-{int(time.time() * 1000)}",
                {
                    "ts":        time.time(),
                    "symbol":    symbol,
                    "side":      side,
                    "qty":       qty,
                    "price":     price,
                    "notional":  notional,
                    "strategy":  strategy,
                    "pnl_usdt":  pnl,
                    "reason":    reason,
                    "mode":      self.mode,
                },
            )
        except Exception as e:
            logger.warning(f"[EXEC] Firestore trade log falló: {e}")

    # ── LIVE / TESTNET ────────────────────────────────────

    def _market_buy(self, symbol, qty, price, notional, strategy, reason):
        try:
            order = binance_post(
                "/api/v3/order",
                params={
                    "symbol":    symbol,
                    "side":      "BUY",
                    "type":      "MARKET",
                    "quantity":  qty,
                },
                signed=True,
            )
            filled_price = float(order.get("fills", [{}])[0].get("price", price))

            # Registrar como posición Eolo — ESTO es lo que nos protege
            # de que luego una señal SELL se coma el wallet entero.
            self._eolo_positions[symbol] = {
                "qty":         qty,
                "entry_price": filled_price,
                "ts":          time.time(),
                "strategy":    strategy,
                "reason":      reason,
                "order_id":    order.get("orderId"),
            }
            self._persist_positions_to_firestore()

            logger.info(
                f"[BINANCE-{self.mode}] 🟢 BUY {symbol} qty={qty} "
                f"filled@${filled_price:.4f} strategy={strategy} — {reason}"
            )
            # Log local para tracking aunque venga de Binance
            self._log_paper(symbol, "BUY", qty, filled_price, qty * filled_price,
                            strategy, 0.0, reason)
            return order
        except Exception as e:
            logger.error(f"[EXEC] MARKET BUY {symbol} falló: {e}")
            return None

    def _market_sell(self, symbol, qty, price, strategy, reason):
        # Capturar posición antes del sell para poder calcular PnL
        position_pre = self._eolo_positions.get(symbol)
        try:
            order = binance_post(
                "/api/v3/order",
                params={
                    "symbol":    symbol,
                    "side":      "SELL",
                    "type":      "MARKET",
                    "quantity":  qty,
                },
                signed=True,
            )
            filled_price = float(order.get("fills", [{}])[0].get("price", price))

            # Calcular PnL con el entry_price que guardamos en la BUY
            pnl = 0.0
            if position_pre and position_pre.get("entry_price"):
                pnl = (filled_price - position_pre["entry_price"]) * qty

            # Remover posición Eolo (close exitoso)
            self._eolo_positions.pop(symbol, None)
            self._persist_positions_to_firestore()

            logger.info(
                f"[BINANCE-{self.mode}] 🔴 SELL {symbol} qty={qty} "
                f"filled@${filled_price:.4f} pnl={pnl:+.2f} "
                f"strategy={strategy} — {reason}"
            )
            self._log_paper(symbol, "SELL", qty, filled_price, qty * filled_price,
                            strategy, pnl, reason)
            return order
        except Exception as e:
            logger.error(f"[EXEC] MARKET SELL {symbol} falló: {e}")
            return None
