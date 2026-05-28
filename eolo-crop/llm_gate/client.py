"""
LLMGateClient - HTTP client para llamar al LLM Engine en Cloud Run.

Maneja:
- Auth con ID token via metadata server (Cloud Run SA) con fallback a gcloud (dev local).
- Fallback a should_call_full=True / verdict=WAIT en cualquier error
- Logging estructurado de requests + responses
"""
import os
import json
import time
import logging
import subprocess
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class LLMGateClient:
    """Cliente HTTP al LLM Engine Service en Cloud Run."""

    def __init__(
        self,
        service_url: Optional[str] = None,
        timeout: float = 60.0,
        haiku_confidence_threshold: int = 7,
    ):
        self.service_url = (service_url or os.getenv("LLM_ENGINE_URL", "")).rstrip("/")
        self.timeout = timeout
        self.haiku_confidence_threshold = haiku_confidence_threshold
        self._token_cache: Optional[str] = None
        self._token_expires_at: float = 0.0

        if not self.service_url:
            logger.warning("LLM_ENGINE_URL no configurada. Client va a fallar al llamar.")

    def _get_id_token(self) -> str:
        """
        Get ID token.
        - En Cloud Run con SA: metadata server (instantaneo, sin deps).
        - En dev local: gcloud CLI fallback.
        """
        now = time.time()
        if self._token_cache and now < self._token_expires_at:
            return self._token_cache

        # Approach 1: metadata server (Cloud Run con SA)
        try:
            resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1"
                "/instance/service-accounts/default/identity",
                params={"audience": self.service_url},
                headers={"Metadata-Flavor": "Google"},
                timeout=2.0,
            )
            if resp.status_code == 200:
                token = resp.text.strip()
                self._token_cache = token
                self._token_expires_at = now + 50 * 60
                logger.debug("[llm_gate] ID token from metadata server (CR)")
                return token
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            # Not in GCP — fallthrough a gcloud
            logger.debug(f"[llm_gate] metadata server unreachable ({type(e).__name__}); trying gcloud")

        # Approach 2: gcloud CLI (dev local)
        try:
            # En Cloud Run con SA: --audiences=URL funciona
            token = subprocess.check_output(
                ["gcloud", "auth", "print-identity-token",
                 "--audiences", self.service_url],
                text=True,
                stderr=subprocess.PIPE,
            ).strip()
        except subprocess.CalledProcessError as e:
            # Fallback: user account no soporta --audiences
            logger.warning(
                f"[llm_gate] gcloud --audiences failed ({e.stderr.strip()[:80]}); "
                "fallback a token generico"
            )
            token = subprocess.check_output(
                ["gcloud", "auth", "print-identity-token"],
                text=True,
            ).strip()

        self._token_cache = token
        self._token_expires_at = now + 50 * 60
        logger.debug("[llm_gate] ID token from gcloud CLI (dev local)")
        return token

    def pre_decide(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Llamada a Haiku pre-filter.

        Returns dict con: should_call_full, reason, haiku_confidence, meta.
        Fallback: should_call_full=True para no perder oportunidades.
        """
        if not self.service_url:
            return self._fallback_pre_decide("LLM_ENGINE_URL not configured")

        try:
            token = self._get_id_token()
        except Exception as e:
            logger.error(f"Failed to get ID token: {e}")
            return self._fallback_pre_decide(f"auth_error: {str(e)[:80]}")

        start = time.time()
        try:
            resp = httpx.post(
                f"{self.service_url}/pre_decide",
                json=snapshot,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            latency_ms = int((time.time() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"[llm_gate] /pre_decide {snapshot.get('ticker', '?')} "
                        f"-> should_call_full={data.get('should_call_full')} "
                        f"conf={data.get('haiku_confidence')} {latency_ms}ms")
            return data
        except Exception as e:
            logger.error(f"/pre_decide failed: {e}")
            return self._fallback_pre_decide(f"http_error: {str(e)[:80]}")

    def decide(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Llamada a Sonnet (full decision).

        Returns dict con: verdict, confidence, strikes, deltas, etc.
        Fallback: verdict=WAIT confidence=0.
        """
        if not self.service_url:
            return self._fallback_decision("LLM_ENGINE_URL not configured")

        try:
            token = self._get_id_token()
        except Exception as e:
            logger.error(f"Failed to get ID token: {e}")
            return self._fallback_decision(f"auth_error: {str(e)[:80]}")

        start = time.time()
        try:
            resp = httpx.post(
                f"{self.service_url}/decide",
                json=snapshot,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            latency_ms = int((time.time() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"[llm_gate] /decide {snapshot.get('ticker', '?')} "
                        f"-> verdict={data.get('verdict')} "
                        f"conf={data.get('confidence')} {latency_ms}ms")
            return data
        except Exception as e:
            logger.error(f"/decide failed: {e}")
            return self._fallback_decision(f"http_error: {str(e)[:80]}")

    def consult(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flow layered completo: pre_decide -> decide si corresponde.

        Returns dict con verdict + meta sobre el path layered:
          - "layered_path": "haiku_skip" | "haiku_pass" | "haiku_low_conf"
          - "pre_decision": el output del Haiku
          - resto = Decision normal (verdict, strikes, etc) si llego a Sonnet
        """
        pre = self.pre_decide(snapshot)
        should_call = pre.get("should_call_full", True)
        conf = pre.get("haiku_confidence", 0)

        # Haiku NO_GO con confidence alta -> skip Sonnet
        if not should_call and conf >= self.haiku_confidence_threshold:
            return {
                "verdict": "WAIT",
                "confidence": conf,
                "main_reason": f"haiku_skip: {pre.get('reason', '')}",
                "tacit_rules_applied": [],
                "safety_overrides": ["HAIKU_PREFILTER_SKIP"],
                "layered_path": "haiku_skip",
                "pre_decision": pre,
            }

        # Haiku duda (conf<threshold) o dice GO -> Sonnet decide
        decision = self.decide(snapshot)
        decision["layered_path"] = (
            "haiku_pass" if should_call else "haiku_low_conf"
        )
        decision["pre_decision"] = pre
        return decision

    def _fallback_pre_decide(self, reason: str) -> Dict[str, Any]:
        return {
            "should_call_full": True,
            "reason": f"client_fallback: {reason}",
            "haiku_confidence": 0,
            "meta": {"fallback": True},
        }

    def _fallback_decision(self, reason: str) -> Dict[str, Any]:
        return {
            "verdict": "WAIT",
            "confidence": 0,
            "strikes": {"put_strike": None, "call_strike": None},
            "deltas": {"put_delta": None, "call_delta": None},
            "dte_target": 0,
            "main_reason": f"client_fallback: {reason}",
            "tacit_rules_applied": [],
            "abort_triggers": [],
            "profit_target_pct": 50,
            "stop_loss_conditions": [],
            "similar_case_used": None,
            "warnings": [reason],
            "safety_overrides": ["CLIENT_FALLBACK"],
            "meta": {"fallback": True},
        }
