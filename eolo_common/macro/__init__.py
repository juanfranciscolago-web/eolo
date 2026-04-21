"""
eolo_common.macro — feeds macro (VIX, VIX9D, VIX3M, TICK, TRIN)
que alimentan las estrategias Nivel 2.
"""
from .feeds import (  # noqa: F401
    MacroFeeds,
    compute_vrp,
    realized_vol_annualized,
)
from .symbols import MACRO_SYMBOLS  # noqa: F401
