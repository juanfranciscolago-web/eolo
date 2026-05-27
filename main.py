import os
import time
from flask import Request
import base64
import requests
from loguru import logger

from helpers import retrieve_google_secret_dict, retrieve_firestore_value, store_firestore_value

cs_app_key_secret_dictionary = retrieve_google_secret_dict(gcp_id="eolo-schwab-agent", secret_id="cs-app-key")



def refresh_tokens(request):
    logger.info("Initializing...")

    app_key = cs_app_key_secret_dictionary["app-key"]
    app_secret = cs_app_key_secret_dictionary["app-secret"]

    # You can pull this from a local file,
    # Google Cloud Firestore/Secret Manager, etc.
    refresh_token_value = retrieve_firestore_value(collection_id="schwab-tokens", document_id="schwab-tokens-auth", key="refresh_token")


    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
    }
    headers = {
        "Authorization": f'Basic {base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()}',
        "Content-Type": "application/x-www-form-urlencoded",
    }

    refresh_token_response = requests.post(
        url="https://api.schwabapi.com/v1/oauth/token",
        headers=headers,
        data=payload,
    )
    if refresh_token_response.status_code == 200:
        logger.info("Retrieved new tokens successfully using refresh token.")
    else:
        logger.error(
            f"Error refreshing access token: {refresh_token_response.text}"
        )
        return None

    refresh_token_dict = refresh_token_response.json()

    # Sprint OAuth proactive: preservar refresh_token_issued_at del doc previo.
    # helpers.store_firestore_value usa .set() (sobreescribe completo) → sin esto, cada
    # refresh exitoso borra el timestamp y el health-check pierde edad real.
    # Si Schwab rota el refresh_token (nuevo valor), reseteamos issued_at = now.
    prev_refresh = retrieve_firestore_value(
        collection_id="schwab-tokens", document_id="schwab-tokens-auth", key="refresh_token"
    )
    prev_issued = retrieve_firestore_value(
        collection_id="schwab-tokens", document_id="schwab-tokens-auth", key="refresh_token_issued_at"
    )
    new_refresh = refresh_token_dict.get("refresh_token")
    if new_refresh and new_refresh != prev_refresh:
        refresh_token_dict["refresh_token_issued_at"] = time.time()
    elif prev_issued is not None:
        refresh_token_dict["refresh_token_issued_at"] = prev_issued

    store_firestore_value(project_id="eolo-schwab-agent", collection_id="schwab-tokens", document_id="schwab-tokens-auth", value=refresh_token_dict)

    logger.info("Token dict refreshed.")

    return "Done!"

# functions-framework --target=refresh_tokens --source=refresh.py --debug
