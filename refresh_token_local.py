#!/usr/bin/env python3
# ============================================================
#  refresh_token_local.py
#
#  Refresca el access_token de Schwab usando el refresh_token
#  almacenado en Firestore y guarda el nuevo par de tokens.
#
#  Uso:
#    cd /path/to/eolo
#    python refresh_token_local.py
#
#  Requiere:
#    - gcloud auth application-default login  (o GOOGLE_APPLICATION_CREDENTIALS)
#    - Secreto "cs-app-key" en Secret Manager con app-key y app-secret
#    - Documento Firestore schwab-tokens/schwab-tokens-auth con refresh_token
# ============================================================
import base64
import sys
import os
import time
import requests
from loguru import logger

# Aseguramos que helpers.py esté accesible
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import (
    retrieve_google_secret_dict,
    retrieve_firestore_value,
    store_firestore_value,
)

GCP_PROJECT       = "eolo-schwab-agent"
COLLECTION_ID     = "schwab-tokens"
DOCUMENT_ID       = "schwab-tokens-auth"
SECRET_ID         = "cs-app-key"
TOKEN_URL         = "https://api.schwabapi.com/v1/oauth/token"


def refresh_tokens() -> bool:
    """
    1. Lee refresh_token de Firestore.
    2. Llama a Schwab OAuth con grant_type=refresh_token.
    3. Guarda el nuevo access_token (y refresh_token si cambia) en Firestore.
    Retorna True si tuvo éxito, False si falló.
    """
    # ── 1. Credenciales Schwab desde Secret Manager ────────
    logger.info("Cargando credenciales Schwab desde Secret Manager...")
    try:
        creds = retrieve_google_secret_dict(gcp_id=GCP_PROJECT, secret_id=SECRET_ID)
        app_key    = creds["app-key"]
        app_secret = creds["app-secret"]
    except Exception as e:
        logger.error(f"No se pudo leer el secreto '{SECRET_ID}': {e}")
        return False

    # ── 2. refresh_token actual desde Firestore ────────────
    logger.info("Leyendo refresh_token de Firestore...")
    refresh_token = retrieve_firestore_value(
        collection_id=COLLECTION_ID,
        document_id=DOCUMENT_ID,
        key="refresh_token",
    )
    if not refresh_token:
        logger.error("refresh_token no encontrado en Firestore. "
                     "Necesitás correr init_auth.py para hacer el auth inicial.")
        return False
    logger.info(f"refresh_token leído: {refresh_token[:20]}...")

    # ── 3. Llamar a Schwab OAuth /token ────────────────────
    logger.info("Solicitando nuevo access_token a Schwab...")
    credentials_b64 = base64.b64encode(
        f"{app_key}:{app_secret}".encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {credentials_b64}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }
    payload = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }

    try:
        resp = requests.post(TOKEN_URL, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as e:
        logger.error(f"HTTP {resp.status_code} al refrescar token: {resp.text}")
        if resp.status_code == 401:
            logger.warning("401 → el refresh_token puede haber expirado (7 días sin uso).")
            logger.warning("Solución: correr init_auth.py para hacer el auth OAuth completo.")
        return False
    except Exception as e:
        logger.error(f"Error de conexión al refrescar token: {e}")
        return False

    tokens = resp.json()
    logger.info(f"Respuesta Schwab: {list(tokens.keys())}")

    if "access_token" not in tokens:
        logger.error(f"Respuesta inesperada: {tokens}")
        return False

    # ── 4. Guardar tokens actualizados en Firestore ────────
    logger.info("Guardando nuevos tokens en Firestore...")
    try:
        # Preserva los campos anteriores y actualiza/agrega los nuevos
        existing = {}
        for key in ["access_token", "refresh_token", "expires_in",
                    "token_type", "scope", "id_token",
                    "refresh_token_issued_at"]:
            val = retrieve_firestore_value(COLLECTION_ID, DOCUMENT_ID, key)
            if val is not None:
                existing[key] = val

        # Sobreescribir con los nuevos
        existing.update({k: v for k, v in tokens.items() if v is not None})

        # Sprint OAuth proactive: mantener simetría con main.py (refresh-tokens function).
        # store_firestore_value usa .set() → sin preservación, cada refresh borra el
        # timestamp y el health-check pierde edad real. Si Schwab rota el refresh_token
        # (nuevo valor != previo), reseteamos issued_at = now.
        new_refresh_val = tokens.get("refresh_token")
        if new_refresh_val and new_refresh_val != refresh_token:
            existing["refresh_token_issued_at"] = time.time()

        store_firestore_value(
            project_id=GCP_PROJECT,
            collection_id=COLLECTION_ID,
            document_id=DOCUMENT_ID,
            value=existing,
        )
    except Exception as e:
        logger.error(f"Error guardando en Firestore: {e}")
        return False

    new_access  = tokens["access_token"]
    new_refresh = tokens.get("refresh_token", "(sin cambio)")
    logger.success(f"✅  access_token actualizado:  {new_access[:25]}...")
    logger.success(f"✅  refresh_token actualizado: {str(new_refresh)[:25]}...")
    return True


if __name__ == "__main__":
    ok = refresh_tokens()
    if ok:
        logger.success("Token refrescado exitosamente. Podés iniciar el bot.")
        sys.exit(0)
    else:
        logger.error("Falló el refresh. Revisá los logs arriba.")
        sys.exit(1)
