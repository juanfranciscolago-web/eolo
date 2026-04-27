#!/usr/bin/env python3
"""
debug_signals.py — Inspecciona las señales generadas antes de pasar a BacktestEngine
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.backtest_engine import BacktestEngine

print("\n" + "="*80)
print("🔍 DEBUG: INSPECCIÓN DE SEÑALES")
print("="*80)

# Cargar datos
data_dir = Path(__file__).parent.parent / "data"
csv_path = data_dir / "SPY.csv"

df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)
df.columns = df.columns.str.capitalize()
df = df.sort_index()

print(f"\n📊 Datos cargados: {len(df)} barras")
print(f"   Índice: {df.index[0]} a {df.index[-1]}")
print(f"   Columnas: {list(df.columns)}")

# === Generar señales manualmente ===
print(f"\n📈 Generando señales RSI < 40...")

deltas = df['Close'].diff()
up = deltas.clip(0).rolling(14).mean()
down = -deltas.clip(None, 0).rolling(14).mean()
rs = up / down
rsi = 100 - (100 / (1 + rs))

signal = np.zeros(len(df))
signal[rsi < 40] = 1
signal[rsi > 65] = -1

print(f"   RSI range: {rsi.min():.2f} - {rsi.max():.2f}")
print(f"   Señales BUY (=1): {(signal == 1).sum()}")
print(f"   Señales SELL (=-1): {(signal == -1).sum()}")

# Primeras 5 señales BUY
buy_indices = np.where(signal == 1)[0]
if len(buy_indices) > 0:
    print(f"\n   Primeras 5 entradas (BUY):")
    for idx in buy_indices[:5]:
        print(f"      Bar {idx}: {df.index[idx].strftime('%Y-%m-%d')} RSI={rsi.iloc[idx]:.2f} Price=${df['Close'].iloc[idx]:.2f}")

# === Crear dict de señales ===
print(f"\n🎯 Creando signal dict...")

signals = {
    "signal": signal,
    "entry_prices": df['Close'].values,
    "stop_loss": df['Close'].values * 0.97,
    "take_profit": df['Close'].values * 1.05
}

print(f"   signal shape: {signals['signal'].shape}")
print(f"   entry_prices shape: {signals['entry_prices'].shape}")
print(f"   stop_loss shape: {signals['stop_loss'].shape}")
print(f"   take_profit shape: {signals['take_profit'].shape}")

# === Pasar a BacktestEngine ===
print(f"\n⚙️  Ejecutando BacktestEngine.run()...")

engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

try:
    results = engine.run(df=df, signals=signals, symbol="SPY")

    print(f"\n✅ BacktestEngine retornó resultados:")
    print(f"   Total trades: {results.get('total_trades', 0)}")
    print(f"   Total PnL: ${results.get('total_pnl', 0):.2f}")
    print(f"   Win rate: {results.get('win_rate', 0):.2f}%")
    print(f"   Max DD: {results.get('max_dd', 0):.2f}%")
    print(f"   Winning PnL: ${results.get('winning_pnl', 0):.2f}")
    print(f"   Losing PnL: ${results.get('losing_pnl', 0):.2f}")

    if results.get('total_trades', 0) == 0:
        print(f"\n⚠️  BacktestEngine retornó 0 trades a pesar de {(signal == 1).sum()} señales BUY")
        print(f"\n💡 Posibles causas:")
        print(f"   1. BacktestEngine no procesa señales correctamente")
        print(f"   2. Las señales de entrada/salida están desalineadas")
        print(f"   3. Hay un problema interno en BacktestEngine.run()")

except Exception as e:
    print(f"\n❌ Error en BacktestEngine: {str(e)}")
    import traceback
    traceback.print_exc()

print()
