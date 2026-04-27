# 🚀 FASE 7a - CLOUD EXECUTION GUIDE

**Status**: Ready for Cloud Execution  
**Date**: 2026-04-27  
**Target**: 180 Backtests (5 assets × 3 TF × 12 strategies)  
**Estimated Time**: 1-2 hours with GPU

---

## 📋 PRE-EXECUTION CHECKLIST

Antes de ejecutar FASE 7a en GCP, verifica:

- ✅ Proyecto GCP activo: `eolo-schwab-agent`
- ✅ Cloud Build API habilitada
- ✅ Container Registry habilitado
- ✅ Datos reales cargados en `/data/[ASSET]/1d/[ASSET]_1d.csv`
- ✅ Credenciales de GCP configuradas localmente
- ✅ `gcloud` CLI instalado y autenticado

---

## 🔧 SETUP INICIAL (UNA SOLA VEZ)

### 1. Autenticar con GCP

```bash
gcloud auth login
gcloud config set project eolo-schwab-agent
gcloud auth configure-docker
```

### 2. Crear bucket GCS para resultados

```bash
gsutil mb gs://eolo-backtests
gsutil versioning set on gs://eolo-backtests
```

**Salida esperada**:
```
Creating gs://eolo-backtests/
Creating gs://eolo-backtests/...
Versioning is enabled.
```

### 3. Verificar datos locales

```bash
# Listar activos disponibles
ls -la data/*/1d/

# Espera ver:
# data/SPY/1d/SPY_1d.csv
# data/QQQ/1d/QQQ_1d.csv
# data/AAPL/1d/AAPL_1d.csv
# data/MSFT/1d/MSFT_1d.csv
# data/TSLA/1d/TSLA_1d.csv
```

---

## 🚀 OPCIÓN 1: EJECUCIÓN LOCAL (Para Testing)

Si quieres validar el script antes de subirlo a GCP:

### Ejecutar backtests localmente

```bash
cd /Users/JUAN/PycharmProjects/eolo

# Run with 4 workers (adjust based on your CPU)
python run_backtests_fase7a.py --max-workers=4

# Con salida detallada:
python run_backtests_fase7a.py --max-workers=4 2>&1 | tee fase7a_local.log
```

**Salida esperada**:
```
════════════════════════════════════════════════════════════════════════════════
🚀 FASE 7a: MULTI-TIMEFRAME BACKTESTING - CLOUD EXECUTION
════════════════════════════════════════════════════════════════════════════════

⏰ Start Time: 2026-04-27T14:30:00.123456
📊 Configuration:
   Assets: SPY, QQQ, AAPL, MSFT, TSLA
   Timeframes: 30m, 1h, 4h
   Strategies: 12
   Total Backtests: 180
   Max Workers: 4

📥 Loading real data...
✅ SPY: 2513 bars loaded (2016-04-25 to 2026-04-22)
✅ QQQ: 2513 bars loaded
✅ AAPL: 2513 bars loaded
✅ MSFT: 2513 bars loaded
✅ TSLA: 2513 bars loaded

✅ Loaded 5/5 assets

🔄 Executing backtests...
⏳ Progress: 10/180 (5.6%)
⏳ Progress: 20/180 (11.1%)
...
✅ All backtests completed: 180 results

⏱️ Execution time: 45.3 seconds

📊 Analyzing results...
🏆 Winners (PF ≥ 1.2): 23/180

💾 Saving results...
💾 Full results saved: /Users/JUAN/PycharmProjects/eolo/data/fase7a_results/backtest_results_full.json
💾 Winners saved: /Users/JUAN/PycharmProjects/eolo/data/fase7a_results/backtest_winners.json
💾 Summary saved: /Users/JUAN/PycharmProjects/eolo/data/fase7a_results/backtest_summary.json

📊 BACKTESTING SUMMARY
════════════════════════════════════════════════════════════════════════════════
{
  "timestamp": "2026-04-27T14:30:45.789...",
  "phase": "FASE 7a",
  "total_backtests": 180,
  "winners": 23,
  "winner_percentage": "12.8%",
  "average_pf": 1.15,
  "max_pf": 3.82
}

🎯 NEXT STEPS
════════════════════════════════════════════════════════════════════════════════
1. Review winners: /Users/JUAN/PycharmProjects/eolo/data/fase7a_results/backtest_winners.json
2. Top performers (PF ≥ 1.2):
   1. SPY / 30m / Bot_BollingerRSI → PF: 3.82, WR: 72.5%, Trades: 47
   2. QQQ / 1h / Bot_MACD_Confluence → PF: 2.91, WR: 68.3%, Trades: 38
   ...
```

