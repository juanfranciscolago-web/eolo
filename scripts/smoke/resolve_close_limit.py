"""
Smoke test — Sprint 1 fix exit_price.

Run pre-deploy durante market hours para validar quote fetch end-to-end:
  python3 scripts/smoke/resolve_close_limit.py

Instancia un OptionChainFetcher real, hace UN fetch sincronizado de AAPL,
inyecta el fetcher en un OptionsTrader paper, y consulta el strike ATM
para la expiración más cercana.

Output esperado:
  - Durante market hours: bid > 0, snapshot["quote_source"] == "schwab_chain"
  - Fuera de market hours: bid = None, quote_source in ("bid_null",
    "no_chain_data") + un ERROR [CLOSE_QUOTE] en logs
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # scripts/smoke/ → scripts/ → repo root
sys.path.insert(0, os.path.join(ROOT, "eolo-options"))

from stream.options_chain import OptionChainFetcher
from execution.options_trader import OptionsTrader


def main():
    # 1. Chain fetcher real, AAPL solamente
    fetcher = OptionChainFetcher(tickers=["AAPL"], interval=30)

    # 2. Trigger 1 fetch sincronizado (private _fetch_chain bypassa el loop async)
    print("→ Fetching AAPL chain from Schwab...")
    chain = fetcher._fetch_chain("AAPL")
    if chain is None:
        print("✗ Chain fetch falló (¿token expirado? ¿market data permission?)")
        return

    # Inyectar manual al cache para que get_chain/get_contract lo encuentren
    fetcher._chains["AAPL"] = chain
    fetcher._last_fetch["AAPL"] = time.time()

    print(f"✓ Chain OK — {len(chain['expirations'])} expirations")
    print(f"  underlying.price = {chain['underlying'].get('price')}")

    # 3. Trader paper con chain_fetcher inyectado
    trader = OptionsTrader(paper=True, chain_fetcher=fetcher)

    # 4. Strike ATM + expiración más cercana
    if not chain["expirations"]:
        print("✗ Sin expirations en chain")
        return
    exp_first = chain["expirations"][0]
    strike_atm = fetcher.get_atm_strike("AAPL")
    if strike_atm is None:
        print("✗ ATM strike no calculable")
        return

    print(f"\n→ Probing AAPL CALL exp={exp_first} strike={strike_atm}")
    bid, snapshot = trader._resolve_close_limit("AAPL", exp_first, strike_atm, "call")
    print(f"  bid      = {bid}")
    print(f"  snapshot = {snapshot}")

    # Sanity: quote_source debe ser uno de los 4 valores esperados
    expected_sources = {"schwab_chain", "bid_null", "no_chain_data", "no_fetcher"}
    src = snapshot.get("quote_source")
    if src not in expected_sources:
        print(f"⚠ quote_source unexpected: {src!r}")
    else:
        print(f"✓ quote_source = {src!r}")


if __name__ == "__main__":
    main()
