# Theta Harvest Backtester — synthetic simulation via Black-Scholes

from .data_loader  import load_market_data, MarketData, compute_sector_direction
from .bs_pricer    import bs_price, bs_delta, find_strike_for_delta, spread_credit, spread_value, spread_pnl
from .pivot_engine import compute_pivots, PivotZoneResult
from .macro_engine import is_macro_blocked, get_macro_events, build_blocked_dates
from .simulator    import run_simulation, run_all_tickers, SpreadPosition
from .analyzer     import compute_metrics, print_summary
from .report       import generate_html_report

__all__ = [
    "load_market_data", "MarketData", "compute_sector_direction",
    "bs_price", "bs_delta", "find_strike_for_delta",
    "spread_credit", "spread_value", "spread_pnl",
    "compute_pivots", "PivotZoneResult",
    "is_macro_blocked", "get_macro_events", "build_blocked_dates",
    "run_simulation", "run_all_tickers", "SpreadPosition",
    "compute_metrics", "print_summary",
    "generate_html_report",
]
