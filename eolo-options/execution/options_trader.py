# ============================================================
#  EOLO v2 — Options Trader (Schwab Order Execution)
#
#  Ejecuta órdenes de opciones via Schwab API REST.
#
#  Modos de operación:
#    PAPER_TRADING = True  → simula órdenes, log a CSV, sin llamadas reales
#    PAPER_TRADING = False → órdenes reales contra Schwab
#
#  Endpoints usados:
#    POST /trader/v1/accounts/{accountNumber}/orders   → crear orden
#    GET  /trader/v1/accounts/{accountNumber}/orders   → listar órdenes
#    DELETE /trader/v1/accounts/{accountNumber}/orders/{orderId} → cancelar
#    GET  /trader/v1/accounts/{accountNumber}/positions → posiciones abiertas
#
#  Tipos de órdenes soportados (Level 1-2):
#    - LONG CALL  : BUY TO OPEN  CALL
#    - LONG PUT   : BUY TO OPEN  PUT
#    - CLOSE LONG : SELL TO CLOSE CALL/PUT
#    - DEBIT SPREAD: BUY TO OPEN + SELL TO OPEN (misma exp, mismo tipo, dist. strike)
#
#  Símbolo de opción (OCC format):
#    TICKER + YYMMDD + C/P + 8-digit strike × 1000
#    SOXL  250516C00045000
#    (6 chars ticker, padded to 6; 6 chars date; 1 char type; 8 chars strike)
#
#  Paper trading CSV:
#    paper_trades_log.csv — una fila por orden simulada
#
#  Uso:
#    trader = OptionsTrader(paper=True)
#    order_id = await trader.open_long_call("SOXL", "2025-05-16", 45.0, 1, limit=2.35)
#    await trader.close_position(order_id)
# ============================================================
import asyncio
import csv
import os
import re
import time
from datetime import datetime, timezone
from typing import Literal, Optional
from loguru import logger

try:
    import requests
except ImportError:
    requests = None

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from helpers import get_access_token

# Shared trade-enrichment helper (Phase 1 — 2026-04-21).
# Produce VIX/session/slippage/counter fields en cada trade para que
# eolo-sheets-sync + Strategy_Stats tengan las columnas analíticas.
try:
    from eolo_common.trade_enrichment import build_enrichment  # type: ignore
except Exception:
    build_enrichment = None  # fallback: sin enrichment si no está disponible

# ── Constantes ────────────────────────────────────────────
SCHWAB_TRADER_BASE = "https://api.schwabapi.com/trader/v1"

# Tiempo de espera máximo para que una orden sea filled
ORDER_FILL_TIMEOUT  = 60    # segundos
ORDER_POLL_INTERVAL = 3     # segundos entre polls de estado

# Slippage máximo permitido (si el mid sube más de este %, rechazar)
MAX_SLIPPAGE_PCT = 5.0

# Paper trading — cambiar a False para ir live
PAPER_TRADING = True

# CSV de log para paper trades
PAPER_LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "paper_trades_log.csv")

# Firestore — persistencia de trades (sobrevive reinicios de Cloud Run,
# feed autoritativo para el dashboard y para eolo-sheets-sync).
# Patrón idéntico a v1 (eolo-trades/YYYY-MM-DD con key = {ts}_{ticker}_{action}).
FIRESTORE_TRADES_COLLECTION = "eolo-options-trades"

# Telegram — mismo token/chat que V1 y Crypto
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8207559403:AAGwiQS15APh3ivFsAUUu_DCMbltMoDYV-o")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5802788501")
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _send_telegram(message: str):
    """Envía notificación a Telegram. No-op si no está configurado."""
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=5)
    except Exception as e:
        logger.warning(f"[TRADER] Telegram error: {e}")


def _persist_trade_to_firestore(trade: dict) -> None:
    """
    Persiste el trade en Firestore (collection eolo-options-trades).
    Mismo patrón que v1 (eolo-trades): un doc por día, key de campo = {ts}_{ticker}_{action}.
    Se escribe con merge=True para que múltiples trades del día coexistan en el mismo doc.

    El campo `trade` tiene que traer como mínimo timestamp, ticker y action.
    Falla silenciosa: si Firestore está caído o no hay creds, se warn-loguea y seguimos
    (el CSV local queda como backup).
    """
    try:
        from google.cloud import firestore
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
        db = firestore.Client(project=project_id)
        today = datetime.now().strftime("%Y-%m-%d")
        ts     = trade.get("timestamp", "")
        ticker = trade.get("ticker", "")
        action = trade.get("action", "")
        key    = f"{ts}_{ticker}_{action}"
        db.collection(FIRESTORE_TRADES_COLLECTION).document(today).set(
            {key: trade}, merge=True
        )
    except Exception as e:
        logger.warning(f"[TRADER] Firestore trade log falló (CSV ok): {e}")


