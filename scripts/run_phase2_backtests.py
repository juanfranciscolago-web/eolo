#!/usr/bin/env python3
"""
run_phase2_backtests.py — Ejecutar backtests de 26 estrategias × 28 ventanas FASE 2

Flujo:
  1. Cargar datos sintéticos (o datos reales si están disponibles)
  2. Cargar todas las 26 estrategias desde Bot/
  3. Generar 28 ventanas walk-forward (train 12m, test 3m)
  4. Ejecutar backtests en paralelo (si posible)
  5. Agregar resultados
  6. Guardar CSV + JSON con resultados
  7. Calcular scores 0-100
  8. Generar matriz de decisión
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

# Setup
sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from eolo_common.backtest import (
    BacktestDataLoader,
    RegimeClassifier,
    WalkForwardValidator,
    BacktestEngine
)
from eolo_common.backtest.strategy_wrapper import load_all_strategies
from eolo_common.backtest.data_generator import generate_backtest_dataset


def main():
    """Ejecutar FASE 2 completa."""

    print("\n" + "="*80)
    print("🚀 FASE 2 - BACKTESTING DE 26 ESTRATEGIAS × 28 VENTANAS")
    print("="*80)

    # ========================================================================
    # PASO 1: Cargar datos
    # ========================================================================
    print(f"\n📥 PASO 1: Cargar datos históricos...")

    try:
        loader = BacktestDataLoader()
        equity_data = loader.load_equities(
            ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"],
            start_date="2017-01-01",
            end_date="2024-12-31",
            use_cache=True
        )
        if len(equity_data) == 0:
            raise ValueError("No equity data loaded")
        logger.info(f"✓ Datos reales cargados: {len(equity_data)} símbolos")
    except Exception as e:
        logger.warning(f"⚠️ No se pudieron cargar datos reales ({str(e)[:50]})")
        logger.info("Usando datos sintéticos realistas...")
        synthetic_data = generate_backtest_dataset()
        # Usar todos los datos sintéticos disponibles
        equity_data = {k: v for k, v in synthetic_data.items()}

    print(f"✅ Datos listos: {len(equity_data)} activos")
    for symbol, df in equity_data.items():
        print(f"   {symbol:6} | {len(df):4} barras | ${df['Close'].iloc[0]:.2f} → ${df['Close'].iloc[-1]:.2f}")

    # ========================================================================
    # PASO 2: Cargar estrategias
    # ========================================================================
    print(f"\n📚 PASO 2: Cargar estrategias...")

    strategies = load_all_strategies()
    print(f"✅ {len(strategies)} estrategias cargadas")
    for name in list(strategies.keys())[:5]:
        print(f"   - {name}")
    if len(strategies) > 5:
        print(f"   ... y {len(strategies)-5} más")

    if len(strategies) == 0:
        logger.error("❌ No se cargaron estrategias. Abortar.")
        return 1

    # ========================================================================
    # PASO 3: Generar ventanas walk-forward
    # ========================================================================
    print(f"\n🪟 PASO 3: Generar ventanas walk-forward...")

    wf = WalkForwardValidator()
    windows = wf.generate_windows("2017-01-01", "2024-12-31")
    print(f"✅ {len(windows)} ventanas generadas")
    print(f"   Primera: Train [{windows[0].train_start} → {windows[0].train_end}] | Test [{windows[0].test_start} → {windows[0].test_end}]")
    print(f"   Última:  Train [{windows[-1].train_start} → {windows[-1].train_end}] | Test [{windows[-1].test_start} → {windows[-1].test_end}]")

    # ========================================================================
    # PASO 4: Ejecutar backtests
    # ========================================================================
    print(f"\n🔄 PASO 4: Ejecutar backtests...")
    print(f"   {len(strategies)} estrategias × {len(windows)} ventanas × {len(equity_data)} activos = {len(strategies) * len(windows) * len(equity_data)} backtests")

    start_time = time.time()
    results = {}
    backtest_count = 0
    total_backtests = len(strategies) * len(windows) * len(equity_data)

    for symbol, df in equity_data.items():
        logger.info(f"\n📊 {symbol}:")

        for strategy_name, wrapper in strategies.items():
            strategy_results = {}

            for window_idx, window in enumerate(windows):
                # Extraer data para esta ventana
                train_data = df[window.train_start:window.train_end]
                test_data = df[window.test_start:window.test_end]

                if len(train_data) == 0 or len(test_data) == 0:
                    logger.debug(f"   {strategy_name} / WF{window_idx}: datos insuficientes")
                    continue

                try:
                    # Ejecutar backtest
                    metrics_train = wrapper.backtest_func(train_data, is_training=True)
                    metrics_test = wrapper.backtest_func(test_data, is_training=False)

                    strategy_results[window_idx] = {
                        "train": metrics_train,
                        "test": metrics_test,
                        "degradation": max(0, 1 - (metrics_test.get("profit_factor", 0) / max(metrics_train.get("profit_factor", 1), 0.01)))
                    }

                    backtest_count += 1

                except Exception as e:
                    logger.debug(f"   {strategy_name} / WF{window_idx}: {e}")
                    continue

            if strategy_results:
                if symbol not in results:
                    results[symbol] = {}
                results[symbol][strategy_name] = strategy_results

            elapsed = time.time() - start_time
            rate = backtest_count / elapsed if elapsed > 0 else 0
            eta_secs = (total_backtests - backtest_count) / rate if rate > 0 else 0
            eta_mins = eta_secs / 60

            if backtest_count % (len(windows) * 3) == 0 and backtest_count > 0:
                logger.info(f"   Progreso: {backtest_count}/{total_backtests} ({100*backtest_count/total_backtests:.0f}%) | {rate:.1f} BT/s | ETA {eta_mins:.0f} min")

    elapsed = time.time() - start_time
    print(f"\n✅ {backtest_count} backtests ejecutados en {elapsed/60:.1f} minutos ({backtest_count/elapsed:.1f} BT/s)")

    # ========================================================================
    # PASO 5: Agregar resultados y calcular métricas
    # ========================================================================
    print(f"\n📊 PASO 5: Agregar resultados...")

    summary = {}
    for symbol in results:
        summary[symbol] = {}

        for strategy_name in results[symbol]:
            windows_results = results[symbol][strategy_name]

            test_pfs = [w["test"].get("profit_factor", 0) for w in windows_results.values()]
            test_pf_mean = np.mean(test_pfs) if test_pfs else 0
            test_pf_std = np.std(test_pfs) if len(test_pfs) > 1 else 0

            degradations = [w["degradation"] for w in windows_results.values()]
            avg_degradation = np.mean(degradations) if degradations else 0

            test_sharpes = [w["test"].get("sharpe", 0) for w in windows_results.values()]
            test_sharpe_mean = np.mean(test_sharpes) if test_sharpes else 0

            num_windows = len(windows_results)
            num_overfitted = sum(1 for d in degradations if d > 0.30)

            # Veredicto
            if test_pf_mean < 1.0:
                verdict = "RECHAZAR"
            elif num_overfitted / num_windows > 0.5:
                verdict = "OVERFITTED"
            elif test_pf_std / max(test_pf_mean, 0.01) > 0.5:
                verdict = "INCONSISTENTE"
            elif test_pf_mean >= 1.2 and avg_degradation < 0.30:
                verdict = "ROBUSTO"
            else:
                verdict = "MARGINAL"

            summary[symbol][strategy_name] = {
                "pf_test_mean": test_pf_mean,
                "pf_test_std": test_pf_std,
                "sharpe_test_mean": test_sharpe_mean,
                "avg_degradation": avg_degradation,
                "num_windows": num_windows,
                "num_overfitted": num_overfitted,
                "verdict": verdict,
            }

    print(f"\n✅ Métricas agregadas para {len(summary)} activos")

    # ========================================================================
    # PASO 6: Guardar resultados
    # ========================================================================
    print(f"\n💾 PASO 6: Guardar resultados...")

    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    # JSON con resultados completos
    results_json = output_dir / "backtest_results.json"
    with open(results_json, 'w') as f:
        # Convertir numpy types para JSON serialization
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"✓ {results_json}")

    # CSV con resumen
    summary_rows = []
    for symbol in summary:
        for strategy in summary[symbol]:
            row = {
                "symbol": symbol,
                "strategy": strategy,
                **summary[symbol][strategy]
            }
            summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    summary_csv = output_dir / "strategy_summary.csv"
    summary_df.to_csv(summary_csv, index=False)
    logger.info(f"✓ {summary_csv}")

    # ========================================================================
    # PASO 7: Mostrar resultados
    # ========================================================================
    print(f"\n" + "="*80)
    print("📈 RESULTADOS POR ESTRATEGIA")
    print("="*80)

    for symbol in ["SPY"]:  # Mostrar solo SPY para brevedad
        if symbol not in summary:
            continue

        print(f"\n{symbol}:")
        print(f"{'Estrategia':<20} {'PF OOS':>10} {'Sharpe':>10} {'Degrad.':>10} {'Veredicto':<15}")
        print("-" * 70)

        for strategy in sorted(summary[symbol].keys()):
            s = summary[symbol][strategy]
            print(f"{strategy:<20} {s['pf_test_mean']:>10.2f} {s['sharpe_test_mean']:>10.2f} {s['avg_degradation']:>10.1%} {s['verdict']:<15}")

    # ========================================================================
    # RESUMEN FINAL
    # ========================================================================
    print(f"\n" + "="*80)
    print("✅ FASE 2 COMPLETADA")
    print("="*80)
    print(f"\n📊 Resultados guardados en: {output_dir}")
    print(f"   - {results_json}")
    print(f"   - {summary_csv}")
    print(f"\n🎯 Próximos pasos:")
    print(f"   1. Generar dashboard HTML interactivo")
    print(f"   2. Crear matriz de decisión (Score 0-100)")
    print(f"   3. Exportar a Sheets para integración Bot")

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
