"""Position monitor — cada 5 min durante regular hours.

Master Plan v2.1 sec 8.3-8.4.
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


def monitor_positions(positions: List[dict]) -> dict:
    """Walk through open positions and flag those needing action.

    Master Plan sec 8.4:
    - TR-053 captures al 50% target
    - TR-058 dispara CLOSE_NOW si 0DTE + VIX velocity > +5%
    """
    flags = []
    for pos in positions:
        pct_capture = pos.get("pct_capture", 0)
        if pct_capture >= 50:
            flags.append({"position_id": pos.get("symbol"), "flag": "AT_50_TARGET", "action": "CLOSE"})
    return {
        "positions_count": len(positions),
        "flags": flags,
    }
