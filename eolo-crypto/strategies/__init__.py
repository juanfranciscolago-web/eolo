# ============================================================
#  EOLO Crypto — Strategies runtime
#
#  Importa las 13 estrategias Eolo v1 desde ../../Bot/ y las
#  ejecuta sobre DataFrames crypto (24/7). Los adapters en
#  crypto_adapters.py ajustan ORB/Gap/VWAP para timeframe
#  continuo UTC (sin sesión de mercado).
# ============================================================
from .crypto_adapters import StrategyRunner

__all__ = ["StrategyRunner"]
