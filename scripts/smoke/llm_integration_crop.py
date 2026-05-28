#!/usr/bin/env python3
"""
Smoke test del package llm_gate: ejercita LLMGateClient contra
el LLM Engine real en Cloud Run con snapshot sintetico.

Uso:
  cd ~/PycharmProjects/eolo
  source llm_engine_eolo/.venv/bin/activate
  LLM_ENGINE_URL="https://llm-engine-service-nmjz4iwcea-uc.a.run.app" \
    python scripts/smoke/llm_integration_crop.py

Validacion:
- 4.A.2.c: confirmar que LLMGateClient hace auth + call OK desde eolo-crop
- layered_path tracking (haiku_skip / haiku_pass / haiku_low_conf)
- Fallback shape consistente con la API real
"""
import sys
import os
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

# Path para importar llm_gate desde eolo-crop
sys.path.insert(0, os.path.expanduser("~/PycharmProjects/eolo/eolo-crop"))

from llm_gate import LLMGateClient
from llm_gate.snapshot import build_market_snapshot_from_crop
from llm_gate.integration import (
    should_call_llm, llm_decision_to_scan_params, decision_indicates_exit,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("smoke")


def make_mock_pivot_result():
    """Mock minimo de PivotAnalysisResult con .atr."""
    atr_ctx = SimpleNamespace(
        atr_day=2.30,
        prev_close=750.49,
        prev_high=752.13,
        prev_low=748.37,
    )
    return SimpleNamespace(atr=atr_ctx)


def make_mock_candle_buffer():
    """
    Mock minimo de CandleBuffer.
    as_df_1min retorna None para forzar defaults en indicators 2m/15m.
    En 4.B integraremos con CandleBuffer real.
    """
    class MockBuffer:
        def as_df_1min(self, symbol):
            return None
    return MockBuffer()


def main():
    url = os.getenv("LLM_ENGINE_URL")
    if not url:
        print("ERROR: LLM_ENGINE_URL env var requerida")
        print("Run: LLM_ENGINE_URL=https://llm-engine-service-... python ...")
        sys.exit(1)

    print(f"=== Smoke 4.A.2.c ===")
    print(f"LLM Engine URL: {url}")
    print()

    # 1. Build snapshot sintetico (mocks de CandleBuffer + pivot)
    snapshot = build_market_snapshot_from_crop(
        ticker="SPY",
        chain={"underlying": {"mark": 750.0}},
        vix_level=17.05,
        pivot_result=make_mock_pivot_result(),
        candle_buffer=make_mock_candle_buffer(),
        vix_velocity_30m_pct=0.5,
        vix_velocity_1d_pct=-2.0,
        allowed_dtes=[0, 1, 2],
        days_to_next_fomc=10,
        days_to_next_cpi=5,
        days_to_next_nfp=12,
    )
    print(f"Snapshot built: {len(snapshot)} fields")
    print(f"  ticker={snapshot['ticker']} price={snapshot['price']} "
          f"vix={snapshot['vix_level']} fibs={snapshot.get('fib_r2')}")
    print()

    # 2. Pre-filter check (should pass para entry window neutral)
    from datetime import time as dt_time
    from zoneinfo import ZoneInfo
    now_et_synthetic = datetime(2026, 5, 27, 10, 30, 0, tzinfo=ZoneInfo("America/New_York"))
    should_call, reason = should_call_llm(
        snapshot,
        tickers_enabled={"SPY": True},
        max_positions=10,
        current_positions_count=0,
        now_et=now_et_synthetic,
    )
    print(f"should_call_llm: {should_call} ({reason})")
    if not should_call:
        print("Pre-filter blocked. Smoke aborts before LLM call.")
        return
    print()

    # 3. Instanciar cliente y consult
    client = LLMGateClient(service_url=url, timeout=60.0)
    print(f"Calling client.consult()...")
    print()
    decision = client.consult(snapshot)

    # 4. Reporte estructurado
    print("=== Decision ===")
    print(f"verdict: {decision.get('verdict')}")
    print(f"confidence: {decision.get('confidence')}")
    print(f"layered_path: {decision.get('layered_path')}")
    print(f"main_reason: {decision.get('main_reason', '')[:200]}")
    if decision.get('layered_path') in ('haiku_pass', 'haiku_low_conf'):
        print(f"strikes: {decision.get('strikes')}")
        print(f"deltas: {decision.get('deltas')}")
        print(f"tacit_rules: {decision.get('tacit_rules_applied', [])[:5]}")
    print()

    pre = decision.get("pre_decision", {})
    if pre:
        print("=== Pre-decision (Haiku) ===")
        print(f"should_call_full: {pre.get('should_call_full')}")
        print(f"haiku_confidence: {pre.get('haiku_confidence')}")
        print(f"reason: {pre.get('reason', '')[:200]}")
        print(f"meta.latency_ms: {pre.get('meta', {}).get('latency_ms')}")
        print(f"meta.model: {pre.get('meta', {}).get('model')}")
        print()

    # 5. Test conversion a scan params
    params = llm_decision_to_scan_params(decision, "SPY")
    print(f"llm_decision_to_scan_params: {params}")

if __name__ == "__main__":
    main()
