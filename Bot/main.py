# ============================================================
#  EOLO Bot — Cloud Run entry point
#  Flask sirve /health para que Cloud Run sepa que el
#  servicio está vivo. El bot corre en un thread separado.
# ============================================================
import threading
import os
from flask import Flask, jsonify
from loguru import logger
import bot_main

app = Flask(__name__)

# ── Health / status endpoints ─────────────────────────────
@app.route("/")
@app.route("/health")
def health():
    return jsonify({"status": "running", "service": "EOLO Bot"}), 200


# ── Arrancar el bot en background al iniciar el proceso ───
def _start_bot():
    try:
        bot_main.main()          # loop infinito con candle-alignment
    except Exception as e:
        logger.error(f"Bot thread crashed: {e}")


bot_thread = threading.Thread(target=_start_bot, daemon=True, name="eolo-bot")
bot_thread.start()
logger.info("🚀 EOLO Bot thread arrancado")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
