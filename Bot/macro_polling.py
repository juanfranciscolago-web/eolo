# ============================================================
#  EOLO v1 (acciones) — MacroFeeds polling thread
#
#  Wrapper sync-only sobre eolo_common.macro.MacroFeeds. El bot v1
#  no corre asyncio, así que levantamos un thread que cada POLL_SEC
#  pide quotes a Schwab via MarketData.get_quotes y pushea los valores
#  directamente al buffer de MacroFeeds.
#
#  Uso:
#      from macro_polling import start_macro_feeds
#      feeds = start_macro_feeds(market_data, poll_sec=60)
#      settings["_macro_feeds"] = feeds
#
#  Si Schwab devuelve None/falla, las estrategias Nivel 2 siguen
#  devolviendo HOLD silenciosamente — no rompen el loop principal.
# ============================================================
import threading
import time
from typing import Optional

from loguru import logger

from eolo_common.macro import MacroFeeds
from eolo_common.macro.symbols import MACRO_SYMBOLS, resolve_schwab


DEFAULT_POLL_SEC = 60


def _poll_loop(feeds: MacroFeeds, market_data, stop_evt: threading.Event, poll_sec: int):
    """
    Thread loop: pide quotes de VIX/VIX9D/VIX3M/TICK/TRIN cada poll_sec segundos
    y pushea los valores al MacroFeeds.

    Tolera 401 silenciosamente (MarketData.get_quotes ya hace refresh_access_token
    antes de cada request). Si un símbolo no devuelve valor, simplemente no pushea.
    """
    # Lista única de símbolos Schwab a pedir
    schwab_symbols = [resolve_schwab(n) for n in MACRO_SYMBOLS.keys()]
    logger.info(f"[MACRO] polling thread arrancado | cada {poll_sec}s | símbolos={schwab_symbols}")

    # Primer poll inmediato para llenar el buffer cuanto antes
    first = True

    while not stop_evt.is_set():
        if not first:
            # sleep en chunks para respetar stop_evt rápido
            for _ in range(poll_sec * 2):
                if stop_evt.is_set():
                    return
                time.sleep(0.5)
        first = False

        try:
            quotes = market_data.get_quotes(schwab_symbols) or {}
        except Exception as e:
            logger.warning(f"[MACRO] get_quotes falló: {e}")
            continue

        if not quotes:
            logger.debug("[MACRO] quotes vacío (Schwab no devolvió datos)")
            continue

        ts = time.time()
        pushed = []
        for name, meta in MACRO_SYMBOLS.items():
            for key in (meta["schwab"], *meta["aliases"]):
                if key in quotes and quotes[key] is not None:
                    feeds.push(name, quotes[key], ts=ts)
                    pushed.append(f"{name}={quotes[key]:.2f}")
                    break
        if pushed:
            logger.debug(f"[MACRO] poll OK → {', '.join(pushed)}")
        else:
            logger.debug("[MACRO] poll sin símbolos reconocidos (Schwab cuenta sin permisos?)")


def start_macro_feeds(market_data, poll_sec: int = DEFAULT_POLL_SEC) -> MacroFeeds:
    """
    Crea un MacroFeeds y arranca su thread de polling. Retorna la instancia
    para inyectar en `settings["_macro_feeds"]` desde bot_main.

    El thread es daemon, así que termina cuando el proceso se cierra.
    """
    feeds = MacroFeeds(poll_sec=poll_sec)
    stop_evt = threading.Event()
    feeds._stop_evt = stop_evt  # para debugging / tests; no usado por las estrategias

    t = threading.Thread(
        target=_poll_loop,
        args=(feeds, market_data, stop_evt, poll_sec),
        name="macro-feeds-poll",
        daemon=True,
    )
    t.start()
    feeds._thread = t
    logger.info(f"[MACRO] MacroFeeds inicializado (poll={poll_sec}s, daemon thread)")
    return feeds


def stop_macro_feeds(feeds: Optional[MacroFeeds]):
    """Detiene el thread de polling. Llamado al shutdown (opcional)."""
    if feeds is None:
        return
    evt: Optional[threading.Event] = getattr(feeds, "_stop_evt", None)
    if evt is not None:
        evt.set()
    t: Optional[threading.Thread] = getattr(feeds, "_thread", None)
    if t is not None and t.is_alive():
        t.join(timeout=3.0)
