"""Watchlist builder — pre-market scan (Phase 1).

Master Plan v2.1 sec 8.1:
- News scan overnight (deferred)
- Gainers/Losers movers ≥ 3% (needs QD Tier B endpoint, deferred)
- IV Rank screen > 30
- Earnings blacklist
- OI Change institucional

Phase 1 scope: solo IV Rank + earnings exclude basados en data del bot.
"""
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


DEFAULT_UNIVERSE = ["SPY", "QQQ", "IWM"]  # extender con tickers individuales en F-3
EARNINGS_BLACKLIST_DAYS = 5  # excluir si earnings en ventana DTE


def build_watchlist(
    universe: Optional[List[str]] = None,
    iv_rank_threshold: float = 30.0,
    iv_rank_lookup: Optional[dict] = None,  # ticker → iv_rank, mock for tests
) -> dict:
    """Return watchlist con candidatos del día.

    Returns:
        {"selected": [tickers], "rejected": {ticker: reason}, "universe": [...]}
    """
    universe = universe or DEFAULT_UNIVERSE
    iv_rank_lookup = iv_rank_lookup or {}

    selected = []
    rejected = {}

    for ticker in universe:
        iv_rank = iv_rank_lookup.get(ticker)
        if iv_rank is None:
            rejected[ticker] = "iv_rank_unavailable"
            continue
        if iv_rank < iv_rank_threshold:
            rejected[ticker] = f"iv_rank {iv_rank:.0f} < threshold {iv_rank_threshold:.0f}"
            continue
        selected.append(ticker)

    return {
        "selected": selected,
        "rejected": rejected,
        "universe": universe,
        "iv_rank_threshold": iv_rank_threshold,
    }
