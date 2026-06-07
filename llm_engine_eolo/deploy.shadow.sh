#!/bin/bash
set -e
cd "$(dirname "$0")"
gcloud run deploy llm-engine-shadow-service \
  --source . \
  --region us-central1 \
  --project eolo-schwab-agent \
  --no-allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --min-instances 1 --max-instances 5 \
  --timeout 120 \
  --concurrency 10 \
  --set-env-vars="SHADOW_MODE=true,KB_PATH=/app/kb/EOLO_ThetaHarvest_v1.8.xlsx,PAPER_TRADING_ONLY=true,LLM_MODEL=claude-sonnet-4-5-20250929,LLM_MAX_TOKENS=4096,LLM_TEMPERATURE=0.3" \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  2>&1 | tail -20
