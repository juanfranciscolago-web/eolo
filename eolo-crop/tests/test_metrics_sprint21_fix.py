#!/usr/bin/env python3
"""Tests para Sprint 21 fix: Haiku tokens cost tracking en layered flow.

Run standalone (sin pytest):
    cd eolo-crop && python3 tests/test_metrics_sprint21_fix.py

Cubre:
    1. Sonnet-only (backward compat, sin haiku_* kwargs)
    2. Haiku-only (haiku_skip path: primary kwargs con model=haiku)
    3. Layered haiku_pass (Sonnet primario + Haiku via haiku_* kwargs)
    4. haiku_skip via primary kwargs (Haiku-only, no haiku_* needed)
    5. Backward compat exacto: haiku_*=0 == no haiku_* args
"""
from __future__ import annotations

import importlib.util
import os
import sys

# Cargamos metrics.py directo desde su path para evitar disparar
# llm_gate/__init__.py (que importa client.py → httpx, no requerido para
# estos tests). Tests deben correr sin instalar deps del runtime.
_HERE = os.path.dirname(os.path.abspath(__file__))
_METRICS_PATH = os.path.join(os.path.dirname(_HERE), "llm_gate", "metrics.py")

_spec = importlib.util.spec_from_file_location("_metrics_under_test", _METRICS_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load metrics module from {_METRICS_PATH}")
_metrics_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metrics_mod)

LLMMetrics = _metrics_mod.LLMMetrics
HAIKU_INPUT_PER_1M = _metrics_mod.HAIKU_INPUT_PER_1M
HAIKU_OUTPUT_PER_1M = _metrics_mod.HAIKU_OUTPUT_PER_1M
SONNET_INPUT_PER_1M = _metrics_mod.SONNET_INPUT_PER_1M
SONNET_OUTPUT_PER_1M = _metrics_mod.SONNET_OUTPUT_PER_1M


def _cost_sonnet(in_tok: int, out_tok: int) -> float:
    return (in_tok * SONNET_INPUT_PER_1M + out_tok * SONNET_OUTPUT_PER_1M) / 1e6


def _cost_haiku(in_tok: int, out_tok: int) -> float:
    return (in_tok * HAIKU_INPUT_PER_1M + out_tok * HAIKU_OUTPUT_PER_1M) / 1e6


def _assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: actual={actual!r} != expected={expected!r}")


def test_sonnet_only() -> None:
    """Sonnet-only path: sin haiku_* kwargs (backward compat)."""
    m = LLMMetrics()
    m.record_call(
        "SELL_PUT", 1500.0, "LLM_SONNET_CONSULT",
        input_tokens=2000, output_tokens=300, model="sonnet",
    )
    expected = round(_cost_sonnet(2000, 300), 4)
    stats = m.stats()
    _assert_eq(stats["cost_estimate_usd"], expected, "sonnet-only cost")
    _assert_eq(stats["total_calls"], 1, "total_calls")
    print(f"  cost=${expected:.4f}  total_calls=1")
    print("[ OK ] test_sonnet_only")


def test_haiku_only() -> None:
    """Haiku-only via primary kwargs (model=haiku)."""
    m = LLMMetrics()
    m.record_call(
        "WAIT", 350.0, "LLM_HAIKU_SKIP",
        input_tokens=1200, output_tokens=80, model="haiku",
    )
    expected = round(_cost_haiku(1200, 80), 4)
    stats = m.stats()
    _assert_eq(stats["cost_estimate_usd"], expected, "haiku-only cost")
    print(f"  cost=${expected:.4f}")
    print("[ OK ] test_haiku_only")


def test_layered_haiku_pass() -> None:
    """Layered haiku_pass: Sonnet primario + Haiku additive via haiku_* kwargs.

    Este es el core del Sprint 21 fix: antes solo Sonnet contaba; ahora
    suman ambos.
    """
    m = LLMMetrics()
    m.record_call(
        "SELL_PUT", 1800.0, "LLM_SONNET_CONSULT",
        input_tokens=2100, output_tokens=320, model="sonnet",
        haiku_input_tokens=1200, haiku_output_tokens=80,
    )
    expected = round(_cost_sonnet(2100, 320) + _cost_haiku(1200, 80), 4)
    stats = m.stats()
    _assert_eq(stats["cost_estimate_usd"], expected, "layered sonnet+haiku cost")
    # Sanity: el cost debe ser estrictamente mayor que sólo Sonnet.
    sonnet_only = round(_cost_sonnet(2100, 320), 4)
    if not stats["cost_estimate_usd"] > sonnet_only:
        raise AssertionError(
            f"layered cost ({stats['cost_estimate_usd']}) "
            f"should be > sonnet-only ({sonnet_only})"
        )
    print(f"  cost=${expected:.4f}  (sonnet_only=${sonnet_only:.4f} + haiku=${expected-sonnet_only:.4f})")
    print("[ OK ] test_layered_haiku_pass")


def test_haiku_skip_via_kwargs() -> None:
    """haiku_skip via primary kwargs con model=haiku (no haiku_* needed).

    Pre-fix bug: cost=$0 porque decision.meta=={} en path haiku_skip.
    Post-fix: crop_main lee pre_decision.meta y pasa como primary kwargs.
    """
    m = LLMMetrics()
    m.record_call(
        "WAIT", 300.0, "LLM_HAIKU_SKIP",
        input_tokens=1500, output_tokens=120, model="haiku",
        haiku_input_tokens=0, haiku_output_tokens=0,
    )
    expected = round(_cost_haiku(1500, 120), 4)
    stats = m.stats()
    _assert_eq(stats["cost_estimate_usd"], expected, "haiku_skip cost")
    if stats["cost_estimate_usd"] <= 0:
        raise AssertionError(
            f"haiku_skip cost should be > $0 (pre-fix bug); got {stats['cost_estimate_usd']}"
        )
    print(f"  cost=${expected:.4f}  (pre-fix this was $0.0000)")
    print("[ OK ] test_haiku_skip_via_kwargs")


def test_backward_compat_zero_haiku_kwargs() -> None:
    """haiku_*=0 explícito == no haiku_* args (backward compat exact)."""
    m1 = LLMMetrics()
    m1.record_call(
        "SELL_PUT", 1500.0, "LLM_SONNET_CONSULT",
        input_tokens=2000, output_tokens=300, model="sonnet",
    )
    cost1 = m1.stats()["cost_estimate_usd"]

    m2 = LLMMetrics()
    m2.record_call(
        "SELL_PUT", 1500.0, "LLM_SONNET_CONSULT",
        input_tokens=2000, output_tokens=300, model="sonnet",
        haiku_input_tokens=0, haiku_output_tokens=0,
    )
    cost2 = m2.stats()["cost_estimate_usd"]

    _assert_eq(cost1, cost2, "backward compat exact match")
    print(f"  cost_no_kwargs=${cost1:.4f}  cost_kwargs_zero=${cost2:.4f}")
    print("[ OK ] test_backward_compat_zero_haiku_kwargs")


def main() -> int:
    tests = [
        test_sonnet_only,
        test_haiku_only,
        test_layered_haiku_pass,
        test_haiku_skip_via_kwargs,
        test_backward_compat_zero_haiku_kwargs,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print()
    print("=" * 50)
    print(f"Passed: {len(tests) - failed}/{len(tests)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
