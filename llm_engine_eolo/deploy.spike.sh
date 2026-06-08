#!/bin/bash
# Deploy spike test engine — shadow aislado para sprint TR-109
# IMPORTANTE: incluye --set-secrets para ANTHROPIC_API_KEY (igual que prod)
set -e
cd "$(dirname "$0")"

gcloud run deploy llm-engine-spike-test-service \
  --source . \
  --region us-central1 \
  --project eolo-schwab-agent \
  --no-allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --min-instances 1 --max-instances 3 \
  --timeout 120 \
  --concurrency 10 \
  --set-env-vars="SHADOW_MODE=true,KB_PATH=/app/kb/EOLO_ThetaHarvest_v1.9.xlsx,PAPER_TRADING_ONLY=true,LLM_MODEL=claude-sonnet-4-5-20250929,LLM_MAX_TOKENS=4096,LLM_TEMPERATURE=0.3" \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  2>&1 | tail -15

echo ""
echo "Service URL:"
gcloud run services describe llm-engine-spike-test-service --region us-central1 --project eolo-schwab-agent --format='value(status.url)'
