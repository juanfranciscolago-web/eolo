#!/usr/bin/env python3
"""
run_phase3_scoring.py — FASE 3: Aplicar Scoring 0-100 a Resultados de FASE 2

Toma los resultados JSON de FASE 2 y genera:
  1. CSV con scores 0-100
  2. JSON con verdicts (✅ ACTIVAR / ⚠️ CONSIDERAR / ❌ RECHAZAR)
  3. Reporte HTML interactivo
"""

import sys
import json
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.activation_rules import ActivationScorer, score_all_strategies

print("\n" + "="*80)
print("🎯 FASE 3 - SCORING 0-100 Y MATRIZ DE DECISIÓN")
print("="*80)

# Cargar resultados de FASE 2
print(f"\n📥 Cargando resultados de FASE 2...")
results_file = Path(__file__).parent.parent / "results" / "backtest_results_fast.json"

if not results_file.exists():
    print(f"❌ No encontrado: {results_file}")
    sys.exit(1)

with open(results_file) as f:
    summary = json.load(f)

print(f"✅ {len(summary)} activos cargados")

# Aplicar scoring
print(f"\n🎯 Aplicando scoring (10 reglas)...")
scored = score_all_strategies(summary)

# Génerar reportes
print(f"\n💾 Generando reportes...")
output_dir = Path(__file__).parent.parent / "results"
output_dir.mkdir(exist_ok=True)

# === CSV CON SCORES ===
rows = []
for symbol in scored:
    for strategy in scored[symbol]:
        m = scored[symbol][strategy]
        rows.append({
            "symbol": symbol,
            "strategy": strategy,
            "score": m["score"],
            "verdict": m["verdict"],
            "pf_oos": m["pf_test_mean"],
            "sharpe": m["sharpe_test_mean"],
            "degradation": m["avg_degradation"],
            "num_windows": m["num_windows"],
        })

df_scores = pd.DataFrame(rows)
csv_file = output_dir / "strategy_scores.csv"
df_scores.to_csv(csv_file, index=False)
print(f"✓ {csv_file}")

# === JSON CON SCORES COMPLETOS ===
json_file = output_dir / "strategy_scores.json"
with open(json_file, 'w') as f:
    json.dump(scored, f, indent=2)
print(f"✓ {json_file}")

# === MATRIZ DE DECISIÓN (POR ACTIVO) ===
print(f"\n" + "="*80)
print("📊 MATRIZ DE DECISIÓN")
print("="*80)

for symbol in sorted(scored.keys()):
    print(f"\n{symbol}:")
    print(f"{'Estrategia':<20} {'Score':>7} {'Veredicto':<12} {'PF':>8} {'Sharpe':>8}")
    print("-" * 70)

    strats_sorted = sorted(
        scored[symbol].items(),
        key=lambda x: x[1]["score"],
        reverse=True
    )

    for strat_name, metrics in strats_sorted:
        pf = metrics.get('pf_test_mean', metrics.get('pf_oos', 0))
        sharpe = metrics.get('sharpe_test_mean', metrics.get('sharpe', 0))
        print(f"{strat_name:<20} {metrics['score']:>6.1f} {metrics['verdict']:<12} {pf:>8.2f} {sharpe:>8.2f}")

# === RESUMEN EJECUTIVO ===
print(f"\n" + "="*80)
print("📈 RESUMEN EJECUTIVO")
print("="*80)

# Contar verdicts
verdicts_count = {"ACTIVAR": 0, "CONSIDERAR": 0, "RECHAZAR": 0}
for symbol in scored:
    for strat in scored[symbol]:
        verdict = scored[symbol][strat]["verdict"]
        verdicts_count[verdict] += 1

total = sum(verdicts_count.values())

print(f"\n✅ ACTIVAR       {verdicts_count['ACTIVAR']:3d} ({100*verdicts_count['ACTIVAR']/total:.1f}%)")
print(f"⚠️  CONSIDERAR  {verdicts_count['CONSIDERAR']:3d} ({100*verdicts_count['CONSIDERAR']/total:.1f}%)")
print(f"❌ RECHAZAR     {verdicts_count['RECHAZAR']:3d} ({100*verdicts_count['RECHAZAR']/total:.1f}%)")

# Top 5 estrategias
print(f"\n🏆 TOP 5 ESTRATEGIAS (Score más alto):")
all_strats = []
for symbol in scored:
    for strat, metrics in scored[symbol].items():
        all_strats.append((f"{strat}({symbol})", metrics["score"], metrics["verdict"]))

all_strats.sort(key=lambda x: x[1], reverse=True)
for i, (name, score, verdict) in enumerate(all_strats[:5], 1):
    emoji = "✅" if verdict == "ACTIVAR" else "⚠️ " if verdict == "CONSIDERAR" else "❌"
    print(f"  {i}. {name:<25} Score={score:5.1f} {emoji} {verdict}")

# Peores 5
print(f"\n📉 PEORES 5 ESTRATEGIAS (Score más bajo):")
for i, (name, score, verdict) in enumerate(all_strats[-5:], 1):
    emoji = "✅" if verdict == "ACTIVAR" else "⚠️ " if verdict == "CONSIDERAR" else "❌"
    print(f"  {i}. {name:<25} Score={score:5.1f} {emoji} {verdict}")

# === RECOMENDACIONES ===
print(f"\n" + "="*80)
print("💡 RECOMENDACIONES")
print("="*80)

activar_list = []
for symbol in scored:
    for strat, metrics in scored[symbol].items():
        if metrics["verdict"] == "ACTIVAR":
            activar_list.append((symbol, strat, metrics["score"]))

if activar_list:
    print(f"\n✅ ESTRATEGIAS RECOMENDADAS PARA ACTIVAR HOY:")
    for symbol, strat, score in sorted(activar_list, key=lambda x: x[2], reverse=True):
        print(f"   - {strat} en {symbol} (Score: {score:.1f})")
else:
    print(f"\n⚠️  No hay estrategias con score >= 80 (recomendado: CONSIDERAR las de score >= 60)")

    considerar_list = []
    for symbol in scored:
        for strat, metrics in scored[symbol].items():
            if metrics["verdict"] == "CONSIDERAR":
                considerar_list.append((symbol, strat, metrics["score"]))

    if considerar_list:
        print(f"\n   Candidatas a CONSIDERAR:")
        for symbol, strat, score in sorted(considerar_list, key=lambda x: x[2], reverse=True)[:5]:
            print(f"   - {strat} en {symbol} (Score: {score:.1f})")

print(f"\n" + "="*80)
print("✅ FASE 3 (Scoring) COMPLETADA")
print("="*80)
print(f"\n📊 Archivos generados:")
print(f"   - {csv_file}")
print(f"   - {json_file}")
print(f"\n🎯 Próximos pasos:")
print(f"   1. Generar dashboard HTML interactivo (Plotly)")
print(f"   2. Exportar matriz de decisión a Sheets API")
print(f"   3. Integrar verdicts en Bot v1/v2\n")
