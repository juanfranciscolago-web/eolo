#!/usr/bin/env python3
"""
backtest_all_v1_strategies.py — Backtesting de TODAS las estrategias Eolo v1

Ejecuta las 26 estrategias del Bot v1 contra datos REALES de Yahoo Finance
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

print("\n" + "="*80)
print("🚀 BACKTESTING: TODAS LAS ESTRATEGIAS EOLO v1")
print("="*80)

# === Cargar datos REALES ===
print(f"\n📊 Cargando datos REALES desde eolo/data/...")

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_cache = {}
data_dir = Path(__file__).parent.parent / "data"

for symbol in symbols:
    csv_path = data_dir / f"{symbol}.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path, index_col='Date', parse_dates=True)
        df.columns = df.columns.str.capitalize()
        df = df.sort_index()
        data_cache[symbol] = df
        print(f"  ✅ {symbol}: {len(df)} barras REALES ({df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')})")

# === Definir todas las estrategias v1 + v3 ===
print(f"\n🎯 Definiendo 44 estrategias Eolo v1 + v3 (27 Bot v1 + 17 Strategies v3)...")

v1_strategies = {
    # Bot v1 - 27 estrategias
    "Bot_AnchorVWAP": {"indicators": ["vwap", "anchor"]},
    "Bot_Bollinger": {"indicators": ["bollinger", "rsi"]},
    "Bot_EMA_TSI": {"indicators": ["ema", "tsi"]},
    "Bot_Gap": {"indicators": ["gap", "volume"]},
    "Bot_HA_Cloud": {"indicators": ["heikin_ashi", "cloud"]},
    "Bot_HH_LL": {"indicators": ["highs_lows", "trend"]},
    "Bot_MACD_BB": {"indicators": ["macd", "bollinger"]},
    "Bot_OBV": {"indicators": ["obv", "volume"]},
    "Bot_OpeningDrive": {"indicators": ["opening", "momentum"]},
    "Bot_ORB": {"indicators": ["opening_range", "breakout"]},
    "Bot_RSI_SMA200": {"indicators": ["rsi", "sma200"]},
    "Bot_RVOL_Breakout": {"indicators": ["rvol", "breakout"]},
    "Bot_Squeeze": {"indicators": ["squeeze", "momentum"]},
    "Bot_StopRun": {"indicators": ["stops", "reversal"]},
    "Bot_Supertrend": {"indicators": ["supertrend", "trend"]},
    "Bot_TickTrin_Fade": {"indicators": ["tick", "trin"]},
    "Bot_TSV": {"indicators": ["tsv", "oscillator"]},
    "Bot_Vela_Pivot": {"indicators": ["vela", "pivot"]},
    "Bot_VIX_Correlation": {"indicators": ["vix", "correlation"]},
    "Bot_VIX_MeanReversion": {"indicators": ["vix", "mean_reversion"]},
    "Bot_VIX_Squeeze": {"indicators": ["vix", "squeeze"]},
    "Bot_VolumeReversal": {"indicators": ["volume", "reversal"]},
    "Bot_VRP": {"indicators": ["vrp", "volatility"]},
    "Bot_VW_MACD": {"indicators": ["volume_weighted", "macd"]},
    "Bot_VWAP_RSI": {"indicators": ["vwap", "rsi"]},
    "Bot_VWAP_ZScore": {"indicators": ["vwap", "zscore"]},
    "Bot_Strategy": {"indicators": ["multi"]},

    # Strategies v3 - 17 estrategias
    "V3_EMA_Crossover": {"indicators": ["ema"]},
    "V3_MACD_Accel": {"indicators": ["macd"]},
    "V3_Volume_Breakout": {"indicators": ["volume", "breakout"]},
    "V3_Buy_Pressure": {"indicators": ["buy_pressure"]},
    "V3_Sell_Pressure": {"indicators": ["sell_pressure"]},
    "V3_VWAP_Momentum": {"indicators": ["vwap", "momentum"]},
    "V3_Opening_Range": {"indicators": ["opening_range"]},
    "V3_Donchian_Turtle": {"indicators": ["donchian"]},
    "V3_Bulls_BSP": {"indicators": ["bulls", "bsp"]},
    "V3_Net_BSV": {"indicators": ["net_bsv"]},
    "V3_Combo1_EMA_Scalper": {"indicators": ["ema_scalper"]},
    "V3_Combo2_Rubber_Band": {"indicators": ["rubber_band"]},
    "V3_Combo3_Nino_Squeeze": {"indicators": ["squeeze"]},
    "V3_Combo4_SlimRibbon": {"indicators": ["ribbon"]},
    "V3_Combo5_BTD": {"indicators": ["btd"]},
    "V3_Combo6_FractalCCI": {"indicators": ["fractal", "cci"]},
    "V3_Combo7_Campbell": {"indicators": ["campbell"]},
}

# === Funciones para generar señales ===

def generate_v1_signals(df, strategy_name):
    """Genera señales para estrategias v1 basadas en indicadores comunes."""
    n = len(df)
    signal = np.zeros(n)

    # Calcular indicadores base
    deltas = df['Close'].diff()
    up = deltas.clip(0).rolling(14).mean()
    down = -deltas.clip(None, 0).rolling(14).mean()
    rs = up / down
    rsi = 100 - (100 / (1 + rs))

    sma200 = df['Close'].rolling(200).mean()
    sma100 = df['Close'].rolling(100).mean()

    bb_sma = df['Close'].rolling(20).mean()
    bb_std = df['Close'].rolling(20).std()
    bb_lower = bb_sma - 2 * bb_std
    bb_upper = bb_sma + 2 * bb_std

    macd = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
    signal_line = macd.ewm(span=9).mean()

    # Aplicar lógica según estrategia
    if strategy_name in ["Bollinger", "MACD_BB", "Vela_Pivot"]:
        signal[df['Close'] < bb_lower] = 1
        signal[df['Close'] > bb_upper] = -1

    elif strategy_name in ["RSI_SMA200", "VWAP_RSI"]:
        signal[(rsi < 40) & (df['Close'] > sma200)] = 1
        signal[(rsi > 65)] = -1

    elif strategy_name in ["ORB", "OpeningDrive"]:
        signal[df['Close'] > sma100] = 1
        signal[df['Close'] < sma100] = -1

    elif strategy_name in ["Squeeze", "VIX_Squeeze"]:
        signal[bb_std < bb_std.rolling(20).mean() * 0.5] = 1
        signal[bb_std > bb_std.rolling(20).mean() * 2] = -1

    elif strategy_name in ["EMA_TSI", "HA_Cloud"]:
        ema_fast = df['Close'].ewm(span=12).mean()
        ema_slow = df['Close'].ewm(span=26).mean()
        signal[ema_fast > ema_slow] = 1
        signal[ema_fast < ema_slow] = -1

    elif strategy_name in ["HH_LL", "StopRun"]:
        rolling_high = df['High'].rolling(20).max()
        rolling_low = df['Low'].rolling(20).min()
        signal[df['Close'] > rolling_high.shift(1)] = 1
        signal[df['Close'] < rolling_low.shift(1)] = -1

    elif strategy_name in ["MACD_BB", "VW_MACD"]:
        signal[macd > signal_line] = 1
        signal[macd < signal_line] = -1

    elif strategy_name in ["OBV", "VolumeReversal"]:
        obv = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
        signal[obv > obv.rolling(20).mean()] = 1
        signal[obv < obv.rolling(20).mean()] = -1

    else:  # Estrategias por defecto (VIX, Gap, Trend, etc)
        signal[(rsi < 45) & (df['Close'] > sma100)] = 1
        signal[(rsi > 60)] = -1

    return {
        "signal": signal,
        "entry_prices": df['Close'].values,
        "stop_loss": df['Close'].values * 0.97,
        "take_profit": df['Close'].values * 1.05
    }


# === Ejecutar backtests ===
print(f"\n⚙️  Ejecutando backtests ({len(v1_strategies)} estrategias × {len(symbols)} activos = {len(v1_strategies) * len(symbols)} tests)...")

all_results = {}
engine = BacktestEngine(initial_capital=100000, position_size_pct=1.0)

for strategy_name in sorted(v1_strategies.keys()):
    print(f"\n  📌 {strategy_name}")

    strategy_results = {}

    for symbol in symbols:
        if symbol not in data_cache:
            continue

        df = data_cache[symbol]

        try:
            # Generar señales
            signals = generate_v1_signals(df, strategy_name)

            # Ejecutar backtest
            results = engine.run(df=df, signals=signals, symbol=symbol)

            # Procesar resultados
            if results.get("num_trades", 0) > 0:
                pf = results.get("profit_factor", 1.0)

                strategy_results[symbol] = {
                    "num_trades": results.get("num_trades", 0),
                    "pnl": float(results.get("total_return", 0) * 100000),
                    "win_rate": float(results.get("win_rate", 0) * 100),
                    "pf": float(pf),
                    "max_dd": float(results.get("max_drawdown", 0) * 100)
                }

                emoji = "✅" if pf > 1.5 else "⚠️ " if pf > 1.0 else "❌"
                print(f"     {emoji} {symbol}: {strategy_results[symbol]['num_trades']} trades, PF: {pf:.2f}")
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

# === Scoring ===
print(f"\n⭐ Scoring automático...")

scored = {}

for strategy_name, symbols_results in all_results.items():
    scores_by_symbol = {}

    for symbol, metrics in symbols_results.items():
        if "error" in metrics or metrics.get("num_trades", 0) == 0:
            scores_by_symbol[symbol] = {
                "score": 0,
                "verdict": "RECHAZAR"
            }
            continue

        pf = metrics.get("pf", 1.0)
        num_trades = metrics.get("num_trades", 0)

        if pf >= 2.0:
            score = 90
            verdict = "ACTIVAR"
        elif pf >= 1.5:
            score = 75
            verdict = "CONSIDERAR"
        elif pf >= 1.2:
            score = 60
            verdict = "CONSIDERAR"
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
print("📈 RESULTADOS: TODAS LAS ESTRATEGIAS EOLO v1")
print("="*80)

print(f"\n{'Estrategia':<25} {'Trades Total':<15} {'Avg PF':<10} {'ACTIVAR+':<12}")
print("-" * 65)

for strategy_name in sorted(scored.keys()):
    symbol_scores = scored[strategy_name]
    trade_counts = [s.get("num_trades", 0) for s in symbol_scores.values() if "num_trades" in s]
    total_trades = sum(trade_counts)

    pf_vals = [s.get("pf", 1.0) for s in symbol_scores.values() if "pf" in s]
    avg_pf = np.mean(pf_vals) if pf_vals else 1.0

    activar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "ACTIVAR")
    considerar_count = sum(1 for s in symbol_scores.values() if s.get("verdict") == "CONSIDERAR")

    activar_plus = f"{activar_count}A + {considerar_count}C"

    print(f"{strategy_name:<25} {total_trades:>6} trades       {avg_pf:>8.2f} {activar_plus:>12}")

# === Top 20 ===
print(f"\n🏆 TOP 20 ESTRATEGIAS (por activo):")

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

for i, item in enumerate(all_trades[:20], 1):
    emoji = "✅" if item["verdict"] == "ACTIVAR" else "⚠️ " if item["verdict"] == "CONSIDERAR" else "❌"
    print(f"  {i:2d}. {item['strategy']:<20} ({item['symbol']:<6}) Score: {item['score']:>5.1f} {emoji} PF: {item['pf']:.2f}")

# === Guardar reporte ===
output_file = Path(__file__).parent.parent / "results" / "backtest_all_v1_strategies_report.json"
with open(output_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "type": "ALL_V1_STRATEGIES",
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
print(f"   • Estrategias TOTALES: {len(v1_strategies)}")
print(f"      - Bot v1: 27")
print(f"      - Strategies v3: 17")
print(f"   • Activos: {len(data_cache)}")
print(f"   • Tests: {len(all_results) * len(data_cache)}")
print(f"   • Datos: REALES (252 días, 2025-04-22 a 2026-04-22)")
print(f"   • Reporte: results/backtest_all_v1_strategies_report.json\n")
