# ============================================================
#  EOLO Crypto — Dockerfile para Cloud Run
#
#  Build context = raíz `eolo/` (igual que v2) porque el bot
#  importa las 13 estrategias de Bot/*.py además del código
#  en eolo-crypto/.
#
#  Build manual:
#    cd /path/to/eolo
#    docker build -f eolo-crypto/Dockerfile -t eolo-bot-crypto .
#
#  Deploy vía cloudbuild:
#    gcloud builds submit --config eolo-crypto/cloudbuild.yaml .
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ── Dependencias (cache Docker layer) ─────────────────────
COPY eolo-crypto/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ── Estrategias Eolo v1 desde Bot/ ────────────────────────
# El StrategyRunner importa dinámicamente bot_*_strategy modules.
RUN mkdir -p /app/Bot
COPY Bot/bot_strategy.py          /app/Bot/
COPY Bot/bot_gap_strategy.py      /app/Bot/
COPY Bot/bot_vwap_rsi_strategy.py /app/Bot/
COPY Bot/bot_bollinger_strategy.py /app/Bot/
COPY Bot/bot_orb_strategy.py      /app/Bot/
COPY Bot/bot_rsi_sma200_strategy.py /app/Bot/
COPY Bot/bot_supertrend_strategy.py /app/Bot/
COPY Bot/bot_hh_ll_strategy.py    /app/Bot/
COPY Bot/bot_ha_cloud_strategy.py /app/Bot/
COPY Bot/bot_squeeze_strategy.py  /app/Bot/
COPY Bot/bot_macd_bb_strategy.py  /app/Bot/
COPY Bot/bot_ema_tsi_strategy.py  /app/Bot/
COPY Bot/bot_vela_pivot_strategy.py /app/Bot/
# ── Nivel 1 (trading_strategies_v2.md) — 7 aplicables a crypto ──
COPY Bot/bot_rvol_breakout_strategy.py        /app/Bot/
COPY Bot/bot_stop_run_strategy.py             /app/Bot/
COPY Bot/bot_vwap_zscore_strategy.py          /app/Bot/
COPY Bot/bot_volume_reversal_bar_strategy.py  /app/Bot/
COPY Bot/bot_obv_strategy.py                  /app/Bot/
COPY Bot/bot_tsv_strategy.py                  /app/Bot/
COPY Bot/bot_vw_macd_strategy.py              /app/Bot/

# ── Paquete compartido eolo_common (multi-TF + confluencia) ─
COPY eolo_common /app/eolo_common

# ── Código eolo-crypto completo ───────────────────────────
COPY eolo-crypto/ /app/eolo-crypto/

# Limpieza de archivos locales que no deberían viajar a prod
RUN rm -rf /app/eolo-crypto/__pycache__ \
           /app/eolo-crypto/*/__pycache__ \
           /app/eolo-crypto/paper_trades_crypto.csv \
           /app/eolo-crypto/*.log || true

ENV PYTHONPATH=/app:/app/eolo-crypto:/app/Bot:/app/eolo_common

WORKDIR /app/eolo-crypto

# gunicorn sirve Flask en `main:app`. El watchdog dentro de main.py
# arranca el bot crypto en un thread separado.
#   --workers 1           single worker (estado en memoria)
#   --threads 4           para atender /health durante trading
#   --timeout 0           no matar worker por inactividad HTTP
#   --graceful-timeout 30 margen para cerrar WS en SIGTERM
CMD exec gunicorn \
    --bind :$PORT \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    --graceful-timeout 30 \
    --access-logfile '-' \
    --error-logfile '-' \
    main:app
