# ============================================================
#  EOLO — Sheets Sync — Dockerfile
#
#  Build manual (context = raíz del repo eolo/):
#    cd /path/to/eolo
#    docker build -f eolo-sheets-sync/Dockerfile -t eolo-sheets-sync .
#
#  Deploy vía cloudbuild:
#    gcloud builds submit --config eolo-sheets-sync/cloudbuild.yaml .
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY eolo-sheets-sync/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY eolo-sheets-sync/ /app/

# gunicorn sirve Flask en `main:app`. No hace falta watchdog ni estado en memoria:
# el servicio es stateless (todo vive en Firestore/Sheets).
CMD exec gunicorn \
    --bind :$PORT \
    --workers 1 \
    --threads 4 \
    --timeout 300 \
    --access-logfile '-' \
    --error-logfile '-' \
    main:app
