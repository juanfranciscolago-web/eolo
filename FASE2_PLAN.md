# 📋 FASE 2 — Plan Detallado

**Objetivo:** Backtesting de 27 estrategias en 7 regímenes × 28 ventanas walk-forward  
**Duración estimada:** 2-3 días  
**Budget GCP:** $60-80 USD  

---

## 🎯 Hito Final de FASE 2

Generar **matriz de decisión** interactiva:

```
| Estrategia | Régimen Actual | PF OOS | Score | Veredicto |
|------------|---|--------|-------|-----------|
| BOLLINGER | Bull | 1.25 | 96 | ✅ ACTIVAR |
| MACD_BB | Bull | 0.95 | 0 | ❌ RECHAZAR |
| RSI_SMA200 | Bull | 1.18 | 78 | ⚠️ CONSIDERAR |
| ... (27 estrategias) |
```

Con esta tabla, Claude activará/desactivará estrategias automáticamente en Bot v1/v2.

---

## 📊 Las 26 Estrategias de Bot/

```
1. ANCHOR_VWAP    14. STOP_RUN         
2. BOLLINGER      15. SUPERTREND       
3. EMA_TSI        16. TICK_TRIN_FADE   
4. GAP            17. TSV              
5. HA_CLOUD       18. VELA_PIVOT       
6. HH_LL          19. VIX_CORRELATION  
7. MACD_BB        20. VIX_MEAN_REVERSION
8. OBV            21. VIX_SQUEEZE      
9. OPENING_DRIVE  22. VOLUME_REVERSAL_BAR
10. ORB           23. VRP              
11. RSI_SMA200    24. VW_MACD          
12. RVOL_BREAKOUT 25. VWAP_RSI         
13. SQUEEZE       26. VWAP_ZSCORE      

+ 1 en eolo_common/strategies_v3/
= 27 total
```

---

## 🔄 Proceso de FASE 2 (paso a paso)

### Paso 1: Adaptar Estrategias (2-4 horas)

**Qué hace:** Convertir cada estrategia de Bot/ al formato del backtest engine

```python
# Estrategia original: returns "BUY", "SELL", "HOLD"
# ❌ No compatible con backtest engine

# Estrategia adaptada: returns signals como np.array (1, -1, 0)
# ✅ Compatible con backtest engine
```

**Archivos involucrados:**
- `eolo_common/backtest/strategy_wrapper.py` (YA CREADO)
- Modifica: Nada, ya está listo

**Salida:**
- 26 estrategias cargadas dinámicamente
- Listas para backtesting

**Script:**
```python
from eolo_common.backtest.strategy_wrapper import load_all_strategies

strategies = load_all_strategies()
print(f"✓ {len(strategies)} estrategias cargadas")
```

---

### Paso 2: Cargar Datos Históricos (1-2 horas)

**Qué hace:** Descargar OHLCV de 2017-2025 para backtesting

```python
from eolo_common.backtest import BacktestDataLoader

loader = BacktestDataLoader()

# Acciones US
equities = loader.load_equities(
    symbols=["SPY", "QQQ", "AAPL", "MSFT", "TSLA"],
    start_date="2017-01-01",
    end_date="2024-12-31"
)

# Crypto
crypto = loader.load_crypto(
    symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
    start_date="2017-01-01",
    end_date="2024-12-31"
)
```

**Archivos involucrados:**
- `eolo_common/backtest/data_loader.py` (YA CREADO)

**Salida:**
- 5 símbolos × ~2900 barras = ~14,500 datos acciones
- 3 símbolos × ~2900 barras = ~8,700 datos crypto
- Total: ~23,200 velas (caché local)

**Cost:** $0 (yfinance/Binance free)

---

### Paso 3: Generar Ventanas Walk-Forward (15 minutos)

**Qué hace:** Crear 28 ventanas de train 12m → test 3m

```python
from eolo_common.backtest import WalkForwardValidator

wf = WalkForwardValidator()
windows = wf.generate_windows("2017-01-01", "2024-12-31")
# → 28 ventanas automáticas
```