def _log_paper_trade(action: str, symbol: str, ticker: str, contracts: int,
                     limit: float | None, option_type: str,
                     expiration: str, strike: float,
                     strategy: str = "", reason: str = "",
                     pnl_usd: float | None = None,
                     pnl_pct: float | None = None,
                     macro_feeds=None,
                     entry_price_override: Optional[float] = None,
                     opened_at_ts: Optional[float] = None,
                     expected_price: Optional[float] = None,
                     fill_price: Optional[float] = None,
                     quote_snapshot: dict | None = None,
                     data_quality: str = "n/a",
                     isolation_info: dict | None = None) -> str:
    """
    Loguea una orden paper en CSV + Firestore y retorna un order_id fake.
    Si la orden es SELL_TO_CLOSE el caller puede pasar pnl_usd/pnl_pct ya calculados
    para que queden persistidos junto al trade.

    Enriquecimiento (Phase 1 — 2026-04-21):
        macro_feeds          → VIX snapshot + bucket
        entry_price_override → precio de apertura (SELL_TO_CLOSE)
        opened_at_ts         → epoch seconds de la apertura (SELL_TO_CLOSE)
        expected_price       → precio esperado (para slippage live)
        fill_price           → precio de llenado (para slippage live)
    """
    order_id  = f"PAPER-{int(time.time() * 1000) % 10_000_000:07d}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    price_str = f"{limit:.2f}" if limit else "MARKET"
    total_est = round((limit or 0) * contracts * 100, 2)

    logger.info(
        f"[📄 PAPER] {action} {contracts}x {symbol} "
        f"@ ${price_str} | total≈${total_est} | order={order_id}"
    )

    # CSV log
    log_path   = os.path.abspath(PAPER_LOG_FILE)
    file_exists = os.path.isfile(log_path)
    try:
        with open(log_path, "a", newline="") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow([
                    "timestamp", "order_id", "action", "ticker", "option_type",
                    "expiration", "strike", "contracts", "limit_price", "total_est",
                    "symbol", "strategy", "reason", "pnl_usd", "pnl_pct"
                ])
            w.writerow([
                timestamp, order_id, action, ticker, option_type,
                expiration, strike, contracts, price_str, total_est,
                symbol, strategy, reason[:120] if reason else "",
                pnl_usd if pnl_usd is not None else "",
                pnl_pct if pnl_pct is not None else "",
            ])
    except Exception as e:
        logger.warning(f"[PAPER] CSV log error: {e}")

    # Enrichment (Phase 1 — 2026-04-21):
    # build_enrichment() produce vix/session/slippage/counter fields. En paper
    # simula slippage ~2bps. El caller pasa macro_feeds + opened_at_ts cuando
    # los tiene (SELL_TO_CLOSE); en BUY_TO_OPEN solo mete ts+session+vix+counter.
    side = "BUY" if "BUY" in action else "SELL"
    enrich: dict = {}
    if build_enrichment is not None:
        try:
            enrich = build_enrichment(
                ts_utc          = datetime.now(timezone.utc),
                asset_class     = "stock",
                side            = side,  # BUY / SELL
                mode            = "PAPER",
                expected_price  = expected_price if expected_price is not None else limit,
                fill_price      = fill_price     if fill_price     is not None else limit,
                macro_feeds     = macro_feeds,
                spy_ret_5d_fn   = None,
                entry_price     = entry_price_override,
                opened_at_ts    = opened_at_ts,
                reason          = reason or "",
                counter_key     = "eolo_v2",
            ) or {}
        except Exception as e:
            logger.debug(f"[PAPER] build_enrichment falló: {e}")

    # Firestore (daily doc) — mismo schema que v1 pero con campos de opciones
    trade_payload = {
        "timestamp":   timestamp,
        "mode":        "PAPER",
        "action":      action,
        "ticker":      ticker,
        "option_type": option_type,
        "expiration":  expiration,
        "strike":      strike,
        "contracts":   contracts,
        "limit_price": limit if limit is not None else None,
        "total_est":   total_est,
        "symbol":      symbol,
        "strategy":    strategy,
        "reason":      (reason[:300] if reason else ""),
        "order_id":    order_id,
        "pnl_usd":     pnl_usd,
        "pnl_pct":     pnl_pct,
        "data_quality": data_quality,
    }
    if enrich:
        trade_payload.update(enrich)

    # Sprint 1 fix: snapshot completo del quote (9 keys). Si quote_snapshot is None
    # (BUY_TO_OPEN, o close con limit explícito), las 9 keys quedan en None —
    # el dashboard debe distinguir None (sin dato) vs 0 (precio cero).
    quote_keys = ("quote_bid", "quote_ask", "quote_mid", "quote_last", "quote_mark",
                  "quote_spot", "quote_iv", "quote_fetched_at", "quote_source")
    qs = quote_snapshot or {}
    for k in quote_keys:
        trade_payload[k] = qs.get(k)

    # Logging de safety net (Pure Isolation Opción C)
    if isolation_info:
        cs = isolation_info.get("closer_strategy")
        cr = isolation_info.get("closer_reason")
        if cs is not None:
            trade_payload["closer_strategy"] = cs
        if cr is not None:
            trade_payload["closer_reason"] = cr

    _persist_trade_to_firestore(trade_payload)

    # Telegram
    mode_icon = "📄 PAPER"
    action_icon = "🟢" if "BUY" in action else "🔴"
    pnl_line = ""
    if pnl_usd is not None:
        pnl_line = f"\n💰 P&amp;L: <b>{pnl_pct:+.1f}% (${pnl_usd:+.2f})</b>"
    _send_telegram(
        f"{action_icon} <b>{action} — {mode_icon}</b>\n"
        f"📌 {ticker} {option_type.upper()} K={strike} exp={expiration}\n"
        f"📦 Contratos: {contracts} @ ${price_str} (≈${total_est})"
        f"{pnl_line}\n"
        f"🕐 {timestamp}\n"
        f"🔖 order_id: {order_id}"
    )

    return order_id


def _resolve_close_isolation_v2(opener_strategy: str, closer_strategy: str,
                                 reason: str = ""
                                 ) -> tuple[bool, str, str, dict]:
    """
    Pure Isolation V2 gate (simple).

    Reglas:
      • opener == closer (no vacío)  →  ALLOW
      • opener != closer             →  REJECT

    Nota: los safety nets de V2 (auto_close_loop, close_all_positions) llaman a
    close_long_call/put pasando closer_override explícito, NO a través de este
    gate. Por eso el gate solo distingue allow/reject sin safety nets en código.

    Returns (allowed, log_strategy, log_reason, isolation_info).
    """
    if opener_strategy and opener_strategy == closer_strategy:
        return True, opener_strategy, reason, {
            "closer_strategy": None,
            "closer_reason":   None,
        }
    return False, closer_strategy, reason, {
        "closer_strategy": None,
        "closer_reason":   None,
    }


