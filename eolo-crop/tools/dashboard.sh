#!/bin/bash
# Open Eolo Crop LLM Audit dashboard en navegador local.
# Usa gcloud run services proxy para auth transparente.
#
# Endpoints disponibles vía el proxy:
#   /audit         — HTML dashboard (server-side rendered, focus LLM audit)
#   /audit.json    — mismo payload en JSON
#   /dashboard     — UI legacy trading (estática, GitHub-dark)
set -e
PORT=${PORT:-8089}
echo "→ Starting Cloud Run proxy en puerto $PORT..."
echo "→ Una vez listo:"
echo "    http://localhost:$PORT/audit         (LLM audit, nuevo)"
echo "    http://localhost:$PORT/dashboard     (trading, legacy)"
echo "→ Ctrl-C para cerrar"
sleep 2
( sleep 4 && open "http://localhost:$PORT/audit" 2>/dev/null || true ) &
exec gcloud run services proxy eolo-bot-crop \
  --region=us-east1 \
  --project=eolo-schwab-agent \
  --port=$PORT
