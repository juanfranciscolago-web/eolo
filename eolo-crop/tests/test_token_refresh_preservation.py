"""Sub-F MEGATERMINATOR: refresh_token_issued_at preservation tests.

Verifica que el flow de access_token refresh:
1. NO sobrescribe refresh_token_issued_at cuando solo cambia access_token.
2. SÍ actualiza refresh_token_issued_at cuando Schwab rotó refresh_token.
3. Siempre actualiza access_token_issued_at.
"""
from unittest.mock import patch
import time


def _simulate_refresh_logic(
    existing_db: dict,
    schwab_response: dict,
    fixed_now: float = 1780500000.0,
) -> dict:
    """Inline of the fix from crop_main._do_token_refresh post Sub-F.

    Aislada de la clase para que sea testable sin instanciar el bot completo.
    """
    prev_refresh_token = None
    existing = {}
    for key in ["access_token", "refresh_token", "expires_in",
                "token_type", "scope", "id_token",
                "refresh_token_issued_at", "access_token_issued_at"]:
        val = existing_db.get(key)
        if val is not None:
            existing[key] = val
        if key == "refresh_token":
            prev_refresh_token = val

    existing.update({k: v for k, v in schwab_response.items() if v is not None})

    new_refresh_token = schwab_response.get("refresh_token")
    if new_refresh_token and new_refresh_token != prev_refresh_token:
        existing["refresh_token_issued_at"] = fixed_now
    existing["access_token_issued_at"] = fixed_now
    return existing


def test_refresh_token_issued_at_preserved_on_access_refresh():
    """Schwab devuelve solo access_token (no rota refresh_token) → issued_at preservado."""
    existing_db = {
        "access_token":              "OLD_ACCESS",
        "refresh_token":             "RT_UNCHANGED",
        "refresh_token_issued_at":   1780000000.0,  # ~3 días atrás
        "expires_in":                1800,
    }
    schwab_response = {
        "access_token": "NEW_ACCESS",
        "expires_in":   1800,
    }
    result = _simulate_refresh_logic(existing_db, schwab_response, fixed_now=1780259200.0)
    assert result["refresh_token_issued_at"] == 1780000000.0, \
        f"refresh_token_issued_at debe preservarse cuando solo cambia access_token. Got {result.get('refresh_token_issued_at')}"
    assert result["access_token"] == "NEW_ACCESS"
    assert result["access_token_issued_at"] == 1780259200.0


def test_refresh_token_issued_at_updated_when_refresh_rotates():
    """Schwab devuelve refresh_token nuevo → issued_at updated a ahora."""
    existing_db = {
        "access_token":              "OLD_ACCESS",
        "refresh_token":             "RT_OLD",
        "refresh_token_issued_at":   1780000000.0,
    }
    schwab_response = {
        "access_token":  "NEW_ACCESS",
        "refresh_token": "RT_BRAND_NEW",
    }
    result = _simulate_refresh_logic(existing_db, schwab_response, fixed_now=1780259200.0)
    assert result["refresh_token_issued_at"] == 1780259200.0, \
        "refresh_token rotado debe set issued_at to now"
    assert result["refresh_token"] == "RT_BRAND_NEW"


def test_first_time_refresh_no_prior_issued_at():
    """Si Firestore no tiene refresh_token_issued_at previo, solo se setea
    cuando Schwab rotó refresh_token (no se inventa para un access_token refresh)."""
    existing_db = {"refresh_token": "RT_EXISTING"}
    schwab_response = {"access_token": "NEW_ACCESS"}
    result = _simulate_refresh_logic(existing_db, schwab_response, fixed_now=1780259200.0)
    assert "refresh_token_issued_at" not in result, \
        "No debe inventarse refresh_token_issued_at si nunca existía y solo se renueva access"


def test_access_token_issued_at_always_updated():
    """access_token_issued_at se renueva siempre."""
    existing_db = {"refresh_token": "RT", "access_token_issued_at": 1780000000.0}
    schwab_response = {"access_token": "NEW"}
    result = _simulate_refresh_logic(existing_db, schwab_response, fixed_now=1780999999.0)
    assert result["access_token_issued_at"] == 1780999999.0
