#!/usr/bin/env python3
"""
generate_dashboard_nn.py — FASE 4: Dashboard Interactivo NN + Estrategias Generadas

Genera visualizaciones:
  1. Training Loss Curves (LSTM)
  2. Confluence Score Distribution
  3. Top Generated Strategies (Score)
  4. Feature Importance (Multi-TF)
  5. Tabla interactiva de estrategias
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
print("📊 FASE 4 - GENERANDO DASHBOARD NN + ESTRATEGIAS")
print("="*80)

# Cargar datos
print(f"\n📥 Cargando datos...")
results_dir = Path(__file__).parent.parent / "results"

with open(results_dir / "generated_strategies.json") as f:
    nn_data = json.load(f)

df_strategies = pd.read_csv(results_dir / "generated_strategies_scores.csv")

print(f"✅ {len(df_strategies)} estrategias cargadas")

# === CREAR FIGURAS ===
print(f"\n📊 Creando gráficos...")

# 1. Training Loss Curves
train_loss = nn_data["nn_training_history"]["train_loss"]
val_loss = nn_data["nn_training_history"]["val_loss"]
epochs = list(range(1, len(train_loss) + 1))

fig_loss = go.Figure()

fig_loss.add_trace(go.Scatter(
    x=epochs, y=train_loss,
    name="Train Loss",
    mode="lines",
    line=dict(color="blue", width=2)
))

fig_loss.add_trace(go.Scatter(
    x=epochs, y=val_loss,
    name="Validation Loss",
    mode="lines",
    line=dict(color="red", width=2, dash="dash")
))

fig_loss.update_layout(
    title="LSTM Training Progress (50 épocas)",
    xaxis_title="Epoch",
    yaxis_title="Loss (MSE)",
    hovermode="x unified",
    height=400,
    template="plotly_dark"
)

# 2. Confluence Score Distribution
fig_confluence = px.histogram(
    df_strategies,
    x="confluence",
    nbins=10,
    title="Distribución de NN Confluency Scores",
    labels={"confluence": "Confluency Score (0-1)", "count": "Estrategias"},
    color_discrete_sequence=["#4ade80"]
)
fig_confluence.update_layout(height=400, template="plotly_dark")

# 3. Top Estrategias (Score)
top_strats = df_strategies.nlargest(10, "score")

fig_top = px.bar(
    top_strats,
    x="strategy",
    y="score",
    color="verdict",
    title="Top 10 Estrategias Generadas (por Score)",
    labels={"score": "Score (0-100)", "strategy": "Estrategia"},
    color_discrete_map={
        "ACTIVAR": "#4ade80",
        "CONSIDERAR": "#fbbf24",
        "RECHAZAR": "#ef4444"
    }
)
fig_top.update_layout(height=400, xaxis_tickangle=-45, template="plotly_dark")

# 4. Score vs Confluence Scatter
fig_scatter = px.scatter(
    df_strategies,
    x="confluence",
    y="score",
    color="verdict",
    size="score",
    hover_data=["strategy", "entry_rule"],
    title="Score vs NN Confluency",
    labels={"confluence": "NN Confluency", "score": "Score (0-100)"},
    color_discrete_map={
        "ACTIVAR": "#4ade80",
        "CONSIDERAR": "#fbbf24",
        "RECHAZAR": "#ef4444"
    }
)
fig_scatter.update_layout(height=400, template="plotly_dark")

# 5. Verdict Distribution (Pie)
verdict_counts = df_strategies["verdict"].value_counts()
fig_verdict = px.pie(
    values=verdict_counts.values,
    names=verdict_counts.index,
    color=verdict_counts.index,
    title="Distribución de Verdicts (Estrategias Generadas)",
    color_discrete_map={
        "ACTIVAR": "#4ade80",
        "CONSIDERAR": "#fbbf24",
        "RECHAZAR": "#ef4444"
    }
)
fig_verdict.update_layout(height=400, template="plotly_dark")

# === CREAR HTML ===
print(f"\n🖥️  Construyendo HTML...")

html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Eolo FASE 4 - NN Confluency Dashboard</title>
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
            max-width: 1600px;
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
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            margin: 20px 0;
        }}
        .metric-card {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .metric-value {{
            font-size: 1.8em;
            font-weight: bold;
            color: #4ade80;
        }}
        .metric-label {{
            color: #999;
            margin-top: 8px;
            font-size: 0.85em;
        }}
        .chart {{
            background: rgba(255,255,255,0.02);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }}
        .chart-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
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
        .info-box {{
            background: rgba(74, 222, 128, 0.1);
            border-left: 4px solid #4ade80;
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🧠 Eolo FASE 4 - NN Confluency Dashboard</h1>
        <div class="subtitle">Red Neuronal Multi-TF + Estrategias Auto-generadas</div>

        <!-- MÉTRICAS CLAVE -->
        <div class="metrics">
            <div class="metric-card">
                <div class="metric-value">{len(df_strategies)}</div>
                <div class="metric-label">Estrategias Generadas</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: #4ade80;">{len(df_strategies[df_strategies['verdict'] == 'ACTIVAR'])}</div>
                <div class="metric-label">✅ ACTIVAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: #fbbf24;">{len(df_strategies[df_strategies['verdict'] == 'CONSIDERAR'])}</div>
                <div class="metric-label">⚠️ CONSIDERAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: #ef4444;">{len(df_strategies[df_strategies['verdict'] == 'RECHAZAR'])}</div>
                <div class="metric-label">❌ RECHAZAR</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{nn_data['num_features']}</div>
                <div class="metric-label">Features Multi-TF</div>
            </div>
        </div>

        <!-- INFO BOX -->
        <div class="info-box">
            <strong>📊 FASE 4 Completada:</strong>
            <br>Red Neuronal LSTM entrenada en {len(nn_data['feature_names'])} features (7 por timeframe × 4 TF).
            <br>Generadas {len(df_strategies)} estrategias usando confluencia NN + scoring FASE 3.
        </div>

        <!-- ENTRENAMIENTO NN -->
        <h2>🧠 Entrenamiento Red Neuronal</h2>
        <div class="chart">
            {fig_loss.to_html(include_plotlyjs=False, div_id="loss")}
        </div>

        <!-- ANÁLISIS ESTRATEGIAS GENERADAS -->
        <h2>🔬 Análisis Estrategias Generadas</h2>

        <div class="chart-row">
            <div class="chart">
                <h3>Confluence Score Distribution</h3>
                {fig_confluence.to_html(include_plotlyjs=False, div_id="confluence")}
            </div>
            <div class="chart">
                <h3>Verdicts Distribution</h3>
                {fig_verdict.to_html(include_plotlyjs=False, div_id="verdict")}
            </div>
        </div>

        <div class="chart">
            <h3>Score vs NN Confluency</h3>
            {fig_scatter.to_html(include_plotlyjs=False, div_id="scatter")}
        </div>

        <div class="chart">
            <h3>Top 10 Estrategias (por Score)</h3>
            {fig_top.to_html(include_plotlyjs=False, div_id="top")}
        </div>

        <!-- TABLA DETALLADA -->
        <h2>📋 Estrategias Generadas (Detalles)</h2>
        <table>
            <thead>
                <tr>
                    <th>Estrategia</th>
                    <th>Score</th>
                    <th>Veredicto</th>
                    <th>NN Confluency</th>
                    <th>Entry Rule</th>
                    <th>Exit Rule</th>
                </tr>
            </thead>
            <tbody>
"""

