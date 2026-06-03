"""Sprint S5 backtest engine tests.

Cubre los modulos nuevos del Sub S5-SCAFFOLDING:
- snapshot_replay.iter_historical_snapshots
- cost_optimizer.haiku_prescreen + backtest_call_with_cost_control
- runner.run_backtest budget cap respeto
- metrics_aggregator.aggregate
"""
from datetime import date
from unittest.mock import patch


def test_iter_historical_snapshots_skips_weekends(tmp_path):
    """Weekends NO devuelven snapshots, weekdays SÍ (cuando hay cache)."""
    from backtest.snapshot_replay import iter_historical_snapshots
    # cache_dir vacío → no devuelve nada para weekdays tampoco
    snaps = list(iter_historical_snapshots(
        "SPY", date(2026, 5, 1), date(2026, 5, 4), cache_dir=str(tmp_path)
    ))
    assert snaps == []


def test_iter_historical_snapshots_reads_cache(tmp_path):
    """Cuando hay cache, devuelve snapshot reconstructed."""
    from backtest.snapshot_replay import iter_historical_snapshots
    import json
    # Crear cache fake con shape mínimo (parsing tolerante)
    cache_file = tmp_path / "SPY_2026-05-04.json"
    cache_file.write_text(json.dumps({
        "ticker": "SPY",
        "timestamp": "2026-05-04T09:30:00",
        "_raw": {
            "_qd_max-pain": {"maxPainStrikePrice": 500.0, "stockPrice": 502.0},
        },
    }))
    snaps = list(iter_historical_snapshots(
        "SPY", date(2026, 5, 4), date(2026, 5, 4), cache_dir=str(tmp_path)
    ))
    assert len(snaps) == 1
    assert snaps[0]["ticker"] == "SPY"
    assert snaps[0]["max_pain_strike"] == 500.0
    assert snaps[0]["price"] == 502.0


def test_haiku_prescreen_should_call_sonnet_when_full_true():
    from backtest.cost_optimizer import haiku_prescreen
    with patch("backtest.cost_optimizer.http_post_json") as mock_post:
        mock_post.return_value = {"should_call_full": True, "haiku_confidence": 9, "reason": "GOLDEN"}
        r = haiku_prescreen({"ticker": "SPY"}, "https://engine", "tok")
    assert r["should_call_sonnet"] is True
    assert r["haiku_confidence"] == 9
    assert r["cost_usd"] > 0


def test_haiku_prescreen_skips_sonnet_when_full_false_high_conf():
    from backtest.cost_optimizer import haiku_prescreen
    with patch("backtest.cost_optimizer.http_post_json") as mock_post:
        mock_post.return_value = {"should_call_full": False, "haiku_confidence": 9, "reason": "SKIP"}
        r = haiku_prescreen({"ticker": "SPY"}, "https://engine", "tok", confidence_threshold=7)
    assert r["should_call_sonnet"] is False


def test_haiku_prescreen_fails_open_on_error():
    """Si Haiku call falla, default conservative is call Sonnet."""
    from backtest.cost_optimizer import haiku_prescreen
    with patch("backtest.cost_optimizer.http_post_json", side_effect=Exception("network")):
        r = haiku_prescreen({"ticker": "SPY"}, "https://engine", "tok")
    assert r["should_call_sonnet"] is True
    assert "prescreen_error" in r["reason"]
    assert r["cost_usd"] == 0.0


def test_backtest_call_skipped_by_haiku():
    from backtest.cost_optimizer import backtest_call_with_cost_control
    with patch("backtest.cost_optimizer.http_post_json") as mock_post:
        mock_post.return_value = {"should_call_full": False, "haiku_confidence": 9}
        r = backtest_call_with_cost_control({"ticker": "SPY"}, "https://engine", "tok")
    assert r["skipped"] is True
    assert r["tier"] == "haiku"
    assert r["verdict"] == "SKIPPED_BY_HAIKU"


def test_backtest_call_force_sonnet_bypasses_prescreen():
    from backtest.cost_optimizer import backtest_call_with_cost_control
    with patch("backtest.cost_optimizer.http_post_json") as mock_post:
        mock_post.return_value = {"verdict": "SELL_PUT", "confidence": 8}
        r = backtest_call_with_cost_control(
            {"ticker": "SPY"}, "https://engine", "tok",
            use_prescreen=True, force_sonnet=True,
        )
    assert r["tier"] == "sonnet"
    assert r["skipped"] is False
    # cost = sonnet only (no haiku prescreen)
    from backtest.cost_optimizer import SONNET_USD_PER_CALL
    assert r["cost_usd"] == SONNET_USD_PER_CALL


def test_runner_respects_budget_cap(tmp_path):
    """run_backtest detiene cuando cost_total >= budget_cap."""
    from backtest.runner import run_backtest
    from datetime import date as _date

    # Cache con 5 días de SPY (weekdays only)
    import json as _json
    for day_n in (4, 5, 6, 7, 8):  # 5 weekdays of may 2026
        (tmp_path / f"SPY_2026-05-{day_n:02d}.json").write_text(_json.dumps({
            "ticker": "SPY",
            "timestamp": f"2026-05-{day_n:02d}T09:30:00",
            "_raw": {"_qd_max-pain": {"maxPainStrikePrice": 500.0, "stockPrice": 500.0}},
        }))

    with patch("backtest.runner.backtest_call_with_cost_control") as mock_call:
        # Cada call cuesta $0.20. Cap $0.50 → 2-3 calls, luego STOP.
        mock_call.return_value = {
            "verdict": "WAIT", "decision": {}, "tier": "sonnet",
            "cost_usd": 0.20, "skipped": False, "haiku_meta": None,
        }
        result = run_backtest(
            tickers=["SPY"],
            start_date=_date(2026, 5, 4),
            end_date=_date(2026, 5, 8),
            auth_token="tok",
            sample_hours=[10],
            budget_cap_usd=0.50,
            output_dir=str(tmp_path / "out"),
            cache_dir=str(tmp_path),
        )
    assert result["budget_hit"] is True
    # Decisions producidas hasta el cap (3 a $0.20 c/u = $0.60 > $0.50; el 3ro
    # entra, el 4to no — porque check es ANTES de cada call):
    # cost_total partía en 0, entró call 1 ($0.20), call 2 ($0.40), call 3 ($0.60),
    # check call 4: 0.60 >= 0.50 → STOP. Así que 3 decisions.
    assert result["metrics"]["n_decisions"] == 3


def test_metrics_aggregator_verdicts_and_citations():
    from backtest.metrics_aggregator import aggregate
    decisions = [
        {"verdict": "WAIT", "cost_usd": 0.1,
         "decision": {"tacit_rules_applied": ["TR-Juan-001", "TR-Juan-043"]},
         "snapshot": {"gex_regime": "positive_high"}},
        {"verdict": "SELL_PUT", "cost_usd": 0.1,
         "decision": {"tacit_rules_applied": ["TR-Juan-043"]},
         "snapshot": {"gex_regime": "positive_high"}},
        {"verdict": "WAIT", "cost_usd": 0.001,
         "decision": {},
         "snapshot": {"gex_regime": "flip_zone"}},
    ]
    m = aggregate(decisions, requested_count=3)
    assert m["n_decisions"] == 3
    assert m["coverage_pct"] == 100.0
    assert m["verdicts"]["WAIT"] == 2
    assert m["verdicts"]["SELL_PUT"] == 1
    assert m["regimes"]["positive_high"] == 2
    assert m["rule_citations"]["TR-Juan-043"] == 2
    assert m["total_cost_usd"] == 0.201
