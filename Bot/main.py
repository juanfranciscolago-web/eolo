# ============================================================
#  EOLO Bot — Cloud Run entry point
#
#  Flask sirve /health para que Cloud Run sepa que el
#  servicio está vivo. El bot corre en un thread separado.
#
#  WATCHDOG: si el bot thread muere (por cualquier error),
#  el watchdog lo reinicia automáticamente después de 10s.
#  Esto evita que el bot quede caído sin que nadie lo sepa.
# ============================================================
import threading
import time
import os
from datetime import datetime
from flask import Flask, jsonify
from loguru import logger
import bot_main

app = Flask(__name__)

# ── Estado global del bot (para el endpoint /health) ──────
bot_status = {
    "running":    False,
    "starts":     0,          # cantidad de veces que arrancó
    "last_start": None,
    "last_error": None,
    "restarts":   0,          # cantidad de reinicios por crash
}

# ── Watchdog: reinicia el bot si muere ────────────────────

def _bot_watchdog():
    """
    Loop infinito que mantiene el bot vivo.
    Si bot_main.main() termina (por error o excepción),
    espera RESTART_DELAY segundos y lo reinicia.
    """
    RESTART_DELAY = 15   # segundos de espera antes de reiniciar

    while True:
        bot_status["running"]    = True
        bot_status["starts"]    += 1
        bot_status["last_start"] = datetime.utcnow().isoformat()

        logger.info(f"🚀 EOLO Bot arrancando (intento #{bot_status['starts']})...")

        try:
            bot_main.main()   # loop infinito — solo termina por error
            # Si llega acá, bot_main.main() terminó sin excepción (raro)
            logger.warning("⚠️  bot_main.main() terminó inesperadamente — reiniciando...")
        except Exception as e:
            bot_status["last_error"] = str(e)
            bot_status["restarts"]  += 1
            logger.error(f"💥 Bot crashed (restart #{bot_status['restarts']}): {e}")

        bot_status["running"] = False
        logger.info(f"⏳ Reiniciando bot en {RESTART_DELAY}s...")
        time.sleep(RESTART_DELAY)


# Arrancar el watchdog en background al iniciar el proceso
watchdog_thread = threading.Thread(
    target=_bot_watchdog,
    daemon=True,
    name="eolo-watchdog"
)
watchdog_thread.start()
logger.info("🛡️  EOLO Watchdog arrancado")


# ── Health / status endpoints ─────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    """
    Cloud Run usa este endpoint para saber si el servicio
    está vivo. Retorna 200 siempre (el watchdog garantiza
    que el bot se reinicia solo si cae).
    """
    return jsonify({
        "status":      "running" if bot_status["running"] else "restarting",
        "service":     "EOLO Bot",
        "bot_starts":  bot_status["starts"],
        "restarts":    bot_status["restarts"],
        "last_start":  bot_status["last_start"],
        "last_error":  bot_status["last_error"],
    }), 200


@app.route("/status")
def status():
    """Info detallada del estado del bot."""
    return jsonify(bot_status), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
