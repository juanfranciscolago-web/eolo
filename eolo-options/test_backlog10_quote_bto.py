"""Test ad-hoc Backlog #10: BTO paper persiste quote_snapshot."""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from execution.options_trader import OptionsTrader


def make_mock_chain_fetcher(bid=1.50, ask=1.55, mark=1.525, last=1.52, iv=0.45, spot=100.5):
    """Mock chain_fetcher con contract válido."""
    cf = MagicMock()
    cf.get_contract.return_value = {
        "bid": bid, "ask": ask, "mark": mark, "last": last, "iv": iv,
    }
    cf.get_chain.return_value = {
        "underlying": {"price": spot}, "ts": 1747000000.0,
    }
    return cf


async def test_bto_paper_persists_quote_snapshot():
    """Caso 1: chain válido → BTO con todas las quote_* pobladas."""
    cf = make_mock_chain_fetcher()
    trader = OptionsTrader(paper=True, chain_fetcher=cf)

    with patch("execution.options_trader._log_paper_trade") as mock_log:
        mock_log.return_value = "PAPER-1234567"
        await trader.open_long_call(
            ticker="SOXL", expiration="2026-05-22",
            strike=45.0, contracts=1, limit=1.50,
            strategy="theo_mispricing", reason="test BTO snapshot",
        )

    assert mock_log.called, "expected _log_paper_trade to be called"
    kwargs = mock_log.call_args.kwargs
    qs = kwargs.get("quote_snapshot")
    assert qs is not None, f"quote_snapshot None, expected dict: {qs}"
    assert qs["quote_bid"] == 1.50, f"expected bid=1.50, got {qs['quote_bid']}"
    assert qs["quote_ask"] == 1.55
    assert qs["quote_mid"] == 1.525
    assert qs["quote_iv"] == 0.45
    assert qs["quote_spot"] == 100.5
    assert qs["quote_source"] == "schwab_chain"
    assert kwargs.get("data_quality") == "quote_resolved", \
        f"expected quote_resolved, got {kwargs.get('data_quality')}"
    print("[Test 1] BTO con chain válido: PASS")


async def test_bto_paper_fail_loud_when_no_chain():
    """Caso 2: chain_fetcher = None → snapshot 9 keys None + data_quality=quote_unavailable."""
    trader = OptionsTrader(paper=True, chain_fetcher=None)

    with patch("execution.options_trader._log_paper_trade") as mock_log:
        mock_log.return_value = "PAPER-1234568"
        await trader.open_long_put(
            ticker="SOXL", expiration="2026-05-22",
            strike=45.0, contracts=1, limit=1.20,
            strategy="theo_mispricing", reason="test no chain",
        )

    kwargs = mock_log.call_args.kwargs
    qs = kwargs.get("quote_snapshot")
    assert qs is not None, "snapshot None — should always be a dict"
    assert qs["quote_bid"] is None
    assert qs["quote_source"] == "no_fetcher"
    assert kwargs.get("data_quality") == "quote_unavailable", \
        f"expected quote_unavailable, got {kwargs.get('data_quality')}"
    print("[Test 2] BTO sin chain_fetcher: PASS")


async def test_bto_paper_fail_loud_when_bid_null():
    """Caso 3: bid=None en chain → snapshot poblado parcialmente + quote_unavailable."""
    cf = make_mock_chain_fetcher(bid=None)
    trader = OptionsTrader(paper=True, chain_fetcher=cf)

    with patch("execution.options_trader._log_paper_trade") as mock_log:
        mock_log.return_value = "PAPER-1234569"
        await trader.open_long_call(
            ticker="SOXL", expiration="2026-05-22",
            strike=45.0, contracts=1, limit=1.50,
            strategy="iv_skew_jump", reason="test bid null",
        )

    kwargs = mock_log.call_args.kwargs
    qs = kwargs.get("quote_snapshot")
    assert qs["quote_bid"] is None
    assert qs["quote_ask"] == 1.55  # ask sí está
    assert qs["quote_source"] == "bid_null"
    assert kwargs.get("data_quality") == "quote_unavailable"
    print("[Test 3] BTO con bid null: PASS")


async def main():
    await test_bto_paper_persists_quote_snapshot()
    await test_bto_paper_fail_loud_when_no_chain()
    await test_bto_paper_fail_loud_when_bid_null()
    print("\n3/3 PASS — Backlog #10 fix OK")


if __name__ == "__main__":
    asyncio.run(main())
