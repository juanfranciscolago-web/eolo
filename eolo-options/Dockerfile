# ============================================================
#  EOLO v2 — Dockerfile para Cloud Run
#
#  IMPORTANTE: el build context debe ser la raíz `eolo/` (no
#  `eolo-options/`) porque necesitamos incluir también las
#  estrategias en ../Bot/ que importa eolo_v2_main.py.
#
#  Build manual:
#    cd /path/to/eolo  (raíz del repo)
#    docker build -f eolo-options/Dockerfile -t eolo-bot-v2 .
#
#  Deploy vía cloudbuild:
#    gcloud builds submit --config eolo-options/cloudbuild.yaml .
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ── Dependencias (cache Docker layer) ─────────────────────
COPY eolo-options/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ── Estrategias Eolo v1 desde Bot/ ────────────────────────
# eolo_v2_main.py importa bot_strategy, bot_gap_strategy, etc.
# Van en /app/Bot/ porque eolo_v2_main.py hace:
#   sys.path.insert(0, os.path.join(BASE_DIR, "..", "Bot"))
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
# ── Nivel 1 (trading_strategies_v2.md) ─────────────────────
COPY Bot/bot_rvol_breakout_strategy.py        /app/Bot/
COPY Bot/bot_stop_run_strategy.py             /app/Bot/
COPY Bot/bot_vwap_zscore_strategy.py          /app/Bot/
COPY Bot/bot_volume_reversal_bar_strategy.py  /app/Bot/
COPY Bot/bot_anchor_vwap_strategy.py          /app/Bot/
COPY Bot/bot_obv_strategy.py                  /app/Bot/
COPY Bot/bot_tsv_strategy.py                  /app/Bot/
COPY Bot/bot_vw_macd_strategy.py              /app/Bot/
COPY Bot/bot_opening_drive_strategy.py        /app/Bot/
# ── Nivel 2 (requieren macro feeds; HOLD silencioso si macro=None) ─
COPY Bot/bot_vix_mean_reversion_strategy.py   /app/Bot/
COPY Bot/bot_vix_correlation_strategy.py      /app/Bot/
COPY Bot/bot_vix_squeeze_strategy.py          /app/Bot/
COPY Bot/bot_tick_trin_fade_strategy.py       /app/Bot/
COPY Bot/bot_vrp_strategy.py                  /app/Bot/

# ── Paquete compartido eolo_common (multi-TF + confluencia) ─
COPY eolo_common /app/eolo_common

# ── Código v2 completo ────────────────────────────────────
COPY eolo-options/ /app/eolo-options/

# Limpieza: no incluir cache/logs/CSVs locales
RUN rm -rf /app/eolo-options/__pycache__ \
           /app/eolo-options/*/__pycache__ \
           /app/eolo-options/eolo_bot.log \
           /app/eolo-options/paper_trades_log.csv \
           /app/eolo-options/eolo_state.json || true

ENV PYTHONPATH=/app:/app/eolo-options:/app/Bot:/app/eolo_common

WORKDIR /app/eolo-options

# Cloud Run expone PORT (default 8080). gunicorn sirve Flask en `main:app`,
# y el watchdog dentro de main.py arranca el bot v2 en un thread separado.
#   --workers 1     : single worker (el bot mantiene estado en memoria)
#   --threads 4     : para poder atender /health durante trading
#   --timeout 0     : no matar al worker por inactividad HTTP (el bot vive horas)
#   --graceful-timeout 30 : margen para cerrar WebSocket al reiniciar
CMD exec gunicorn \
    --bind :$PORT \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    --graceful-timeout 30 \
    --access-logfile '-' \
    --error-logfile '-' \
    main:app
