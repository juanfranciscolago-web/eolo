# ============================================================
#  EOLO Crypto — Cloud Run entry point
#
#  Flask sirve /health para el health check de Cloud Run.
#  El bot crypto (asyncio) corre en un thread separado.
#
#  WATCHDOG: si el event loop del bot termina por excepción,
#  el watchdog lo reinicia automáticamente tras RESTART_DELAY.
#  Mismo patrón que eolo-options/main.py (validado en prod v2).
# ============================================================
import asyncio
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify
from loguru import logger

import settings
import eolo_crypto_main

app = Flask(__name__)

# ── Estado global del bot (para health / status) ─────────
bot_status = {
    "running":     False,
    "starts":      0,
    "restarts":    0,
    "last_start":  None,
    "last_stop":   None,
    "last_error":  None,
    "service":     "eolo-bot-crypto",
    "mode":        settings.BINANCE_MODE,
}


def _bot_watchdog():
    """
    Loop infinito que mantiene el bot crypto vivo. Si
    `eolo_crypto_main.main()` termina (por error o clean return),
    espera RESTART_DELAY y lo arranca de nuevo.
    """
    while True:
        bot_status["running"]    = True
        bot_status["starts"]    += 1
        bot_status["last_start"] = datetime.utcnow().isoformat()
        bot_status["mode"]       = settings.BINANCE_MODE

        logger.info(
            f"🚀 EOLO Crypto arrancando (intento #{bot_status['starts']}) "
            f"| mode={settings.BINANCE_MODE}"
        )

        try:
            asyncio.run(eolo_crypto_main.main())
            logger.warning(
                "⚠️  eolo_crypto_main.main() terminó sin excepción — reiniciando..."
            )
        except Exception as e:
            bot_status["last_error"] = f"{type(e).__name__}: {e}"
            bot_status["restarts"]  += 1
            logger.error(
                f"💥 Bot crypto crashed (restart #{bot_status['restarts']}): {e}"
            )

        bot_status["running"]   = False
        bot_status["last_stop"] = datetime.utcnow().isoformat()
        logger.info(f"⏳ Reiniciando EOLO Crypto en {settings.WATCHDOG_RESTART_DELAY}s...")
        time.sleep(settings.WATCHDOG_RESTART_DELAY)


# Arrancar el watchdog al importar el módulo (gunicorn carga `main:app`)
watchdog_thread = threading.Thread(
    target=_bot_watchdog,
    daemon=True,
    name="eolo-crypto-watchdog",
)
watchdog_thread.start()
logger.info("🛡️  EOLO Crypto Watchdog arrancado")


# ── Health / status endpoints ─────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    """
    Cloud Run health check. Devuelve 200 siempre — el watchdog
    reinicia el bot, así que el servicio está "vivo" aunque el
    bot esté reiniciando.
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
    return jsonify(bot_status), 200


# ── Entry point local (dev) ───────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
