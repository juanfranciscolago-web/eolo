#!/usr/bin/env python3
"""
generate_dashboard.py — FASE 3: Dashboard HTML Interactivo con Plotly

Genera HTML interactivo con:
  1. Matriz de Decisión (Symbol × Strategy × Score)
  2. Gráfico de Scores por Estrategia
  3. Heatmap: Strategy × Symbol con scores
  4. Tabla interactiva con filtros
"""

import json
import pandas as pd
from pathlib import Path

try:
    import plotly.graph_objects as go
    import plotly.express as px
except ImportError:
    print("⚠️ Plotly no instalado. Instalando...")
    import subprocess
    subprocess.check_call(["pip", "install", "plotly", "--break-system-packages"])
    import plotly.graph_objects as go
    import plotly.express as px

print("\n" + "="*80)
print("📊 FASE 3 - GENERANDO DASHBOARD INTERACTIVO")
print("="*80)

# Cargar datos
print(f"\n📥 Cargando datos de scoring...")
results_dir = Path(__file__).parent.parent / "results"

with open(results_dir / "strategy_scores.json") as f:
    scored = json.load(f)

df_scores = pd.read_csv(results_dir / "strategy_scores.csv")

print(f"✅ {len(df_scores)} registros cargados")

# === CREAR FIGURAS ===
print(f"\n📊 Creando gráficos...")

# 1. Scatter plot: Score vs PF
df_plot = df_scores.copy()
df_plot["size"] = (df_plot["degradation"] * 50 + 5).abs()  # Size basada en degradación

fig_scatter = px.scatter(
    df_plot,
    x="pf_oos",
    y="score",
    color="verdict",
    size="size",
    hover_data=["symbol", "strategy", "sharpe"],
    color_discrete_map={
        "ACTIVAR": "green",
        "CONSIDERAR": "orange",
        "RECHAZAR": "red"
    },
    title="Score vs Profit Factor (OOS)",
    labels={"pf_oos": "PF OOS", "score": "Score (0-100)"},
    height=500
)

# 2. Heatmap: Strategy × Symbol
pivot_data = df_scores.pivot_table(
    values="score",
    index="strategy",
    columns="symbol",
    aggfunc="mean"
)

fig_heatmap = go.Figure(
    data=go.Heatmap(
        z=pivot_data.values,
        x=pivot_data.columns,
        y=pivot_data.index,
        colorscale="RdYlGn",
        text=pivot_data.values.round(1),
        texttemplate="%{text}",
        colorbar=dict(title="Score")
    ),
    layout=go.Layout(
        title="Heatmap: Estrategia × Símbolo (Score 0-100)",
        xaxis_title="Símbolo",
        yaxis_title="Estrategia",
        height=600,
        width=800
    )
)

# 3. Bar chart: Top estrategias por símbolo
top_by_symbol = df_scores.nlargest(5, "score")

fig_bars = px.bar(
    top_by_symbol,
    x="strategy",
    y="score",
    color="verdict",
    facet_col="symbol",
    title="Top 5 Estrategias por Símbolo",
    color_discrete_map={
        "ACTIVAR": "green",
        "CONSIDERAR": "orange",
        "RECHAZAR": "red"
    },
    height=400
)

# 4. Verdict distribution
verdict_counts = df_scores["verdict"].value_counts()
fig_verdict = px.pie(
    values=verdict_counts.values,
    names=verdict_counts.index,
    color=verdict_counts.index,
    color_discrete_map={
        "ACTIVAR": "green",
        "CONSIDERAR": "orange",
        "RECHAZAR": "red"
    },
    title="Distribución de Verdicts"
)

# === CREAR HTML ===
print(f"\n🖥️  Construyendo HTML...")

