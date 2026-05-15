# ============================================================
#  EOLO v2 — Smoke test Sem 2 (paper exit logic + feed guard)
#
#  Validación funcional de los 2 fixes commiteados en Sem 2:
#    - 833df0b feat(v2): refresh paper current_price from chain bid
#    - cef93c0 feat(v2): feed corruption guard pre-mispricing scanner
#    - 571466f fix(v2): FEED_GUARD warns instead of errors
#
#  Uso: python3 smoke_sem2_test.py
#  Exit code: 0 si todo passes, 1 si algo falla.
# ============================================================
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, ".."))
sys.path.insert(0, os.path.join(BASE_DIR, "..", "Bot"))

# Mock dependencias de nube (mismo patrón que smoke_test.py)
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.cloud", MagicMock())
sys.modules.setdefault("google.cloud.secretmanager", MagicMock())
sys.modules.setdefault("google.cloud.firestore", MagicMock())
sys.modules.setdefault("helpers", MagicMock())
sys.modules.setdefault("secret_stuff", MagicMock())

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"


def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")


# ──────────────────────────────────────────────────────────
# Tests paper exit
# ──────────────────────────────────────────────────────────

async def test_paper_exit_TP():
    """Test 1: TP dispara cuando bid resuelto > entry * (1 + TP%)."""
    print("\n[1/5] Paper exit: TP dispara con bid alto")
    from eolo_v2_main import EoloV2

    bot = MagicMock(spec=EoloV2)
    bot._open_positions = [{
        "symbol": "SPY 250522C00450",
        "ticker": "SPY", "expiration": "2026-05-22", "strike": 450.0,
        "option_type": "call",
        "entry_price": 2.00, "current_price": 0,
        "contracts": 1,
    }]
    bot._default_stop_loss_pct = 25.0
    bot._default_take_profit_pct = 50.0
    bot.trader = MagicMock()
    bot.trader.resolve_position_price = MagicMock(return_value=3.50)  # +75% > 50% TP
    bot._close_position = AsyncMock()

    await EoloV2._check_exit_conditions(bot)

    if bot._close_position.call_count == 1:
        ok(f"_close_position llamado 1 vez (TP), bid=3.50 sobre entry=2.00 (+75%)")
        return True
    fail(f"esperaba _close_position llamado 1 vez, fue llamado {bot._close_position.call_count}")
    return False


async def test_paper_exit_SL():
    """Test 2: SL dispara cuando bid resuelto < entry * (1 - SL%)."""
    print("\n[2/5] Paper exit: SL dispara con bid bajo")
    from eolo_v2_main import EoloV2

    bot = MagicMock(spec=EoloV2)
    bot._open_positions = [{
        "symbol": "SPY 250522P00450",
        "ticker": "SPY", "expiration": "2026-05-22", "strike": 450.0,
        "option_type": "put",
        "entry_price": 2.00, "current_price": 0,
        "contracts": 1,
    }]
    bot._default_stop_loss_pct = 25.0
    bot._default_take_profit_pct = 50.0
    bot.trader = MagicMock()
    bot.trader.resolve_position_price = MagicMock(return_value=1.40)  # -30% < -25% SL
    bot._close_position = AsyncMock()

    await EoloV2._check_exit_conditions(bot)

    if bot._close_position.call_count == 1:
        ok(f"_close_position llamado 1 vez (SL), bid=1.40 sobre entry=2.00 (-30%)")
        return True
    fail(f"esperaba _close_position llamado 1 vez, fue llamado {bot._close_position.call_count}")
    return False


# ──────────────────────────────────────────────────────────
# Tests feed corruption guard
# ──────────────────────────────────────────────────────────

def test_feed_guard_rejects_14may_pattern():
    """Test 3: rechazo del patrón de feed corrupto del 14-may."""
    print("\n[3/5] Feed guard rechaza patrón 14-may (OHLC inconsistent)")
    from eolo_v2_main import EoloV2

    chain = {
        "underlying": {
            "price": 200.50,
            "open": 299.25,
            "high": 305.0,
            "low": 198.0,
            "close": 51988.0,   # absurdo — caso AAPL 14-may UTC 15:26
        }
    }

    sane, reason = EoloV2._validate_chain_sanity(MagicMock(), "AAPL", chain)

    if not sane and "OHLC inconsistent" in reason:
        ok(f"rechazó: {reason}")
        return True
    fail(f"esperaba reject por OHLC inconsistent, obtuve sane={sane}, reason={reason!r}")
    return False


def test_feed_guard_accepts_healthy():
    """Test 4: chain saludable pasa los 3 checks."""
    print("\n[4/5] Feed guard acepta chain saludable")
    from eolo_v2_main import EoloV2

    chain = {
        "underlying": {
            "price": 450.0, "open": 448.0, "high": 451.5,
            "low": 447.8, "close": 449.2,
        },
        "expirations": ["2026-05-22"],
        "calls": {
            "2026-05-22": {
                "445.0": {"bid": 6.20, "ask": 6.30},
                "447.5": {"bid": 4.50, "ask": 4.60},
                "450.0": {"bid": 3.10, "ask": 3.20},
                "452.5": {"bid": 2.10, "ask": 2.20},
                "455.0": {"bid": 1.40, "ask": 1.50},
            }
        }
    }

    sane, reason = EoloV2._validate_chain_sanity(MagicMock(), "SPY", chain)

    if sane and reason == "":
        ok("aceptó chain saludable (price razonable, OHLC consistente, bid/ask coherentes)")
        return True
    fail(f"esperaba sane=True, obtuve sane={sane}, reason={reason!r}")
    return False


def test_feed_guard_accepts_minimal():
    """Test 5: chain mínimo (solo underlying.price) NO se rechaza por incompletitud."""
    print("\n[5/5] Feed guard acepta chain mínimo (política no-falsear)")
    from eolo_v2_main import EoloV2

    chain = {"underlying": {"price": 100.0}}

    sane, reason = EoloV2._validate_chain_sanity(MagicMock(), "NVDA", chain)

    if sane and reason == "":
        ok("aceptó chain mínimo (Check 1 pasa, Check 2 salta sin OHLC, Check 3 salta sin expirations)")
        return True
    fail(f"esperaba sane=True, obtuve sane={sane}, reason={reason!r}")
    return False


# ──────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────

async def main():
    print("=" * 64)
    print("SMOKE SEM 2 — paper exit logic + feed corruption guard")
    print("=" * 64)

    results = []
    results.append(await test_paper_exit_TP())
    results.append(await test_paper_exit_SL())
    results.append(test_feed_guard_rejects_14may_pattern())
    results.append(test_feed_guard_accepts_healthy())
    results.append(test_feed_guard_accepts_minimal())

    passed = sum(results)
    total = len(results)

    print("\n" + "=" * 64)
    if passed == total:
        print(f"{GREEN}✅ SMOKE SEM 2 PASADO — {passed}/{total} tests OK{RESET}")
        return 0
    else:
        print(f"{RED}❌ SMOKE SEM 2 FALLÓ — {passed}/{total} tests OK{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
