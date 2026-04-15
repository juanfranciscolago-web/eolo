import os
import base64
import requests
import webbrowser
from loguru import logger

from helpers import retrieve_google_secret_dict, retrieve_firestore_value, store_firestore_value

cs_app_key_secret_dictionary = retrieve_google_secret_dict(gcp_id="eolo-schwab-agent", secret_id="cs-app-key")


def construct_init_auth_url() -> tuple[str, str, str]:
    app_key = cs_app_key_secret_dictionary["app-key"]
    app_secret = cs_app_key_secret_dictionary["app-secret"]

    auth_url = f"https://api.schwabapi.com/v1/oauth/authorize?client_id={app_key}&redirect_uri=https://127.0.0.1"

    logger.info("Click to authenticate:")
    logger.info(auth_url)

    return app_key, app_secret, auth_url


def construct_headers_and_payload(returned_url, app_key, app_secret):
    response_code = f"{returned_url[returned_url.index('code=') + 5: returned_url.index('%40')]}@"

    credentials = f"{app_key}:{app_secret}"
    base64_credentials = base64.b64encode(credentials.encode("utf-8")).decode(
        "utf-8"
    )

    headers = {
        "Authorization": f"Basic {base64_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {
        "grant_type": "authorization_code",
        "code": response_code,
        "redirect_uri": "https://127.0.0.1",
    }

    return headers, payload


def retrieve_tokens(headers, payload) -> dict:
    init_token_response = requests.post(
        url="https://api.schwabapi.com/v1/oauth/token",
        headers=headers,
        data=payload,
    )

    init_tokens_dict = init_token_response.json()

    return init_tokens_dict


def main(request):
    app_key, app_secret, cs_auth_url = construct_init_auth_url()
    webbrowser.open(cs_auth_url)

    logger.info("Paste Returned URL:")
    returned_url = input()

    init_token_headers, init_token_payload = construct_headers_and_payload(
        returned_url, app_key, app_secret
    )

    init_tokens_dict = retrieve_tokens(
        headers=init_token_headers, payload=init_token_payload
    )

    store_firestore_value(project_id="eolo-schwab-agent", collection_id="schwab-tokens", document_id="schwab-tokens-auth", value=init_tokens_dict)

    return "Done!"

# functions framework --target=main --source=refresh_token/init_auth.py --debug