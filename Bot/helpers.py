from loguru import logger
import json
import time
from google.cloud import secretmanager, firestore

# Cache TTL en RAM para firestore reads repetitivos (access_token sobre
# todo — se re-lee antes de cada HTTP call a Schwab). El token dura 30min,
# 30s de cache es conservador y reduce ~1500 reads/ciclo → ~50.
# Si Schwab devuelve 401 por token stale, el caller hace retry y puede
# llamar a _invalidate_firestore_cache() para forzar re-lectura.
_FS_CACHE: dict = {}
_FS_CACHE_TTL_SEC = 30

def _invalidate_firestore_cache(collection_id=None, document_id=None, key=None):
    """Invalida entradas del cache. Sin args = flush total."""
    if collection_id is None:
        _FS_CACHE.clear()
        return
    ck = (collection_id, document_id, key)
    _FS_CACHE.pop(ck, None)

def retrieve_google_secret_dict(
    gcp_id, secret_id, version_id="latest"
) -> dict:
    client = secretmanager.SecretManagerServiceClient()

    name = f"projects/{gcp_id}/secrets/{secret_id}/versions/{version_id}"

    secret_response = client.access_secret_version(request={"name": name})

    secret_string = secret_response.payload.data.decode("UTF-8")

    secret_dict = json.loads(secret_string)

    logger.debug(f"Retrieved {version_id} secret value for {secret_id}.")

    return secret_dict

def retrieve_firestore_value(collection_id, document_id, key) -> str:
    ck = (collection_id, document_id, key)
    hit = _FS_CACHE.get(ck)
    if hit and (time.time() - hit[0]) < _FS_CACHE_TTL_SEC:
        return hit[1]

    db = firestore.Client()

    try:
        document = db.collection(collection_id).document(document_id)

        doc = document.get()

        if doc.exists:
            logger.debug(f"Successfully retrieved {key} value (fresh).")
            value = doc.get(key)
            _FS_CACHE[ck] = (time.time(), value)
            return value

        else:
            logger.error(f"Failed to retrieve {key} value")

    except Exception as e:

        logger.error(f"Failed to retrieve {key} value")

        return None

def store_firestore_value(project_id, collection_id, document_id, value):
    db = firestore.Client(project=project_id)

    collection = db.collection(collection_id)

    document = collection.document(document_id)

    document.set(value)

    logger.debug(f"Updated {document_id} value.")
