"""
LLM Engine Client - Para usar desde Eolo Crop.

Copiá este archivo a tu repo de Eolo Crop y usalo para llamar al LLM service.

USAGE en Eolo Crop:
    from llm_client import LLMEngineClient

    client = LLMEngineClient(service_url="https://llm-engine-service-xxx.run.app")

    snapshot_dict = {
        "timestamp": "...",
        "ticker": "SPY",
        "price": 750.0,
        ...
    }

    decision = client.decide(snapshot_dict)

    if decision["verdict"] in ["SELL_PUT", "SELL_CALL", "IRON_CONDOR_SEQUENTIAL"]:
        if decision["confidence"] >= 7:
            execute_paper_trade(decision)
"""
import requests
import logging
import os
from typing import Dict, Any
import google.auth
import google.auth.transport.requests
from google.oauth2 import id_token

logger = logging.getLogger(__name__)


class LLMEngineClient:
    """Cliente para llamar al LLM Engine Service en Cloud Run."""

    def __init__(self, service_url: str, timeout: int = 30):
        """
        Args:
            service_url: URL del Cloud Run service (con https://)
            timeout: Timeout en segundos
        """
        self.service_url = service_url.rstrip('/')
        self.timeout = timeout
        self._auth_token = None
        self._auth_token_expires = 0

    def _get_auth_token(self) -> str:
        """
        Get auth token para Cloud Run service-to-service.
        Usa Application Default Credentials del service account de Eolo Crop.
        """
        # Token caching simple - en producción usar refresh apropiado
        auth_req = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_req, self.service_url)
        return token

    def decide(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Llama al endpoint /decide del LLM service.

        Args:
            snapshot: dict con todos los campos de MarketSnapshot

        Returns:
            dict con la decisión (verdict, confidence, strikes, etc)
        """
        url = f"{self.service_url}/decide"

        try:
            token = self._get_auth_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                url,
                json=snapshot,
                headers=headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            logger.error(f"LLM service timeout after {self.timeout}s")
            return self._safe_fallback("LLM service timeout")
        except requests.exceptions.HTTPError as e:
            logger.error(f"LLM service HTTP error: {e}")
            return self._safe_fallback(f"HTTP error: {e}")
        except Exception as e:
            logger.error(f"LLM service unexpected error: {e}")
            return self._safe_fallback(f"Unexpected error: {str(e)[:100]}")

    def health(self) -> Dict[str, Any]:
        """Health check del LLM service."""
        try:
            response = requests.get(f"{self.service_url}/health", timeout=5)
            return response.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    def kb_stats(self) -> Dict[str, Any]:
        """Stats del KB cargado."""
        try:
            token = self._get_auth_token()
            response = requests.get(
                f"{self.service_url}/kb_stats",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5
            )
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def _safe_fallback(self, reason: str) -> Dict[str, Any]:
        """Fallback seguro - SIEMPRE WAIT cuando falla."""
        return {
            "verdict": "WAIT",
            "confidence": 0,
            "strikes": {"put_strike": None, "call_strike": None},
            "deltas": {"put_delta": None, "call_delta": None},
            "dte_target": 0,
            "main_reason": f"LLM service unavailable: {reason}",
            "tacit_rules_applied": [],
            "abort_triggers": [],
            "profit_target_pct": 50,
            "stop_loss_conditions": [],
            "similar_case_used": None,
            "warnings": [reason],
            "safety_overrides": ["CLIENT_FALLBACK"],
            "meta": {"fallback": True}
        }


# ═══════════════════════════════════════════════════════
# Example integration en Eolo Crop loop
# ═══════════════════════════════════════════════════════

def example_eolo_crop_loop():
    """
    EJEMPLO de cómo integrar en tu loop de Eolo Crop.

    NOTA: Esto es pseudo-código. Adaptá a tu estructura real.
    """
    import time

    LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL")
    client = LLMEngineClient(LLM_SERVICE_URL)

    # Verificar salud del servicio al startup
    health = client.health()
    logger.info(f"LLM service health: {health}")

    if health.get("status") != "healthy":
        logger.error("LLM service unhealthy - aborting bot")
        return

    while market_is_open():  # tu función existente
        try:
            # 1. Build snapshot from Schwab (función a implementar en Eolo Crop)
            snapshot = build_market_snapshot()

            # 2. Llamar al LLM
            decision = client.decide(snapshot)

            # 3. Loggear SIEMPRE
            logger.info(f"LLM decision: {decision['verdict']} "
                       f"(confidence {decision['confidence']}/10) - "
                       f"{decision['main_reason']}")

            # 4. Ejecutar si aplica
            if decision["verdict"] == "WAIT":
                logger.info(f"WAIT - reason: {decision['main_reason']}")

            elif decision["verdict"] == "CLOSE_POSITIONS":
                close_all_open_positions()  # tu función existente

            elif decision["verdict"] in ["SELL_PUT", "SELL_CALL"]:
                if decision["confidence"] >= 7:
                    execute_paper_trade(decision)  # tu función existente
                else:
                    logger.info(f"Confidence {decision['confidence']} below threshold 7")

            elif decision["verdict"] == "IRON_CONDOR_SEQUENTIAL":
                if decision["confidence"] >= 7:
                    # Sequential: vender PUT primero, después CALL
                    # Esto requiere coordinación con tu order management
                    execute_iron_condor_sequential(decision)

        except Exception as e:
            logger.exception(f"Error in main loop: {e}")

        # Sleep entre iteraciones (ej. cada 2 minutos)
        time.sleep(120)
