# ============================================================
#  eolo_common.multi_tf — multi-timeframe para los 4 Eolos
#
#  Exporta:
#    CandleBuffer         — almacena velas 1min por símbolo
#    resample_to_tf()     — 1min DF → TF arbitrario (5/15/30/60/240…)
#    BufferMarketData     — adaptador drop-in (interface MarketData)
#    ConfluenceFilter     — reduce señales multi-TF a una sola
#    DEFAULT_TIMEFRAMES   — [1, 5, 15, 30, 60, 240]
#    SUPPORTED_TIMEFRAMES — set de TFs soportados
#    load_multi_tf_config — parsea settings dict de Firestore
# ============================================================
from .buffer       import CandleBuffer
from .resample     import resample_to_tf, SUPPORTED_TIMEFRAMES, DEFAULT_TIMEFRAMES
from .market_data  import BufferMarketData
from .confluence   import ConfluenceFilter
from .settings     import load_multi_tf_config, MultiTFConfig

__all__ = [
    "CandleBuffer",
    "resample_to_tf",
    "SUPPORTED_TIMEFRAMES",
    "DEFAULT_TIMEFRAMES",
    "BufferMarketData",
    "ConfluenceFilter",
    "load_multi_tf_config",
    "MultiTFConfig",
]
