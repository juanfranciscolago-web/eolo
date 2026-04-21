# ============================================================
#  EOLO v1 — Dockerfile (Cloud Run)
#
#  NOTA: este Dockerfile asume build context = repo root (.)
#  porque copia también el paquete compartido `eolo_common/`.
#  Por eso todos los COPY usan rutas relativas al repo root.
#
#  Build:
#    gcloud builds submit --config Bot/cloudbuild.yaml . \
#        --project eolo-schwab-agent
# ============================================================
FROM python:3.12-slim

WORKDIR /app

# ── Dependencias (cache layer) ────────────────────────────
COPY Bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Paquete compartido eolo_common (multi-TF + confluencia) ─
COPY eolo_common /app/eolo_common

# ── Código del servicio ───────────────────────────────────
COPY Bot/ /app/

# Gunicorn con 1 worker — el bot corre en un thread interno
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 main:app