# Agregar filas de tabla
for _, row in df_strategies.nlargest(20, "score").iterrows():
    verdict_class = f"verdict-{row['verdict'].lower()}"
    html_content += f"""
                <tr>
                    <td><strong>{row['strategy']}</strong></td>
                    <td>{row['score']:.1f}</td>
                    <td><span class="{verdict_class}">{row['verdict']}</span></td>
                    <td>{row['confluence']:.1%}</td>
                    <td>{row['entry_rule'][:40]}...</td>
                    <td>{row['exit_rule'][:30]}...</td>
                </tr>
"""

html_content += """
            </tbody>
        </table>

        <div class="footer">
            <p>🧠 Dashboard generado por Eolo FASE 4 - NN Confluency</p>
            <p>Estrategias auto-generadas usando LSTM + scoring FASE 3</p>
            <p>Próximos pasos: Validación en backtests reales + Integración en Bot v1/v2</p>
        </div>
    </div>
</body>
</html>
"""

# Guardar HTML
html_file = results_dir / "dashboard_fase4_nn.html"
with open(html_file, 'w') as f:
    f.write(html_content)

print(f"✅ Dashboard guardado: {html_file}")

print(f"\n" + "="*80)
print("✅ DASHBOARD FASE 4 COMPLETADO")
print("="*80)
print(f"\n📊 Archivos generados:")
print(f"   - {html_file}")
print(f"\n🌐 Para ver el dashboard:")
print(f"   Abre en navegador: results/dashboard_fase4_nn.html\n")
