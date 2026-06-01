"""Tests para /api/version + /api/trades (Sprint OBS-1 + OBS-2 backend).

Run standalone (sin servidor live, Firestore mocked):
    cd eolo-crop && python3 tests/test_api_version_trades.py

Cubre:
1. /api/version shape (todas las keys + tipos)
2. /api/trades date inválida → 400
3. /api/trades limit inválido → 400
4. /api/trades limit > 200 capped a 200 (con Firestore mocked)
5. UUID regex filtra day-doc legacy

Nota: `crop_main` y el cascade de `theta_harvest` se stubbean en
sys.modules ANTES de `import main` para evitar requerir las deps reales
del runtime del bot en el env de testing. `llm_gate.trade_logger` también
se stubbea con sólo la constante FIRESTORE_COLLECTION.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


# ── Stub heavy deps antes de importar main ──────────────────────────
_EOLO_CROP = str(Path(__file__).resolve().parent.parent)
if _EOLO_CROP not in sys.path:
    sys.path.insert(0, _EOLO_CROP)


def _stub_module(name: str, **attrs) -> types.ModuleType:
    """Inserta un módulo stub en sys.modules con los attrs dados."""
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# Stubs requeridos para `import main` sin disparar el cascade real.
_stub_module("crop_main", bot_instance=None)
_stub_module("theta_harvest")
_stub_module(
    "theta_harvest.theta_harvest_strategy",
    VIX_CREDIT_TABLE={},
    TICKER_CONFIG={},
    TARGET_DTES={},
    DELTA_DRIFT_MAX=0.0,
    SPY_DROP_PCT_30M=0.0,
    VIX_MAX_ENTRY=30.0,
    VIX_SPIKE_DELTA=5.0,
    VVIX_PANIC_THRESHOLD=120.0,
    STOP_LOSS_MULT=2.0,
    PROFIT_TARGET_PCT=0.5,
    TRANCHE_PROFIT_TARGETS={},
    MIN_MINUTES_TO_EXP=15,
    ENTRY_HOUR_ET=9,
    ENTRY_WINDOW_MINUTES=15,
)
_stub_module("theta_harvest.pivot_analysis", DELTA_BY_RISK={})
_stub_module("llm_gate")
_stub_module("llm_gate.trade_logger", FIRESTORE_COLLECTION="eolo-crop-trades")

import main as app_module  # noqa: E402


class TestAPIVersion(unittest.TestCase):
    """Sprint OBS-1: /api/version shape + helpers."""

    def setUp(self) -> None:
        self.client = app_module.app.test_client()

    def test_version_shape(self) -> None:
        resp = self.client.get("/api/version")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for key in (
            "git_commit", "git_branch", "build_timestamp", "kb_version",
            "llm_engine_url", "llm_engine_health", "python_version",
            "bot_uptime_seconds",
        ):
            self.assertIn(key, data, f"missing key: {key}")
        self.assertIsInstance(data["git_commit"], str)
        self.assertIsInstance(data["git_branch"], str)
        self.assertIsInstance(data["bot_uptime_seconds"], (int, float))
        self.assertGreaterEqual(data["bot_uptime_seconds"], 0)
        self.assertIsInstance(data["llm_engine_health"], dict)
        # En dev env el engine no es alcanzable; status debe ser uno conocido.
        self.assertIn(
            data["llm_engine_health"].get("status"),
            {"auth_unavailable", "unreachable", "ok", "healthy"},
        )
        print("[ OK ] test_version_shape")


class TestAPITrades(unittest.TestCase):
    """Sprint OBS-2 backend: validación de params + shape + filtros."""

    def setUp(self) -> None:
        self.client = app_module.app.test_client()

    def test_invalid_date(self) -> None:
        resp = self.client.get("/api/trades?date=bad-date")
        self.assertEqual(resp.status_code, 400)
        err = (resp.get_json() or {}).get("error", "")
        self.assertIn("date", err)
        print("[ OK ] test_invalid_date")

    def test_invalid_limit(self) -> None:
        resp = self.client.get("/api/trades?limit=abc")
        self.assertEqual(resp.status_code, 400)
        print("[ OK ] test_invalid_limit")

    def test_limit_capped(self) -> None:
        """limit=99999 debe quedar en 200 (max), Firestore mockeado vacío."""
        with patch("google.cloud.firestore.Client") as mock_client:
            chain = (
                mock_client.return_value
                .collection.return_value
                .where.return_value
                .where.return_value
                .order_by.return_value
                .limit.return_value
            )
            chain.stream.return_value = iter([])
            resp = self.client.get("/api/trades?limit=99999")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["limit"], 200)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["trades"], [])
        print("[ OK ] test_limit_capped")

    def test_uuid_filter_skips_legacy(self) -> None:
        """El UUID regex acepta UUID v4 y rechaza day-doc IDs (YYYY-MM-DD)."""
        uuid_re = app_module._UUID_RE
        self.assertTrue(uuid_re.match("550e8400-e29b-41d4-a716-446655440000"))
        self.assertTrue(uuid_re.match("00000000-0000-0000-0000-000000000000"))
        self.assertFalse(uuid_re.match("2026-06-01"))  # day-doc legacy
        self.assertFalse(uuid_re.match("not-a-uuid"))
        self.assertFalse(uuid_re.match(""))
        print("[ OK ] test_uuid_filter_skips_legacy")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    print()
    print("=" * 50)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"Passed: {passed}/{result.testsRun}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
