#!/usr/bin/env python
"""
test_backtest_basic.py — Test básico de FASE 1

Verifica que todos los módulos funcionen correctamente juntos.
"""

import sys
import logging
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
from eolo_common.backtest import (
    RegimeClassifier,
    WalkForwardValidator,
    BacktestEngine,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_dummy_data(days: int = 1000) -> pd.DataFrame:
    """Crear DataFrame dummy para testing."""
    dates = pd.date_range("2017-01-01", periods=days, freq="D")

    # Generar precio realista
    returns = np.random.normal(0.0001, 0.01, days)
    price = 100 * np.exp(np.cumsum(returns))

    df = pd.DataFrame({
        "Open": price * (1 + np.random.uniform(-0.005, 0.005, days)),
        "High": price * (1 + np.random.uniform(0, 0.01, days)),
        "Low": price * (1 - np.random.uniform(0, 0.01, days)),
        "Close": price,
        "Volume": np.random.randint(1000000, 100000000, days),
    }, index=dates)

    return df


def test_regime_classifier():
    """Test RegimeClassifier."""
    print("\n" + "="*60)
    print("TEST 1: RegimeClassifier")
    print("="*60)

    df = create_dummy_data(2920)  # ~8 años
    classifier = RegimeClassifier()

    # Test: Etiquetar
    logger.info("Etiquetando DataFrame con regímenes...")
    df_labeled = classifier.label_dataframe(df)
    logger.info(f"✓ DataFrame etiquetado. Columnas: {df_labeled.columns.tolist()}")

    # Test: Detectar LIVE
    logger.info("Detectando régimen ACTUAL...")
    regime, metrics = classifier.detect_live(df, lookback_days=60)
    logger.info(f"✓ Régimen detectado: {regime}")

    assert regime is not None, "Regime should be detected"
    assert metrics["volatility"] > 0, "Volatility should be positive"

    return True


def test_walk_forward():
    """Test WalkForwardValidator."""
    print("\n" + "="*60)
    print("TEST 2: WalkForwardValidator")
    print("="*60)

    df = create_dummy_data(2920)  # ~8 años
    wf = WalkForwardValidator(train_months=12, test_months=3)

    logger.info("Generando ventanas walk-forward...")
    windows = wf.generate_windows("2017-01-01", "2024-12-31")
    logger.info(f"✓ {len(windows)} ventanas generadas")

    assert len(windows) > 5, "Should have multiple windows"
    assert windows[0].train_start < windows[0].train_end, "Train should be before test"
    assert windows[0].train_end <= windows[0].test_start, "Test should be after train"

    return True


def test_backtest_engine():
    """Test BacktestEngine."""
    print("\n" + "="*60)
    print("TEST 3: BacktestEngine")
    print("="*60)

    df = create_dummy_data(500)
    engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

    # Generar señales simples (moving average crossover)
    logger.info("Ejecutando backtest con señales dummy...")
    close = df["Close"].values
    sma20 = pd.Series(close).rolling(20).mean().values
    signal = np.where(close > sma20, 1, 0).astype(float)
    signal[np.isnan(sma20)] = 0

    signals = {
        "signal": signal,
        "entry_prices": close,
    }

    metrics = engine.run(df, signals, regime="bull_2024", symbol="SPY_TEST")
    logger.info(f"✓ Backtest completado")
    logger.info(f"  Trades: {metrics.get('num_trades', 0)}")
    logger.info(f"  PF: {metrics.get('profit_factor', 0):.2f}")
    logger.info(f"  Sharpe: {metrics.get('sharpe', 0):.2f}")

    assert metrics["num_trades"] > 0, "Should have some trades"

    return True


def test_integration():
    """Test integración completa."""
    print("\n" + "="*60)
    print("TEST 4: Integración Completa")
    print("="*60)

    df = create_dummy_data(1000)

    # 1. Régimen
    logger.info("1. Detectando régimen...")
    classifier = RegimeClassifier()
    regime, metrics = classifier.detect_live(df)
    logger.info(f"   ✓ Régimen: {regime}")

    # 2. Walk-forward
    logger.info("2. Generando ventanas...")
    wf = WalkForwardValidator()
    windows = wf.generate_windows()
    logger.info(f"   ✓ {len(windows)} ventanas")

    # 3. Backtest
    logger.info("3. Ejecutando backtest...")
    engine = BacktestEngine()

    close = df["Close"].values
    sma20 = pd.Series(close).rolling(20).mean().values
    signal = np.where(close > sma20, 1, 0).astype(float)
    signal[np.isnan(sma20)] = 0

    metrics = engine.run(
        df,
        {"signal": signal},
        regime=regime,
        symbol="SPY"
    )
    logger.info(f"   ✓ PF: {metrics['profit_factor']:.2f}")

    return True


def main():
    """Ejecutar todos los tests."""
    print("\n" + "="*80)
    print("🧪 TESTS DE FASE 1 — INFRAESTRUCTURA DE BACKTESTING")
    print("="*80)

    tests = [
        ("RegimeClassifier", test_regime_classifier),
        ("WalkForwardValidator", test_walk_forward),
        ("BacktestEngine", test_backtest_engine),
        ("Integración Completa", test_integration),
    ]

    results = {}
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results[test_name] = "✅ PASSED"
            logger.info(f"✓ {test_name} PASSED")
        except Exception as e:
            results[test_name] = f"❌ FAILED: {str(e)}"
            logger.error(f"❌ {test_name} FAILED: {str(e)}")

    # Resumen
    print("\n" + "="*80)
    print("RESUMEN DE TESTS")
    print("="*80)
    for test_name, result in results.items():
        print(f"  {test_name}: {result}")

    total = len(results)
    passed = sum(1 for r in results.values() if "✅" in r)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n✅ TODOS LOS TESTS PASARON. FASE 1 LISTA PARA PRODUCCIÓN.")
        return 0
    else:
        print(f"\n❌ {total - passed} tests fallaron. Revisar errores arriba.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
