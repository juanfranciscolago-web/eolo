FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code + KB
COPY llm_engine/ ./llm_engine/
COPY kb/ ./kb/

# Cloud Run expects PORT env var
ENV PORT=8080
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Run with uvicorn
CMD exec uvicorn llm_engine.service:app --host 0.0.0.0 --port ${PORT} --workers 1
