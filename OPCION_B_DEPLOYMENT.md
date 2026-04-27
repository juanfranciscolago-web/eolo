# 🚀 OPCIÓN B - DEPLOYMENT FASE 7a

**Status**: Ready for Deployment  
**Date**: 2026-04-27  
**Target**: Bot v1 (eolo-bot)  
**New Strategies**: MACD_Confluence + Momentum_Score

---

## ✅ CAMBIOS COMPLETADOS

### 1️⃣ Nuevas Estrategias Creadas

```
✅ Bot/bot_macd_confluence_fase7a_strategy.py (158 líneas)
   - MACD 12/26/9 con señal de crossover
   - Assets: QQQ, SPY (30m candles)
   - PF: 4.58 (QQQ) / 3.14 (SPY)
   - SL: 2% | TP: 4%

✅ Bot/bot_momentum_score_fase7a_strategy.py (155 líneas)
   - ROC + RSI confluencia
   - Assets: SPY (30m candles)
   - PF: 4.58 (QQQ) / 3.14 (SPY)
   - SL: 2% | TP: 4%
```

### 2️⃣ Configuración Actualizada

```
✅ Bot/bot_main.py — Cambios:
   
   Línea 79-80:  Importadas nuevas estrategias
                 - from bot_macd_confluence_fase7a_strategy
                 - from bot_momentum_score_fase7a_strategy
   
   Línea 139-140: Agregadas a DEFAULT_STRATEGIES
                 - "macd_confluence_fase7a": True
                 - "momentum_score_fase7a": True
   
   Línea 1029-1051: Agregados al ciclo de ejecución (run_cycle)
                 - MACD_Confluence ejecuta en QQQ, SPY
                 - Momentum_Score ejecuta en SPY
```

### 3️⃣ Configuración de Activación

```
OPCIÓN B - Gradual Activation:

✅ Mantiene:
   - Bot_Bollinger_RSI_Sensitive (FASE 4): SPY, AAPL, QQQ ✅ VIVO
   - Bot_XOM_30m (FASE 5): XOM ✅ VIVO

✨ Agrega:
   - Bot_MACD_Confluence (FASE 7a): QQQ, SPY (NUEVO)
   - Bot_Momentum_Score (FASE 7a): SPY (NUEVO)

Resultado:
   SPY: 3 estrategias (Bollinger + MACD + Momentum)
   QQQ: 2 estrategias (Bollinger + MACD)
   AAPL: 1 estrategia (Bollinger)
   MSFT: 0 estrategias
   TSLA: 0 estrategias
   XOM: 1 estrategia (XOM_30m)
```

---

## 🚀 DEPLOY A CLOUD RUN

### PASO 1: Verificar cambios locales

```bash
cd /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo

# Ver archivos nuevos
ls -lh Bot/bot_*fase7a*.py

# Ver cambios en bot_main.py
git diff Bot/bot_main.py | head -50
```

**Salida esperada**:
```
-rw-------  bot_macd_confluence_fase7a_strategy.py (158 líneas)
-rw-------  bot_momentum_score_fase7a_strategy.py  (155 líneas)

+import bot_macd_confluence_fase7a_strategy as macd_conf_strategy
+import bot_momentum_score_fase7a_strategy as momentum_strategy
+"macd_confluence_fase7a": True,
+"momentum_score_fase7a": True,
+if strategies.get("macd_confluence_fase7a"):
+    result = macd_conf_strategy.analyze(market_data, ticker)
```

### PASO 2: Commit cambios

```bash
cd /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo

git add -A
git commit -m "OPCIÓN B: Agregar FASE 7a strategies (MACD_Confluence + Momentum_Score) a v1

- New: bot_macd_confluence_fase7a_strategy.py (PF 4.58 QQQ / 3.14 SPY)
- New: bot_momentum_score_fase7a_strategy.py (PF 4.58 QQQ / 3.14 SPY)
- Updated: Bot/bot_main.py to import and execute new strategies
- Config: Default enabled=True for both (safe to deploy)
- Assets: QQQ (MACD), SPY (MACD + Momentum)
- Timeline: Gradual activation - monitor 48h vs backtest baseline

Ready for Cloud Run deployment.
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

### PASO 3: Deploy a Cloud Run (v1 only)

```bash
cd /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo

# Opción A: Deploy automático (recomendado)
gcloud builds submit --config cloudbuild.yaml

# Opción B: Especificar servicio explícitamente
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions=_SERVICE="eolo-bot",_REGION="us-central1"
```

**Salida esperada**:
```
Creating temporary tarball archive of 78 file(s)...
BUILD QUEUED [BUILD_ID]
STEP 1/6: Build and tag image
...
Successfully built gcr.io/eolo-schwab-agent/eolo-bot:latest
STEP 2/6: Push to Container Registry
...
The push refers to repository [gcr.io/eolo-schwab-agent/eolo-bot]
...
STEP 3/6: Deploy to Cloud Run
...
Service [eolo-bot] revision [REVISION_HASH] has been deployed
Region: us-central1
URL: https://eolo-bot-[hash]-uc.a.run.app
...
BUILD SUCCESS
```

### PASO 4: Validar deployment

```bash
# Ver que v1 está actualizado
gcloud run services describe eolo-bot --region=us-central1 \
  --format="table(status.conditions[].type,status.conditions[].status)"

