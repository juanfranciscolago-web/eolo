#!/usr/bin/env bash
# ============================================================
#  EOLO v1.2 — Launcher local
#
#  Uso:
#    ./start_eolo_v12.sh           # arranca en foreground
#    ./start_eolo_v12.sh --bg      # arranca en background (logs a archivo)
#    ./start_eolo_v12.sh --stop    # mata el proceso background
#
#  Este script NO toca la v1. Corre en un puerto local distinto
#  para que no choque con el dashboard v1.
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-eolo-schwab-agent}"
export PORT="${PORT:-8082}"        # 8080=v1 local, 8081=v2 options, 8082=v1.2

LOG_FILE="eolo_v12.log"
PID_FILE="eolo_v12.pid"

case "${1:-}" in
  --stop)
    if [[ -f "$PID_FILE" ]]; then
      PID=$(cat "$PID_FILE")
      if kill -0 "$PID" 2>/dev/null; then
        echo "Deteniendo eolo-v12 (pid=$PID)..."
        kill "$PID"
        sleep 2
        kill -9 "$PID" 2>/dev/null || true
      fi
      rm -f "$PID_FILE"
      echo "Detenido."
    else
      echo "No hay PID file ($PID_FILE). Nada que detener."
    fi
    exit 0
    ;;

  --bg)
    echo "Arrancando eolo-v12 en background (puerto $PORT)..."
    nohup python -u bot_v12_main.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "PID=$(cat $PID_FILE), logs → $LOG_FILE"
    exit 0
    ;;

  *)
    echo "Arrancando eolo-v12 en foreground (puerto $PORT)..."
    echo "Firestore namespace: eolo-config-v12"
    echo "Ctrl+C para detener."
    exec python -u bot_v12_main.py
    ;;
esac
