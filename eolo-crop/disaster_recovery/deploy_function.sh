#!/bin/bash
# Deploy auto_close.py como Cloud Function HTTPS independiente del bot.
# Sobrevive a crashes del eolo-bot-crop. Trigger manual o vía alert (Cloud Monitoring).
#
# Master Plan v2.1 sec 19 — Sub-C (TERMINATOR sprint).
set -e
cd "$(dirname "$0")"
gcloud functions deploy disaster-recovery-auto-close \
  --gen2 \
  --runtime=python311 \
  --region=us-east1 \
  --project=eolo-schwab-agent \
  --source=. \
  --entry-point=disaster_recovery_handler \
  --trigger-http \
  --no-allow-unauthenticated \
  --timeout=300s \
  --memory=512Mi \
  --service-account=eolo-scheduler@eolo-schwab-agent.iam.gserviceaccount.com \
  --set-env-vars=BOT_URL=https://eolo-bot-crop-nmjz4iwcea-ue.a.run.app,PAPER_TRADING_ONLY=true
