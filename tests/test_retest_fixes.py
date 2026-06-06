# ============================================================
# Tests RETEST-FIX 2026-06-05 — pre-requisitos del re-test V1
#
# Cubre los 3 fixes que invalidaban el re-test:
#   1. Wrapper direccional: señal opuesta pasa como exit_only
#      (antes: HOLD → _LONG sin SELL de cierre, _SHORT sin cover → WR 0%)
#   2. trader.execute: exit_only solo cierra, nunca abre
#   3. close_all_open_positions cubre SHORTs (en source)
# ============================================================
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "Bot"))

import pytest


# ── 1. Wrapper direccional ──────────────────────────────────

def _mk_fn(signal):
    return lambda df, *a, **k: {"signal": signal, "reason": "base"}


def test_wrapper_long_pasa_buy():
    from eolo_common.strategies_v3.strategies import _directional_wrapper
    out = _directional_wrapper(_mk_fn("BUY"), "long")(None)
    assert out["signal"] == "BUY"
    assert not out.get("exit_only")


def test_wrapper_long_sell_es_exit_only():
    """El SELL de un _LONG es su señal de SALIDA — debe pasar marcado."""
    from eolo_common.strategies_v3.strategies import _directional_wrapper
    out = _directional_wrapper(_mk_fn("SELL"), "long")(None)
    assert out["signal"] == "SELL"
    assert out.get("exit_only") is True


def test_wrapper_short_buy_es_exit_only():
    """El BUY de un _SHORT es su BUY_TO_COVER — debe pasar marcado."""
    from eolo_common.strategies_v3.strategies import _directional_wrapper
    out = _directional_wrapper(_mk_fn("BUY"), "short")(None)
    assert out["signal"] == "BUY"
    assert out.get("exit_only") is True


def test_wrapper_hold_sigue_hold():
    from eolo_common.strategies_v3.strategies import _directional_wrapper
    out = _directional_wrapper(_mk_fn("HOLD"), "long")(None)
    assert out["signal"] == "HOLD"


# ── 2. trader.execute con exit_only ─────────────────────────

_BT_CACHE = {}


def _load_bot_trader():
    """Carga Bot/bot_trader.py por path explícito.

    Hay un bot_trader.py legacy en el repo root; un `import bot_trader`
    plano puede resolver al equivocado según qué suite corrió antes.
    El deployado (Bot/Dockerfile copia Bot/ → /app) es Bot/bot_trader.py.
    """
    if "mod" not in _BT_CACHE:
        import importlib.util
        bot_dir = os.path.join(ROOT, "Bot")
        # Aislar deps: otras suites pueden haber cacheado los helpers/marketdata
        # del repo root (versiones legacy distintas) bajo los mismos nombres.
        _dep_names = ("helpers", "marketdata", "secret_stuff")
        saved = {n: sys.modules.pop(n, None) for n in _dep_names}
        sys.path.insert(0, bot_dir)
        try:
            path = os.path.join(bot_dir, "bot_trader.py")
            spec = importlib.util.spec_from_file_location("bot_trader_v1_test", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["bot_trader_v1_test"] = mod
            spec.loader.exec_module(mod)
            _BT_CACHE["mod"] = mod
        finally:
            sys.path.remove(bot_dir)
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)
    return _BT_CACHE["mod"]


@pytest.fixture()
def bt(monkeypatch):
    _bt = _load_bot_trader()
    monkeypatch.setattr(_bt, "_log_trade", lambda *a, **k: None)
    monkeypatch.setattr(_bt, "_send_telegram", lambda *a, **k: None)
    monkeypatch.setattr(_bt, "save_positions", lambda *a, **k: None)
    monkeypatch.setattr(_bt, "_recover_entry_price", lambda *a, **k: None)
    monkeypatch.setattr(_bt, "_place_live_order", lambda *a, **k: None)
    for t in _bt.positions:
        _bt.positions[t] = None
        _bt.entry_prices[t] = None
        _bt.entry_open_ts[t] = None
    return _bt


def test_flat_sell_exit_only_no_abre_short(bt):
    bt.execute({"ticker": "SPY", "signal": "SELL", "price": 757.0,
                "strategy": "EMA_3_8_LONG", "_budget": 100,
                "_allow_short": True, "exit_only": True})
    assert bt.positions["SPY"] is None


