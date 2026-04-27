#!/usr/bin/env python3
"""
run_phase2_complete.py — FASE 2 Backtesting simplificado y robusto

Versión mejorada que:
  1. Ejecuta todos los backtests sin interrupciones
  2. Guarda resultados progresivamente
  3. Genera reportes en tiempo real
"""

import sys
import logging
from pathlib import Path
from datetime import datetime
import json
import pandas as pd
import numpy as np
from typing import Dict, List
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from eolo_common.backtest import BacktestDataLoader, WalkForwardValidator
from eolo_common.backtest.strategy_wrapper import load_all_strategies
from eolo_common.backtest.data_generator import generate_backtest_dataset


def main():
    """Ejecutar FASE 2."""
    print("\n" + "="*80)
    print("🚀 FASE 2 - BACKTESTING ROBUSTO DE 26 ESTRATEGIAS")
    print("="*80)

    # Datos
    print(f"\n📥 Cargando datos...")
    try:
        loader = BacktestDataLoader()
        equity_data = loader.load_equities(
            ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"],
            start_date="2017-01-01",
            end_date="2024-12-31",
            use_cache=True
        )
        if len(equity_data) == 0:
            raise ValueError("No data")
    except:
        logger.info("Usando datos sintéticos...")
        synthetic_data = generate_backtest_dataset()
        equity_data = {k: v for k, v in synthetic_data.items()}

    print(f"✅ {len(equity_data)} activos cargados")

    # Estrategias
    print(f"\n📚 Cargando estrategias...")
    strategies = load_all_strategies()
    print(f"✅ {len(strategies)} estrategias")

    if len(strategies) == 0:
        logger.error("No strategies loaded")
        return 1

    # Ventanas
    print(f"\n🪟 Generando ventanas walk-forward...")
    wf = WalkForwardValidator()
    windows = wf.generate_windows("2017-01-01", "2024-12-31")
    print(f"✅ {len(windows)} ventanas")

    # Ejecutar backtests
    print(f"\n🔄 Ejecutando backtests...")
    print(f"   Total esperado: {len(strategies)} × {len(windows)} × {len(equity_data)} = {len(strategies)*len(windows)*len(equity_data)} backtests")

    results = {}
    executed = 0
    failed = 0
    start_time = time.time()

    for symbol_idx, (symbol, df) in enumerate(equity_data.items()):
        results[symbol] = {}
        print(f"\n📊 {symbol_idx+1}/{len(equity_data)} — {symbol}")

        for strat_idx, (strategy_name, wrapper) in enumerate(strategies.items()):
            strategy_results = {}
            success_count = 0

            for window_idx, window in enumerate(windows):
                try:
                    train_data = df[window.train_start:window.train_end]
                    test_data = df[window.test_start:window.test_end]

                    if len(train_data) < 10 or len(test_data) < 10:
                        continue

                    metrics_train = wrapper.backtest_func(train_data.copy(), is_training=True)
                    metrics_test = wrapper.backtest_func(test_data.copy(), is_training=False)

                    degradation = max(0, 1 - (metrics_test.get("profit_factor", 0) / max(metrics_train.get("profit_factor", 1), 0.01)))

                    strategy_results[window_idx] = {
                        "train": metrics_train,
                        "test": metrics_test,
                        "degradation": degradation
                    }

                    executed += 1
                    success_count += 1

                except Exception as e:
                    failed += 1
                    continue

            if strategy_results:
                results[symbol][strategy_name] = strategy_results

            # Progress
            if (strat_idx + 1) % 5 == 0 or strat_idx == len(strategies) - 1:
                elapsed = time.time() - start_time
                rate = executed / elapsed if elapsed > 0 else 0
                print(f"   [{strat_idx+1:2d}/{len(strategies):2d}] {success_count:2d} windows | {executed:4d} total | {rate:.1f} BT/s")

    elapsed = time.time() - start_time
    print(f"\n✅ {executed} backtests ejecutados en {elapsed:.1f}s ({executed/elapsed:.1f} BT/s)")
    print(f"⚠️  {failed} fallos")

    # Agregar resultados
    print(f"\n📊 Agregando resultados...")
    summary = {}

    for symbol in results:
        summary[symbol] = {}

        for strategy_name in results[symbol]:
            windows_results = results[symbol][strategy_name]

            test_pfs = [w["test"].get("profit_factor", 0) for w in windows_results.values()]
            degradations = [w["degradation"] for w in windows_results.values()]
            sharpes = [w["test"].get("sharpe", 0) for w in windows_results.values()]

            test_pf_mean = np.mean(test_pfs) if test_pfs else 0
            test_pf_std = np.std(test_pfs) if len(test_pfs) > 1 else 0
            avg_degradation = np.mean(degradations) if degradations else 0
            sharpe_mean = np.mean(sharpes) if sharpes else 0

            num_windows = len(windows_results)
            num_overfitted = sum(1 for d in degradations if d > 0.30)

            if test_pf_mean < 1.0:
                verdict = "RECHAZAR"
            elif num_overfitted / max(num_windows, 1) > 0.5:
                verdict = "OVERFITTED"
            elif test_pf_std / max(test_pf_mean, 0.01) > 0.5:
                verdict = "INCONSISTENTE"
            elif test_pf_mean >= 1.2:
                verdict = "ROBUSTO"
            else:
                verdict = "MARGINAL"

            summary[symbol][strategy_name] = {
                "pf_test_mean": float(test_pf_mean),
                "pf_test_std": float(test_pf_std),
                "sharpe_test_mean": float(sharpe_mean),
                "avg_degradation": float(avg_degradation),
                "num_windows": int(num_windows),
                "num_overfitted": int(num_overfitted),
                "verdict": verdict,
            }

    # Guardar
    print(f"\n💾 Guardando resultados...")
    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    json_file = output_dir / "backtest_results_complete.json"
    with open(json_file, 'w') as f:
        json.dump(summary, f, indent=2)
    logger.info(f"✓ {json_file}")

    # CSV
    rows = []
    for symbol in summary:
        for strategy in summary[symbol]:
            row = {"symbol": symbol, "strategy": strategy, **summary[symbol][strategy]}
            rows.append(row)

    csv_file = output_dir / "strategy_summary_complete.csv"
    pd.DataFrame(rows).to_csv(csv_file, index=False)
    logger.info(f"✓ {csv_file}")

    # Resumen
    print(f"\n" + "="*80)
    print("📈 RESULTADOS PRINCIPALES")
    print("="*80)

    for symbol in ["SPY"]:
        if symbol not in summary:
            continue

        print(f"\n{symbol} (top 10):")
        print(f"{'Estrategia':<20} {'PF':>8} {'Sharpe':>8} {'Degrad':>8} {'Veredicto':<12}")
        print("-" * 65)

        strategies_sorted = sorted(
            summary[symbol].items(),
            key=lambda x: x[1]["pf_test_mean"],
            reverse=True
        )

        for strat, metrics in strategies_sorted[:10]:
            print(f"{strat:<20} {metrics['pf_test_mean']:>8.2f} {metrics['sharpe_test_mean']:>8.2f} {metrics['avg_degradation']:>7.0%} {metrics['verdict']:<12}")

    print(f"\n" + "="*80)
    print("✅ FASE 2 COMPLETADA")
    print("="*80)
    print(f"\n📊 Archivos generados:")
    print(f"   - {json_file}")
    print(f"   - {csv_file}")
    print(f"\n🎯 Próximos pasos:")
    print(f"   1. Generar dashboard HTML")
    print(f"   2. Crear matriz de decisión (Score 0-100)")
    print(f"   3. Exportar a Sheets")

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
