"""Smoke tests REAL contra QuantData API.

Requiere:
    - GCP auth (gcloud auth application-default login) o env QUANTDATA_API_KEY
    - Network access a api.quantdata.us

Run:
    cd ~/PycharmProjects/eolo-quantdata/eolo-crop
    python3 tests/test_external_data_quantdata.py

Tests:
    1. get_max_pain(SPY, próximo viernes) — shape OK + valores plausibles
    2. get_iv_rank(SPY) — shape OK + valores 0..100
    3. get_gex_regime(SPY) — shape OK + regime ∈ {positive_high|low|flip_zone|negative}
    4. cache hit en 2do call de iv_rank (t2 << t1)

get_net_premium_drift NO se testea (shape no validada 2026-06-01); se llama
una sola vez para dump del response real al log + adelantar el ajuste del
parser sin hacer assertions.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from datetime import date, timedelta
from pathlib import Path


# ── Cargar módulo bajo test sin disparar el package llm_gate/__init__.py ──
_HERE = Path(__file__).resolve().parent
_EOLO_CROP = _HERE.parent
_MOD_PATH = _EOLO_CROP / "llm_gate" / "external_data_quantdata.py"

_spec = importlib.util.spec_from_file_location("_qd_under_test", _MOD_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load module from {_MOD_PATH}")
qd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qd)


# ── Helpers ───────────────────────────────────────────────────────────
def _next_friday() -> str:
    """Próximo viernes en YYYY-MM-DD (hoy mismo si hoy es viernes)."""
    today = date.today()
    days_ahead = (4 - today.weekday()) % 7  # 4 = Friday
    if days_ahead == 0:
        days_ahead = 7  # mañana en una semana si hoy es viernes
    return (today + timedelta(days=days_ahead)).isoformat()


def _next_business_friday() -> str:
    """Próximo viernes hábil — usado como expiration default para SPY."""
    return _next_friday()


# ── Tests ─────────────────────────────────────────────────────────────
def test_max_pain() -> None:
    expiration = _next_business_friday()
    result = qd.get_max_pain("SPY", expiration)
    assert result is not None, (
        f"max-pain returned None for SPY {expiration}; ver warnings de shape"
    )
    for key in ("max_pain_strike", "stock_price", "distance_pct"):
        assert key in result, f"missing key '{key}' en max-pain response"
    assert result["max_pain_strike"] > 0, "max_pain_strike <= 0 inesperado"
    assert result["stock_price"] > 0, "stock_price <= 0 inesperado"
    print(
        f"[ OK ] max_pain: strike={result['max_pain_strike']:.2f} "
        f"stock={result['stock_price']:.2f} dist={result['distance_pct']:.2f}% "
        f"exp={expiration}"
    )


def test_iv_rank() -> None:
    result = qd.get_iv_rank("SPY", look_back_period=30, maturity=7)
    assert result is not None, "iv-rank returned None; ver warnings de shape"
    # Al menos uno de los lados debe traer rank.
    assert result.get("call_rank_pct") is not None \
        or result.get("put_rank_pct") is not None, (
        "ambos rank_pct son None; iv-rank parser no encontró ningún field útil"
    )
    for k in ("call_rank_pct", "put_rank_pct"):
        v = result.get(k)
        if v is not None:
            assert 0.0 <= v <= 100.0, f"{k}={v} fuera de [0,100]"
    print(
        f"[ OK ] iv_rank: call={result.get('call_rank_pct')}% "
        f"put={result.get('put_rank_pct')}% "
        f"call_iv={result.get('call_last_iv')} put_iv={result.get('put_last_iv')}"
    )


def test_gex_regime() -> None:
    result = qd.get_gex_regime("SPY")
    assert result is not None, "gex returned None; ver warnings de shape"
    for key in ("total_gamma", "regime", "stock_price",
                "max_call_strike", "max_put_strike"):
        assert key in result, f"missing key '{key}' en gex response"
    assert result["regime"] in {"positive_high", "positive_low",
                                "flip_zone", "negative"}, (
        f"regime '{result['regime']}' fuera del vocabulario esperado"
    )
    print(
        f"[ OK ] gex: regime={result['regime']} "
        f"total_gamma={result['total_gamma']:.2e} "
        f"max_call={result['max_call_strike']:.2f} "
        f"max_put={result['max_put_strike']:.2f}"
    )


def test_cache() -> None:
    """Cache hit debe ser >5x más rápido que cache miss (usa gex que ya pasa)."""
    # Reset cache solo para gex de SPY para tener t1 medible.
    keys_to_drop = [k for k in list(qd._CACHE.keys()) if k.startswith("gex:SPY")]
    for k in keys_to_drop:
        qd._CACHE.pop(k, None)

    t0 = time.time()
    r1 = qd.get_gex_regime("SPY")
    t1 = time.time() - t0
    if r1 is None:
        print("[SKIP] test_cache: get_gex_regime returned None")
        return

    t0 = time.time()
    r2 = qd.get_gex_regime("SPY")
    t2 = time.time() - t0
    assert r2 is not None, "segundo call (cache hit) retornó None"

    assert t2 < t1 / 5 or t2 < 0.01, (
        f"posible cache miss: t1={t1:.3f}s t2={t2:.4f}s "
        f"(ratio={t1/max(t2,1e-9):.1f}x)"
    )
    print(f"[ OK ] cache: t1={t1:.3f}s t2={t2:.4f}s ({t1/max(t2,1e-9):.0f}x speedup)")


def test_net_drift() -> None:
    """Net drift parser con timestamp_ms extraído del key del bucket."""
    result = qd.get_net_premium_drift("SPY")
    if result is None:
        print("[SKIP] test_net_drift: get_net_premium_drift returned None")
        return
    for key in ("net_call_premium", "net_put_premium", "stock_price", "timestamp_ms"):
        assert key in result, f"missing key '{key}' en net-drift response"
    assert result["timestamp_ms"] > 0, (
        f"timestamp_ms invalid: {result.get('timestamp_ms')}"
    )
    print(
        f"[ OK ] net_drift: call=${result['net_call_premium']:.0f} "
        f"put=${result['net_put_premium']:.0f} stock={result['stock_price']:.2f} "
        f"ts={result['timestamp_ms']}"
    )


def main() -> int:
    tests = [
        ("test_max_pain",   test_max_pain),
        ("test_iv_rank",    test_iv_rank),
        ("test_gex_regime", test_gex_regime),
        ("test_cache",      test_cache),
        ("test_net_drift",  test_net_drift),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {name}: {type(e).__name__}: {e}")
            failed += 1

    print()
    print("═" * 50)
    print(f"Passed: {len(tests) - failed}/{len(tests)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
