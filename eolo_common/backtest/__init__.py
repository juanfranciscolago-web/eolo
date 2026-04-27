# ============================================================
#  eolo_common.backtest — Framework de Backtesting Robusto
#
#  Módulos:
#    data_loader.py        — OHLCV fetcher (yfinance + Binance API)
#    regime_classifier.py  — Etiquetar regímenes + detector LIVE
#    walk_forward.py       — Motor de validación walk-forward
#    backtest_engine.py    — Simulador de trading CON fricción real
#    metrics.py            — Cálculo de 10+ métricas de rendimiento
#    validation_engine.py  — 10 mejoras críticas + análisis OOS
#    activation_rules.py   — Score 0-100 para decisiones binarias
#    reporter.py           — Generador de reportes HTML/JSON
#
#  Características:
#    ✓ Slippage dinámico + comisiones por régimen/activo
#    ✓ Walk-forward con train 12m → test 3m (rolling windows)
#    ✓ Validación contra datos reales ejecutados (Firestore logs)
#    ✓ Régimen classifier en VIVO (detección de régimen actual)
#    ✓ Significancia estadística (mínimo trades por estrategia/régimen)
#    ✓ Degradación OOS detection (rechazar overfitting >30%)
#    ✓ Separación long/short por régimen
#    ✓ Drawdown real + racha máxima de pérdidas
#    ✓ Equity curve smoothness (detectar luck concentrado)
#    ✓ Stress-test: cambios de régimen durante posición
#    ✓ Dashboard Score 0-100 (✅ ACTIVAR / ⚠️ CONSIDERAR / ❌ RECHAZAR)
#
# ============================================================

from .data_loader import BacktestDataLoader, load_all_backtest_data
from .regime_classifier import RegimeClassifier, create_regime_labels, detect_live_regime
from .walk_forward import WalkForwardValidator, WFWindow, create_standard_windows
from .backtest_engine import BacktestEngine, Trade, TradeType
from .data_generator import SyntheticOHLCVGenerator, generate_backtest_dataset
from .activation_rules import ActivationScorer, score_all_strategies
from .multi_tf_features import MultiTFFeatureExtractor, MultiTFFeatures
from .confluence_nn import SimpleNNConfluence
from .strategy_generator import StrategyGenerator, GeneratedStrategy

# Alias para conveniencia
SyntheticDataGenerator = SyntheticOHLCVGenerator

__version__ = "1.0.0"
__author__ = "Eolo Backtesting Team"

__all__ = [
    "BacktestDataLoader",
    "load_all_backtest_data",
    "RegimeClassifier",
    "create_regime_labels",
    "detect_live_regime",
    "WalkForwardValidator",
    "WFWindow",
    "create_standard_windows",
    "BacktestEngine",
    "Trade",
    "TradeType",
    "SyntheticOHLCVGenerator",
    "SyntheticDataGenerator",
    "generate_backtest_dataset",
    "ActivationScorer",
    "score_all_strategies",
    "MultiTFFeatureExtractor",
    "MultiTFFeatures",
    "SimpleNNConfluence",
    "StrategyGenerator",
    "GeneratedStrategy",
]
