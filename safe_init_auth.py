#!/usr/bin/env python3
"""
safe_init_auth.py — Wrapper validado de init_auth.py

Mismo OAuth dance que init_auth.py, pero con guards:
  - Valida que la response de Schwab tenga access_token + refresh_token
    (NO sobreescribe Firestore si Schwab devolvió error o response parcial)
  - Tolera URL devuelta con '@' literal o '%40' (init_auth.py original sólo acepta %40)
  - Exit codes:
      0 = éxito (Firestore actualizado)
      1 = error de input/validación (Firestore intacto)
      2 = error de red/secret (Firestore intacto)

NO modifica init_auth.py. Lo deja como fallback.
"""
import base64
import sys
import webbrowser

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


def get_credentials() -> tuple[str, str]:
    logger.info("[1/6] Leyendo secreto cs-app-key de Secret Manager...")
    creds = retrieve_google_secret_dict(gcp_id=GCP_PROJECT, secret_id=SECRET_ID)
    app_key    = creds["app-key"]
    app_secret = creds["app-secret"]
    logger.info(f"      app-key: {app_key[:8]}...  app-secret: {app_secret[:4]}...")
    return app_key, app_secret


def show_auth_url(app_key: str) -> str:
    auth_url = (
        f"https://api.schwabapi.com/v1/oauth/authorize"
        f"?client_id={app_key}&redirect_uri={REDIRECT_URI}"
    )
    logger.info("[2/6] URL de autorizacion Schwab:")
    logger.info(f"      {auth_url}")
    logger.info("      Abriendo browser...")
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        logger.warning(f"      No se pudo abrir browser ({e}). Copiá la URL manualmente.")
    return auth_url


def parse_code(returned_url: str) -> str:
    """
    Schwab redirige a https://127.0.0.1/?code=XXXXX%40YYY&session=...
    Algunos browsers decodifican %40 -> @ antes de mostrar la URL.
    Aceptamos ambas formas y devolvemos el code con '@' literal (que es lo que Schwab espera).
    """
    if "code=" not in returned_url:
        raise ValueError("URL devuelta NO contiene 'code='. Pegaste la URL correcta del browser?")
    code_start = returned_url.index("code=") + 5
    rest = returned_url[code_start:]

    # Cortar en '%40' o '@' literal (lo que aparezca primero)
    idx_pct = rest.find("%40")
    idx_at  = rest.find("@")
    candidates = [i for i in (idx_pct, idx_at) if i >= 0]
    if not candidates:
        raise ValueError(
            "URL devuelta NO tiene '%40' ni '@' despues de code=. "
            "Formato inesperado. URL recibida (primeros 80 chars): "
            f"{returned_url[:80]}..."
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
    logger.info(f"[4/6] POST {TOKEN_URL}  (grant_type=authorization_code)")
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
    logger.info(f"[6/6] Guardando en Firestore {COLLECTION_ID}/{DOCUMENT_ID}...")
    logger.info(f"      Campos: {list(tokens.keys())}")
    store_firestore_value(
        project_id=GCP_PROJECT,
        collection_id=COLLECTION_ID,
        document_id=DOCUMENT_ID,
        value=tokens,
    )
    logger.success("      OK Tokens guardados")


def main() -> int:
    try:
        app_key, app_secret = get_credentials()
    except Exception as e:
        logger.error(f"[1/6] Error leyendo Secret Manager: {e}")
        return 2

    show_auth_url(app_key)

    logger.info("[3/6] Esperando URL devuelta (despues del login en Schwab):")
    logger.info("      La URL debe contener 'code=XXX%40YYY' (o 'code=XXX@YYY')")
    logger.info("      CRITICO: el code expira en ~30s, pegala rapido.")
    try:
        returned_url = input("Paste Returned URL: ").strip()
    except (KeyboardInterrupt, EOFError):
        logger.error("Cancelado por user (Ctrl+C o EOF)")
        return 1

    if not returned_url:
        logger.error("URL vacia. Abortando.")
        return 1

    try:
        code = parse_code(returned_url)
        logger.info(f"      code parseado OK (len={len(code)}, prefix={code[:10]}..., suffix=...{code[-5:]})")
    except ValueError as e:
        logger.error(f"[3/6] Parse fallo: {e}")
        return 1

    try:
        status_code, tokens = exchange_code(code, app_key, app_secret)
    except requests.RequestException as e:
        logger.error(f"[4/6] Error de red: {e}")
        return 2

    logger.info("[5/6] Validando response...")
    ok, msg = validate_response(status_code, tokens)
    if not ok:
        logger.error(f"      FAILED: {msg}")
        logger.error("      Firestore NO se modifico (guard funciono correctamente).")
        logger.error("      Si el code expiro, reintenta correr safe_init_auth.py de nuevo.")
        return 1
    logger.success(f"      OK ({len(tokens)} campos: {sorted(tokens.keys())})")

    try:
        save_tokens(tokens)
    except Exception as e:
        logger.error(f"[6/6] Error guardando en Firestore: {e}")
        return 2

    logger.success("=" * 60)
    logger.success("OAuth dance completado. Bot deberia recuperar en proximo ciclo.")
    logger.success("Validar con: python3 check_schwab_token.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
