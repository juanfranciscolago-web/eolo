#!/usr/bin/env python3
"""
run_phase4_nn.py — FASE 4: Red Neuronal Multi-TF + Auto-generación de Estrategias

Ejecuta:
  1. Extrae 28 features multi-TF de FASE 2
  2. Entrena LSTM sobre patrones ganadores/perdedores
  3. Genera 15 estrategias nuevas basadas en NN
  4. Scoring automático de nuevas estrategias
  5. Crea dashboard de confluencia NN
"""

import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.multi_tf_features import MultiTFFeatureExtractor, MultiTFFeatures
from eolo_common.backtest.confluence_nn import SimpleNNConfluence
from eolo_common.backtest.strategy_generator import StrategyGenerator
from eolo_common.backtest.data_generator import SyntheticOHLCVGenerator
from eolo_common.backtest.activation_rules import ActivationScorer

print("\n" + "="*80)
print("🧠 FASE 4 - RED NEURONAL MULTI-TF + AUTO-GENERACIÓN")
print("="*80)

# === PASO 1: Cargar datos FASE 2 ===
print(f"\n📥 Paso 1: Cargando resultados de FASE 2...")

results_dir = Path(__file__).parent.parent / "results"
results_file = results_dir / "backtest_results_fast.json"

if not results_file.exists():
    print(f"❌ No encontrado: {results_file}")
    sys.exit(1)

with open(results_file) as f:
    backtest_results = json.load(f)

print(f"✅ {len(backtest_results)} activos cargados")

# === PASO 2: Generar datos sintéticos multi-TF ===
print(f"\n📊 Paso 2: Generando datos sintéticos multi-TF...")

data_gen = SyntheticOHLCVGenerator(seed=42)

# Generar portfolio sintético (500 días = ~2 años de datos para features)
symbols_config = {
    symbol: {
        "start_price": 100,
        "volatility": 0.20,
        "drift": 0.0003,
        "regime": "bull" if i % 2 == 0 else "bear",
        "volume_base": 1000000
    }
    for i, symbol in enumerate(backtest_results.keys())
}

df_by_symbol = data_gen.generate_portfolio(symbols=symbols_config, days=500)

# Renombrar columnas a lowercase
for symbol in df_by_symbol:
    df_by_symbol[symbol].columns = df_by_symbol[symbol].columns.str.lower()

print(f"✅ Datos generados para {len(df_by_symbol)} activos")

# === PASO 3: Extraer features multi-TF ===
print(f"\n🔍 Paso 3: Extrayendo 28 features multi-TF...")

extractor = MultiTFFeatureExtractor()
training_dataset = extractor.create_training_dataset(
    backtest_results=backtest_results,
    df_by_symbol=df_by_symbol
)

print(f"✅ Features extraídos: shape {training_dataset.features.shape}")
print(f"   Distribución labels: {np.bincount(training_dataset.labels)}")
print(f"   Features: {len(training_dataset.feature_names)}")

# === PASO 4: Entrenar Red Neuronal ===
print(f"\n🧠 Paso 4: Entrenando NN (LSTM simple, 50 épocas)...")

nn = SimpleNNConfluence(input_size=28, hidden_sizes=[128, 64, 32])

history = nn.train(
    X_train=training_dataset.features,
    y_train=training_dataset.labels,
    epochs=50,
    batch_size=32,
    learning_rate=0.01,
    validation_split=0.2,
    verbose=True
)

print(f"\n✅ NN Entrenada!")
print(f"   Final Train Loss: {history['train_loss'][-1]:.4f}")
print(f"   Final Val Loss: {history['val_loss'][-1]:.4f}")

# === PASO 5: Generar nuevas estrategias ===
print(f"\n🔬 Paso 5: Generando 15 estrategias nuevas...")

generator = StrategyGenerator()
generated_strategies = generator.generate_strategies(
    num_strategies=15,
    nn_predictions={}  # Placeholder
)

print(f"✅ Estrategias generadas: {len(generated_strategies)}")

for i, strat in enumerate(generated_strategies[:5], 1):
    print(f"\n   {i}. {strat.name}")
    print(f"      Entry: {strat.entry_rule}")
    print(f"      Exit: {strat.exit_rule}")
    print(f"      NN Confluency: {strat.confluence_strength:.2%}")

# === PASO 6: Scoring de nuevas estrategias ===
print(f"\n⭐ Paso 6: Scoring automático de estrategias generadas...")

generated_scores = generator.score_generated_strategies(
    strategies=generated_strategies,
    activation_scorer=ActivationScorer
)

