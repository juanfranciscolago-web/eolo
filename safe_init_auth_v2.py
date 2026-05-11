#!/usr/bin/env python3
"""
safe_init_auth_v2.py — OAuth dance Schwab con URL via argv (no input() interactivo)

Mismas garantias que safe_init_auth.py:
  - Valida response de Schwab antes de tocar Firestore
  - Tolera '%40' o '@' literal en el code
  - Firestore intacto si validation falla
  - Exit codes: 0 = ok, 1 = usage/validation, 2 = network/secret

Diferencias vs v1:
  - URL como sys.argv[1] (no input())
  - NO abre browser (vos haces el OAuth dance manualmente antes)

Uso:
  1) Abri en browser:
       https://api.schwabapi.com/v1/oauth/authorize?client_id=<APP_KEY>&redirect_uri=https://127.0.0.1
     (este script imprime la URL exacta si lo corres sin argumentos)
  2) Login Schwab + Allow
  3) Browser redirige a https://127.0.0.1/?code=XXX%40YYY  (pagina falla — esperado)
  4) Copia la URL COMPLETA del address bar
  5) Corre:
       python3 safe_init_auth_v2.py "https://127.0.0.1/?code=XXX..."
     (las comillas son CRITICAS por los caracteres especiales en la URL)
"""
import base64
import sys

import requests
from loguru import logger

from helpers import retrieve_google_secret_dict, store_firestore_value

GCP_PROJECT   = "eolo-schwab-agent"
SECRET_ID     = "cs-app-key"
COLLECTION_ID = "schwab-tokens"
DOCUMENT_ID   = "schwab-tokens-auth"
TOKEN_URL     = "https://api.schwabapi.com/v1/oauth/token"
REDIRECT_URI  = "https://127.0.0.1"

REQUIRED_FIELDS = ("access_token", "refresh_token", "expires_in", "token_type")


def print_usage_and_auth_url() -> None:
    """
    Cuando se llama sin argumentos, imprimimos la URL de auth para que el user
    haga el OAuth dance en el browser. NO abrimos browser automaticamente.
    """
    logger.info("Leyendo cs-app-key para construir auth URL...")
    try:
        creds = retrieve_google_secret_dict(gcp_id=GCP_PROJECT, secret_id=SECRET_ID)
        app_key = creds["app-key"]
    except Exception as e:
        logger.error(f"No se pudo leer Secret Manager: {e}")
        return

    auth_url = (
        f"https://api.schwabapi.com/v1/oauth/authorize"
        f"?client_id={app_key}&redirect_uri={REDIRECT_URI}"
    )
    print()
    print("=" * 70)
    print("USAGE:  python3 safe_init_auth_v2.py \"<URL_DEVUELTA>\"")
    print("=" * 70)
    print()
    print("Pasos:")
    print("  1) Abri esta URL en el browser:")
    print()
    print(f"     {auth_url}")
    print()
    print("  2) Login Schwab + click Allow")
    print("  3) Browser redirige a https://127.0.0.1/?code=...  (pagina falla)")
    print("  4) Copia la URL COMPLETA del address bar")
    print("  5) Corre este script con la URL entre comillas:")
    print()
    print("     python3 safe_init_auth_v2.py \"https://127.0.0.1/?code=XXX...\"")
    print()
    print("CRITICO: el code expira ~30s desde el redirect. Apurate.")
    print()


def get_credentials() -> tuple[str, str]:
    logger.info("[1/5] Leyendo secreto cs-app-key de Secret Manager...")
    creds = retrieve_google_secret_dict(gcp_id=GCP_PROJECT, secret_id=SECRET_ID)
    app_key    = creds["app-key"]
    app_secret = creds["app-secret"]
    logger.info(f"      app-key: {app_key[:8]}...  app-secret: {app_secret[:4]}...")
    return app_key, app_secret


