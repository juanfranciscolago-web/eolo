#!/usr/bin/env python3
"""
analyze_data.py — Analiza los datos REALES para entender por qué no hay trades

Inspecciona RSI, SMA, Bollinger, etc. para cada símbolo
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

print("\n" + "="*80)
print("📊 ANÁLISIS DE DATOS REALES")
print("="*80)

data_dir = Path(__file__).parent.parent / "data"
symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]

for symbol in symbols:
    csv_path = data_dir / f"{symbol}.csv"

    if not csv_path.exists():
        continue

    df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)
    df.columns = df.columns.str.capitalize()
    df = df.sort_index()

    # Calcular indicadores
    # RSI (CORREGIDO)
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()  # Mantener solo ganancias
    down = -deltas.clip(None, 0).rolling(14).mean()  # Mantener solo pérdidas y negar
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # SMA100
    sma100 = df['Close'].rolling(100).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std
    bb_upper = bb_sma + 2 * bb_std

    # MACD
    macd = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()

    print(f"\n{'='*80}")
    print(f"📈 {symbol}")
    print(f"{'='*80}")

    print(f"\n💰 Precio (Close):")
    print(f"   Min: ${df['Close'].min():.2f}")
    print(f"   Max: ${df['Close'].max():.2f}")
    print(f"   Mean: ${df['Close'].mean():.2f}")
    print(f"   Últimamente: ${df['Close'].iloc[-1]:.2f}")

    print(f"\n📊 RSI (14):")
    print(f"   Min: {rsi.min():.2f}")
    print(f"   Max: {rsi.max():.2f}")
    print(f"   Mean: {rsi.mean():.2f}")
    print(f"   Ahora: {rsi.iloc[-1]:.2f}")

    # Contar cuántas barras cumplen condiciones
    rsi_below_40 = (rsi < 40).sum()
    rsi_below_30 = (rsi < 30).sum()
    price_above_sma100 = (df['Close'] > sma100).sum()
    close_below_bblow = (df['Close'] < bb_lower).sum()
    macd_positive = (macd > 0).sum()

    total_bars = len(df)

    print(f"\n✅ Condiciones cumplidas:")
    print(f"   RSI < 40: {rsi_below_40:>3} barras ({100*rsi_below_40/total_bars:>5.1f}%) ← Entrada NN/RSI")
    print(f"   RSI < 30: {rsi_below_30:>3} barras ({100*rsi_below_30/total_bars:>5.1f}%) ← Original")
    print(f"   Price > SMA100: {price_above_sma100:>3} barras ({100*price_above_sma100/total_bars:>5.1f}%)")
    print(f"   Close < BB Lower: {close_below_bblow:>3} barras ({100*close_below_bblow/total_bars:>5.1f}%)")
    print(f"   MACD > 0: {macd_positive:>3} barras ({100*macd_positive/total_bars:>5.1f}%)")

    # Volatilidad
    returns = df['Close'].pct_change()
    volatility = returns.std() * np.sqrt(252)  # Anualizada
    print(f"\n📉 Volatilidad (anualizada): {volatility*100:.2f}%")
    print(f"   Daily avg move: {returns.mean()*100:.3f}%")

    # Trend
    drift = (df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0]
    print(f"\n📈 Trend (último año): {drift*100:+.2f}%")

print(f"\n" + "="*80)
print("💡 DIAGNÓSTICO")
print("="*80)

# Análisis general
total_bars = 252
print(f"\n📌 Si NO hay barras con RSI < 40:")
print(f"   → Mercado estuvo EXTREMADAMENTE alcista (bull puro)")
print(f"   → No hay pullbacks/retrocesos para entradas")
print(f"   → Estrategias de mean reversion no funcionan en bull markets")
print(f"\n📌 Si MUY POCAS barras con RSI < 40 (<5%):")
print(f"   → Consideraría usar RSI < 45 o < 50")
print(f"   → O cambiar a estrategias MOMENTUM (siguiendo tendencia)")
print(f"\n📌 Si HAY barras con RSI < 40 pero SIN TRADES:")
print(f"   → BacktestEngine no ejecuta correctamente")
print(f"   → O hay conflicto entre múltiples condiciones")

print()
