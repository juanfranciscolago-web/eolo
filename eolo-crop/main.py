# ============================================================
#  CROP — Cloud Run entry point (Theta Harvest only)
#
#  Flask sirve /health para que Cloud Run sepa que el servicio
#  está vivo. El bot (asyncio) corre en un thread separado.
#
#  WATCHDOG: si el event loop del bot termina por cualquier
#  excepción, el watchdog lo reinicia automáticamente después
#  de RESTART_DELAY segundos. Esto replica lo que hacía el
#  loop de auto-restart de start_crop.sh cuando corría local.
# ============================================================
import asyncio
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify
from loguru import logger

import crop_main

app = Flask(__name__)

# ── Estado global del bot (para health / status) ─────────
bot_status = {
    "running":     False,
    "starts":      0,
    "restarts":    0,
    "last_start":  None,
    "last_stop":   None,
    "last_error":  None,
    "service":     "eolo-bot-crop",
    "mode":        "PAPER",
}


# ── Watchdog: reinicia el bot si muere ────────────────────

def _bot_watchdog():
    """
    Loop infinito que mantiene el bot CROP vivo. Si `crop_main.main()`
    termina (por error, excepción o vuelta clean del event loop),
    espera RESTART_DELAY y lo arranca de nuevo.
    """
    RESTART_DELAY = 15

    while True:
        bot_status["running"]    = True
        bot_status["starts"]    += 1
        bot_status["last_start"] = datetime.utcnow().isoformat()

        logger.info(
            f"🚀 CROP Theta arrancando (intento #{bot_status['starts']})..."
        )

        try:
            # crop_main.main() es async → usar asyncio.run
            asyncio.run(crop_main.main())
            # Si llega acá, main() terminó limpio (raro salvo KeyboardInterrupt)
            logger.warning(
                "⚠️  crop_main.main() terminó sin excepción — reiniciando..."
            )
        except Exception as e:
            bot_status["last_error"] = f"{type(e).__name__}: {e}"
            bot_status["restarts"]  += 1
            logger.error(
                f"💥 Bot CROP crashed (restart #{bot_status['restarts']}): {e}"
            )

        bot_status["running"]  = False
        bot_status["last_stop"] = datetime.utcnow().isoformat()
        logger.info(f"⏳ Reiniciando CROP Theta en {RESTART_DELAY}s...")
        time.sleep(RESTART_DELAY)


# Arrancar el watchdog al importar el módulo (gunicorn carga `main:app` → aquí)
watchdog_thread = threading.Thread(
    target=_bot_watchdog,
    daemon=True,
    name="eolo-v2-watchdog",
)
watchdog_thread.start()
logger.info("🛡️  CROP Theta Watchdog arrancado")


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
    bot = getattr(crop_main, "bot_instance", None)
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
    bot = getattr(crop_main, "bot_instance", None)
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


@app.route("/daily-open-reset", methods=["GET", "POST"])
def daily_open_reset():
    """
    Disparado por Cloud Scheduler a las 9:30am ET (lunes–viernes).
    1. Cierra todas las posiciones abiertas (theta harvest + otras)
    2. Limpia _theta_positions y _theta_slots → bot re-entra fresh
    3. Limpia el doc de trades de ayer en Firestore (P&L del día vuelve a $0)
    4. Loguea el reset para auditoría

    Idempotente: si no hay posiciones abiertas, no hace nada dañino.
    """
    import asyncio as _asyncio
    from datetime import datetime, timedelta, timezone
    from google.cloud import firestore as _fs
    import os as _os

    bot = getattr(crop_main, "bot_instance", None)
    if bot is None:
        return jsonify({"ok": False, "error": "bot_instance no disponible aún"}), 503

    results = {}

    # ── 1. Cerrar todas las posiciones abiertas ───────────────
    try:
        loop = getattr(bot, "_loop", None)
        if loop and loop.is_running():
            future = _asyncio.run_coroutine_threadsafe(
                bot._execute_close_all(reason="daily-open-reset"), loop
            )
            future.result(timeout=30)
            results["close_all"] = "ok"
        else:
            results["close_all"] = "loop no disponible — skip"
    except Exception as e:
        results["close_all"] = f"error: {e}"

    # ── 2. Limpiar estado de theta harvest ────────────────────
    try:
        bot._theta_positions.clear()
        bot._theta_slots.clear()
        bot._theta_stats = {"credit_total": 0, "closed_pnl": 0}
        results["theta_reset"] = "ok"
    except Exception as e:
        results["theta_reset"] = f"error: {e}"

    # ── 3. Limpiar P&L de ayer en Firestore ──────────────────
    try:
        project_id = _os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
        db = _fs.Client(project=project_id)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        db.collection("eolo-crop-trades").document(yesterday).delete()
        results["firestore_cleanup"] = f"eliminado eolo-crop-trades/{yesterday}"
    except Exception as e:
        results["firestore_cleanup"] = f"error: {e}"

    # ── 4. Resetear daily loss cap status ─────────────────────
    try:
        bot._daily_loss_cap_status = {}
        bot._daily_loss_cap_log_ts = 0.0
        results["daily_cap_reset"] = "ok"
    except Exception as e:
        results["daily_cap_reset"] = f"error: {e}"

    logger.warning(
        f"[DAILY_OPEN_RESET] ✅ Reset completado a las 9:30am ET | {results}"
    )
    return jsonify({"ok": True, "results": results}), 200


# ── Entry point local (dev) ───────────────────────────────
# En Cloud Run se arranca con gunicorn; este if solo sirve para correr
# `python main.py` en el Mac como alternativa a start_eolo.sh.
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
