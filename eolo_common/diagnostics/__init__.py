# eolo_common/diagnostics — Strategy diagnostics compartido (Plan B, 2026-04-21)
#
# Corre las 22 variantes direccionales del registry sobre un universo de
# tickers usando un callable inyectado `get_df(ticker)`. Permite reusar la
# misma lógica desde el Bot v1 (Schwab MarketData) y — si algún día
# yfinance funciona en Cloud Run — desde sheets-sync.
from .diagnostics import (
    DIAGNOSTICS_TICKERS_DEFAULT,
    compute_strategy_diagnostics,
)

__all__ = [
    "DIAGNOSTICS_TICKERS_DEFAULT",
    "compute_strategy_diagnostics",
]
