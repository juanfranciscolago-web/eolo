# ============================================================
#  EOLO Crypto — Helpers
#
#  Binance auth (HMAC-SHA256), Secret Manager loader, Firestore
#  helpers. Sigue el patrón de eolo-options/helpers.py.
# ============================================================
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests
from loguru import logger
from google.cloud import firestore, secretmanager

import settings


# ── Cache simple para secretos (evitar hit a SM en cada call) ──
_SECRET_CACHE: dict[str, str] = {}


def _retrieve_google_secret(secret_id: str, version_id: str = "latest") -> str:
    """
    Lee un secret plano (string) desde Secret Manager.
    Retorna el string tal cual (sin parse JSON).
    """
    if secret_id in _SECRET_CACHE:
        return _SECRET_CACHE[secret_id]

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{settings.GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    resp = client.access_secret_version(request={"name": name})
    value = resp.payload.data.decode("UTF-8").strip()
    _SECRET_CACHE[secret_id] = value
    logger.debug(f"[AUTH] Secret {secret_id} cargado desde SM")
    return value


def get_binance_api_key() -> str:
    """Retorna la API key de Binance desde Secret Manager."""
    return _retrieve_google_secret(settings.SECRET_BINANCE_KEY_NAME)


def get_binance_api_secret() -> str:
    """Retorna el API secret de Binance desde Secret Manager."""
    return _retrieve_google_secret(settings.SECRET_BINANCE_SEC_NAME)


def get_anthropic_api_key() -> str:
    """Reusa el mismo secret que v2 options."""
    return _retrieve_google_secret(settings.SECRET_ANTHROPIC_NAME)


# ── Firestore ────────────────────────────────────────────

def firestore_client() -> firestore.Client:
    return firestore.Client(project=settings.GCP_PROJECT_ID)


def firestore_write(collection: str, document: str, data: dict):
    db = firestore_client()
    db.collection(collection).document(document).set(data)


def firestore_read(collection: str, document: str) -> dict | None:
    db = firestore_client()
    doc = db.collection(collection).document(document).get()
    return doc.to_dict() if doc.exists else None


# ── Binance signing ──────────────────────────────────────

def sign_query(params: dict) -> str:
    """
    Genera la firma HMAC-SHA256 de un query string de Binance.
    Retorna el query string completo incluyendo &signature=...
    Uso:
        url = f"{base}/api/v3/account?{sign_query({'timestamp': now})}"
    """
    secret = get_binance_api_secret()
    # Binance requiere que params incluya timestamp
    if "timestamp" not in params:
        params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{query}&signature={signature}"


def auth_headers() -> dict:
    """Headers para endpoints autenticados de Binance."""
    return {"X-MBX-APIKEY": get_binance_api_key()}


def get_server_time_offset() -> int:
    """
    Calcula el offset (ms) entre reloj local y servidor Binance.
    Binance rechaza órdenes con timestamp que difiera del suyo
    más allá de recvWindow (default 5000 ms). En Cloud Run el
    reloj es NTP pero no está de más tener un margen.

    Retorna: server_time - local_time (positivo si local está atrás)
    """
    try:
        resp = requests.get(
            f"{settings.get_endpoint('rest')}/api/v3/time",
            timeout=5,
        )
        resp.raise_for_status()
        server_ms = resp.json()["serverTime"]
        local_ms  = int(time.time() * 1000)
        offset = server_ms - local_ms
        logger.debug(f"[AUTH] Binance server_time offset = {offset} ms")
        return offset
    except Exception as e:
        logger.warning(f"[AUTH] No pude calcular server_time offset: {e}")
        return 0


def binance_timestamp(offset_ms: int = 0) -> int:
    """Timestamp ms corregido por el offset del server."""
    return int(time.time() * 1000) + offset_ms


# ── REST helpers de alto nivel ────────────────────────────

def binance_get(path: str, params: dict | None = None, signed: bool = False,
                timeout: int = 10) -> dict:
    """
    GET a la API de Binance. Si signed=True, firma los params.
    path ejemplo: '/api/v3/ticker/24hr'
    """
    url = f"{settings.get_endpoint('rest')}{path}"
    headers = auth_headers() if signed else {}

    if signed:
        params = params or {}
        query = sign_query(params)
        url = f"{url}?{query}"
        resp = requests.get(url, headers=headers, timeout=timeout)
    else:
        resp = requests.get(url, params=params or {}, headers=headers, timeout=timeout)

    resp.raise_for_status()
    return resp.json()


def binance_post(path: str, params: dict, signed: bool = True,
                 timeout: int = 10) -> dict:
    """
    POST a la API de Binance (típicamente para órdenes).
    Para signed=True (default), firma los params.
    """
    url = f"{settings.get_endpoint('rest')}{path}"
    headers = auth_headers()

    if signed:
        query = sign_query(params)
        url = f"{url}?{query}"
        resp = requests.post(url, headers=headers, timeout=timeout)
    else:
        resp = requests.post(url, params=params, headers=headers, timeout=timeout)

    resp.raise_for_status()
    return resp.json()


def binance_delete(path: str, params: dict, timeout: int = 10) -> dict:
    """DELETE a la API de Binance (cancelar orden)."""
    url = f"{settings.get_endpoint('rest')}{path}"
    query = sign_query(params)
    url = f"{url}?{query}"
    resp = requests.delete(url, headers=auth_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.json()
