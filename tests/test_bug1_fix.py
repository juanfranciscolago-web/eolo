import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_guard_present_in_source():
    path = os.path.join(ROOT, "eolo-options", "execution", "options_trader.py")
    with open(path) as f:
        source = f.read()

    occurrences = source.count("if limit is not None:")
    assert occurrences >= 2, f"Esperado >=2, encontrado {occurrences}"


def test_old_pattern_removed():
    path = os.path.join(ROOT, "eolo-options", "execution", "options_trader.py")
    with open(path) as f:
        source = f.read()

    bad_pattern = "current = limit if limit is not None else entry"
    assert bad_pattern not in source, \
        f"Patrón viejo todavía presente: {bad_pattern}"


def test_buy_to_open_unchanged():
    path = os.path.join(ROOT, "eolo-options", "execution", "options_trader.py")
    with open(path) as f:
        source = f.read()

    open_pattern = '"entry_price":  limit if limit is not None else 0'
    assert open_pattern in source, \
        "BUY_TO_OPEN line 734 NO debe haber cambiado"


def test_pnl_none_when_limit_none():
    """Replica la lógica del fix en aislamiento."""
    pnl_usd = None
    pnl_pct = None
    entry = 5.0
    limit = None
    contracts = 1

    if limit is not None:
        current = limit
        if entry:
            pnl_pct = round((current - entry) / entry * 100, 2)
        pnl_usd = round((current - entry) * contracts * 100, 2)
    # else: pnl_usd y pnl_pct quedan None

    assert pnl_usd is None, f"Esperado None, obtenido {pnl_usd}"
    assert pnl_pct is None, f"Esperado None, obtenido {pnl_pct}"


def test_pnl_calculated_when_limit_present():
    """Replica la lógica con limit válido."""
    pnl_usd = None
    pnl_pct = None
    entry = 5.0
    limit = 7.5
    contracts = 2

    if limit is not None:
        current = limit
        if entry:
            pnl_pct = round((current - entry) / entry * 100, 2)
        pnl_usd = round((current - entry) * contracts * 100, 2)

    assert pnl_usd == 500.0, f"Esperado 500.0, obtenido {pnl_usd}"
    assert pnl_pct == 50.0, f"Esperado 50.0, obtenido {pnl_pct}"


def test_pnl_with_entry_zero():
    """Si entry=0 (porque BUY persistió mal), pnl_pct queda None."""
    pnl_usd = None
    pnl_pct = None
    entry = 0  # caso del bug del open (no fixeamos hoy)
    limit = 7.5
    contracts = 1

    if limit is not None:
        current = limit
        if entry:
            pnl_pct = round((current - entry) / entry * 100, 2)
        pnl_usd = round((current - entry) * contracts * 100, 2)

    # pnl_pct queda None porque "if entry:" es False
    assert pnl_pct is None, f"Esperado None, obtenido {pnl_pct}"
    # pnl_usd se calcula igual (= current * contracts * 100)
    assert pnl_usd == 750.0
