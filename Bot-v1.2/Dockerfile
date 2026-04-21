# ============================================================
#  EOLO v1.2 — Dockerfile (Cloud Run)
#
#  NOTA: build context = repo root (.) para poder incluir el
#  paquete compartido eolo_common/ (multi-TF + confluencia).
#
#  Diffs vs v1:
#    - CMD usa python directo (no gunicorn) porque el bot es
#      async (asyncio + aiohttp). El health server escucha en $PORT.
#
#  Build:
#    gcloud builds submit --config Bot-v1.2/cloudbuild.yaml . \
#        --project eolo-schwab-agent
# ============================================================
FROM python:3.12-slim

WORKDIR /app

# Dependencias Python
COPY Bot-v1.2/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Paquete compartido eolo_common (multi-TF + confluencia)
COPY eolo_common /app/eolo_common

# Código del bot
COPY Bot-v1.2/ /app/

# Cloud Run le pasa $PORT — el bot lo lee en _health_server().
# No se usa gunicorn porque todo el runtime es asyncio.
CMD ["python", "-u", "bot_v12_main.py"]
