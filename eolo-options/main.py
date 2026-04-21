# ============================================================
#  EOLO v2 — Cloud Run entry point
#
#  Flask sirve /health para que Cloud Run sepa que el servicio
#  está vivo. El bot (asyncio) corre en un thread separado.
#
#  WATCHDOG: si el event loop del bot termina por cualquier
#  excepción, el watchdog lo reinicia automáticamente después
#  de RESTART_DELAY segundos. Esto replica lo que hacía el
#  loop de auto-restart de start_eolo.sh cuando corría local.
# ============================================================
import asyncio
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify
from loguru import logger

import eolo_v2_main

app = Flask(__name__)

# ── Estado global del bot (para health / status) ─────────
bot_status = {
    "running":     False,
    "starts":      0,
    "restarts":    0,
    "last_start":  None,
    "last_stop":   None,
    "last_error":  None,
    "service":     "eolo-bot-v2",
    "mode":        "PAPER",
}


# ── Watchdog: reinicia el bot si muere ────────────────────

def _bot_watchdog():
    """
    Loop infinito que mantiene el bot v2 vivo. Si `eolo_v2_main.main()`
    termina (por error, excepción o vuelta clean del event loop),
    espera RESTART_DELAY y lo arranca de nuevo.
    """
    RESTART_DELAY = 15

    while True:
        bot_status["running"]    = True
        bot_status["starts"]    += 1
        bot_status["last_start"] = datetime.utcnow().isoformat()

        logger.info(
            f"🚀 EOLO v2 arrancando (intento #{bot_status['starts']})..."
        )

        try:
            # eolo_v2_main.main() es async → usar asyncio.run
            asyncio.run(eolo_v2_main.main())
            # Si llega acá, main() terminó limpio (raro salvo KeyboardInterrupt)
            logger.warning(
                "⚠️  eolo_v2_main.main() terminó sin excepción — reiniciando..."
            )
        except Exception as e:
            bot_status["last_error"] = f"{type(e).__name__}: {e}"
            bot_status["restarts"]  += 1
            logger.error(
                f"💥 Bot v2 crashed (restart #{bot_status['restarts']}): {e}"
            )

        bot_status["running"]  = False
        bot_status["last_stop"] = datetime.utcnow().isoformat()
        logger.info(f"⏳ Reiniciando EOLO v2 en {RESTART_DELAY}s...")
        time.sleep(RESTART_DELAY)


# Arrancar el watchdog al importar el módulo (gunicorn carga `main:app` → aquí)
watchdog_thread = threading.Thread(
    target=_bot_watchdog,
    daemon=True,
    name="eolo-v2-watchdog",
)
watchdog_thread.start()
logger.info("🛡️  EOLO v2 Watchdog arrancado")


# ── Health / status endpoints ─────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    """
    Cloud Run usa este endpoint para el health check de cada instancia.
    Devolvemos 200 siempre — el watchdog se encarga de reiniciar el bot
    si cae, así que el servicio está "vivo" aunque el bot esté reiniciando.
    """
    return jsonify({
        "status":     "running" if bot_status["running"] else "restarting",
        "service":    bot_status["service"],
        "mode":       bot_status["mode"],
        "starts":     bot_status["starts"],
        "restarts":   bot_status["restarts"],
        "last_start": bot_status["last_start"],
        "last_stop":  bot_status["last_stop"],
        "last_error": bot_status["last_error"],
    }), 200


@app.route("/status")
def status():
    """Info detallada del estado del bot (debug)."""
    payload = dict(bot_status)
    # Exponer estado del circuit breaker de billing (si el bot ya arrancó)
    bot = getattr(eolo_v2_main, "bot_instance", None)
    if bot is not None:
        payload["billing"] = {
            "anthropic_billing_paused": bool(getattr(bot, "_anthropic_billing_paused", False)),
            "errors_streak":            int(getattr(bot, "_anthropic_billing_errors", 0)),
            "threshold":                int(getattr(bot, "_anthropic_billing_threshold", 5)),
            "last_error":               str(getattr(bot, "_anthropic_billing_last_err", ""))[:300],
            "last_ts":                  float(getattr(bot, "_anthropic_billing_last_ts", 0.0)),
            "bot_active":               bool(getattr(bot, "_active", True)),
        }
    return jsonify(payload), 200


@app.route("/billing")
def billing():
    """
    Endpoint dedicado del circuit breaker — lo lee el dashboard para el semáforo.
    """
    bot = getattr(eolo_v2_main, "bot_instance", None)
    if bot is None:
        return jsonify({
            "anthropic_billing_paused": False,
            "errors_streak": 0,
            "threshold": 5,
            "bot_active": False,
            "bot_started": False,
        }), 200
    return jsonify({
        "anthropic_billing_paused": bool(getattr(bot, "_anthropic_billing_paused", False)),
        "errors_streak":            int(getattr(bot, "_anthropic_billing_errors", 0)),
        "threshold":                int(getattr(bot, "_anthropic_billing_threshold", 5)),
        "last_error":               str(getattr(bot, "_anthropic_billing_last_err", ""))[:300],
        "last_ts":                  float(getattr(bot, "_anthropic_billing_last_ts", 0.0)),
        "bot_active":               bool(getattr(bot, "_active", True)),
        "bot_started":              True,
    }), 200


# ── Entry point local (dev) ───────────────────────────────
# En Cloud Run se arranca con gunicorn; este if solo sirve para correr
# `python main.py` en el Mac como alternativa a start_eolo.sh.
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