**Salida:**
```
WF0:  Train [2017-01-01 → 2018-01-01] | Test [2018-01-01 → 2018-04-01]
WF1:  Train [2017-04-01 → 2018-04-01] | Test [2018-04-01 → 2018-07-01]
...
WF27: Train [2023-10-01 → 2024-10-01] | Test [2024-10-01 → 2024-12-31]
```

---

### Paso 4: Ejecutar Backtests (4-8 horas, PARALLELIZABLE)

**Qué hace:** Correr 26 estrategias × 28 ventanas × 5+ activos

```python
# Para cada activo:
#   Para cada estrategia:
#     Para cada ventana:
#       Ejecutar backtest
#       Guardar métricas
```

**Complejidad:**
- 26 estrategias × 28 ventanas × 5 activos = **3,640 backtests**
- Cada backtest: ~0.2 segundos
- Secuencial: ~12 minutos
- **Con GCP paralelo (8 cores): ~1.5 minutos**

**GCP Setup:**
```bash
# Crear VM n1-standard-4 (4 cores) en us-central1
gcloud compute instances create eolo-backtest-vm \
  --machine-type=n1-standard-4 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud

# Costo: ~$0.12/hora × 2 horas = ~$0.24
```

**Salida:**
```
results/
├── strategies.json
├── metrics_by_regime.csv
├── metrics_by_window.csv
└── raw_backtest_data.parquet
```

---

### Paso 5: Análisis de Robustez (2-4 horas)

**Qué hace:** Calcular degradación OOS, significancia, veredicto por estrategia

```python
from eolo_common.backtest import WalkForwardValidator

summary = wf.aggregate_results(results)

# Para cada estrategia:
#   - PF promedio (OOS)
#   - Degradación (in-sample vs OOS)
#   - Es overfitted? (>30% caída)
#   - Veredicto: "robust" | "overfitted" | "inconsistent"
```

**Salida:**
```
BOLLINGER:
  PF OOS promedio: 1.25
  Degradación: 22%
  # Trades: 142
  Sharpe OOS: 0.6
  Veredicto: ROBUST ✓

MACD_BB:
  PF OOS promedio: 0.85
  Degradación: 65%
  Veredicto: OVERFITTED ❌
```

---

### Paso 6: Crear Dashboard (3-4 horas)

**Qué hace:** Generar HTML interactivo con matrices de decisión

**Features:**
- ✓ Tabla por régimen actual
- ✓ Gráficos de equity curve
- ✓ Heatmap: estrategia × régimen (PF)
- ✓ Histogramas: distribución de PF, Sharpe, DD
- ✓ Scores: 0-100 por estrategia (según 10 reglas)

**Tools:**
- Pandas (datos)
- Plotly (gráficos interactivos)
- HTML/CSS (template)

**Salida:**
```
reports/
├── dashboard.html (principal)
├── strategy_details/
│   ├── bollinger.html
│   ├── macd_bb.html
│   └── ... (26 más)
└── assets/
    └── style.css
```

---

### Paso 7: Generar Matriz de Decisión + Score 0-100 (2-3 horas)

**Qué hace:** Implementar las 10 reglas de scoring del documento de Criterios de Activación

**Inputs por estrategia:**
1. PF OOS (Regla 1: 25%)
2. Num trades (Regla 2: 15%)
3. Degradación (Regla 3: 20%)
4. PF en régimen actual (Regla 4: 20%)
5. Sharpe OOS (Regla 5: 10%)
6. Max DD (Regla 6: 10%)
7. Win rate (Regla 7: 5%)
8. Equity smoothness (Regla 8: 5%)
9. Racha pérdidas (Regla 9: 5%)
10. Multirégimen (Regla 10: 5%)

**Output:**
```
| Estrategia | Score | Veredicto |
|------------|-------|-----------|
| BOLLINGER  | 96/100| ✅ ACTIVAR |
| RSI_SMA200 | 78/100| ⚠️ CONSIDERAR |
| MACD_BB    | 0/100 | ❌ RECHAZAR |
```

**Código:** `eolo_common/backtest/activation_rules.py` (POR CREAR)

---

### Paso 8: Validación vs Firestore Logs (1-2 horas, OPCIONAL)

