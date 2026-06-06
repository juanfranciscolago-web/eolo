"""Earnings calendar filter for QQQ/SPY top holdings.

Hardcoded for shadow run; replace with API in prod.
"""
from datetime import date


QQQ_TOP_HOLDINGS_EARNINGS_Q2_Q3_2026 = {
    "AAPL": [date(2026, 7, 30)],
    "MSFT": [date(2026, 7, 28)],
    "NVDA": [date(2026, 8, 26)],
    "AMZN": [date(2026, 7, 30)],
    "GOOGL": [date(2026, 7, 28)],
    "META": [date(2026, 7, 30)],
    "TSLA": [date(2026, 7, 22)],
    "AVGO": [date(2026, 9, 3)],
    "NFLX": [date(2026, 7, 16)],
    "AMD": [date(2026, 7, 28)],
}

SPY_KEY_EARNINGS = {
    **QQQ_TOP_HOLDINGS_EARNINGS_Q2_Q3_2026,
    "BRK.B": [date(2026, 8, 1)],
    "JPM": [date(2026, 7, 11)],
    "V": [date(2026, 7, 22)],
    "WMT": [date(2026, 8, 19)],
}


def is_earnings_blackout(ticker, today):
    """True if today is T-1 or T0 of a major holding earnings."""
    if ticker == "QQQ":
        blackouts = QQQ_TOP_HOLDINGS_EARNINGS_Q2_Q3_2026
    elif ticker == "SPY":
        blackouts = SPY_KEY_EARNINGS
    else:
        return False, ""
    for sym, dates in blackouts.items():
        for d in dates:
            if today == d or today == date.fromordinal(d.toordinal() - 1):
                return True, f"{sym} earnings on {d.isoformat()}"
    return False, ""
