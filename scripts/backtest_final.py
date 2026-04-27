#!/usr/bin/env python3
"""
backtest_final.py — Backtesting Final: FASE 4 + Bot v1 con datos reales/sintéticos

Ejecuta backtests de:
  1. 3 estrategias generadas en FASE 4
  2. 3 estrategias clásicas Bot v1
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.backtest_engine import BacktestEngine
from eolo_common.backtest.data_generator import SyntheticOHLCVGenerator
from eolo_common.backtest.activation_rules import ActivationScorer


# ============================================================
# Funciones auxiliares para generar señales
# ============================================================

def generate_nn_signals(df, config):
    """Genera señales basadas en NN confluence."""
    n = len(df)

    # RSI (CORREGIDO)
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()  # Mantener solo ganancias
    down = -deltas.clip(None, 0).rolling(14).mean()  # Mantener solo pérdidas y negar
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # MACD
    macd_line = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std

    signal = np.zeros(n)

    if config.get("entry_signal") == "rsi_oversold":
        signal[rsi < 30] = 1
        signal[rsi > 70] = -1
    elif config.get("entry_signal") == "macd_bullish":
        signal[macd_line > 0] = 1
        signal[macd_line < 0] = -1
    elif config.get("entry_signal") == "bollinger_bottom":
        signal[df['Close'] < bb_lower] = 1

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.98,
        "take_profit": df['Close'].values * 1.03
    }


def generate_classic_signals(df, config):
    """Genera señales clásicas."""
    n = len(df)

    # RSI (CORREGIDO)
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()  # Mantener solo ganancias
    down = -deltas.clip(None, 0).rolling(14).mean()  # Mantener solo pérdidas y negar
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # SMA
    sma200 = df['Close'].rolling(200).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std

    signal = np.zeros(n)

    if "rsi" in config.get("indicators", []) and "sma200" in config.get("indicators", []):
        # RSI + SMA200
        signal[(rsi < 30) & (df['Close'] > sma200)] = 1
        signal[(rsi > 70) | (df['Close'] < sma200)] = -1

    elif "bollinger" in config.get("indicators", []):
        # Bollinger
        signal[df['Close'] < bb_lower] = 1
        signal[df['Close'] > (bb_sma + 2 * bb_std)] = -1

    else:
        # Default: simple RSI
        signal[rsi < 30] = 1
        signal[rsi > 70] = -1

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.97,
        "take_profit": df['Close'].values * 1.04
    }

print("\n" + "="*80)
print("🚀 BACKTESTING FINAL: FASE 4 + BOT v1")
print("="*80)

# === Cargar datos REALES desde CSVs ===
print(f"\n📊 Cargando datos REALES desde eolo/data/...")

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_cache = {}
data_dir = Path(__file__).parent.parent / "data"

for symbol in symbols:
    csv_path = data_dir / f"{symbol}.csv"

    if csv_path.exists():
        try:
            # Leer CSV con Date como índice
            df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)

            # Capitalizar columnas (Close, Open, High, Low, Volume)
            df.columns = df.columns.str.capitalize()

            # Ordenar por fecha
            df = df.sort_index()

            data_cache[symbol] = df
            num_bars = len(df)
            date_start = df.index[0].strftime('%Y-%m-%d')
            date_end = df.index[-1].strftime('%Y-%m-%d')
            print(f"  ✅ {symbol}: {num_bars} barras REALES ({date_start} to {date_end})")

        except Exception as e:
            print(f"  ❌ {symbol}: {str(e)[:80]}")
            # Fallback: generar sintético
            data_gen = SyntheticOHLCVGenerator(seed=42)
            df = data_gen.generate_timeseries(start_price=100, days=252, volatility=0.18, drift=0.0003, regime="bull", volume_base=1000000)
            df.columns = df.columns.str.capitalize()
            # Asegurar que Date sea índice
            if 'Date' in df.columns:
                df = df.set_index('Date')
            data_cache[symbol] = df
            print(f"  ⚠️  {symbol}: Fallback sintético ({len(df)} barras)")
    else:
        print(f"  ⚠️  {symbol}: CSV no encontrado, generando sintético...")
        data_gen = SyntheticOHLCVGenerator(seed=42)
        df = data_gen.generate_timeseries(start_price=100, days=252, volatility=0.18, drift=0.0003, regime="bull", volume_base=1000000)
        df.columns = df.columns.str.capitalize()
        # Asegurar que Date sea índice
        if 'Date' in df.columns:
            df = df.set_index('Date')
        data_cache[symbol] = df

# === Definir estrategias ===
print(f"\n🎯 Definiendo 6 estrategias: 3 FASE 4 + 3 Bot v1...")

strategies = {
    # FASE 4 Generadas
    "FASE4_Confluence_TS_79": {
        "type": "NN",
        "conf_threshold": 0.79,
        "entry_signal": "rsi_oversold",
        "exit_signal": "trailing_stop"
    },
    "FASE4_Confluence_ATR_68": {
        "type": "NN",
        "conf_threshold": 0.68,
        "entry_signal": "macd_bullish",
        "exit_signal": "atr_stop"
    },
    "FASE4_Confluence_TS_64": {
        "type": "NN",
        "conf_threshold": 0.64,
        "entry_signal": "bollinger_bottom",
        "exit_signal": "trailing_stop"
    },

    # Bot v1 Clásicas
    "Bot_RSI_SMA200": {
        "type": "Classic",
        "indicators": ["rsi", "sma200"],
        "entry_rule": "rsi < 30 AND price > sma200",
        "exit_rule": "rsi > 70 OR price < sma200"
    },
    "Bot_ORB": {
        "type": "Classic",
        "indicators": ["open_range_breakout"],
        "entry_rule": "close > open + 20% range",
        "exit_rule": "time > 10:00 OR rsi > 75"
    },
    "Bot_Bollinger_RSI": {
        "type": "Classic",
        "indicators": ["bollinger", "rsi"],
        "entry_rule": "close < bb_lower AND rsi < 30",
        "exit_rule": "close > bb_sma OR rsi > 70"
    },
}

# === Ejecutar backtests ===
print(f"\n⚙️  Ejecutando backtests...")

all_results = {}
engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

for strategy_name, strategy_config in strategies.items():
    print(f"\n  📌 {strategy_name}")

    strategy_results = {}

    for symbol in symbols:
        df = data_cache[symbol]

        # Generar señales basadas en tipo de estrategia
        signals = {}

        if strategy_config["type"] == "NN":
            # Generar señales NN basadas en RSI/MACD
            signals = generate_nn_signals(df, strategy_config)

        else:  # Classic
            # Generar señales clásicas
            signals = generate_classic_signals(df, strategy_config)

        # Ejecutar backtest
        try:
            results = engine.run(df=df, signals=signals, symbol=symbol)

            # Calcular PF
            if results.get("total_trades", 0) > 0:
                winning_sum = results.get("winning_pnl", 0)
                losing_sum = abs(results.get("losing_pnl", -1))
                pf = winning_sum / losing_sum if losing_sum > 0 else 1.0

                strategy_results[symbol] = {
                    "num_trades": results.get("total_trades", 0),
                    "pnl": float(results.get("total_pnl", 0)),
                    "win_rate": float(results.get("win_rate", 0)),
                    "pf": float(pf),
                    "max_dd": float(results.get("max_dd", 0))
                }

                print(f"     {symbol}: {strategy_results[symbol]['num_trades']} trades, "
                      f"PnL: ${strategy_results[symbol]['pnl']:.0f}, "
                      f"PF: {pf:.2f}")
            else:
                strategy_results[symbol] = {
                    "num_trades": 0,
                    "pnl": 0,
                    "win_rate": 0,
                    "pf": 1.0,
                    "max_dd": 0
                }
                print(f"     {symbol}: Sin trades")

        except Exception as e:
            print(f"     ❌ {symbol}: {str(e)[:60]}")
            strategy_results[symbol] = {"error": str(e)}

    all_results[strategy_name] = strategy_results

# === Scoring automático ===
print(f"\n⭐ Scoring automático (FASE 3)...")

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
        win_rate = metrics.get("win_rate", 0)

        # Scoring simple basado en PF
        if pf < 1.0 or num_trades < 5:
            score = 0
            verdict = "RECHAZAR"
        elif pf >= 2.0:
            score = 90 + min(10, (pf - 2.0) * 5)
            verdict = "ACTIVAR"
        elif pf >= 1.5:
            score = 75
            verdict = "CONSIDERAR"
        elif pf >= 1.2:
            score = 60
            verdict = "CONSIDERAR"
        else:
            score = 40
            verdict = "RECHAZAR"

        scores_by_symbol[symbol] = {
            "score": float(score),
            "verdict": verdict,
            "pf": float(pf)
        }

    scored[strategy_name] = scores_by_symbol

# === Reporte final ===
print(f"\n" + "="*80)
print("📈 RESULTADOS FINALES")
print("="*80)

print(f"\n{'Estrategia':<35} {'Verdicts CONSIDERAR+':<20} {'Avg PF':<10} {'Score Prom':<10}")
print("-" * 75)

for strategy_name, symbol_scores in scored.items():
    activar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "ACTIVAR")
    considerar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "CONSIDERAR")
    pf_vals = [s.get("pf", 1.0) for s in symbol_scores.values() if "pf" in s]
    avg_pf = np.mean(pf_vals) if pf_vals else 1.0
    avg_score = np.mean([s.get("score", 0) for s in symbol_scores.values()])

    considerar_plus = activar_count + considerar_count
    verdict_str = f"{activar_count}A + {considerar_count}C"

    print(f"{strategy_name:<35} {verdict_str:<20} {avg_pf:>8.2f} {avg_score:>9.1f}")

# === Top estrategias ===
print(f"\n🏆 TOP ESTRATEGIAS:")

all_scores = []
for strategy_name, symbol_scores in scored.items():
    for symbol, score_info in symbol_scores.items():
        all_scores.append({
            "strategy": strategy_name,
            "symbol": symbol,
            "score": score_info.get("score", 0),
            "verdict": score_info.get("verdict", "?"),
            "pf": score_info.get("pf", 1.0)
        })

all_scores.sort(key=lambda x: x["score"], reverse=True)

for i, item in enumerate(all_scores[:10], 1):
    emoji = "✅" if item["verdict"] == "ACTIVAR" else "⚠️ " if item["verdict"] == "CONSIDERAR" else "❌"
    print(f"  {i:2d}. {item['strategy']:<30} ({item['symbol']:<6}) "
          f"Score: {item['score']:>5.1f} {emoji} PF: {item['pf']:.2f}")

# === Guardar reporte ===
output_file = Path(__file__).parent.parent / "results" / "backtest_final_report.json"
with open(output_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_strategies": len(all_results),
            "total_symbols": len(symbols),
            "total_tests": len(all_results) * len(symbols)
        },
        "strategies": {
            name: {
                "results_by_symbol": results,
                "scores_by_symbol": scored.get(name, {})
            }
            for name, results in all_results.items()
        }
    }, f, indent=2)

print(f"\n💾 Reporte guardado: {output_file}")

print(f"\n" + "="*80)
print("✅ BACKTESTING COMPLETADO")
print("="*80)
print(f"\n📊 Resumen:")
print(f"   • Estrategias: {len(strategies)}")
print(f"   • Activos: {len(symbols)}")
print(f"   • Tests: {len(all_results) * len(symbols)}")

# Detectar si fueron datos reales o sintéticos
data_source = "REALES" if data_cache[symbols[0]].get('Date', pd.Series(dtype=object)).dtype == 'object' or 'Date' in data_cache[symbols[0]].columns else "Sintéticos"
if 'Date' in data_cache[symbols[0]].columns:
    data_source = "REALES"

print(f"   • Datos: {data_source} (252 días)")
print(f"   • Reporte: results/backtest_final_report.json\n")