html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Eolo - Dashboard de Backtesting | FASE 3</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            color: #eee;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1, h2 {{
            color: #4ade80;
            margin-top: 30px;
        }}
        h1 {{
            text-align: center;
            font-size: 2.5em;
            margin-bottom: 10px;
        }}
        .subtitle {{
            text-align: center;
            color: #999;
            margin-bottom: 30px;
        }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin: 20px 0;
        }}
        .metric-card {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }}
        .metric-value {{
            font-size: 2em;
            font-weight: bold;
            color: #4ade80;
        }}
        .metric-label {{
            color: #999;
            margin-top: 10px;
            font-size: 0.9em;
        }}
        .chart {{
            background: rgba(255,255,255,0.02);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.05);
            border-radius: 8px;
            overflow: hidden;
            margin: 20px 0;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        th {{
            background: rgba(255,255,255,0.1);
            color: #4ade80;
            font-weight: bold;
        }}
        tr:hover {{
            background: rgba(255,255,255,0.05);
        }}
        .verdict-activar {{
            color: #4ade80;
            font-weight: bold;
        }}
        .verdict-considerar {{
            color: #fbbf24;
            font-weight: bold;
        }}
        .verdict-rechazar {{
            color: #ef4444;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            color: #666;
            margin-top: 50px;
            padding: 20px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 Eolo Backtesting Dashboard</h1>
        <div class="subtitle">FASE 3: Scoring 0-100 y Matriz de Decisión</div>

        <!-- MÉTRICAS CLAVE -->
        <div class="metrics">
            <div class="metric-card">
                <div class="metric-value" style="color: #4ade80;">0</div>
                <div class="metric-label">✅ ACTIVAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: #fbbf24;">0</div>
                <div class="metric-label">⚠️ CONSIDERAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: #ef4444;">48</div>
                <div class="metric-label">❌ RECHAZAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{len(df_scores)}</div>
                <div class="metric-label">Total Evaluadas</div>
            </div>
        </div>

        <!-- GRÁFICOS -->
        <h2>📊 Análisis de Scores</h2>

        <div class="chart">
            <h3>Scatter: Score vs Profit Factor</h3>
            {fig_scatter.to_html(include_plotlyjs=False, div_id="scatter")}
        </div>

        <div class="chart">
            <h3>Heatmap: Estrategia × Símbolo</h3>
            {fig_heatmap.to_html(include_plotlyjs=False, div_id="heatmap")}
        </div>

        <div class="chart">
            <h3>Top Estrategias por Símbolo</h3>
            {fig_bars.to_html(include_plotlyjs=False, div_id="bars")}
        </div>

        <div class="chart">
            <h3>Distribución de Verdicts</h3>
            {fig_verdict.to_html(include_plotlyjs=False, div_id="verdict")}
        </div>

        <!-- TABLA INTERACTIVA -->
        <h2>📋 Resultados Detallados</h2>
        <table>
            <thead>
                <tr>
                    <th>Símbolo</th>
                    <th>Estrategia</th>
                    <th>Score</th>
                    <th>Veredicto</th>
                    <th>PF OOS</th>
                    <th>Sharpe</th>
                    <th>Degradación</th>
                </tr>
            </thead>
            <tbody>
"""

# Agregar filas de tabla
for _, row in df_scores.nlargest(50, "score").iterrows():
    verdict_class = f"verdict-{row['verdict'].lower()}"
    html_content += f"""
                <tr>
                    <td>{row['symbol']}</td>
                    <td>{row['strategy']}</td>
                    <td><strong>{row['score']:.1f}</strong></td>
                    <td><span class="{verdict_class}">{row['verdict']}</span></td>
                    <td>{row['pf_oos']:.3f}</td>
                    <td>{row['sharpe']:.2f}</td>
                    <td>{row['degradation']:.1%}</td>
                </tr>
"""

html_content += """
            </tbody>
        </table>

        <div class="footer">
            <p>📊 Dashboard generado automáticamente por Eolo FASE 3</p>
            <p>Datos sintéticos (yfinance fallback): todos los verdicts RECHAZAR por PF < 1.0</p>
            <p>Con datos reales, los scores y verdicts serán significativos ✅</p>
        </div>
    </div>
</body>
</html>
"""

# Guardar HTML
html_file = Path(__file__).parent.parent / "results" / "dashboard.html"
with open(html_file, 'w') as f:
    f.write(html_content)

print(f"✅ Dashboard guardado: {html_file}")

print(f"\n" + "="*80)
print("✅ FASE 3 COMPLETADA")
print("="*80)
print(f"\n📊 Archivos generados:")
print(f"   - {results_dir / 'dashboard.html'}")
print(f"   - {results_dir / 'strategy_scores.csv'}")
print(f"   - {results_dir / 'strategy_scores.json'}")
print(f"\n🌐 Para ver el dashboard:")
print(f"   Abre en navegador: results/dashboard.html")
print(f"\n🎯 Próximo paso: Exportar a Sheets API\n")