class OptionsTrader:
    """
    Ejecuta y gestiona órdenes de opciones via Schwab REST API.

    En modo paper (PAPER_TRADING=True):
      - Las órdenes se loguean en CSV pero NO se envían a Schwab
      - Las posiciones se mantienen en memoria (_paper_positions)
      - get_positions() retorna las posiciones paper
      - close_all_positions() limpia el estado paper
    """

    def __init__(self, paper: bool = PAPER_TRADING, macro_feeds=None,
                 chain_fetcher=None):
        self.paper         = paper
        self._account_id   = None
        self._open_positions: dict[str, dict] = {}   # order_id → position_dict
        # Paper trading: estado en memoria
        self._paper_positions: list[dict] = []        # list de dicts como get_positions()
        self._paper_order_counter = 0
        # MacroFeeds inyectado desde eolo_v2_main (para VIX snapshot en enrichment).
        # Se puede setear después con set_macro_feeds(); None → campos VIX quedan vacíos.
        self._macro_feeds = macro_feeds
        # OptionChainFetcher inyectado desde eolo_v2_main (para resolver bid en SELL_TO_CLOSE).
        # Se puede setear después con set_chain_fetcher(); None → _resolve_close_limit
        # devuelve fail-loud y trade se persiste con data_quality=quote_unavailable.
        self._chain_fetcher = chain_fetcher

        mode = "📄 PAPER" if self.paper else "💰 LIVE"
        logger.info(f"[TRADER] Modo: {mode}")

    def set_macro_feeds(self, macro_feeds):
        """Inyecta MacroFeeds post-init (eolo_v2_main lo crea async)."""
        self._macro_feeds = macro_feeds

    def set_chain_fetcher(self, chain_fetcher):
        """Inyecta OptionChainFetcher post-init (eolo_v2_main puede crearlo después)."""
        self._chain_fetcher = chain_fetcher

    # ── Autenticación y cuenta ─────────────────────────────

    def _headers(self) -> dict:
        token = get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def get_account_id(self) -> str:
        """Obtiene el accountNumber de la cuenta principal."""
        if self._account_id:
            return self._account_id

        resp = requests.get(
            f"{SCHWAB_TRADER_BASE}/accounts",
            headers=self._headers(),
            timeout=10
        )
        resp.raise_for_status()
        accounts = resp.json()

        if not accounts:
            raise ValueError("No se encontraron cuentas en Schwab")

        # Tomar la primera cuenta
        self._account_id = accounts[0].get("securitiesAccount", {}).get("accountNumber")
        if not self._account_id:
            raise ValueError("No se encontró accountNumber en la respuesta")

        logger.info(f"[TRADER] Account: {self._account_id}")
        return self._account_id

    # ── Símbolo OCC ───────────────────────────────────────

    @staticmethod
    def build_occ_symbol(ticker: str, expiration: str,
                          option_type: Literal["call", "put"],
                          strike: float) -> str:
        """
        Construye el símbolo OCC para opciones americanas.

        Formato: ROOT(6)YYMMDD(6)C/P(1)STRIKE×1000(8)
        Ejemplo: SOXL  250516C00045000

        Args:
            ticker:      "SOXL"
            expiration:  "2025-05-16"
            option_type: "call" o "put"
            strike:      45.0
        """
        root     = ticker.upper().ljust(6)[:6]  # padded/truncated a 6
        date_obj = datetime.strptime(expiration, "%Y-%m-%d")
        date_str = date_obj.strftime("%y%m%d")   # YYMMDD
        cp       = "C" if option_type == "call" else "P"
        strike_i = int(round(strike * 1000))
        strike_s = str(strike_i).zfill(8)
        return f"{root}{date_str}{cp}{strike_s}"

    # ── Órdenes simples ────────────────────────────────────

    async def open_long_call(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        contracts:  int   = 1,
        limit:      float | None = None,
        strategy:   str = "",
        reason:     str = "",
    ) -> str | None:
        """
        Compra contratos de call (BUY TO OPEN).
        Retorna order_id o None si falla.
        """
        return await self._place_single(
            ticker, expiration, strike, "call",
            "BUY_TO_OPEN", contracts, limit,
            strategy=strategy, reason=reason,
        )

    async def open_long_put(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        contracts:  int   = 1,
        limit:      float | None = None,
        strategy:   str = "",
        reason:     str = "",
    ) -> str | None:
        """Compra contratos de put (BUY TO OPEN)."""
        return await self._place_single(
            ticker, expiration, strike, "put",
            "BUY_TO_OPEN", contracts, limit,
            strategy=strategy, reason=reason,
        )

    async def close_long_call(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        contracts:  int   = 1,
        limit:      float | None = None,
        strategy:   str = "",
        reason:     str = "",
        closer_override: str | None = None,
        closer_reason_override: str | None = None,
    ) -> str | None:
        """
        Cierra una posición larga de call (SELL TO CLOSE).

        closer_override: si se pasa, identifica un safety net cerrando la posición.
            Bypasea el gate de Pure Isolation y se loguea en el trade payload.
            Valores esperados: "auto_close" | "close_all" | "manual"
        closer_reason_override: categoría del cierre (p.ej. "eod_15_27", "operator_command").
        """
        # Si caller no pasó limit, resolver desde chain (Sprint 1 fix exit_price).
        quote_snapshot: dict | None = None
        if limit is None:
            limit, quote_snapshot = self._resolve_close_limit(
                ticker, expiration, strike, "call"
            )
        return await self._place_single(
            ticker, expiration, strike, "call",
            "SELL_TO_CLOSE", contracts, limit,
            strategy=strategy, reason=reason,
            quote_snapshot=quote_snapshot,
            closer_override=closer_override,
            closer_reason_override=closer_reason_override,
        )

    async def close_long_put(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        contracts:  int   = 1,
        limit:      float | None = None,
        strategy:   str = "",
        reason:     str = "",
        closer_override: str | None = None,
        closer_reason_override: str | None = None,
    ) -> str | None:
        """
        Cierra una posición larga de put (SELL TO CLOSE).

        closer_override: si se pasa, identifica un safety net cerrando la posición.
            Bypasea el gate de Pure Isolation y se loguea en el trade payload.
            Valores esperados: "auto_close" | "close_all" | "manual"
        closer_reason_override: categoría del cierre (p.ej. "eod_15_27", "operator_command").
        """
        # Si caller no pasó limit, resolver desde chain (Sprint 1 fix exit_price).
        quote_snapshot: dict | None = None
        if limit is None:
            limit, quote_snapshot = self._resolve_close_limit(
                ticker, expiration, strike, "put"
            )
        return await self._place_single(
            ticker, expiration, strike, "put",
            "SELL_TO_CLOSE", contracts, limit,
            strategy=strategy, reason=reason,
            quote_snapshot=quote_snapshot,
            closer_override=closer_override,
            closer_reason_override=closer_reason_override,
        )

    # ── Debit Spread ───────────────────────────────────────

    async def open_debit_spread(
        self,
        ticker:       str,
        expiration:   str,
        option_type:  Literal["call", "put"],
        buy_strike:   float,
        sell_strike:  float,
        contracts:    int   = 1,
        net_debit:    float | None = None,
    ) -> str | None:
        """
        Abre un debit spread:
          - BUY TO OPEN (buy_strike, más cercano ATM)
          - SELL TO OPEN (sell_strike, más lejos ATM)

        Para call spread: buy_strike < sell_strike
        Para put spread:  buy_strike > sell_strike
        """
        buy_symbol  = self.build_occ_symbol(ticker, expiration, option_type, buy_strike)
        sell_symbol = self.build_occ_symbol(ticker, expiration, option_type, sell_strike)

        account_id = await asyncio.get_event_loop().run_in_executor(
            None, self.get_account_id
        )

        order = {
            "orderType":          "NET_DEBIT",
            "session":            "NORMAL",
            "duration":           "DAY",
            "orderStrategyType":  "SINGLE",
            "price":              round(net_debit, 2) if net_debit else None,
            "orderLegCollection": [
                {
                    "instruction": "BUY_TO_OPEN",
                    "quantity":    contracts,
                    "instrument":  {"symbol": buy_symbol, "assetType": "OPTION"},
                },
                {
                    "instruction": "SELL_TO_OPEN",
                    "quantity":    contracts,
                    "instrument":  {"symbol": sell_symbol, "assetType": "OPTION"},
                },
            ],
        }
        if not net_debit:
            order.pop("price")
            order["orderType"] = "MARKET"

        return await self._submit_order(account_id, order, f"DEBIT_SPREAD {ticker}")

    # ── Credit Spreads (Theta Harvest) ────────────────────

    async def open_credit_spread(
        self,
        ticker:       str,
        expiration:   str,
        spread_type:  str,          # "put_credit_spread" | "call_credit_spread"
        short_strike: float,        # strike que VENDEMOS (más ATM)
        long_strike:  float,        # strike de protección (más OTM)
        contracts:    int   = 1,
        net_credit:   float | None = None,
        strategy:     str   = "theta_harvest",
        reason:       str   = "",
    ) -> str | None:
        """
        Abre un credit spread (theta harvest):
          - SELL TO OPEN short_strike (cobra prima)
          - BUY  TO OPEN long_strike  (protección)
        Net credit → orden NET_CREDIT.

        put_credit_spread : short_strike > long_strike  (put OTM vendida)
        call_credit_spread: short_strike < long_strike  (call OTM vendida)
        """
        opt_type     = "put" if "put" in spread_type else "call"
        short_symbol = self.build_occ_symbol(ticker, expiration, opt_type, short_strike)
        long_symbol  = self.build_occ_symbol(ticker, expiration, opt_type, long_strike)

        tag = f"CREDIT_SPREAD {'PUT' if opt_type == 'put' else 'CALL'} {ticker} K={short_strike}/{long_strike} exp={expiration}"

        if self.paper:
            trade_id = f"PAPER_{ticker}_CSPREAD_{short_strike}_{expiration}_{int(asyncio.get_event_loop().time())}"
            _log_paper_trade(
                action=f"SELL_TO_OPEN_SPREAD ({spread_type})",
                symbol=short_symbol,
                ticker=ticker,
                contracts=contracts,
                limit=net_credit or 0,
                option_type="put" if "put" in spread_type else "call",
                expiration=expiration,
                strike=short_strike,
                strategy=strategy,
                reason=reason,
            )
            logger.info(
                f"[TRADER PAPER] {tag} | credit=${net_credit:.2f} | "
                f"reason={reason[:80]}"
            )
            # Persistir en posiciones paper (short leg como referencia del spread)
            self._update_paper_positions(
                instruction = "BUY_TO_OPEN",   # reutiliza el tracker de posiciones
                ticker      = ticker,
                expiration  = expiration,
                strike      = short_strike,
                opt_type    = opt_type,
                contracts   = contracts,
                entry_price = net_credit,       # crédito cobrado (precio efectivo)
                order_id    = trade_id,
            )
            return trade_id

        # ── Live ──────────────────────────────────────────
        account_id = await asyncio.get_event_loop().run_in_executor(
            None, self.get_account_id
        )
        order = {
            "orderType":          "NET_CREDIT",
            "session":            "NORMAL",
            "duration":           "DAY",
            "orderStrategyType":  "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "SELL_TO_OPEN",
                    "quantity":    contracts,
                    "instrument":  {"symbol": short_symbol, "assetType": "OPTION"},
                },
                {
                    "instruction": "BUY_TO_OPEN",
                    "quantity":    contracts,
                    "instrument":  {"symbol": long_symbol, "assetType": "OPTION"},
                },
            ],
        }
        if net_credit:
            order["price"] = round(net_credit, 2)
        else:
            order["orderType"] = "MARKET"

        return await self._submit_order(account_id, order, tag)

    async def close_spread(
        self,
        ticker:       str,
        expiration:   str,
        spread_type:  str,
        short_strike: float,
        long_strike:  float,
        contracts:    int   = 1,
        net_debit:    float | None = None,
        reason:       str   = "",
    ) -> str | None:
        """
        Cierra un credit spread existente:
          - BUY  TO CLOSE short_strike
          - SELL TO CLOSE long_strike
        Es una orden NET_DEBIT (pagamos para cerrar).
        """
        opt_type     = "put" if "put" in spread_type else "call"
        short_symbol = self.build_occ_symbol(ticker, expiration, opt_type, short_strike)
        long_symbol  = self.build_occ_symbol(ticker, expiration, opt_type, long_strike)

        tag = f"CLOSE_CSPREAD {ticker} K={short_strike}/{long_strike} exp={expiration}"

        if self.paper:
            trade_id = f"PAPER_{ticker}_CLOSE_CSPREAD_{short_strike}_{expiration}_{int(asyncio.get_event_loop().time())}"
            _log_paper_trade(
                action=f"BUY_TO_CLOSE_SPREAD ({reason[:40]})",
                symbol=short_symbol,
                ticker=ticker,
                contracts=contracts,
                limit=net_debit or 0,
                option_type="put" if "put" in short_symbol else "call",
                expiration=expiration,
                strike=short_strike,
                strategy="theta_harvest",
                reason=reason,
                fill_price=net_debit or 0,
            )
            logger.info(f"[TRADER PAPER] {tag} | debit=${net_debit:.2f if net_debit else 0:.2f} | {reason[:80]}")
            return trade_id

        # ── Live ──────────────────────────────────────────
        account_id = await asyncio.get_event_loop().run_in_executor(
            None, self.get_account_id
        )
        order = {
            "orderType":          "NET_DEBIT",
            "session":            "NORMAL",
            "duration":           "DAY",
            "orderStrategyType":  "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "BUY_TO_CLOSE",
                    "quantity":    contracts,
                    "instrument":  {"symbol": short_symbol, "assetType": "OPTION"},
                },
                {
                    "instruction": "SELL_TO_CLOSE",
                    "quantity":    contracts,
                    "instrument":  {"symbol": long_symbol, "assetType": "OPTION"},
                },
            ],
        }
        if net_debit:
            order["price"] = round(net_debit, 2)
        else:
            order["orderType"] = "MARKET"

        return await self._submit_order(account_id, order, tag)

    # ── Close quote helper ────────────────────────────────

    def _resolve_close_limit(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        opt_type:   Literal["call", "put"],
    ) -> tuple[float | None, dict]:
        """
        Resuelve precio de cierre (BID) + arma snapshot del quote para persistencia.

        Política (Sprint 1 fix forward, 2026-05-13):
          - Usa BID puro como `limit` (consistente con SELL_TO_CLOSE live,
            que en orden MARKET se ejecuta cerca del bid en el peor caso).
          - Si bid es None o <= 0: fail-loud (retorna limit=None). El caller
            debe persistir el trade con data_quality="quote_unavailable" y
            pnl_usd/pnl_pct = None.
          - NO hay fallback a last/mark/spot (paper simula live worst-case).
          - Snapshot completo del quote SIEMPRE va a Firestore para auditoría,
            incluso si bid era inválido.

        Returns:
            (limit, snapshot)
              limit:    float si chain está OK y bid > 0; None en fallos.
              snapshot: dict con keys quote_* (siempre presente; en fallos
                        los campos numéricos quedan None, quote_source
                        indica la razón).
        """
        strike = float(strike)   # blindaje: previene str(45) vs str(45.0) mismatch en lookup
        symbol = self.build_occ_symbol(ticker, expiration, opt_type, strike)

        def _fail(reason: str) -> tuple[None, dict]:
            """Snapshot vacío con quote_source=reason."""
            return None, {
                "quote_bid":        None,
                "quote_ask":        None,
                "quote_mid":        None,
                "quote_last":       None,
                "quote_mark":       None,
                "quote_spot":       None,
                "quote_iv":         None,
                "quote_fetched_at": None,
                "quote_source":     reason,
            }

        # (a) chain_fetcher no inyectado → WARNING (config issue, no runtime)
        if self._chain_fetcher is None:
            logger.warning(
                f"[CLOSE_QUOTE] chain_fetcher not injected — cannot quote {symbol}"
            )
            return _fail("no_fetcher")

        # (b)-(c) Lookup contract en cache → ERROR (cierre sin precio = bug original)
        option_type_plural = opt_type + "s"   # "call" → "calls"
        contract = self._chain_fetcher.get_contract(
            ticker, expiration, strike, option_type_plural
        )
        if not contract:
            logger.error(
                f"[CLOSE_QUOTE] no chain data for {symbol} "
                f"(ticker={ticker} exp={expiration} strike={strike} type={opt_type})"
            )
            return _fail("no_chain_data")

        # (d) Campos del contract
        bid  = contract.get("bid")
        ask  = contract.get("ask")
        mark = contract.get("mark")
        last = contract.get("last")
        iv   = contract.get("iv")

        # (e) Spot + timestamp del chain entero (API pública)
        chain      = self._chain_fetcher.get_chain(ticker) or {}
        underlying = chain.get("underlying") or {}
        spot       = underlying.get("price")
        chain_ts   = chain.get("ts")   # unix time.time() local del último fetch

        # (f) Mid si ambos lados existen
        mid = round((bid + ask) / 2, 4) if (bid is not None and ask is not None) else None

        # Snapshot completo (siempre presente, fail-loud o no)
        snapshot = {
            "quote_bid":        bid,
            "quote_ask":        ask,
            "quote_mid":        mid,
            "quote_last":       last,    # último trade real ejecutado (puede ser stale)
            "quote_mark":       mark,    # mid calculado por Schwab
            "quote_spot":       spot,
            "quote_iv":         iv,
            "quote_fetched_at": chain_ts,
            "quote_source":     None,
        }

        # (g) bid null / no positivo → ERROR (cierre sin precio = bug original)
        if bid is None or bid <= 0:
            logger.error(
                f"[CLOSE_QUOTE] bid unavailable for {symbol} "
                f"(bid={bid} ask={ask} mark={mark}) — fail-loud, no fallback"
            )
            snapshot["quote_source"] = "bid_null"
            return None, snapshot

        # (h) OK
        snapshot["quote_source"] = "schwab_chain"
        return float(bid), snapshot

    # ── Orden genérica ─────────────────────────────────────

    async def _place_single(
        self,
        ticker:     str,
        expiration: str,
        strike:     float,
        opt_type:   Literal["call", "put"],
        instruction: str,
        contracts:  int,
        limit:      float | None,
        strategy:   str = "",
        reason:     str = "",
        quote_snapshot: dict | None = None,
        closer_override: str | None = None,
        closer_reason_override: str | None = None,
    ) -> str | None:
        symbol = self.build_occ_symbol(ticker, expiration, opt_type, strike)

        # Pure Isolation logging: si vino un safety net override, lo usamos.
        # Si no, el gate determinará el closer (None si intra-strategy, rechazo si cross).
        isolation_info: dict | None = None
        if closer_override is not None:
            isolation_info = {
                "closer_strategy": closer_override,
                "closer_reason":   closer_reason_override,
            }

        # ── PAPER MODE ────────────────────────────────────
        if self.paper:
            # Si es cierre, calculamos P&L contra la posición abierta ANTES
            # de loguear, así queda persistido junto al trade en Firestore/CSV.
            # También rescatamos entry_price + opened_at_ts para enrichment.
            pnl_usd = None
            pnl_pct = None
            entry_price_override: Optional[float] = None
            opened_at_ts:         Optional[float] = None
            if instruction == "SELL_TO_CLOSE":
                for p in self._paper_positions:
                    if (p["ticker"] == ticker and p["expiration"] == expiration
                        and p["strike"] == strike and p["option_type"] == opt_type):
                        # Pure Isolation gate
                        opener_strategy = p.get("strategy", "")
                        if closer_override is not None:
                            # Safety net externo: bypass del gate, usar strategy del opener
                            strategy = opener_strategy if opener_strategy else strategy
                            log_strategy = strategy
                            # isolation_info ya viene seteado de la inicialización
                        else:
                            allowed, log_strategy, log_reason, gate_isolation_info = _resolve_close_isolation_v2(opener_strategy, strategy, reason)
                            if not allowed:
                                logger.warning(
                                    f"[ISOLATION] SELL_TO_CLOSE PAPER {symbol} bloqueado: "
                                    f"opener={opener_strategy!r} ≠ closer={strategy!r}."
                                )
                                return None
                            strategy = log_strategy   # preserva atribución al opener
                            reason = log_reason
                            isolation_info = gate_isolation_info  # siempre {None, None} si pasamos por aquí

                        entry   = p.get("entry_price", 0) or 0
                        if limit is not None:
                            current = limit
                            if entry:
                                pnl_pct = round((current - entry) / entry * 100, 2)
                            pnl_usd = round((current - entry) * p["contracts"] * 100, 2)
                        # else: limit ausente → pnl_usd y pnl_pct quedan None
                        # (data_quality="quote_unavailable" se setea más abajo)
                        entry_price_override = float(entry) if entry else None
                        opened_at_ts         = p.get("opened_at_ts")
                        break

            # Sprint 1 fix: data_quality refleja si el quote fue resuelto.
            data_quality = "quote_resolved"
            if limit is None:
                qs = quote_snapshot or {}
                data_quality = "quote_unavailable"
                logger.error(
                    f"[CLOSE_PAPER] limit unavailable for {symbol} "
                    f"(quote_source={qs.get('quote_source', 'unknown')}) "
                    f"— persisting with pnl=None, data_quality=quote_unavailable"
                )

            order_id = _log_paper_trade(
                action      = instruction,
                symbol      = symbol,
                ticker      = ticker,
                contracts   = contracts,
                limit       = limit,
                option_type = opt_type,
                expiration  = expiration,
                strike      = strike,
                strategy    = strategy,
                reason      = reason,
                pnl_usd     = pnl_usd,
                pnl_pct     = pnl_pct,
                macro_feeds = self._macro_feeds,
                entry_price_override = entry_price_override,
                opened_at_ts         = opened_at_ts,
                quote_snapshot       = quote_snapshot,
                data_quality         = data_quality,
                isolation_info       = isolation_info,
            )
            # Actualizar posiciones paper en memoria
            self._update_paper_positions(
                instruction, ticker, expiration, strike,
                opt_type, contracts, limit, order_id,
                strategy=strategy,
            )
            return order_id

        # ── LIVE MODE ─────────────────────────────────────
        account_id = await asyncio.get_event_loop().run_in_executor(
            None, self.get_account_id
        )

        order = {
            "orderType":         "LIMIT" if limit else "MARKET",
            "session":           "NORMAL",
            "duration":          "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [{
                "instruction": instruction,
                "quantity":    contracts,
                "instrument":  {"symbol": symbol, "assetType": "OPTION"},
            }],
        }
        if limit:
            order["price"] = round(limit, 2)

        label = f"{instruction} {contracts}x {symbol}"

        # LIVE isolation gate ANTES de _submit_order: post-submit la orden ya está en broker.
        opened: Optional[dict] = None
        if instruction == "SELL_TO_CLOSE":
            opened = self._open_positions.get(symbol)
            if opened:
                opener_strategy = opened.get("strategy", "")
                allowed, log_strategy, _log_reason, _isolation_info = _resolve_close_isolation_v2(
                    opener_strategy, strategy, reason
                )
                if not allowed:
                    logger.warning(
                        f"[ISOLATION] SELL_TO_CLOSE LIVE {symbol} bloqueado: "
                        f"opener={opener_strategy!r} ≠ closer={strategy!r}."
                    )
                    return None
                strategy = log_strategy

        order_id = await self._submit_order(account_id, order, label)

        # Persistencia LIVE → Firestore (idempotente via key {ts}_{ticker}_{action})
        # y update de `_open_positions` en memoria para computar P&L en close.
        if order_id:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_est = round((limit or 0) * contracts * 100, 2)
            pnl_usd = None
            pnl_pct = None
            entry_price_override: Optional[float] = None
            opened_at_ts:         Optional[float] = None
            # TODO(safety_net_logging): Path LIVE no tiene closer_strategy/closer_reason
            # implementados todavía. Cuando se active LIVE trading, replicar el patrón
            # de PAPER (closer_override passthrough + isolation_info al log).
            # Decisión del 13-may-2026: EOLO es paper-only, no es prioridad mes 1.
            if instruction == "SELL_TO_CLOSE":
                if opened:
                    self._open_positions.pop(symbol, None)
                    entry   = opened.get("entry_price", 0) or 0
                    if limit is not None:
                        current = limit
                        if entry:
                            pnl_pct = round((current - entry) / entry * 100, 2)
                        pnl_usd = round((current - entry) * opened.get("contracts", contracts) * 100, 2)
                    # else: limit ausente → pnl_usd y pnl_pct quedan None (init)
                    entry_price_override = float(entry) if entry else None
                    opened_at_ts         = opened.get("opened_at_ts")
            elif instruction == "BUY_TO_OPEN":
                self._open_positions[symbol] = {
                    "ticker":       ticker,
                    "expiration":   expiration,
                    "strike":       strike,
                    "option_type":  opt_type,
                    "contracts":    contracts,
                    "entry_price":  limit if limit is not None else 0,
                    "opened_at":    timestamp,
                    "opened_at_ts": time.time(),  # epoch para hold_seconds en cierre
                    "order_id":     order_id,
                    "strategy":     strategy,
                }

            # Enrichment LIVE — mismo helper que paper, modo LIVE usa slippage real.
            side = "BUY" if "BUY" in instruction else "SELL"
            enrich: dict = {}
            if build_enrichment is not None:
                try:
                    enrich = build_enrichment(
                        ts_utc          = datetime.now(timezone.utc),
                        asset_class     = "stock",
                        side            = side,
                        mode            = "LIVE",
                        expected_price  = limit,   # mid/limit que enviamos
                        fill_price      = limit,   # Schwab no nos devuelve fill aquí; usar limit como proxy
                        macro_feeds     = self._macro_feeds,
                        spy_ret_5d_fn   = None,
                        entry_price     = entry_price_override,
                        opened_at_ts    = opened_at_ts,
                        reason          = reason or "",
                        counter_key     = "eolo_v2",
                    ) or {}
                except Exception as e:
                    logger.debug(f"[LIVE] build_enrichment falló: {e}")

            trade_payload = {
                "timestamp":   timestamp,
                "mode":        "LIVE",
                "action":      instruction,
                "ticker":      ticker,
                "option_type": opt_type,
                "expiration":  expiration,
                "strike":      strike,
                "contracts":   contracts,
                "limit_price": limit if limit is not None else None,
                "total_est":   total_est,
                "symbol":      symbol,
                "strategy":    strategy,
                "reason":      (reason[:300] if reason else ""),
                "order_id":    order_id,
                "pnl_usd":     pnl_usd,
                "pnl_pct":     pnl_pct,
            }
            if enrich:
                trade_payload.update(enrich)
            _persist_trade_to_firestore(trade_payload)

        return order_id

    def _update_paper_positions(
        self,
        instruction: str,
        ticker:      str,
        expiration:  str,
        strike:      float,
        opt_type:    str,
        contracts:   int,
        entry_price: float | None,
        order_id:    str,
        strategy:    str = "",
    ):
        """
        Mantiene el estado de posiciones paper en memoria.
        BUY_TO_OPEN  → agrega posición
        SELL_TO_CLOSE → calcula P&L y elimina posición
        """
        symbol = self.build_occ_symbol(ticker, expiration, opt_type, strike)

        if instruction == "BUY_TO_OPEN":
            self._paper_positions.append({
                "order_id":        order_id,
                "symbol":          symbol,
                "ticker":          ticker,
                "expiration":      expiration,
                "strike":          strike,
                "option_type":     opt_type,
                "contracts":       contracts,
                "long":            True,
                "entry_price":     entry_price or 0,
                "current_price":   entry_price or 0,
                "market_value":    (entry_price or 0) * contracts * 100,
                "unrealized_pnl":  0.0,
                "unrealized_pnl_pct": 0.0,
                "opened_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "opened_at_ts":    time.time(),  # epoch para hold_seconds en enrichment
                "strategy":        strategy,
            })
            logger.info(
                f"[📄 PAPER] Posición abierta: {ticker} {opt_type.upper()} "
                f"K={strike} exp={expiration} x{contracts} @ ${entry_price or 0:.2f}"
            )

        elif instruction == "SELL_TO_CLOSE":
            # Buscar y eliminar la posición correspondiente.
            # (El P&L ya fue logueado + notificado desde `_log_paper_trade`
            #  con los valores que calcula `_place_single` antes del log,
            #  así que acá solo actualizamos estado en memoria.)
            closed = [
                p for p in self._paper_positions
                if (p["ticker"] == ticker and p["expiration"] == expiration
                    and p["strike"] == strike and p["option_type"] == opt_type)
            ]
            for pos in closed:
                entry   = pos.get("entry_price", 0) or 0
                current = entry_price if entry_price is not None else entry
                pnl_usd = round((current - entry) * pos["contracts"] * 100, 2) if entry else 0.0
                icon    = "🎯" if pnl_usd > 0 else "🛑"
                logger.info(
                    f"[📄 PAPER] {icon} Posición cerrada (state): {pos['symbol']} "
                    f"entry=${entry:.2f} exit=${current:.2f}"
                )
                self._paper_positions.remove(pos)

    # ── Submit + polling ───────────────────────────────────

    async def _submit_order(self, account_id: str, order: dict,
                             label: str) -> str | None:
        """
        Envía la orden a Schwab y hace polling hasta que sea FILLED o falle.
        Retorna el order_id (str) o None.
        """
        loop = asyncio.get_event_loop()
        url  = f"{SCHWAB_TRADER_BASE}/accounts/{account_id}/orders"

        logger.info(f"[TRADER] Enviando orden: {label}")

        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    url,
                    headers=self._headers(),
                    json=order,
                    timeout=15,
                )
            )
        except Exception as e:
            logger.error(f"[TRADER] Error HTTP enviando orden: {e}")
            return None

        if resp.status_code not in (200, 201):
            logger.error(
                f"[TRADER] Orden rechazada ({resp.status_code}): {resp.text[:300]}"
            )
            return None

        # Schwab retorna el orderId en el header Location
        location = resp.headers.get("Location", "")
        order_id = location.split("/")[-1] if location else None

        if not order_id:
            # Intentar parsear del body si viene
            try:
                body = resp.json()
                order_id = str(body.get("orderId", ""))
            except Exception:
                pass

        if not order_id:
            logger.warning(f"[TRADER] Orden enviada pero sin order_id en Location header")
            return None

        logger.info(f"[TRADER] Orden {order_id} enviada ✅ — polling...")

        # Polling de estado
        filled = await self._poll_order_status(account_id, order_id)
        if filled:
            logger.info(f"[TRADER] Orden {order_id} FILLED ✅")
        else:
            logger.warning(f"[TRADER] Orden {order_id} no llenada en {ORDER_FILL_TIMEOUT}s")

        return order_id

    async def _poll_order_status(self, account_id: str,
                                  order_id: str) -> bool:
        """
        Polling hasta FILLED o timeout.
        Retorna True si fue llenada.
        """
        loop     = asyncio.get_event_loop()
        url      = f"{SCHWAB_TRADER_BASE}/accounts/{account_id}/orders/{order_id}"
        deadline = time.time() + ORDER_FILL_TIMEOUT

        while time.time() < deadline:
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(url, headers=self._headers(), timeout=10)
                )
                resp.raise_for_status()
                data   = resp.json()
                status = data.get("status", "")

                logger.debug(f"[TRADER] Orden {order_id} status={status}")

                if status == "FILLED":
                    return True
                if status in ("CANCELED", "REJECTED", "EXPIRED"):
                    logger.warning(f"[TRADER] Orden {order_id} terminó con status={status}")
                    return False

            except Exception as e:
                logger.warning(f"[TRADER] Error polling orden {order_id}: {e}")

            await asyncio.sleep(ORDER_POLL_INTERVAL)

        return False

    # ── Posiciones abiertas ────────────────────────────────

    async def get_positions(self) -> list[dict]:
        """
        Retorna lista de posiciones abiertas de opciones.
        En paper mode: retorna posiciones en memoria.
        En live mode: consulta Schwab REST.
        """
        if self.paper:
            return list(self._paper_positions)

        loop = asyncio.get_event_loop()
        account_id = await loop.run_in_executor(None, self.get_account_id)
        url = f"{SCHWAB_TRADER_BASE}/accounts/{account_id}?fields=positions"

        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=self._headers(), timeout=10)
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[TRADER] Error obteniendo posiciones: {e}")
            return []

        positions = (
            data.get("securitiesAccount", {}).get("positions", [])
        )

        options_positions = []
        for pos in positions:
            instrument = pos.get("instrument", {})
            if instrument.get("assetType") != "OPTION":
                continue

            symbol = instrument.get("symbol", "")
            parsed = self._parse_occ_symbol(symbol)

            options_positions.append({
                "symbol":          symbol,
                "ticker":          parsed.get("ticker", ""),
                "expiration":      parsed.get("expiration", ""),
                "strike":          parsed.get("strike", 0),
                "option_type":     parsed.get("option_type", ""),
                "contracts":       pos.get("longQuantity", 0) or pos.get("shortQuantity", 0),
                "long":            pos.get("longQuantity", 0) > 0,
                "entry_price":     pos.get("averagePrice", 0),
                "current_price":   pos.get("marketValue", 0) / max(pos.get("longQuantity", 1), 1) / 100,
                "market_value":    pos.get("marketValue", 0),
                "unrealized_pnl":  pos.get("currentDayProfitLoss", 0),
                "unrealized_pnl_pct": pos.get("currentDayProfitLossPercentage", 0),
            })

        return options_positions

    # ── Cancelar orden ─────────────────────────────────────

    async def cancel_order(self, order_id: str) -> bool:
        """Cancela una orden pendiente."""
        loop = asyncio.get_event_loop()
        account_id = await loop.run_in_executor(None, self.get_account_id)
        url = f"{SCHWAB_TRADER_BASE}/accounts/{account_id}/orders/{order_id}"

        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.delete(url, headers=self._headers(), timeout=10)
            )
            if resp.status_code == 200:
                logger.info(f"[TRADER] Orden {order_id} cancelada ✅")
                return True
            else:
                logger.warning(f"[TRADER] No se pudo cancelar orden {order_id}: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"[TRADER] Error cancelando orden {order_id}: {e}")
            return False

    # ── Cerrar todas las posiciones ────────────────────────

    async def close_all_positions(
        self,
        closer_override: str | None = None,
        closer_reason_override: str | None = None,
    ) -> list[str]:
        """
        Cierra todas las posiciones de opciones abiertas.
        En paper mode: cierra posiciones en memoria y loguea P&L.
        Retorna lista de order_ids de cierre.

        closer_override / closer_reason_override: si vienen seteados,
        identifican que este cierre fue disparado por un safety net externo
        (auto_close 15:27 ET, close_all command, etc.) y se loguea en el
        trade payload de cada posición cerrada. Ver _resolve_close_isolation_v2.
        """
        positions = await self.get_positions()
        close_orders = []

        for pos in positions:
            if not pos["long"] or pos["contracts"] <= 0:
                continue

            strategy = pos.get("strategy", "")
            reason   = pos.get("reason", "")
            opt_type = pos["option_type"]
            if opt_type == "call":
                order_id = await self.close_long_call(
                    pos["ticker"], pos["expiration"],
                    pos["strike"], pos["contracts"],
                    strategy=strategy, reason=reason,
                    closer_override=closer_override,
                    closer_reason_override=closer_reason_override,
                )
            else:
                order_id = await self.close_long_put(
                    pos["ticker"], pos["expiration"],
                    pos["strike"], pos["contracts"],
                    strategy=strategy, reason=reason,
                    closer_override=closer_override,
                    closer_reason_override=closer_reason_override,
                )

            if order_id:
                close_orders.append(order_id)
                logger.info(
                    f"[TRADER] Cierre ejecutado: {pos['symbol']} "
                    f"x{pos['contracts']} → order {order_id}"
                )

        return close_orders

    # ── Utilidades ─────────────────────────────────────────

    @staticmethod
    def _parse_occ_symbol(symbol: str) -> dict:
        """
        Parsea un símbolo OCC a sus componentes.
        Ej: "SOXL  250516C00045000" → {ticker, expiration, option_type, strike}
        """
        try:
            # Remover espacios extra y parsear
            s = symbol.replace(" ", "")
            # Detectar el punto donde empieza la fecha (6 dígitos)
            match = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', s)
            if not match:
                return {}

            ticker_raw, date_str, cp, strike_str = match.groups()
            date_obj    = datetime.strptime(date_str, "%y%m%d")
            expiration  = date_obj.strftime("%Y-%m-%d")
            strike      = int(strike_str) / 1000
            option_type = "call" if cp == "C" else "put"

            return {
                "ticker":      ticker_raw,
                "expiration":  expiration,
                "strike":      strike,
                "option_type": option_type,
            }
        except Exception:
            return {}

    async def execute_decision(self, decision: dict) -> str | None:
        """
        Conveniencia: recibe el dict de decisión de OptionsBrain
        y llama al método correcto.
        """
        action      = decision.get("action", "HOLD")
        ticker      = decision.get("ticker", "")
        exp         = decision.get("expiration")
        strike      = decision.get("strike")
        opt_type    = decision.get("option_type", "call")
        contracts   = decision.get("contracts", 1)
        limit       = decision.get("limit_price")
        reason      = decision.get("reason", "")
        confidence  = decision.get("confidence", "")
        mp_type     = decision.get("mispricing_type") or ""
        _raw_strat  = mp_type if mp_type else f"claude_{confidence.lower()}" if confidence else "claude_bot"
        strategy    = _raw_strat.lower()

        if action == "BUY" and exp and strike:
            if opt_type == "call":
                return await self.open_long_call(ticker, exp, strike, contracts, limit,
                                                  strategy=strategy, reason=reason)
            else:
                return await self.open_long_put(ticker, exp, strike, contracts, limit,
                                                 strategy=strategy, reason=reason)

        elif action == "SELL_TO_CLOSE" and exp and strike:
            if opt_type == "call":
                return await self.close_long_call(ticker, exp, strike, contracts, limit,
                                                   strategy=strategy, reason=reason)
            else:
                return await self.close_long_put(ticker, exp, strike, contracts, limit,
                                                  strategy=strategy, reason=reason)

        elif action == "SELL_SPREAD":
            # Credit spread — Theta Harvest
            return await self.open_credit_spread(
                ticker       = ticker,
                expiration   = decision.get("expiration", ""),
                spread_type  = decision.get("spread_type", "put_credit_spread"),
                short_strike = decision.get("short_strike", 0),
                long_strike  = decision.get("long_strike", 0),
                contracts    = contracts,
                net_credit   = decision.get("net_credit"),
                strategy     = decision.get("strategy", "theta_harvest").lower(),
                reason       = reason,
            )

        elif action == "CLOSE_SPREAD":
            # Cierre de credit spread (profit/stop/time)
            return await self.close_spread(
                ticker       = ticker,
                expiration   = decision.get("expiration", ""),
                spread_type  = decision.get("spread_type", "put_credit_spread"),
                short_strike = decision.get("short_strike", 0),
                long_strike  = decision.get("long_strike", 0),
                contracts    = contracts,
                net_debit    = decision.get("close_debit"),
                reason       = reason,
            )

        else:
            return None