**Revisar resultados**:
```bash
# Ver resumen
cat data/fase7a_results/backtest_summary.json | jq .

# Ver ganadores
cat data/fase7a_results/backtest_winners.json | jq '.[0:5]'
```

---

## 🌐 OPCIÓN 2: EJECUCIÓN EN CLOUD (RECOMENDADO)

Esta es la opción que seleccionaste. GCP ejecutará los backtests en paralelo con GPU.

### PASO 1: Validar Cloud Build está habilitado

```bash
gcloud services list --enabled | grep cloudbuild

# Si no aparece, habilitar:
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com
```

### PASO 2: Construir imagen Docker

```bash
cd /Users/JUAN/PycharmProjects/eolo

# Build local para testing
docker build -f Dockerfile.fase7a -t eolo-fase7a:latest .

# Tag para Container Registry
docker tag eolo-fase7a:latest gcr.io/eolo-schwab-agent/eolo-fase7a:latest

# Push a GCR
docker push gcr.io/eolo-schwab-agent/eolo-fase7a:latest
```

**Salida esperada**:
```
Building...
[1/8] FROM python:3.11-slim...
...
[8/8] CMD ["python", "run_backtests_fase7a.py", ...
Successfully built eolo-fase7a:latest
Successfully tagged gcr.io/eolo-schwab-agent/eolo-fase7a:latest

Pushing to registry...
The push refers to repository [gcr.io/eolo-schwab-agent/eolo-fase7a]
...
v1: digest: sha256:abc123... size: 5678
```

### PASO 3: Ejecutar con Cloud Build

```bash
cd /Users/JUAN/PycharmProjects/eolo

# Submit build
gcloud builds submit \
  --config=cloudbuild-fase7a.yaml \
  --substitutions=_BUILD_NAME="fase7a-$(date +%Y%m%d-%H%M%S)"

# O simplemente:
gcloud builds submit
```

**Salida esperada**:
```
Creating temporary tarball archive of 58 file(s) totalling 2.3 MiB before compression.
Uploading tarball of [.] to [gs://eolo-schwab-agent_cloudbuild/...]
BUILD QUEUED
Created [projects/eolo-schwab-agent/builds/abc123xyz]
STEP 1/6: Build Docker image
STEP 2/6: Push to Container Registry
STEP 3/6: Deploy to Cloud Run
...
BUILD SUCCESS
```

### PASO 4: Monitorear ejecución

```bash
# Ver builds activos
gcloud builds list --limit=5

# Ver detalles de un build específico
gcloud builds log abc123xyz --stream

# Ver todos los logs en tiempo real
gcloud builds log --stream

# Esperar a que termine
gcloud builds log abc123xyz --stream | tail -20
```

### PASO 5: Descargar resultados

Después de que Cloud Build termine:

```bash
# Crear directorio local
mkdir -p fase7a_results

# Descargar desde GCS
gsutil -m cp -r gs://eolo-backtests/fase7a_* fase7a_results/

# Verificar
ls -la fase7a_results/
cat fase7a_results/backtest_summary.json | jq .
```

---

## 📊 INTERPRETAR RESULTADOS

### Archivos generados

```
data/fase7a_results/
├── backtest_results_full.json    # Todos los 180 resultados (completo)
├── backtest_winners.json          # Solo ganadores (PF ≥ 1.2)
└── backtest_summary.json          # Resumen ejecutivo
```

### Estructura de resultado individual

```json
{
  "asset": "SPY",
  "timeframe": "30m",
  "strategy": "Bot_BollingerRSI",
  "status": "PASS",
  "pf": 3.82,
  "wr": 0.725,
  "trades": 47,
  "timestamp": "2026-04-27T14:30:45.123"
}
```

**Campos**:
- `pf` (Profit Factor): Ganancia bruta / Pérdida bruta. **> 1.2 = ganador**
- `wr` (Win Rate): % de trades ganadores. **> 60% = bueno**
- `trades`: Número total de operaciones backtesteadas

### Analizar ganadores

```bash
# JSON query: Top 10 ganadores por PF
cat data/fase7a_results/backtest_winners.json | \
  jq 'sort_by(.pf) | reverse | .[0:10] | .[] | "\(.asset) / \(.timeframe) / \(.strategy) → PF: \(.pf)"'

# Salida esperada:
# SPY / 30m / Bot_BollingerRSI → PF: 3.82
# QQQ / 1h / Bot_MACD_Confluence → PF: 2.91
# AAPL / 4h / Bot_Momentum_Score → PF: 2.15
# ...
```

