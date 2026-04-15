FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias primero (cache de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todos los archivos del bot
COPY . .

# Gunicorn con 1 worker — el bot corre en un thread interno
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 main:app
