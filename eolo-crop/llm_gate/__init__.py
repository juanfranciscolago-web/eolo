"""
llm_gate — Cliente del LLM Engine para eolo-crop.

Modulos:
- client: LLMGateClient (HTTP + auth + fallback)
- indicators: cálculo de RSI/ATR/EMAs/etc para snapshot
- snapshot: build_market_snapshot_from_crop (viene en 4.A.2.b)
- integration: should_call_llm + llm_decision_to_signal (viene en 4.A.2.b)

URL del servicio en Cloud Run (env var LLM_ENGINE_URL):
  https://llm-engine-service-nmjz4iwcea-uc.a.run.app
"""
__version__ = "0.1.0"

from llm_gate.client import LLMGateClient

__all__ = ["LLMGateClient"]
