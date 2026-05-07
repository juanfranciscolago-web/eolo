import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eolo-crypto"))


# ── TEST 1 ───────────────────────────────────────────────────────────────────
def test_whitelist_has_rsi_sma200_with_btc_eth():
    import settings
    wl = settings.STRATEGY_SYMBOL_WHITELIST
    assert "rsi_sma200" in wl
    assert wl["rsi_sma200"] == {"BTCUSDT", "ETHUSDT"}


# ── TEST 2 ───────────────────────────────────────────────────────────────────
def test_whitelist_allows_symbol_in_list():
    wl = {"rsi_sma200": {"BTCUSDT", "ETHUSDT"}}
    strategy = "rsi_sma200"
    symbol = "BTCUSDT"

    _wl = wl.get(strategy)
    blocked = _wl is not None and symbol not in _wl
    assert blocked is False, "BTCUSDT debe pasar para rsi_sma200"


# ── TEST 3 ───────────────────────────────────────────────────────────────────
def test_whitelist_blocks_symbol_not_in_list():
    wl = {"rsi_sma200": {"BTCUSDT", "ETHUSDT"}}
    strategy = "rsi_sma200"
    symbol = "SOLUSDT"

    _wl = wl.get(strategy)
    blocked = _wl is not None and symbol not in _wl
    assert blocked is True, "SOLUSDT debe ser bloqueado para rsi_sma200"


# ── TEST 4 ───────────────────────────────────────────────────────────────────
def test_whitelist_allows_when_strategy_not_in_dict():
    wl = {"rsi_sma200": {"BTCUSDT", "ETHUSDT"}}
    strategy = "squeeze"  # NO está en whitelist

    for symbol in ["BTCUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT"]:
        _wl = wl.get(strategy)
        blocked = _wl is not None and symbol not in _wl
        assert blocked is False, f"{strategy} debe operar en {symbol}"


# ── TEST 5 ───────────────────────────────────────────────────────────────────
def test_filter_code_present_in_main():
    main_path = os.path.join(ROOT, "eolo-crypto", "eolo_crypto_main.py")
    with open(main_path) as f:
        source = f.read()

    assert "STRATEGY_SYMBOL_WHITELIST.get(strat)" in source
    assert "if _wl is not None and symbol not in _wl:" in source
    assert "[WHITELIST]" in source


# ── TEST 6 ───────────────────────────────────────────────────────────────────
def test_filter_NOT_in_sells_loop():
    main_path = os.path.join(ROOT, "eolo-crypto", "eolo_crypto_main.py")
    with open(main_path) as f:
        source = f.read()

    sells_idx = source.index("for strat in sells:")
    next_for  = source.index("for strat in ", sells_idx + 10)
    sells_block = source[sells_idx:next_for]

    assert "STRATEGY_SYMBOL_WHITELIST" not in sells_block, \
        "El filtro NO debe estar en el loop de sells (cierres deben permitirse)"
