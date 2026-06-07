#!/bin/bash
# Deploy LLM Engine Service to Cloud Run
#
# Pre-requisitos:
# 1. gcloud CLI autenticado al proyecto
# 2. ANTHROPIC_API_KEY guardada en Secret Manager como "anthropic-api-key"
# 3. Habilitada Cloud Run API
#
# Usage: bash deploy.sh

set -e

PROJECT_ID="${GCP_PROJECT_ID:-eolo-schwab-agent}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="llm-engine-service"

echo "Deploying ${SERVICE_NAME} to ${PROJECT_ID}/${REGION}..."

gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 180s \
  --max-instances 3 \
  --min-instances 0 \
  --concurrency 10 \
  --set-env-vars="SHADOW_MODE=true,KB_PATH=/app/kb/EOLO_ThetaHarvest_v1.8.xlsx,PAPER_TRADING_ONLY=true,LLM_MODEL=claude-sonnet-4-5-20250929,LLM_MAX_TOKENS=4096,LLM_TEMPERATURE=0.3" \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest"

echo ""
echo "✅ Deploy complete"
echo ""
echo "To grant Eolo Crop access to this service, run:"
echo ""
echo "  gcloud run services add-iam-policy-binding ${SERVICE_NAME} \\"
echo "    --region=${REGION} \\"
echo "    --project=${PROJECT_ID} \\"
echo "    --member='serviceAccount:EOLO_CROP_SERVICE_ACCOUNT@${PROJECT_ID}.iam.gserviceaccount.com' \\"
echo "    --role='roles/run.invoker'"
echo ""
echo "Service URL:"
gcloud run services describe ${SERVICE_NAME} --region ${REGION} --project ${PROJECT_ID} --format='value(status.url)'
