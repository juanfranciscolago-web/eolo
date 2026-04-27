#!/usr/bin/env python3
"""
backtest_improved.py — Backtesting Mejorado con Datos Realistas

Genera datos sintéticos con VOLATILIDAD E CRASHES para obtener trades
- Cambios de régimen (bull, bear, chop)
- Crashes ocasionales
- Volatility clustering
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.backtest_engine import BacktestEngine
from eolo_common.backtest.data_generator import SyntheticOHLCVGenerator

print("\n" + "="*80)
print("📊 BACKTESTING MEJORADO: FASE 4 + Bot v1 (Con Trades Reales)")
print("="*80)

# === Generar datos con regímenes mixtos (bull, bear, chop, crash) ===
print(f"\n📊 Generando datos realistas con volatility + crashes...")

data_gen = SyntheticOHLCVGenerator(seed=42)
symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_cache = {}

regimes = ["bull", "bear", "chop", "crash"]

for i, symbol in enumerate(symbols):
    # Crear 4 segmentos de 91 días cada uno con diferentes regímenes
    dfs = []

    for regime in regimes:
        df = data_gen.generate_timeseries(
            start_price=100 if regime == "bull" else 95,
            days=91,
            volatility=0.25 if regime in ["crash", "bear"] else 0.18,
            drift=0.0005 if regime == "bull" else -0.0003,
            regime=regime,
            volume_base=1000000
        )
        dfs.append(df)

    # Combinar todos los segmentos
    df_full = pd.concat(dfs)
    df_full.columns = df_full.columns.str.capitalize()

    data_cache[symbol] = df_full
    print(f"  ✅ {symbol}: {len(df_full)} días (bull+bear+chop+crash)")

# === Definir estrategias ===
print(f"\n🎯 Definiendo 6 estrategias...")

strategies = {
    "FASE4_Confluence_TS_79": {
        "type": "NN",
        "entry_signal": "rsi_oversold",
        "exit_signal": "trailing_stop"
    },
    "FASE4_Confluence_ATR_68": {
        "type": "NN",
        "entry_signal": "macd_bullish",
        "exit_signal": "atr_stop"
    },
    "FASE4_Confluence_TS_64": {
        "type": "NN",
        "entry_signal": "bollinger_bottom",
        "exit_signal": "trailing_stop"
    },
    "Bot_RSI_SMA200": {
        "type": "Classic",
        "indicators": ["rsi", "sma200"],
    },
    "Bot_ORB": {
        "type": "Classic",
        "indicators": ["breakout"],
    },
    "Bot_Bollinger_RSI": {
        "type": "Classic",
        "indicators": ["bollinger", "rsi"],
    },
}

# === Funciones para generar señales ===

def generate_nn_signals(df):
    """Genera señales NN con mejor detección."""
    n = len(df)

    # RSI
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()
    down = -deltas.clip(1, None).rolling(14).mean()
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # MACD
    macd_line = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std

    signal = np.zeros(n)

    # Más sensible: 35 en lugar de 30
    signal[rsi < 35] = 1
    signal[rsi > 65] = -1

    # MACD bullish
    signal[macd_line > 0] = np.maximum(signal[macd_line > 0], 1)

    # Bollinger
    signal[df['Close'] < bb_lower] = np.maximum(signal[df['Close'] < bb_lower], 1)

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.97,
        "take_profit": df['Close'].values * 1.05
    }


def generate_classic_signals(df):
    """Genera señales clásicas mejoradas."""
    n = len(df)

    # RSI
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()
    down = -deltas.clip(1, None).rolling(14).mean()
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # SMA
    sma200 = df['Close'].rolling(200).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std

    signal = np.zeros(n)

    # RSI + SMA200
    signal[(rsi < 35) & (df['Close'] > sma200)] = 1
    signal[(rsi > 65)] = -1

    # Bollinger backup
    signal[df['Close'] < bb_lower] = np.maximum(signal[df['Close'] < bb_lower], 1)

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.96,
        "take_profit": df['Close'].values * 1.06
    }


# === Ejecutar backtests ===
print(f"\n⚙️  Ejecutando backtests ({len(strategies)} estrategias × {len(symbols)} activos)...")

all_results = {}
engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

for strategy_name, config in strategies.items():
    print(f"\n  📌 {strategy_name}")

    strategy_results = {}

    for symbol in symbols:
        df = data_cache[symbol]

        # Generar señales
        if config["type"] == "NN":
            signals = generate_nn_signals(df)
        else:
            signals = generate_classic_signals(df)

        # Ejecutar backtest
        try:
            results = engine.run(df=df, signals=signals, symbol=symbol)

            # Calcular PF
            winning = results.get("winning_pnl", 0)
            losing = abs(results.get("losing_pnl", -1))
            pf = winning / losing if losing > 0 else 1.0

            num_trades = results.get("total_trades", 0)

            strategy_results[symbol] = {
                "num_trades": num_trades,
                "pnl": float(results.get("total_pnl", 0)),
                "win_rate": float(results.get("win_rate", 0)),
                "pf": float(pf),
            }

            emoji = "✅" if num_trades > 0 else "○"
            print(f"     {emoji} {symbol}: {num_trades:>3} trades | PnL: ${strategy_results[symbol]['pnl']:>7.0f} | PF: {pf:.2f}")

        except Exception as e:
            print(f"     ❌ {symbol}: {str(e)[:50]}")
            strategy_results[symbol] = {"error": str(e)}

    all_results[strategy_name] = strategy_results

# === Scoring ===
print(f"\n⭐ Aplicando scoring FASE 3...")

scored = {}

for strategy_name, symbols_results in all_results.items():
    scores_by_symbol = {}

    for symbol, metrics in symbols_results.items():
        if "error" in metrics:
            scores_by_symbol[symbol] = {
                "score": 0,
                "verdict": "RECHAZAR"
            }
            continue

        pf = metrics.get("pf", 1.0)
        num_trades = metrics.get("num_trades", 0)

        # Scoring más realista
        if pf < 1.0 or num_trades < 5:
            score = 0
            verdict = "RECHAZAR"
        elif pf >= 2.0 and num_trades >= 20:
            score = 90
            verdict = "ACTIVAR"
        elif pf >= 1.5 and num_trades >= 10:
            score = 75
            verdict = "CONSIDERAR"
        elif pf >= 1.2:
            score = 60
            verdict = "CONSIDERAR"
        elif pf > 1.0:
            score = 45
            verdict = "RECHAZAR"
        else:
            score = 0
            verdict = "RECHAZAR"

        scores_by_symbol[symbol] = {
            "score": float(score),
            "verdict": verdict,
            "pf": float(pf),
            "num_trades": num_trades
        }

    scored[strategy_name] = scores_by_symbol

# === Reporte ===
print(f"\n" + "="*80)
print("📈 RESULTADOS CON DATOS REALISTAS")
print("="*80)

print(f"\n{'Estrategia':<35} {'Trades Total':<15} {'Avg PF':<10} {'ACTIVAR+':<12}")
print("-" * 72)

for strategy_name, symbol_scores in scored.items():
    trade_counts = [s.get("num_trades", 0) for s in symbol_scores.values() if "num_trades" in s]
    total_trades = sum(trade_counts)

    pf_vals = [s.get("pf", 1.0) for s in symbol_scores.values() if "pf" in s]
    avg_pf = np.mean(pf_vals) if pf_vals else 1.0

    activar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "ACTIVAR")
    considerar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "CONSIDERAR")

    activar_plus = f"{activar_count}A + {considerar_count}C"

    print(f"{strategy_name:<35} {total_trades:>6} trades       {avg_pf:>8.2f} {activar_plus:>12}")

# === Top 10 ===
print(f"\n🏆 TOP 10 OPERACIONES:")

all_trades = []
for strategy_name, symbol_scores in scored.items():
    for symbol, score_info in symbol_scores.items():
        all_trades.append({
            "strategy": strategy_name,
            "symbol": symbol,
            "score": score_info.get("score", 0),
            "verdict": score_info.get("verdict", "?"),
            "pf": score_info.get("pf", 1.0),
            "trades": score_info.get("num_trades", 0)
        })

all_trades.sort(key=lambda x: (x["score"], x["trades"]), reverse=True)

for i, item in enumerate(all_trades[:10], 1):
    emoji = "✅" if item["verdict"] == "ACTIVAR" else "⚠️ " if item["verdict"] == "CONSIDERAR" else "❌"
    print(f"  {i:2d}. {item['strategy']:<30} {item['symbol']:<6} "
          f"Score:{item['score']:>5.1f} {emoji} Trades:{item['trades']:>3} PF:{item['pf']:.2f}")

# === Guardar resultados ===
output_file = Path(__file__).parent.parent / "results" / "backtest_improved_report.json"
with open(output_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "data_type": "Sintético Realista (Bull+Bear+Chop+Crash)",
        "strategies": {
            name: {
                "results": all_results.get(name, {}),
                "scores": scored.get(name, {})
            }
            for name in all_results.keys()
        }
    }, f, indent=2)

print(f"\n💾 Reporte: {output_file}")

print(f"\n" + "="*80)
print("✅ BACKTESTING COMPLETADO")
print("="*80)
print(f"\n📊 Resumen:")
print(f"   • Estrategias: {len(strategies)}")
print(f"   • Activos: {len(symbols)}")
print(f"   • Datos: Sintéticos Realistas (Bull+Bear+Chop+Crash)")
print(f"   • Trades Generados: {sum(sum(1 for s in scores.values() if s.get('num_trades', 0) > 0) for scores in scored.values())}/{len(all_trades)} combos")
print(f"   • Estrategias ACTIVAR+: {sum(sum(1 for s in scores.values() if s.get('verdict') in ['ACTIVAR', 'CONSIDERAR']) for scores in scored.values())}")
print()