def test_long_sell_exit_only_cierra(bt):
    bt.positions["SPY"] = "LONG"
    bt.entry_prices["SPY"] = 750.0
    bt.entry_open_ts["SPY"] = 0
    bt.execute({"ticker": "SPY", "signal": "SELL", "price": 757.0,
                "strategy": "EMA_3_8_LONG", "_budget": 100, "exit_only": True})
    assert bt.positions["SPY"] is None


def test_short_buy_de_short_cubre(bt):
    bt.positions["QQQ"] = "SHORT"
    bt.entry_prices["QQQ"] = 745.0
    bt.entry_open_ts["QQQ"] = 0
    bt.execute({"ticker": "QQQ", "signal": "BUY", "price": 742.0,
                "strategy": "EMA_3_8_SHORT", "_budget": 100})
    assert bt.positions["QQQ"] is None


def test_flat_buy_heuristica_confluence_short_no_abre_long(bt):
    """Path confluencia: el flag se pierde, la heurística por sufijo cubre."""
    bt.execute({"ticker": "QQQ", "signal": "BUY", "price": 742.0,
                "strategy": "CONFLUENCE:EMA_3_8_SHORT", "_budget": 100})
    assert bt.positions["QQQ"] is None


def test_flat_buy_normal_abre_long(bt):
    """Control: estrategia no-direccional abre LONG como siempre."""
    bt.execute({"ticker": "QQQ", "signal": "BUY", "price": 742.0,
                "strategy": "SQUEEZE", "_budget": 100})
    assert bt.positions["QQQ"] == "LONG"


def test_flat_sell_normal_con_allow_short_abre(bt):
    """Control: SELL no-direccional con allow_short sigue abriendo SHORT."""
    bt.execute({"ticker": "SPY", "signal": "SELL", "price": 757.0,
                "strategy": "SQUEEZE", "_budget": 100, "_allow_short": True})
    assert bt.positions["SPY"] == "SHORT"


# ── 3. Guards en source (close_all + auto_close + daily cap) ─

def _source(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def test_close_all_cubre_shorts():
    src = _source("Bot/bot_main.py")
    assert 'state in ("LONG", "SHORT")' in src, \
        "close_all_open_positions debe cerrar también SHORTs"


def test_auto_close_done_solo_en_exito():
    src = _source("Bot/bot_main.py")
    bloque = src.split("AUTO-CLOSE — cerrando")[1][:600]
    idx_try   = bloque.find("close_all_open_positions")
    idx_done  = bloque.find("auto_close_done_date = today")
    idx_exc   = bloque.find("except Exception")
    assert idx_try < idx_done < idx_exc, \
        "auto_close_done_date debe setearse dentro del try, antes del except"


def test_recover_entry_price_existe():
    src = _source("Bot/bot_trader.py")
    assert "_recover_entry_price" in src
    assert src.count("_recover_entry_price(ticker") >= 2, \
        "ambos cierres (LONG y SHORT) deben intentar recovery del entry"


# ── 4. Routing Tier 2 ampliado al universo V1 ───────────────

def _router():
    import importlib.util
    path = os.path.join(ROOT, "Bot", "strategy_router.py")
    spec = importlib.util.spec_from_file_location("strategy_router_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_V1_INDEX = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]
_V1_LEV = ["SOXL", "TSLL", "NVDL", "TQQQ"]
_V1_ALL = _V1_INDEX + _V1_LEV


def test_vwap_zscore_corre_en_v1_30m():
    """Antes solo JPM/AMZN (fuera de V1) → nunca disparaba."""
    f = _router().should_run_strategy
    assert all(f("vwap_zscore", t, 30) for t in _V1_ALL)


def test_supertrend_corre_en_leveraged_30m():
    """Antes QQQ/UNH pero el bloque itera leveraged → nunca disparaba."""
    f = _router().should_run_strategy
    assert all(f("supertrend", t, 30) for t in _V1_LEV)


def test_macd_bb_corre_en_leveraged_30m():
    f = _router().should_run_strategy
    assert all(f("macd_bb", t, 30) for t in _V1_LEV)


def test_gap_fade_sigue_gateada_a_60m():
    """No romper la restricción existente: gap_fade solo 60m+."""
    f = _router().should_run_strategy
    assert f("gap_fade", "SPY", 60) and not f("gap_fade", "SPY", 30)


def test_suite_corre_en_todos_los_tf_activos():
    f = _router().should_run_strategy
    assert all(f("ema_3_8", "SPY", tf) for tf in (5, 15, 30, 60))
