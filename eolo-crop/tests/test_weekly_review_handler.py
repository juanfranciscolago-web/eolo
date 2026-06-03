"""Sub-C/D MEGATERMINATOR: weekly_review_handler + phase_checkpoint tests."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_weekly_review_runs_backtest_with_defaults(monkeypatch):
    """run_weekly_backtest delega a backtest.runner.run_backtest con tickers default."""
    from learning import weekly_review_handler as wrh

    captured = {}

    def fake_run_backtest(**kwargs):
        captured.update(kwargs)
        return {"metrics": {"n_decisions": 5, "total_cost_usd": 1.23}}

    monkeypatch.setattr(wrh, "_get_auth_token", lambda url: "fake-token")
    monkeypatch.setattr("backtest.runner.run_backtest", fake_run_backtest)
    monkeypatch.setattr(wrh, "_persist_to_firestore", lambda d, s: True)

    summary = wrh.run_weekly_backtest(budget_cap_usd=5.0)
    assert summary["metrics"]["n_decisions"] == 5
    assert captured["tickers"] == ["SPY", "QQQ", "IWM"]
    assert captured["budget_cap_usd"] == 5.0
    assert captured["use_prescreen"] is True


def test_weekly_review_handles_missing_auth_token(monkeypatch):
    """Si get_auth_token devuelve None → return error status without crashing."""
    from learning import weekly_review_handler as wrh
    monkeypatch.setattr(wrh, "_get_auth_token", lambda url: None)
    summary = wrh.run_weekly_backtest()
    assert summary["status"] == "ERROR_NO_AUTH_TOKEN"


def test_persist_to_firestore_uses_iso_week_id(monkeypatch):
    """Document id = YYYY-WNN (ISO week)."""
    from learning import weekly_review_handler as wrh
    from datetime import date as _date

    captured = {}

    class _FakeDoc:
        def set(self, data):
            captured["data"] = data

    class _FakeCollection:
        def document(self, doc_id):
            captured["doc_id"] = doc_id
            return _FakeDoc()

    class _FakeClient:
        def collection(self, name):
            captured["coll"] = name
            return _FakeCollection()

    monkeypatch.setattr("google.cloud.firestore.Client", lambda: _FakeClient())
    ok = wrh._persist_to_firestore(_date(2026, 6, 7), {"foo": "bar"})  # 2026 W23 (sunday week)
    assert ok is True
    assert captured["coll"] == "weekly_reviews"
    assert captured["doc_id"].startswith("2026-W")
    assert captured["data"]["summary"] == {"foo": "bar"}