def parse_code(returned_url: str) -> str:
    """
    Tolera '%40' (URL-encoded) o '@' literal despues del code.
    """
    if "code=" not in returned_url:
        raise ValueError("URL NO contiene 'code='. Es la URL devuelta del browser?")
    code_start = returned_url.index("code=") + 5
    rest = returned_url[code_start:]
    idx_pct = rest.find("%40")
    idx_at  = rest.find("@")
    candidates = [i for i in (idx_pct, idx_at) if i >= 0]
    if not candidates:
        raise ValueError(
            "URL NO tiene '%40' ni '@' despues de code=. "
            f"Primeros 100 chars: {returned_url[:100]}..."
        )
    code_end = min(candidates)
    code = rest[:code_end] + "@"
    return code


def exchange_code(code: str, app_key: str, app_secret: str) -> tuple[int, dict]:
    creds_b64 = base64.b64encode(f"{app_key}:{app_secret}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {creds_b64}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }
    payload = {
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": REDIRECT_URI,
    }
    logger.info(f"[3/5] POST {TOKEN_URL}  (grant_type=authorization_code)")
    resp = requests.post(TOKEN_URL, headers=headers, data=payload, timeout=15)
    logger.info(f"      HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        body = {"_raw": resp.text}
    return resp.status_code, body


def validate_response(status_code: int, tokens: dict) -> tuple[bool, str]:
    if status_code != 200:
        return False, f"HTTP {status_code}. Body: {tokens}"
    if "error" in tokens:
        return False, (
            f"Schwab devolvio error: '{tokens.get('error')}' — "
            f"'{tokens.get('error_description', '(no description)')}'"
        )
    missing = [f for f in REQUIRED_FIELDS if f not in tokens]
    if missing:
        return False, f"Response incompleta. Faltan: {missing}. Keys recibidas: {list(tokens.keys())}"
    if not tokens.get("access_token"):
        return False, "access_token vacio o None"
    if not tokens.get("refresh_token"):
        return False, "refresh_token vacio o None"
    return True, "OK"


def save_tokens(tokens: dict) -> None:
    logger.info(f"[5/5] Guardando en Firestore {COLLECTION_ID}/{DOCUMENT_ID}...")
    logger.info(f"      Campos: {sorted(tokens.keys())}")
    store_firestore_value(
        project_id=GCP_PROJECT,
        collection_id=COLLECTION_ID,
        document_id=DOCUMENT_ID,
        value=tokens,
    )
    logger.success("      OK Tokens guardados")


def main() -> int:
    if len(sys.argv) < 2:
        print_usage_and_auth_url()
        return 1

    returned_url = sys.argv[1].strip()
    if not returned_url:
        logger.error("URL vacia. Uso: python3 safe_init_auth_v2.py \"<URL>\"")
        return 1
    if "code=" not in returned_url:
        logger.error(f"URL NO contiene 'code='. URL recibida: {returned_url[:120]}...")
        return 1

    try:
        app_key, app_secret = get_credentials()
    except Exception as e:
        logger.error(f"[1/5] Error leyendo Secret Manager: {e}")
        return 2

    try:
        code = parse_code(returned_url)
        logger.info(f"[2/5] code parseado OK (len={len(code)}, prefix={code[:12]}..., suffix=...{code[-5:]})")
    except ValueError as e:
        logger.error(f"[2/5] Parse fallo: {e}")
        return 1

    try:
        status_code, tokens = exchange_code(code, app_key, app_secret)
    except requests.RequestException as e:
        logger.error(f"[3/5] Error de red: {e}")
        return 2

    logger.info("[4/5] Validando response...")
    ok, msg = validate_response(status_code, tokens)
    if not ok:
        logger.error(f"      FAILED: {msg}")
        logger.error("      Firestore NO se modifico (guard funciono).")
        logger.error("      Si el code expiro, hace OAuth dance de nuevo y reintenta.")
        return 1
    logger.success(f"      OK ({len(tokens)} campos: {sorted(tokens.keys())})")

    try:
        save_tokens(tokens)
    except Exception as e:
        logger.error(f"[5/5] Error guardando en Firestore: {e}")
        return 2

    logger.success("=" * 60)
    logger.success("OAuth dance completado. Bot deberia recuperar en proximo ciclo.")
    logger.success("Validar con: python3 check_schwab_token.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