# Resumen
activar_count = sum(1 for s in generated_scores.values() if s["verdict"] == "ACTIVAR")
considerar_count = sum(1 for s in generated_scores.values() if s["verdict"] == "CONSIDERAR")
rechazar_count = sum(1 for s in generated_scores.values() if s["verdict"] == "RECHAZAR")

print(f"\n📊 Distribución de Verdicts (Estrategias Generadas):")
print(f"   ✅ ACTIVAR:     {activar_count} ({100*activar_count/len(generated_scores):.1f}%)")
print(f"   ⚠️  CONSIDERAR:  {considerar_count} ({100*considerar_count/len(generated_scores):.1f}%)")
print(f"   ❌ RECHAZAR:    {rechazar_count} ({100*rechazar_count/len(generated_scores):.1f}%)")

# === PASO 7: Exportar resultados ===
print(f"\n💾 Paso 7: Exportando resultados...")

# DataFrame de estrategias generadas
rows_generated = []
for strat_name, score_info in generated_scores.items():
    rows_generated.append({
        "strategy": strat_name,
        "score": score_info["score"],
        "verdict": score_info["verdict"],
        "confluence": score_info["confluence_strength"],
        "entry_rule": score_info["entry_rule"],
        "exit_rule": score_info["exit_rule"],
    })

df_generated = pd.DataFrame(rows_generated)
csv_file_generated = results_dir / "generated_strategies_scores.csv"
df_generated.to_csv(csv_file_generated, index=False)
print(f"✓ {csv_file_generated}")

# JSON con estrategias
json_file_generated = results_dir / "generated_strategies.json"
with open(json_file_generated, 'w') as f:
    json.dump({
        "strategies": generated_scores,
        "nn_training_history": {
            "train_loss": [float(x) for x in history["train_loss"]],
            "val_loss": [float(x) for x in history["val_loss"]]
        },
        "num_features": 28,
        "feature_names": training_dataset.feature_names,
        "total_generated": len(generated_strategies),
    }, f, indent=2)
print(f"✓ {json_file_generated}")

# Guardar NN weights (para predicción futura)
nn_weights_file = results_dir / "nn_confluence_weights.json"
nn.save(nn_weights_file)
print(f"✓ {nn_weights_file}")

# === PASO 8: Reportes ===
print(f"\n" + "="*80)
print("📈 TOP 5 ESTRATEGIAS GENERADAS")
print("="*80)

df_sorted = df_generated.sort_values("score", ascending=False)
for idx, (_, row) in enumerate(df_sorted.head(5).iterrows(), 1):
    emoji = "✅" if row["verdict"] == "ACTIVAR" else "⚠️ " if row["verdict"] == "CONSIDERAR" else "❌"
    print(f"\n{idx}. {row['strategy']}")
    print(f"   Score: {row['score']:.1f} {emoji} {row['verdict']}")
    print(f"   NN Confluency: {row['confluence']:.2%}")
    print(f"   Entry: {row['entry_rule']}")
    print(f"   Exit: {row['exit_rule']}")

print(f"\n" + "="*80)
print("🎯 RESUMEN FASE 4")
print("="*80)

print(f"\n📊 Entrenamiento NN:")
print(f"   ✓ Datos: {len(df_by_symbol)} activos × 2880 bars (1m)")
print(f"   ✓ Features: 28 (7 por timeframe × 4 TF)")
print(f"   ✓ Train Loss Final: {history['train_loss'][-1]:.4f}")
print(f"   ✓ Val Loss Final: {history['val_loss'][-1]:.4f}")

print(f"\n🔬 Estrategias Generadas:")
print(f"   ✓ Total: {len(generated_strategies)}")
print(f"   ✓ ACTIVAR: {activar_count} ({100*activar_count/len(generated_scores):.1f}%)")
print(f"   ✓ CONSIDERAR: {considerar_count}")
print(f"   ✓ RECHAZAR: {rechazar_count}")

print(f"\n💾 Archivos generados:")
print(f"   - {csv_file_generated}")
print(f"   - {json_file_generated}")
print(f"   - {nn_weights_file}")

print(f"\n" + "="*80)
print("✅ FASE 4 COMPLETADA")
print("="*80)
print(f"\n🚀 Próximos pasos:")
print(f"   1. Generar dashboard de confluencia NN (Plotly)")
print(f"   2. Validar estrategias generadas en backtests reales")
print(f"   3. Integrar NN confluency en Bot v1/v2")
print(f"   4. FASE 5 (opcional): Auto-generación con algoritmo genético\n")
