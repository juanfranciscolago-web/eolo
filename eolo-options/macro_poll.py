# ============================================================
#  EOLO v2 (opciones) — MacroFeeds quote fetcher
#
#  Wrapper async sobre eolo_common.macro.MacroFeeds:
#    - quote_fn(symbols) pide /marketdata/v1/quotes a Schwab
#      en un executor (requests es blocking).
#    - make_macro_feeds() devuelve el MacroFeeds + una coroutine
#      `start_macro_loop()` para colgar en asyncio.gather.
#
#  Se invoca desde EoloV2.start() junto con las otras tasks
#  (token_refresh_loop, stream, etc).
# ============================================================
import asyncio
import os
import sys
from typing import Dict, List

import requests
from loguru import logger

# Asegurar que eolo_common está en el path (igual que en eolo_v2_main.py)
_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.macro import MacroFeeds  # noqa: E402

# helpers del v2 (reutilizamos la función de auth existente)
sys.path.insert(0, os.path.dirname(__file__))
from helpers import get_access_token  # noqa: E402


SCHWAB_QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"
DEFAULT_POLL_SEC = 60


def _fetch_quotes_sync(symbols: List[str]) -> Dict[str, float]:
    """
    Pide /quotes a Schwab para los símbolos dados. Devuelve
    dict {symbol: last_price}. Tolera errores HTTP devolviendo
    lo que pudo parsear; si el token está expirado, retorna {}.
    """
    token = get_access_token()
    if not token:
        logger.debug("[MACRO-v2] sin access_token — skip poll")
        return {}

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"symbols": ",".join(symbols), "fields": "quote", "indicative": False}

    try:
        resp = requests.get(
            SCHWAB_QUOTES_URL, headers=headers, params=params, timeout=8,
        )
        if resp.status_code != 200:
            logger.debug(f"[MACRO-v2] quotes HTTP {resp.status_code}")
            return {}
        data = resp.json() or {}
    except Exception as e:
        logger.warning(f"[MACRO-v2] quotes request failed: {e}")
        return {}

    out: Dict[str, float] = {}
    for sym in symbols:
        entry = data.get(sym) or {}
        q = entry.get("quote") or {}
        price = (
            q.get("lastPrice")
            or q.get("mark")
            or q.get("closePrice")
        )
        if price is not None:
            try:
                out[sym] = float(price)
            except (TypeError, ValueError):
                pass
    return out


async def _async_quote_fn(symbols: List[str]) -> Dict[str, float]:
    """
    Adapter async: corre _fetch_quotes_sync en un executor para no
    bloquear el event loop de asyncio.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_quotes_sync, symbols)


def make_macro_feeds(poll_sec: int = DEFAULT_POLL_SEC) -> MacroFeeds:
    """Devuelve una instancia nueva de MacroFeeds lista para `.start()`."""
    return MacroFeeds(poll_sec=poll_sec)


async def start_macro_loop(feeds: MacroFeeds):
    """
    Coroutine que arranca el polling. Pensada para `asyncio.gather()`.
    Si `feeds.start()` falla no propagamos la excepción — Nivel 2
    simplemente queda silenciosa con feeds vacío.
    """
    try:
        await feeds.start(quote_fn=_async_quote_fn)
        # feeds.start() spawnea su propia task y retorna inmediatamente.
        # Mantenemos esta coroutine viva para que gather no la cierre.
        while feeds._running:
            await asyncio.sleep(5)
    except Exception as e:
        logger.error(f"[MACRO-v2] loop falló: {e}")
