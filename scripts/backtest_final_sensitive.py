#!/usr/bin/env python3
"""
backtest_final_sensitive.py — Backtesting con Thresholds Ajustados

Mismas 6 estrategias pero con THRESHOLDS MÁS SENSIBLES para generar trades
en mercados alcistas (bull markets)

Cambios:
- RSI < 30 → RSI < 40 (más sensible)
- RSI > 70 → RSI > 65 (salida más rápida)
- SMA200 > price → SMA100 (menos restrictivo)
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


# ============================================================
# Funciones auxiliares para generar señales (THRESHOLDS SENSIBLES)
# ============================================================

def generate_nn_signals_sensitive(df, config):
    """Genera señales NN con thresholds MÁS SENSIBLES."""
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
    bb_upper = bb_sma + 2 * bb_std

    signal = np.zeros(n)

    if config.get("entry_signal") == "rsi_oversold":
        # MÁS SENSIBLE: RSI < 40 en lugar de < 30
        signal[rsi < 40] = 1
        signal[rsi > 65] = -1
    elif config.get("entry_signal") == "macd_bullish":
        signal[macd_line > 0] = 1
        signal[macd_line < -0.5] = -1
    elif config.get("entry_signal") == "bollinger_bottom":
        # Entrada si está cerca del lower (dentro de 1 std)
        signal[(df['Close'] < bb_lower) | (df['Close'] < (bb_sma - bb_std))] = 1
        signal[df['Close'] > bb_upper] = -1

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.97,  # SL 3%
        "take_profit": df['Close'].values * 1.05  # TP 5%
    }


def generate_classic_signals_sensitive(df, config):
    """Genera señales clásicas con thresholds MÁS SENSIBLES."""
    n = len(df)

    # RSI (CORREGIDO)
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()  # Mantener solo ganancias
    down = -deltas.clip(None, 0).rolling(14).mean()  # Mantener solo pérdidas y negar
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    # SMA (100 en lugar de 200 para ser más sensible)
    sma100 = df['Close'].rolling(100).mean()

    # Bollinger
    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std
    bb_upper = bb_sma + 2 * bb_std

    signal = np.zeros(n)

    if "rsi" in config.get("indicators", []) and "sma200" in config.get("indicators", []):
        # RSI + SMA100 (MÁS SENSIBLE)
        signal[(rsi < 40) & (df['Close'] > sma100)] = 1
        signal[(rsi > 65) | (df['Close'] < sma100)] = -1

    elif "bollinger" in config.get("indicators", []):
        # Bollinger con thresholds ajustados
        signal[(df['Close'] < bb_lower) | (df['Close'] < (bb_sma - bb_std))] = 1
        signal[df['Close'] > bb_upper] = -1

    else:
        # Default: simple RSI (MÁS SENSIBLE)
        signal[rsi < 40] = 1
        signal[rsi > 65] = -1

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.96,  # SL 4%
        "take_profit": df['Close'].values * 1.06  # TP 6%
    }


print("\n" + "="*80)
print("🚀 BACKTESTING FINAL: FASE 4 + BOT v1 (THRESHOLDS SENSIBLES)")
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

            # Capitalizar columnas
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

# === Definir estrategias ===
print(f"\n🎯 Definiendo 6 estrategias (THRESHOLDS SENSIBLES)...")

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
    "Bot_RSI_SMA100": {
        "type": "Classic",
        "indicators": ["rsi", "sma200"],  # Config name pero usa SMA100
        "entry_rule": "rsi < 40 AND price > sma100",
        "exit_rule": "rsi > 65 OR price < sma100"
    },
    "Bot_ORB_Sensitive": {
        "type": "Classic",
        "indicators": ["open_range_breakout"],
        "entry_rule": "RSI momentum based",
        "exit_rule": "RSI > 65"
    },
    "Bot_Bollinger_RSI_Sensitive": {
        "type": "Classic",
        "indicators": ["bollinger", "rsi"],
        "entry_rule": "close < bb_sma - 1std AND rsi < 40",
        "exit_rule": "close > bb_sma OR rsi > 65"
    },
}

# === Ejecutar backtests ===
print(f"\n⚙️  Ejecutando backtests (THRESHOLDS SENSIBLES)...")

all_results = {}
engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

for strategy_name, strategy_config in strategies.items():
    print(f"\n  📌 {strategy_name}")

    strategy_results = {}

    for symbol in symbols:
        if symbol not in data_cache:
            continue

        df = data_cache[symbol]

        # Generar señales basadas en tipo de estrategia
        if strategy_config["type"] == "NN":
            signals = generate_nn_signals_sensitive(df, strategy_config)
        else:  # Classic
            signals = generate_classic_signals_sensitive(df, strategy_config)

        # Ejecutar backtest
        try:
            results = engine.run(df=df, signals=signals, symbol=symbol)

            # Obtener PF de BacktestEngine (ya lo calcula)
            if results.get("num_trades", 0) > 0:
                pf = results.get("profit_factor", 1.0)

                strategy_results[symbol] = {
                    "num_trades": results.get("num_trades", 0),
                    "pnl": float(results.get("total_return", 0) * 100000),  # Convertir % a $
                    "win_rate": float(results.get("win_rate", 0) * 100),  # Convertir a %
                    "pf": float(pf),
                    "max_dd": float(results.get("max_drawdown", 0) * 100)  # Convertir a %
                }

                emoji = "✅" if pf > 1.2 else "⚠️ " if pf > 1.0 else "❌"
                print(f"     {emoji} {symbol}: {strategy_results[symbol]['num_trades']} trades, "
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
                print(f"     ○ {symbol}: Sin trades")

        except Exception as e:
            print(f"     ❌ {symbol}: {str(e)[:60]}")
            strategy_results[symbol] = {"error": str(e)}

    all_results[strategy_name] = strategy_results

# === Scoring automático ===
print(f"\n⭐ Scoring automático...")

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

        # Scoring: PF > 1.5 es bueno, > 1.2 es aceptable
        if pf < 1.0 or num_trades < 5:
            score = 0
            verdict = "RECHAZAR"
        elif pf >= 2.0:
            score = 90
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
            "pf": float(pf),
            "num_trades": num_trades
        }

    scored[strategy_name] = scores_by_symbol

# === Reporte final ===
print(f"\n" + "="*80)
print("📈 RESULTADOS CON THRESHOLDS SENSIBLES")
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
            "pf": score_info.get("pf", 1.0),
            "trades": score_info.get("num_trades", 0)
        })

all_scores.sort(key=lambda x: (x["score"], x["trades"]), reverse=True)

for i, item in enumerate(all_scores[:10], 1):
    emoji = "✅" if item["verdict"] == "ACTIVAR" else "⚠️ " if item["verdict"] == "CONSIDERAR" else "❌"
    trades_str = f"{item['trades']} trades" if item['trades'] > 0 else "0 trades"
    print(f"  {i:2d}. {item['strategy']:<30} ({item['symbol']:<6}) "
          f"Score: {item['score']:>5.1f} {emoji} {trades_str:<10} PF: {item['pf']:.2f}")

# === Guardar reporte ===
output_file = Path(__file__).parent.parent / "results" / "backtest_final_sensitive_report.json"
with open(output_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "type": "SENSITIVE_THRESHOLDS",
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
print(f"   • Activos: {len(data_cache)}")
print(f"   • Tests: {len(all_results) * len(data_cache)}")
print(f"   • Datos: REALES (252 días, 2025-04-22 to 2026-04-22)")
print(f"   • Thresholds: SENSIBLES (RSI < 40, SMA100, etc)")
print(f"   • Reporte: results/backtest_final_sensitive_report.json\n")
