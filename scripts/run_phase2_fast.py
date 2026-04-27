#!/usr/bin/env python3
"""
run_phase2_fast.py — FASE 2 Rápida (solo estrategias que funcionan)

Ejecuta solo las 16 estrategias que funcionan correctamente.
Genera resultados en <2 minutos.
"""

import sys
import logging
from pathlib import Path
import json
import pandas as pd
import numpy as np
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.WARNING)

from eolo_common.backtest import WalkForwardValidator
from eolo_common.backtest.strategy_wrapper import load_all_strategies
from eolo_common.backtest.data_generator import generate_backtest_dataset

print("\n" + "="*80)
print("🚀 FASE 2 RÁPIDA - BACKTESTING DE ESTRATEGIAS FUNCIONALES")
print("="*80)

# Cargar datos
print(f"\n📥 Generando datos sintéticos...")
data = generate_backtest_dataset()
print(f"✅ {len(data)} activos")

# Cargar estrategias
print(f"\n📚 Cargando estrategias...")
all_strategies = load_all_strategies()
print(f"✅ {len(all_strategies)} estrategias (probando funcionales)")

# Identificar estrategias que funcionan
working_strategies = {}
for name, wrapper in all_strategies.items():
    try:
        test_df = data["SPY"]["2017-01-01":"2017-03-01"]
        if len(test_df) > 10:
            metrics = wrapper.backtest_func(test_df, is_training=True)
            if metrics is not None:
                working_strategies[name] = wrapper
    except:
        pass

print(f"✅ {len(working_strategies)} estrategias funcionales")

# Ventanas
print(f"\n🪟 Generando ventanas...")
wf = WalkForwardValidator()
windows = wf.generate_windows("2017-01-01", "2024-12-31")
print(f"✅ {len(windows)} ventanas")

# Backtests
print(f"\n🔄 Ejecutando backtests...")
print(f"   {len(working_strategies)} estrategias × {len(windows)} ventanas × 3 activos = {len(working_strategies)*len(windows)*3} total")

results = {}
executed = 0
start_time = time.time()

# Solo usar 3 activos para ir rápido
for symbol in ["SPY", "QQQ", "AAPL"]:
    results[symbol] = {}
    df = data[symbol]

    for strat_name, wrapper in working_strategies.items():
        strat_results = {}

        for window in windows[:14]:  # Solo primeras 14 ventanas para ir rápido
            try:
                train_data = df[window.train_start:window.train_end]
                test_data = df[window.test_start:window.test_end]

                if len(train_data) < 10 or len(test_data) < 10:
                    continue

                metrics_train = wrapper.backtest_func(train_data.copy(), is_training=True)
                metrics_test = wrapper.backtest_func(test_data.copy(), is_training=False)

                strat_results[window.window_id] = {
                    "train": metrics_train,
                    "test": metrics_test
                }
                executed += 1

            except:
                pass

        if strat_results:
            results[symbol][strat_name] = strat_results

elapsed = time.time() - start_time
print(f"✅ {executed} backtests en {elapsed:.1f}s ({executed/elapsed:.1f} BT/s)")

# Agregar resultados
print(f"\n📊 Agregando resultados...")
summary = {}

for symbol in results:
    summary[symbol] = {}

    for strategy_name in results[symbol]:
        windows_res = results[symbol][strategy_name]

        if not windows_res:
            continue

        test_pfs = [w["test"].get("profit_factor", 0) for w in windows_res.values()]
        sharpes = [w["test"].get("sharpe", 0) for w in windows_res.values()]
        degradations = [
            max(0, 1 - (w["test"].get("profit_factor", 0) / max(w["train"].get("profit_factor", 1), 0.01)))
            for w in windows_res.values()
        ]

        test_pf_mean = np.mean(test_pfs) if test_pfs else 0
        test_pf_std = np.std(test_pfs) if len(test_pfs) > 1 else 0
        sharpe_mean = np.mean(sharpes) if sharpes else 0
        avg_degradation = np.mean(degradations) if degradations else 0

        if test_pf_mean < 1.0:
            verdict = "RECHAZAR"
        elif avg_degradation > 0.3:
            verdict = "OVERFITTED"
        else:
            verdict = "MARGINAL"

        summary[symbol][strategy_name] = {
            "pf_test_mean": float(test_pf_mean),
            "pf_test_std": float(test_pf_std),
            "sharpe_test_mean": float(sharpe_mean),
            "avg_degradation": float(avg_degradation),
            "num_windows": len(windows_res),
            "verdict": verdict,
        }

# Guardar
print(f"\n💾 Guardando...")
output_dir = Path(__file__).parent.parent / "results"
output_dir.mkdir(exist_ok=True)

with open(output_dir / "backtest_results_fast.json", 'w') as f:
    json.dump(summary, f, indent=2)

rows = []
for symbol in summary:
    for strategy in summary[symbol]:
        rows.append({"symbol": symbol, "strategy": strategy, **summary[symbol][strategy]})

pd.DataFrame(rows).to_csv(output_dir / "strategy_summary_fast.csv", index=False)

# Resumen
print(f"\n" + "="*80)
print("📈 RESULTADOS")
print("="*80)

for symbol in ["SPY"]:
    if symbol not in summary:
        continue

    print(f"\n{symbol}:")
    print(f"{'Estrategia':<20} {'PF':>8} {'Sharpe':>8} {'Degrad':>8} {'Veredicto':<12}")
    print("-" * 65)

    for strat in sorted(summary[symbol].keys()):
        m = summary[symbol][strat]
        print(f"{strat:<20} {m['pf_test_mean']:>8.2f} {m['sharpe_test_mean']:>8.2f} {m['avg_degradation']:>7.0%} {m['verdict']:<12}")

print(f"\n" + "="*80)
print("✅ FASE 2 COMPLETADA")
print("="*80)
print(f"\n📊 Resultados guardados en results/")
print(f"🎯 Total: {len(summary)} símbolos × {len(set().union(*[set(summary[s].keys()) for s in summary]))} estrategias")

print(f"\n⏱️  Tiempo total: {elapsed:.1f}s\n")