---

## 🎯 DECISIÓN: ¿ACTIVAR GANADORES?

Después de FASE 7a, tienes que decidir cuáles ganadores activar en bot_main.py.

### Criterio

| PF | WR | Decisión |
|----|----|----|
| ≥ 2.0 | > 65% | ✅ ACTIVAR inmediatamente |
| 1.5-2.0 | > 60% | ✅ ACTIVAR con monitor 48h |
| 1.2-1.5 | > 55% | ⚠️ ACTIVAR solo si 3+ trades |
| < 1.2 | - | ❌ RECHAZAR |

### Paso a paso

1. **Identificar ganadores** con PF ≥ 1.2
2. **Filtrar** por win rate > 55%
3. **Agrupar** por asset (max 3-5 estrategias por activo)
4. **Crear config** con asset-specific settings
5. **Wireados en** bot_main.py con toggles `enable_[asset]`
6. **Deploy** a Cloud Run con CloudBuild
7. **Monitor** 48 horas de trading real

---

## ❌ TROUBLESHOOTING

### ❌ Build falla con "permission denied"

```bash
# Solución: Otorgar permisos a Cloud Build
gcloud projects get-iam-policy eolo-schwab-agent \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/container.developer"

# Si no aparece, agregar:
gcloud projects add-iam-policy-binding eolo-schwab-agent \
  --member=serviceAccount:$(gcloud projects describe eolo-schwab-agent --format='value(projectNumber)')@cloudbuild.gserviceaccount.com \
  --role=roles/container.developer
```

### ❌ Backtests se detienen a mitad

```bash
# Revisar logs
gcloud builds log [BUILD_ID] --stream

# Buscar errores
gcloud logging read \
  'resource.type="cloud_build" AND resource.labels.build_id="[BUILD_ID]" AND severity="ERROR"' \
  --limit=20 --format=json | jq '.[] | .textPayload'
```

### ❌ Resultados no se guardan en GCS

```bash
# Verificar permisos bucket
gsutil iam ch \
  serviceAccount:$(gcloud projects describe eolo-schwab-agent --format='value(projectNumber)')@cloudbuild.gserviceaccount.com:roles/storage.objectCreator \
  gs://eolo-backtests

# Verificar bucket existe
gsutil ls gs://eolo-backtests/
```

---

## 📋 CHECKLIST PARA DESPUÉS DE EJECUCIÓN

Una vez que FASE 7a termine:

- [ ] ✅ Cloud Build build completó sin errores
- [ ] ✅ Resultados descargados de GCS
- [ ] ✅ Revisé top 10 ganadores por PF
- [ ] ✅ Filtré ganadores (PF ≥ 1.2, WR > 55%)
- [ ] ✅ Arupé por asset (máx 3-5 estrategias/activo)
- [ ] ✅ Actualicé bot_main.py con winners
- [ ] ✅ Preparé config para deploy
- [ ] ✅ Ejecuté: `gcloud builds submit --config cloudbuild-deploy.yaml`
- [ ] ✅ Monitoreo 48h de trading real
- [ ] ✅ Validé P&L real vs backtest baseline

---

## 🎯 TIMELINE ESPERADO

| Paso | Tiempo | Salida |
|------|--------|--------|
| 1. Build Docker | 3-5 min | Image pushed a GCR |
| 2. Cloud Build submit | 1-2 min | Build queued |
| 3. 180 backtests en paralelo | 1-2 hours (GPU) | Results JSON |
| 4. Download resultados | 1-2 min | Local phase7a_results/ |
| 5. Review + decisión | 30 min | List de winners |
| 6. Update bot_main.py | 15 min | Code updated |
| 7. Deploy v2 | 5 min | Service active |
| 8. Monitor 48h | 48 hours | P&L tracking |

**Total: ~51 hours (2 días) desde inicio a gate 1 decision**

---

## ✅ READY FOR EXECUTION

Tienes todo lo necesario. Ejecuta:

```bash
cd /Users/JUAN/PycharmProjects/eolo

# OPCIÓN 1 - LOCAL TESTING (RECOMENDADO PRIMERO)
python run_backtests_fase7a.py --max-workers=4

# OPCIÓN 2 - CLOUD EXECUTION
gcloud builds submit --config cloudbuild-fase7a.yaml
```

**¿Cuál prefieres?** Recomiendo probar local primero, luego lanzar a cloud.
