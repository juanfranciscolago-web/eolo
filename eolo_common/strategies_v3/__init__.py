# eolo_common/strategies_v3 — suite "EMA 3/8 y MACD"
#
# 11 estrategias port del trabajo de Juan del suite v3 (ver uploads del 2026-04-21).
# Shared pure-logic que V1 (acciones), V2 (opciones) y Crypto (Binance) consumen
# vía sus respectivos adapters locales.
from .strategies import (
    STRATEGY_REGISTRY_V3,
    StrategyConfig,
    EQUITY_ONLY,
    BREADTH_GATED,
    list_strategies_for_bot,
)

__all__ = [
    "STRATEGY_REGISTRY_V3",
    "StrategyConfig",
    "EQUITY_ONLY",
    "BREADTH_GATED",
    "list_strategies_for_bot",
]
