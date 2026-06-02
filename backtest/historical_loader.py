"""Historical snapshot loader vía Quant Data sessionDate replay + Schwab pricehistory.

Master Plan v2.1 sec 10.1. Cache local disk para no re-fetch.

Usage:
    loader = HistoricalLoader(out_dir="backtest/data")
    snapshot = loader.get_snapshot("SPY", "2026-05-15")
"""
from pathlib import Path
from typing import Optional
import json
import logging
import sys

sys.path.insert(0, "eolo-crop")
sys.path.insert(0, "llm_engine_eolo")

from llm_engine.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class HistoricalLoader:
    """Load historical MarketSnapshot for a ticker on a date.

    Cache results on disk to avoid re-fetching expensive Quant Data calls.
    """

    def __init__(self, out_dir: str = "backtest/data"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, ticker: str, date: str) -> Path:
        return self.out_dir / f"{ticker}_{date}.json"

    def get_snapshot(self, ticker: str, date: str) -> Optional[MarketSnapshot]:
        """Get historical snapshot. Returns None if data unavailable.

        Note: usa sessionDate param de Quant Data API (validado smoke 2-jun).
        Schwab pricehistory para OHLC requires additional client (defer to T4.B.2).
        """
        cache = self._cache_path(ticker, date)
        if cache.exists():
            data = json.loads(cache.read_text())
            return MarketSnapshot(**data) if data else None

        # Para historical, get_X funciones del bot CROP por defecto usan
        # sessionDate=None (latest). Para backtest historical, monkeypatchar la firma
        # o usar curl directo con sessionDate param. Por simplicidad usamos cache:
        # si el ticker/fecha no está en cache, devolver None (operador puede populate
        # con script separado historical_fetcher.py).
        cache.write_text(json.dumps({"_skipped": True, "reason": "implement_qd_historical_fetch_in_T4_C"}))
        return None
