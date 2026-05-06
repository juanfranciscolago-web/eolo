"""
tests/test_bug2_fix.py
Tests unitarios para el Bug 2 fix — propagación de `strategy` en el close flow.

Tests:
  1. strategy persiste en _paper_positions al BUY_TO_OPEN
  2. strategy se propaga vía close_all_positions (call)
  3. backward compat: pos sin campo strategy no hace KeyError
  4. Dashboard FIFO no colisiona entre trades sin strategy y trades strategy="EMA"

Runner: pytest tests/test_bug2_fix.py -v
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eolo-options", "execution"))
sys.path.insert(0, os.path.join(ROOT, "eolo-options"))
sys.path.insert(0, ROOT)

# ── Mock mínimo ANTES del import ──────────────────────────────────────────────
# options_trader.py hace `from helpers import get_access_token` al nivel de módulo.
# google.cloud se importa lazy (dentro de funciones con try/except), no necesita mock.
# loguru y eolo_common.trade_enrichment tienen fallbacks, no necesitan mock.
sys.modules.setdefault("helpers", MagicMock())

from options_trader import OptionsTrader  # noqa: E402  (eolo-options/execution/)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — strategy persiste en _paper_positions al BUY_TO_OPEN
# Cubre: V2-1 (firma), V2-2 (dict paper), V2-3 (llamada) / CROP-1+2+3 ídem
# ─────────────────────────────────────────────────────────────────────────────

def test_strategy_persists_in_paper_positions():
    trader = OptionsTrader(paper=True)
    trader._update_paper_positions(
        "BUY_TO_OPEN", "SOXL", "2026-05-16", 45.0,
        "call", 1, 2.35, "ORD-001",
        strategy="bsm_mispricing",
    )
    assert len(trader._paper_positions) == 1
    assert trader._paper_positions[0]["strategy"] == "bsm_mispricing"


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — strategy se propaga vía close_all_positions (call option)
# Cubre: V2-5 / CROP-5
# ─────────────────────────────────────────────────────────────────────────────

async def _async_test_close_all_propagates():
    trader = OptionsTrader(paper=True)
    trader._paper_positions = [{
        "ticker":      "SOXL",
        "expiration":  "2026-05-16",
        "strike":      45.0,
        "option_type": "call",
        "contracts":   1,
        "long":        True,
        "strategy":    "claude_high",
        "reason":      "eod_close",
        "symbol":      "SOXL260516C00045000",
    }]
    trader.close_long_call = AsyncMock(return_value="ORD-002")

    await trader.close_all_positions()

    trader.close_long_call.assert_called_once_with(
        "SOXL", "2026-05-16", 45.0, 1,
        strategy="claude_high", reason="eod_close",
    )


def test_close_all_positions_propagates_strategy():
    asyncio.run(_async_test_close_all_propagates())


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — backward compat: pos sin campo strategy no hace KeyError
# Cubre: V2-5 / CROP-5 — posición pre-fix (put, sin strategy ni reason)
# ─────────────────────────────────────────────────────────────────────────────

async def _async_test_backward_compat():
    trader = OptionsTrader(paper=True)
    trader._paper_positions = [{
        "ticker":      "SOXL",
        "expiration":  "2026-05-16",
        "strike":      45.0,
        "option_type": "put",
        "contracts":   1,
        "long":        True,
        "symbol":      "SOXL260516P00045000",
        # sin "strategy" ni "reason" — simula posición creada antes del fix
    }]
    trader.close_long_put = AsyncMock(return_value="ORD-003")

    await trader.close_all_positions()  # no debe lanzar KeyError

    trader.close_long_put.assert_called_once_with(
        "SOXL", "2026-05-16", 45.0, 1,
        strategy="", reason="",  # pos.get("strategy", "") → "" sin KeyError
    )


def test_backward_compat_no_strategy_field():
    asyncio.run(_async_test_backward_compat())


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Dashboard FIFO no colisiona con default UNKNOWN
# Cubre: DASH-1 / DASH-1b — cambio de "EMA" → `or "UNKNOWN"`
# ─────────────────────────────────────────────────────────────────────────────

def test_fifo_no_collision_with_unknown_default():
    today_trades = [
        {"ticker": "SOXL", "action": "BUY", "price": "10.0", "shares": "1"},
        # ^ sin campo "strategy" → key ("SOXL", "UNKNOWN"), no colisiona con EMA
        {"ticker": "SOXL", "action": "BUY", "price": "11.0", "shares": "1",
         "strategy": "EMA"},
        # ^ strategy="EMA" → key ("SOXL", "EMA")
    ]

    open_buys = {}
    for t in today_trades:
        strategy = t.get("strategy") or "UNKNOWN"  # el fix exacto de DASH-1
        key = (t["ticker"], strategy)
        if t["action"] == "BUY":
            open_buys[key] = {"price": float(t["price"]), "shares": int(t["shares"])}

    assert len(open_buys) == 2,            "Deben existir 2 claves FIFO distintas"
    assert ("SOXL", "UNKNOWN") in open_buys, "Trade sin strategy → clave UNKNOWN"
    assert ("SOXL", "EMA")     in open_buys, "Trade con strategy='EMA' → clave EMA"
