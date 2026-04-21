#!/bin/bash
# ============================================================
#  EOLO v2 — Arranque en Paper Trading Mode
#
#  Pre-requisitos:
#    1. gcloud auth application-default login
#       (o GOOGLE_APPLICATION_CREDENTIALS apuntando a service account JSON)
#    2. export ANTHROPIC_API_KEY=sk-ant-...
#    3. Schwab token fresco en Firestore
#       (la Cloud Function refresh_tokens lo renueva automáticamente)
#
#  Uso:
#    chmod +x run_paper.sh
#    ./run_paper.sh
#
#  Para ir LIVE:
#    Cambiar PAPER_TRADING = False en eolo_v2_main.py
# ============================================================

set -e
cd "$(dirname "$0")"

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "████████████████████████████████████████████████████"
echo "  EOLO v2 — PAPER TRADING MODE"
echo "████████████████████████████████████████████████████"

# ── 1. Verificar ANTHROPIC_API_KEY ────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}❌  ANTHROPIC_API_KEY no está seteada.${NC}"
    echo "    Corré: export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi
echo -e "${GREEN}✅  ANTHROPIC_API_KEY OK${NC}"

# ── 2. Verificar GCP auth ─────────────────────────────────
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo -e "${GREEN}✅  GCP credentials: $GOOGLE_APPLICATION_CREDENTIALS${NC}"
elif gcloud auth application-default print-access-token &>/dev/null; then
    echo -e "${GREEN}✅  GCP auth: Application Default Credentials${NC}"
else
    echo -e "${RED}❌  GCP credentials no encontradas.${NC}"
    echo "    Opción 1: gcloud auth application-default login"
    echo "    Opción 2: export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json"
    exit 1
fi

# ── 3. Verificar GCP project ──────────────────────────────
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-eolo-schwab-agent}"
echo -e "${GREEN}✅  GCP Project: $GOOGLE_CLOUD_PROJECT${NC}"

# ── 4. Instalar dependencias ──────────────────────────────
echo ""
echo -e "${YELLOW}📦 Instalando dependencias...${NC}"
pip install -r requirements.txt -q
echo -e "${GREEN}✅  Dependencias OK${NC}"

# ── 5. Verificar token de Schwab en Firestore ─────────────
echo ""
echo -e "${YELLOW}🔑 Verificando token de Schwab en Firestore...${NC}"
python -c "
import sys, os
sys.path.insert(0, '.')
try:
    from helpers import get_access_token
    token = get_access_token()
    if token:
        print(f'  ✅  Token OK: {token[:20]}...')
    else:
        print('  ❌  Token no encontrado en Firestore')
        sys.exit(1)
except Exception as e:
    print(f'  ❌  Error conectando a Firestore: {e}')
    sys.exit(1)
"

# ── 6. Arrancar el bot ────────────────────────────────────
echo ""
echo -e "${GREEN}🚀  Arrancando EOLO v2 en PAPER mode...${NC}"
echo "    Tickers  : SOXL, TSLL, SPY, QQQ"
echo "    Análisis : cada 60s por ticker"
echo "    Auto-close: 15:27 ET"
echo "    Log CSV  : paper_trades_log.csv"
echo ""
echo "    Ctrl+C para detener."
echo ""

python eolo_v2_main.py
