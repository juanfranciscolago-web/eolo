#!/bin/bash
# ============================================================
#  EOLO — Setup de Auto-Deploy Diario
#
#  Ejecutar UNA SOLA VEZ desde tu terminal para configurar
#  el deploy automático de Cloud Build + Cloud Scheduler.
#
#  Después de correr este script, el bot y el dashboard
#  se van a redeployar automáticamente todos los días
#  hábiles a las 9:00 AM ET (sin acción humana).
#
#  Prerequisito: tener gcloud autenticado con permisos de Owner.
#  Uso: bash auto-deploy-setup.sh
# ============================================================

set -e   # detener si hay algún error

PROJECT_ID="eolo-schwab-agent"
REGION="us-central1"

echo ""
echo "🚀 Configurando auto-deploy diario para EOLO..."
echo "   Proyecto: $PROJECT_ID"
echo "   Región  : $REGION"
echo ""

# ── 1. Habilitar APIs necesarias ──────────────────────────
echo "📦 Habilitando APIs de GCP..."
gcloud services enable \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  run.googleapis.com \
  containerregistry.googleapis.com \
  --project=$PROJECT_ID

# ── 2. Obtener número de proyecto y service account ───────
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo ""
echo "🔑 Service account de Cloud Build: $CLOUDBUILD_SA"

# ── 3. Dar permisos a Cloud Build para deployar a Cloud Run
echo ""
echo "🔑 Otorgando permisos a Cloud Build..."

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/storage.admin"

echo "   ✅ Permisos otorgados"

# ── 4. Crear Cloud Build Triggers ─────────────────────────
# Nota: los triggers usan gcloud builds submit (no requieren GitHub conectado)
echo ""
echo "🏗  Creando Cloud Build triggers..."

# Trigger para el Bot
gcloud builds triggers create manual \
  --name="eolo-bot-daily-deploy" \
  --description="Deploy diario del bot EOLO a las 9:00 AM ET" \
  --build-config="Bot/cloudbuild.yaml" \
  --project=$PROJECT_ID \
  --region=$REGION \
  2>/dev/null || echo "   (Trigger bot ya existe, continuando...)"

# Obtener el trigger ID del bot
BOT_TRIGGER_ID=$(gcloud builds triggers list \
  --project=$PROJECT_ID \
  --region=$REGION \
  --filter="name=eolo-bot-daily-deploy" \
  --format="value(id)" 2>/dev/null)

# Trigger para el Dashboard
gcloud builds triggers create manual \
  --name="eolo-dashboard-daily-deploy" \
  --description="Deploy diario del dashboard EOLO a las 9:00 AM ET" \
  --build-config="Dashboard/cloudbuild.yaml" \
  --project=$PROJECT_ID \
  --region=$REGION \
  2>/dev/null || echo "   (Trigger dashboard ya existe, continuando...)"

DASHBOARD_TRIGGER_ID=$(gcloud builds triggers list \
  --project=$PROJECT_ID \
  --region=$REGION \
  --filter="name=eolo-dashboard-daily-deploy" \
  --format="value(id)" 2>/dev/null)

echo "   ✅ Bot trigger ID     : $BOT_TRIGGER_ID"
echo "   ✅ Dashboard trigger ID: $DASHBOARD_TRIGGER_ID"

# ── 5. Crear Service Account para Cloud Scheduler ─────────
echo ""
echo "🕒 Configurando Cloud Scheduler..."

SCHEDULER_SA="eolo-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create eolo-scheduler \
  --display-name="EOLO Scheduler" \
  --project=$PROJECT_ID \
  2>/dev/null || echo "   (Service account ya existe, continuando...)"

# Dar permiso para ejecutar Cloud Build triggers
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/cloudbuild.builds.editor"

echo "   ✅ Service account scheduler: $SCHEDULER_SA"

# ── 6. Crear jobs de Cloud Scheduler ──────────────────────
# 9:00 AM ET = 14:00 UTC
# Solo días hábiles: lunes a viernes (cron: Mon-Fri)
# Nota: Cloud Scheduler no sabe de feriados del mercado,
#       pero el bot verifica is_market_open() internamente.

echo ""
echo "📅 Creando jobs de Cloud Scheduler (9:00 AM ET = 14:00 UTC)..."

# Job para el Bot — todos los días hábiles a las 9:00 AM ET
gcloud scheduler jobs create http eolo-bot-deploy-daily \
  --location=$REGION \
  --schedule="0 14 * * 1-5" \
  --uri="https://cloudbuild.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/triggers/${BOT_TRIGGER_ID}:run" \
  --message-body='{"branchName":"main"}' \
  --oauth-service-account-email="$SCHEDULER_SA" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --description="Deploy diario eolo-bot — 9:00 AM ET (lunes a viernes)" \
  --time-zone="America/New_York" \
  --project=$PROJECT_ID \
  2>/dev/null || \
  gcloud scheduler jobs update http eolo-bot-deploy-daily \
    --location=$REGION \
    --schedule="0 9 * * 1-5" \
    --time-zone="America/New_York" \
    --project=$PROJECT_ID

# Job para el Dashboard — mismo horario
gcloud scheduler jobs create http eolo-dashboard-deploy-daily \
  --location=$REGION \
  --schedule="0 9 * * 1-5" \
  --uri="https://cloudbuild.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/triggers/${DASHBOARD_TRIGGER_ID}:run" \
  --message-body='{"branchName":"main"}' \
  --oauth-service-account-email="$SCHEDULER_SA" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --description="Deploy diario eolo-dashboard — 9:00 AM ET (lunes a viernes)" \
  --time-zone="America/New_York" \
  --project=$PROJECT_ID \
  2>/dev/null || \
  gcloud scheduler jobs update http eolo-dashboard-deploy-daily \
    --location=$REGION \
    --schedule="0 9 * * 1-5" \
    --time-zone="America/New_York" \
    --project=$PROJECT_ID

echo "   ✅ Jobs de scheduler creados"

# ── 7. Verificar todo ──────────────────────────────────────
echo ""
echo "🔍 Verificando configuración..."
echo ""
echo "Jobs activos en Cloud Scheduler:"
gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅ Auto-deploy configurado exitosamente!"
echo ""
echo "  📅 El bot y el dashboard se van a redeployar solos"
echo "     todos los días hábiles a las 9:00 AM ET."
echo ""
echo "  Para hacer un deploy manual ahora mismo:"
echo "    gcloud builds triggers run eolo-bot-daily-deploy \\"
echo "      --branch=main --region=$REGION --project=$PROJECT_ID"
echo ""
echo "  Para ver los logs del último build:"
echo "    gcloud builds list --project=$PROJECT_ID --limit=5"
echo ""
echo "  Para ver el estado de los jobs del scheduler:"
echo "    gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
echo "═══════════════════════════════════════════════════════"
