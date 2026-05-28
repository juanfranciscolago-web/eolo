"""
llm_gate — Cliente del LLM Engine para eolo-crop.

Modulos:
- client: LLMGateClient (HTTP + auth + fallback)
- cache: DecisionCache (TTL + invalidacion VIX/positions)
- indicators: cálculo de RSI/ATR/EMAs/etc para snapshot
- snapshot: build_market_snapshot_from_crop
- integration: should_call_llm + llm_decision_to_scan_params

URL del servicio en Cloud Run (env var LLM_ENGINE_URL):
  https://llm-engine-service-nmjz4iwcea-uc.a.run.app
"""
__version__ = "0.1.0"

from llm_gate.client import LLMGateClient
from llm_gate.cache import DecisionCache

__all__ = ["LLMGateClient", "DecisionCache"]