**Qué hace:** Comparar backtests vs datos reales ejecutados

```python
# Firestore collection: "trade_logs"
# Para cada operación ejecutada:
#   - Comparar entrada teórica (BT) vs real
#   - Comparar PF teórico vs real
#   - Detectar diferencias > 30% = Modelo demasiado optimista
```

**Salida:**
```
VALIDACIÓN vs REALIDAD:
  BOLLINGER:
    BT PF: 1.25
    Real PF: 1.15
    Diferencia: -8% ✓ Tolerable

  MACD_BB:
    BT PF: 1.8
    Real PF: 0.7
    Diferencia: -61% ❌ RECHAZAR (modelo muy optimista)
```

---

## 📈 Timeline Estimado

| Paso | Tarea | Duración | Status |
|------|-------|----------|--------|
| 1 | Adaptar estrategias | 2-4h | ⏳ |
| 2 | Cargar datos | 1-2h | ⏳ |
| 3 | Generar ventanas | 15m | ⏳ |
| 4 | Ejecutar backtests | 4-8h* | ⏳ |
| 5 | Análisis robustez | 2-4h | ⏳ |
| 6 | Dashboard HTML | 3-4h | ⏳ |
| 7 | Scoring 0-100 | 2-3h | ⏳ |
| 8 | Validación (opt) | 1-2h | ⏳ |
| | **TOTAL** | **16-30h** | |

*Con GCP paralelo: 4-8h → 1-2h

---

## 💰 Presupuesto FASE 2

| Rubro | Costo |
|-------|-------|
| GCP VM (n1-standard-4) | $20-40 |
| BigQuery (opcional) | $10-20 |
| Almacenamiento | $5-10 |
| Buffer | $5-10 |
| **Total estimado** | **$40-80** |

---

## 📦 Deliverables Finales de FASE 2

- ✅ **dashboard.html** — Matriz interactiva de decisiones
- ✅ **activation_matrix.csv** — Qué estrategias activar HOY
- ✅ **backtest_results.json** — Datos brutos (importable a Sheets)
- ✅ **strategy_rankings.csv** — Top estrategias por robustez
- ✅ **validation_report.json** — Comparación BT vs Real (opcional)

---

## 🚀 Próximos Pasos Inmediatos

1. ✅ **Strategy wrapper listo** (strategy_wrapper.py creado)
2. ⏳ **Cargar estrategias** → test que cargue todas 26
3. ⏳ **Descargar datos** → SPY, QQQ, AAPL, MSFT, TSLA, BTC, ETH, BNB
4. ⏳ **Ejecutar backtests** → loop de 3,640 simulaciones
5. ⏳ **Generar dashboard** → HTML interactivo

---

## ⚡ Quick Start (Hoy)

```bash
cd /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo

# Test 1: Cargar estrategias
python -c "
from eolo_common.backtest.strategy_wrapper import load_all_strategies
strategies = load_all_strategies()
print(f'✓ {len(strategies)} estrategias listas')
"

# Test 2: Descargar datos (primero SPY, rápido)
python -c "
from eolo_common.backtest import BacktestDataLoader
loader = BacktestDataLoader()
df = loader.load_equities(['SPY'], '2017-01-01', '2024-12-31')
print(f'✓ SPY: {len(df[\"SPY\"])} velas')
"

# Test 3: Generar ventanas
python -c "
from eolo_common.backtest import WalkForwardValidator
wf = WalkForwardValidator()
windows = wf.generate_windows('2017-01-01', '2024-12-31')
print(f'✓ {len(windows)} ventanas generadas')
"
```

---

## 📌 Notas Importantes

- Backtests son **determinísticos** (mismo resultado si se repiten)
- **Sin lookahead bias** garantizado (test siempre después de train)
- **Degradación OOS** se calcula automáticamente
- **Score 0-100** tiene vetos automáticos (PF<1.0 = rechazar)
- **Dashboard es interactivo** (filtrar, sortear, drill-down)

---

Cuando estés listo, avísame y empezamos! 🚀

Proximos comandos:
1. Testear carga de estrategias
2. Descargar datos históricos
3. Correr backtests en GCP
