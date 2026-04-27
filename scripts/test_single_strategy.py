#!/usr/bin/env python3
"""
test_single_strategy.py — Test de ejecución de una única estrategia para diagnóstico
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from eolo_common.backtest.strategy_wrapper import load_strategy_from_bot
from eolo_common.backtest.data_generator import generate_backtest_dataset
from eolo_common.backtest import WalkForwardValidator

# Cargar un activo sintético
print("📊 Generando datos sintéticos...")
data = generate_backtest_dataset()
spy = data["SPY"]
print(f"SPY: {len(spy)} barras")
print(f"Columnas: {spy.columns.tolist()}")
print(spy.head())

# Cargar una estrategia
print("\n📚 Cargando estrategia 'bollinger'...")
wrapper = load_strategy_from_bot("bollinger")
if not wrapper:
    print("❌ No se pudo cargar la estrategia")
    sys.exit(1)

print(f"✓ Estrategia cargada: {wrapper.strategy_name}")

# Generar ventanas
print("\n🪟 Generando ventanas walk-forward...")
wf = WalkForwardValidator()
windows = wf.generate_windows("2017-01-01", "2024-12-31")
print(f"✓ {len(windows)} ventanas")

# Test con la primera ventana
window = windows[0]
train_data = spy[window.train_start:window.train_end]
test_data = spy[window.test_start:window.test_end]

print(f"\n🔄 VENTANA 0:")
print(f"  Train: {len(train_data)} barras [{window.train_start} → {window.train_end}]")
print(f"  Test:  {len(test_data)} barras [{window.test_start} → {window.test_end}]")

# Test en train
print(f"\n📈 Ejecutando backtest (TRAIN)...")
try:
    metrics_train = wrapper.backtest_func(train_data.copy(), is_training=True)
    print(f"✓ Éxito!")
    print(f"  Trades: {metrics_train.get('num_trades', 'N/A')}")
    print(f"  PF: {metrics_train.get('profit_factor', 'N/A'):.2f}")
    print(f"  Sharpe: {metrics_train.get('sharpe', 'N/A'):.2f}")
except Exception as e:
    print(f"❌ Error: {type(e).__name__}: {str(e)[:100]}")
    import traceback
    traceback.print_exc()

# Test en test
print(f"\n📈 Ejecutando backtest (TEST)...")
try:
    metrics_test = wrapper.backtest_func(test_data.copy(), is_training=False)
    print(f"✓ Éxito!")
    print(f"  Trades: {metrics_test.get('num_trades', 'N/A')}")
    print(f"  PF: {metrics_test.get('profit_factor', 'N/A'):.2f}")
    print(f"  Sharpe: {metrics_test.get('sharpe', 'N/A'):.2f}")
except Exception as e:
    print(f"❌ Error: {type(e).__name__}: {str(e)[:100]}")
    import traceback
    traceback.print_exc()

print("\n✅ Diagnóstico completado")
