"""
Smoke test — Sprint 2 fix exit_price en CROP (single-leg + spread).

Run pre-deploy durante market hours para validar quote fetch end-to-end:
  python3 scripts/smoke/resolve_close_limit_crop.py

Ejecuta DOS tests independientes:
  1. test_single_leg(): valida _resolve_close_limit (Block 2a)
  2. test_spread():     valida _resolve_spread_close_debit (Block 2c.1)

Output esperado (market hours):
  - single-leg: bid > 0, snapshot["quote_source"] == "schwab_chain",
                snapshot["snapshot_schema"] == "single_leg"
  - spread:     net_debit >= 0, quote_source == "schwab_chain",
                snapshot_schema == "spread", 12 keys quote_short_*/quote_long_*
                + quote_spot + quote_fetched_at + quote_source + snapshot_schema

Fuera de market hours: fail-loud paths exercised (bid_null / short_ask_null / no_chain_data).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # scripts/smoke/ → scripts/ → repo root
sys.path.insert(0, os.path.join(ROOT, "eolo-crop"))

from stream.options_chain import OptionChainFetcher
from execution.options_trader import OptionsTrader


EXPECTED_SOURCES = {
    "schwab_chain",
    "bid_null", "no_chain_data", "no_fetcher",       # single-leg fail reasons
    "short_ask_null", "long_bid_null",               # spread fail reasons
}


def _fetch_spy_chain_and_trader():
    """Helper: fetcher + chain + trader inyectado. Retorna (fetcher, chain, trader)."""
    fetcher = OptionChainFetcher(tickers=["SPY"], interval=30)
    print("→ Fetching SPY chain from Schwab (CROP module)...")
    chain = fetcher._fetch_chain("SPY")
    if chain is None:
        print("✗ Chain fetch falló (¿token expirado? ¿market data permission?)")
        return None, None, None
    fetcher._chains["SPY"] = chain
    fetcher._last_fetch["SPY"] = time.time()
    print(f"✓ Chain OK — {len(chain['expirations'])} expirations, "
          f"underlying.price={chain['underlying'].get('price')}")
    trader = OptionsTrader(paper=True, chain_fetcher=fetcher)
    return fetcher, chain, trader


def test_single_leg(fetcher, chain, trader):
    """Test _resolve_close_limit con strike ATM, expiración más cercana, CALL."""
    print("\n" + "=" * 60)
    print("TEST 1 — single-leg _resolve_close_limit")
    print("=" * 60)

    if not chain["expirations"]:
        print("✗ Sin expirations en chain")
        return False
    exp_first = chain["expirations"][0]
    strike_atm = fetcher.get_atm_strike("SPY")
    if strike_atm is None:
        print("✗ ATM strike no calculable")
        return False

    print(f"→ Probing SPY CALL exp={exp_first} strike={strike_atm}")
    bid, snapshot = trader._resolve_close_limit("SPY", exp_first, strike_atm, "call")
    print(f"  bid      = {bid}")
    print(f"  snapshot = {snapshot}")

    src = snapshot.get("quote_source")
    schema = snapshot.get("snapshot_schema")
    if src not in EXPECTED_SOURCES:
        print(f"✗ quote_source unexpected: {src!r}")
        return False
    if schema != "single_leg":
        print(f"✗ snapshot_schema esperado 'single_leg', got {schema!r}")
        return False
    print(f"✓ quote_source={src!r}, snapshot_schema={schema!r}")
    return True


def test_spread(fetcher, chain, trader):
    """Test _resolve_spread_close_debit con put credit spread sintético."""
    print("\n" + "=" * 60)
    print("TEST 2 — spread _resolve_spread_close_debit (put credit spread)")
    print("=" * 60)

    if not chain["expirations"]:
        print("✗ Sin expirations en chain")
        return False
    exp_first = chain["expirations"][0]
    atm = fetcher.get_atm_strike("SPY")
    if atm is None:
        print("✗ ATM strike no calculable")
        return False

    # Strikes disponibles de PUTS para la exp más cercana
    puts = chain.get("puts", {}).get(exp_first, {})
    if not puts:
        print(f"✗ Sin puts en exp={exp_first}")
        return False
    put_strikes = sorted(float(k) for k in puts.keys())

    # Buscar short strike: el primero <= atm (OTM put cerca del dinero)
    short_candidates = [s for s in put_strikes if s <= atm]
    if not short_candidates:
        print(f"✗ Sin strikes put <= {atm}")
        return False
    short_strike = short_candidates[-1]   # el más cercano al ATM

    # Buscar long strike: 3-5 strikes más OTM (más bajo)
    # Filtramos strikes < short y tomamos el 4to más bajo si existe
    lower = [s for s in put_strikes if s < short_strike]
    if not lower:
        print(f"✗ Sin strikes put < {short_strike} (no podemos armar spread)")
        return False
    # Step puede variar — pick el 4to más bajo si hay, else el último
    long_strike = lower[-4] if len(lower) >= 4 else lower[0]

    print(f"  ATM=${atm}  short_strike=${short_strike}  long_strike=${long_strike}  "
          f"width=${short_strike - long_strike}")
    print(f"→ Probing SPY put credit spread exp={exp_first} "
          f"K={short_strike}/{long_strike}")

    net_debit, snapshot = trader._resolve_spread_close_debit(
        "SPY", exp_first, short_strike, long_strike, "put"
    )
    print(f"  net_debit = {net_debit}")
    print(f"  snapshot keys = {sorted(snapshot.keys())}")
    print(f"  quote_short_bid={snapshot.get('quote_short_bid')}, "
          f"quote_short_ask={snapshot.get('quote_short_ask')}")
    print(f"  quote_long_bid={snapshot.get('quote_long_bid')}, "
          f"quote_long_ask={snapshot.get('quote_long_ask')}")
    print(f"  quote_spot={snapshot.get('quote_spot')}, "
          f"quote_fetched_at={snapshot.get('quote_fetched_at')}")

    src = snapshot.get("quote_source")
    schema = snapshot.get("snapshot_schema")
    if src not in EXPECTED_SOURCES:
        print(f"✗ quote_source unexpected: {src!r}")
        return False
    if schema != "spread":
        print(f"✗ snapshot_schema esperado 'spread', got {schema!r}")
        return False

    # Validar presencia de 12+ keys quote_short_*/quote_long_*
    spread_required_keys = (
        "quote_short_bid", "quote_short_ask", "quote_short_mid",
        "quote_short_last", "quote_short_mark", "quote_short_iv",
        "quote_long_bid", "quote_long_ask", "quote_long_mid",
        "quote_long_last", "quote_long_mark", "quote_long_iv",
        "quote_spot", "quote_fetched_at", "quote_source", "snapshot_schema",
    )
    missing = [k for k in spread_required_keys if k not in snapshot]
    if missing:
        print(f"✗ Faltan keys en snapshot: {missing}")
        return False
    print(f"✓ quote_source={src!r}, snapshot_schema={schema!r}, "
          f"keys completos ({len(spread_required_keys)})")

    # Si market hours y schwab_chain → net_debit debería ser float no-None
    if src == "schwab_chain" and net_debit is None:
        print(f"⚠ quote_source=schwab_chain pero net_debit=None (inesperado)")
        return False
    return True


def main():
    fetcher, chain, trader = _fetch_spy_chain_and_trader()
    if fetcher is None:
        return

    results = {
        "single_leg": test_single_leg(fetcher, chain, trader),
        "spread":     test_spread(fetcher, chain, trader),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")

    if all(results.values()):
        print("\n✓ Todos los tests pasaron")
    else:
        print("\n✗ Algunos tests fallaron — revisar logs arriba")


if __name__ == "__main__":
    main()