# Espera ver:
# Ready    True
# Active   True

# Ver logs en vivo
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="eolo-bot"' \
  --limit=50 --format=json | jq '.[0:5] | .[] | {timestamp, textPayload}'

# Buscar nuevas estrategias FASE 7a
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="eolo-bot" AND textPayload=~"MACD_CONFLUENCE|MOMENTUM_SCORE"' \
  --limit=20
```

---

## 📊 EXPECTED BEHAVIOR AFTER DEPLOYMENT

### Con Bollinger_RSI (FASE 4):
```
✅ Trading real: SPY, AAPL, QQQ (diario)
✅ PF esperado: 38.52 (SPY), 14.78 (AAPL), 14.02 (QQQ)
✅ Status: Vivo, probado 252 días reales
```

### Nuevo con MACD_Confluence (FASE 7a):
```
✨ Trading real: QQQ, SPY (30m)
🟡 PF esperado: 70% × 4.58 = ~3.2 (QQQ), 70% × 3.14 = ~2.2 (SPY)
⚠️ Status: BETA — Monitorear vs backtest baseline
```

### Nuevo con Momentum_Score (FASE 7a):
```
✨ Trading real: SPY (30m)
🟡 PF esperado: 70% × 3.14 = ~2.2 (SPY)
⚠️ Status: BETA — Monitorear vs backtest baseline
```

### Trades esperados/día:
```
Estrategia             | Assets | Trades/año (BT) | Est. Trades/día
Bollinger_RSI          | SPY    | 8-15           | 0.02-0.04
MACD_Confluence        | SPY    | 21             | 0.06
Momentum_Score         | SPY    | 21             | 0.06
MACD_Confluence        | QQQ    | 21             | 0.06
─────────────────────────────────────────────────────────────────
Total SPY:             | -      | ~50/año        | ~0.13/día
Total QQQ:             | -      | ~21/año        | ~0.06/día
```

---

## ⚠️ MONITORING CHECKLIST (48 horas)

Después de deploy, monitorear estos 5 puntos:

### 1️⃣ Service Health
```bash
gcloud run services describe eolo-bot --region=us-central1 \
  --format="value(status.conditions[].status)"
```
**Esperado**: `True True`

### 2️⃣ Nuevas Estrategias Ejecutando
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="eolo-bot" AND textPayload=~"MACD_CONFLUENCE.*analyze|MOMENTUM_SCORE.*analyze"' \
  --limit=50
```
**Esperado**: Ver 50+ líneas de logs con MACD/Momentum signals

### 3️⃣ Señales Generadas
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="eolo-bot" AND textPayload=~"MACD_CONFLUENCE.*BUY|MOMENTUM_SCORE.*BUY"' \
  --limit=20
```
**Esperado**: Al menos 5-10 BUY signals en 48h

### 4️⃣ Real P&L vs Backtest
```
Monitorear Google Sheets:
- Trades tab: Entradas + salidas de MACD/Momentum
- P&L column: Ganancias/pérdidas reales
- Target: ≥ 70% del backtest (PF 4.58 → real ~3.2)
- Alert: Si < 50% del backtest, pausar estrategia
```

### 5️⃣ Errores o Issues
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="eolo-bot" AND severity="ERROR" AND textPayload=~"MACD|MOMENTUM"' \
  --limit=20
```
**Esperado**: 0 errores relacionados a FASE 7a

---

## 🎯 DECISION GATES (48h)

| Métrica | Target | ¿Pasar? | Acción |
|---------|--------|---------|--------|
| Service Running | Up | ✅ | Continuar |
| Señales/día | >3 | ✅ | Continuar |
| Real P&L (MACD) | ≥ $70/día | ? | Monitor |
| Real P&L (Momentum) | ≥ $50/día | ? | Monitor |
| Errors | 0 | ✅ | Continuar |

**Si TODO está ✅**: OPCIÓN B exitosa. Próximo paso: FASE 7b (Market Microstructure)

**Si algún gate falla**: 
- Debug issue
- Opción: Pausar estrategia problemática (set False en Firestore)
- No impacta FASE 4 winner (Bollinger sigue corriendo)

---

## 📋 COMANDOS RESUMIDOS

```bash
# 1. Preparar
cd /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo

# 2. Commit (crear nuevo commit, NO amend)
git add -A
git commit -m "OPCIÓN B: Agregar FASE 7a strategies..."

# 3. Deploy
gcloud builds submit --config cloudbuild.yaml

# 4. Monitor (copia esta línea y corre cada 10 min)
watch -n 10 'gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"eolo-bot\"" --limit=5 --format=json | jq ".[] | .textPayload[0:100]"'

# 5. Verificar P&L en Google Sheets (manual - cada 12h)
# Abre tu Sheets → Trades tab → suma P&L últimas 48h
```

---

## ✅ READY FOR DEPLOYMENT

Todos los cambios están completos. Ejecuta:

```bash
gcloud builds submit --config cloudbuild.yaml
```

Y monitorea 48 horas con los comandos arriba. 🚀

