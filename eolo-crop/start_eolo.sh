#!/bin/bash
# ============================================================
#  EOLO v2 — Script de arranque robusto
#
#  - Verifica credenciales y token
#  - Arranca el bot con auto-restart si se cae
#  - Refresca el token inicial antes de arrancar
#  - Loguea todo a eolo_bot.log
#
#  Uso:
#    chmod +x start_eolo.sh
#
#    Foreground (ver logs en vivo):
#      ./start_eolo.sh
#
#    Background (terminal libre):
#      nohup ./start_eolo.sh > eolo_bot.log 2>&1 &
#      tail -f eolo_bot.log   ← para ver logs
#
#    Detener background:
#      pkill -f eolo_v2_main.py
# ============================================================

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "████████████████████████████████████████████████████"
echo "  EOLO v2 — PAPER TRADING + AUTO RESTART"
echo "████████████████████████████████████████████████████"
echo ""

# ── 1. Variables de entorno ───────────────────────────────
# Cargar .env si existe (para que ANTHROPIC_API_KEY sea persistente por proyecto)
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
    echo -e "${GREEN}✅  .env cargado${NC}"
fi

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-eolo-schwab-agent}"
echo -e "${GREEN}✅  GCP Project: $GOOGLE_CLOUD_PROJECT${NC}"

# Validación de ANTHROPIC_API_KEY: aceptamos DOS fuentes:
#   1) env var ANTHROPIC_API_KEY (export o .env)
#   2) Secret Manager del project (secreto ANTHROPIC_API_KEY)
# El engine prefiere la env var y cae al Secret Manager como fallback.
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo -e "${GREEN}✅  ANTHROPIC_API_KEY OK (env var)${NC}"
else
    echo -e "${YELLOW}ℹ️   ANTHROPIC_API_KEY no está en env — intentando Secret Manager...${NC}"
    if gcloud secrets versions access latest \
          --secret=ANTHROPIC_API_KEY \
          --project="$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
        echo -e "${GREEN}✅  ANTHROPIC_API_KEY OK (Secret Manager)${NC}"
    else
        echo -e "${RED}❌  ANTHROPIC_API_KEY no disponible ni en env ni en Secret Manager.${NC}"
        echo "    Opción A (env var):      export ANTHROPIC_API_KEY=sk-ant-..."
        echo "    Opción B (Secret Mgr):   gcloud secrets create ANTHROPIC_API_KEY \\"
        echo "                               --project=$GOOGLE_CLOUD_PROJECT \\"
        echo "                               --replication-policy=automatic"
        echo "                             echo -n 'sk-ant-...' | gcloud secrets versions add \\"
        echo "                               ANTHROPIC_API_KEY --project=$GOOGLE_CLOUD_PROJECT --data-file=-"
        exit 1
    fi
fi

# ── 2. Dependencias ───────────────────────────────────────
echo -e "${YELLOW}📦 Verificando dependencias...${NC}"
pip install -r requirements.txt -q --break-system-packages 2>/dev/null || \
pip install -r requirements.txt -q
echo -e "${GREEN}✅  Dependencias OK${NC}"

# ── 3. Refresh inicial del token ──────────────────────────
echo ""
echo -e "${YELLOW}🔑 Refrescando token de Schwab...${NC}"
cd ..
python refresh_token_local.py
if [ $? -ne 0 ]; then
    echo -e "${RED}❌  No se pudo refrescar el token.${NC}"
    echo "    Si el refresh_token expiró, corré: python -c \"import init_auth; init_auth.main(None)\""
    exit 1
fi
cd eolo-options
echo -e "${GREEN}✅  Token OK${NC}"

# ── 4. Loop de arranque con auto-restart ──────────────────
RESTART_DELAY=10
ATTEMPT=0

echo ""
echo -e "${GREEN}🚀 Arrancando EOLO v2 — Paper Trading${NC}"
echo "   Auto-restart activado (delay: ${RESTART_DELAY}s)"
echo "   Logs: eolo_bot.log"
echo "   Detener: pkill -f eolo_v2_main.py"
echo ""

while true; do
    ATTEMPT=$((ATTEMPT + 1))
    START_TIME=$(date '+%Y-%m-%d %H:%M:%S')

    echo -e "${CYAN}[$(date '+%H:%M:%S')] ▶  Intento #${ATTEMPT} — Arrancando bot...${NC}"

    # Correr el bot
    python eolo_v2_main.py

    EXIT_CODE=$?
    END_TIME=$(date '+%Y-%m-%d %H:%M:%S')

    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${YELLOW}[$(date '+%H:%M:%S')] Bot terminó normalmente (Ctrl+C). Saliendo.${NC}"
        break
    fi

    echo -e "${RED}[$(date '+%H:%M:%S')] ⚠️  Bot cayó (exit code: $EXIT_CODE). Reiniciando en ${RESTART_DELAY}s...${NC}"
    echo "   Inicio: $START_TIME | Fin: $END_TIME"

    # Refrescar token antes de reintentar (puede haber expirado si estuvo corriendo mucho)
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] 🔑 Refrescando token antes de reiniciar...${NC}"
    cd ..
    python refresh_token_local.py 2>/dev/null && echo -e "${GREEN}   Token OK${NC}" || echo -e "${YELLOW}   Token refresh falló, continuando igual${NC}"
    cd eolo-options

    sleep $RESTART_DELAY
done
