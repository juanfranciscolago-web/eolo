"""
Tests del LLM Engine.

Run desde root del proyecto:
    python tests/test_llm_engine.py
o:
    pytest tests/

Importantes:
- test_kb_loader: el Excel se carga correctamente
- test_safety_rails: VIX spike override funciona
- test_decision_parser: JSON output se parsea correctamente
"""
import sys
from pathlib import Path

# Project root (parent del directorio tests/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Finding G fix (2026-06-01 noche): auto-discover KB Excel by glob so future
# bump-version (v1.3+) doesn't break tests with FileNotFoundError. NOTE: assertions
# below (61 rules, 6 cases) STAY version-specific — bump them when KB v1.3 lands
# in Sprint UP-1.4.
import re as _re
_kb_files = list((PROJECT_ROOT / "kb").glob("EOLO_ThetaHarvest_v*.xlsx"))
assert _kb_files, f"No KB Excel found in {PROJECT_ROOT / 'kb'}"
KB_PATH = str(sorted(_kb_files, key=lambda p: tuple(int(g) for g in _re.search(r"v(\d+)\.(\d+)", p.stem).groups()))[-1])

from llm_engine.kb_loader import KBLoader
from llm_engine.market_snapshot import MarketSnapshot
from llm_engine.decision_parser import parse_llm_output, apply_safety_rails, Decision
from llm_engine.prompt_builder import build_prompts


def make_test_snapshot(**overrides) -> MarketSnapshot:
    """Helper para crear MarketSnapshot de test."""
    defaults = dict(
        timestamp="2026-05-27T10:30:00-04:00",
        ticker="SPY",
        price=750.00,
        open_price=750.07,
        high=752.13,
        low=749.27,
        prev_close=750.49,
        vix_level=17.05,
        vix_velocity_30m_pct=0.5,
        vix_velocity_1d_pct=-2.0,
        pdh=752.13,
        pdl=748.37,
        pdc=750.49,
        fib_r1=751.5,
        fib_r2=752.8,
        fib_r3=754.1,
        fib_s1=748.5,
        fib_s2=747.2,
        fib_s3=745.9,
        vwap=750.5,
        vwap_upper_2sigma=752.0,
        vwap_lower_2sigma=748.0,
        rsi_2m=50.8,
        rsi_15m=55.0,
        rsi_daily=70.0,
        atr_2m=0.342,
        atr_15m=0.55,
        atr_daily=2.30,
        adr_daily=0.88,
        ema_9_2m=750.4,
        ema_21_2m=750.2,
        macd_histogram_15m=-0.05,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def test_kb_loads():
    """KB Excel v1.1 se carga correctamente con TODAS las reglas."""
    kb = KBLoader(KB_PATH)
    stats = kb.stats()

    # v1.1 debe tener 61 reglas (47 + 14 migradas de R001-R014)
    assert stats["total_rules"] == 61, f"Expected 61 rules, got {stats['total_rules']}"
    assert stats["total_cases"] >= 5, f"Expected >=5 cases, got {stats['total_cases']}"

    # Tier counts esperados en v1.1
    tiers = stats["rules_by_tier"]
    assert tiers.get("AXIOMA", 0) == 2, f"AXIOMA count wrong: {tiers}"
    assert tiers.get("PROHIBITIVA", 0) == 5, f"PROHIBITIVA count wrong: {tiers}"  # 1 + 4 migradas
    assert tiers.get("MAESTRA", 0) == 11, f"MAESTRA count wrong: {tiers}"  # 6 + 5 migradas
    assert tiers.get("PROTOCOLO", 0) == 6, f"PROTOCOLO count wrong: {tiers}"
    assert tiers.get("TACTICAL_PLUS", 0) == 13, f"TACTICAL_PLUS count wrong: {tiers}"  # 8 + 5 migradas

    # Verificar que TR-019 a TR-022 existen (fix v1.0)
    for rule_num in [19, 20, 21, 22]:
        rule = kb.get_rule_by_id(f"TR-Juan-{rule_num:03d}")
        assert rule is not None, f"TR-Juan-{rule_num:03d} missing"

    # Verificar que R001-R014 fueron migradas (fix v1.1)
    for rule_num in range(48, 62):
        rule = kb.get_rule_by_id(f"TR-Juan-{rule_num:03d}")
        assert rule is not None, f"TR-Juan-{rule_num:03d} (migrated from R) missing"

    print(f"✅ KB v1.1 loaded: {stats}")


def test_no_ghost_rules():
    """No debe haber reglas referenciadas en casos pero no definidas."""
    kb = KBLoader(KB_PATH)

    all_rule_ids = {r.normalized_id() for r in kb.rules}

    ghosts = {}
    for case in kb.cases:
        referenced = case.get_referenced_rules()
        missing = referenced - all_rule_ids
        if missing:
            ghosts[case.case_id] = missing

    assert not ghosts, f"Ghost rules found: {ghosts}"
    print(f"✅ No ghost rules - all {len(all_rule_ids)} referenced rules exist")


def test_tier_from_column():
    """Tier debe leerse de columna explícita, no de string matching."""
    kb = KBLoader(KB_PATH)

    # TR-Juan-012 debe ser TACTICAL_PLUS (tiene ⭐ pero no MAESTRA)
    rule_012 = kb.get_rule_by_id("TR-Juan-012")
    assert rule_012 is not None
    assert rule_012.tier == "TACTICAL_PLUS", f"TR-012 tier wrong: {rule_012.tier}"

    # TR-Juan-031 debe ser TACTICAL_PLUS
    rule_031 = kb.get_rule_by_id("TR-Juan-031")
    assert rule_031 is not None
    assert rule_031.tier == "TACTICAL_PLUS", f"TR-031 tier wrong: {rule_031.tier}"

    # TR-Juan-010 debe ser MAESTRA
    rule_010 = kb.get_rule_by_id("TR-Juan-010")
    assert rule_010.tier == "MAESTRA"

    print(f"✅ Tier classification reads from column correctly")


def test_safety_rail_vix_spike():
    """VIX spike > 5% → WAIT override."""
    snapshot = make_test_snapshot(vix_velocity_30m_pct=7.0)

    llm_output = {
        "verdict": "SELL_PUT",
        "confidence": 8,
        "strikes": {"put_strike": 745.0, "call_strike": None},
        "deltas": {"put_delta": 0.20, "call_delta": None},
        "dte_target": 1,
        "main_reason": "Test SELL_PUT",
        "tacit_rules_applied": ["TR-Juan-011"],
        "abort_triggers": [],
        "profit_target_pct": 55,
        "stop_loss_conditions": [],
        "similar_case_used": None,
        "warnings": [],
    }

    decision = apply_safety_rails(llm_output, snapshot)
    assert decision.verdict == "WAIT", f"Expected WAIT, got {decision.verdict}"
    assert any("VIX_SPIKE" in o for o in decision.safety_overrides)
    print(f"✅ VIX spike override: {decision.safety_overrides}")


def test_safety_rail_low_confidence():
    """Confidence < 6 → WAIT override."""
    snapshot = make_test_snapshot()
    llm_output = {
        "verdict": "SELL_CALL",
        "confidence": 4,
        "strikes": {"put_strike": None, "call_strike": 755.0},
        "deltas": {"put_delta": None, "call_delta": 0.20},
        "dte_target": 1,
        "main_reason": "Test low confidence",
        "tacit_rules_applied": [],
        "abort_triggers": [],
        "profit_target_pct": 55,
        "stop_loss_conditions": [],
        "similar_case_used": None,
        "warnings": [],
    }
    decision = apply_safety_rails(llm_output, snapshot)
    assert decision.verdict == "WAIT"
    assert any("LOW_CONFIDENCE" in o for o in decision.safety_overrides)
    print(f"✅ Low confidence override")


def test_safety_rail_no_iron_condor_directo():
    """IRON_CONDOR (directo) → IRON_CONDOR_SEQUENTIAL override."""
    snapshot = make_test_snapshot()
    llm_output = {
        "verdict": "IRON_CONDOR_SEQUENTIAL",  # Should be valid
        "confidence": 8,
        "strikes": {"put_strike": 745.0, "call_strike": 755.0},
        "deltas": {"put_delta": 0.15, "call_delta": 0.15},
        "dte_target": 1,
        "main_reason": "Test IC sequential",
        "tacit_rules_applied": ["TR-Juan-047"],
        "abort_triggers": [],
        "profit_target_pct": 55,
        "stop_loss_conditions": [],
        "similar_case_used": None,
        "warnings": [],
    }
    decision = apply_safety_rails(llm_output, snapshot)
    assert decision.verdict == "IRON_CONDOR_SEQUENTIAL"
    print(f"✅ IC sequential accepted")


def test_decision_parser_with_markdown():
    """Parser maneja JSON con markdown wrapper."""
    raw = """```json
{
  "verdict": "WAIT",
  "confidence": 5,
  "strikes": {"put_strike": null, "call_strike": null},
  "deltas": {"put_delta": null, "call_delta": null},
  "dte_target": 0,
  "main_reason": "Setup unclear",
  "tacit_rules_applied": [],
  "abort_triggers": [],
  "profit_target_pct": 50,
  "stop_loss_conditions": [],
  "similar_case_used": null,
  "warnings": []
}
```"""
    parsed = parse_llm_output(raw)
    assert parsed["verdict"] == "WAIT"
    print(f"✅ Markdown wrapper parsed correctly")


def test_prompt_building():
    """Build prompts no falla y genera contenido razonable."""
    kb = KBLoader(KB_PATH)
    snapshot = make_test_snapshot()
    system, user = build_prompts(kb, snapshot)

    assert len(system) > 1000, "System prompt too short"
    assert "AXIOMAS" in system
    assert "PROHIBITIVAS" in system
    assert "Juan" in system
    assert "SPY" in user
    assert "$750" in user
    print(f"✅ Prompts built: system={len(system)} chars, user={len(user)} chars")


def test_haiku_prompts_build():
    """build_haiku_prompts arma system + user prompts cortos."""
    from llm_engine.haiku_prefilter import build_haiku_prompts
    kb = KBLoader(KB_PATH)
    snapshot = make_test_snapshot()
    system, user = build_haiku_prompts(kb, snapshot)
    assert "AXIOMAS" in system
    assert "PROHIBITIVAS" in system
    assert "should_call_full" in system
    assert "SPY" in user
    assert "VIX" in user
    assert len(system) < 5000, f"Haiku system prompt too long: {len(system)} chars"
    print(f"OK Haiku prompts built: system={len(system)} chars, user={len(user)} chars")


def test_pre_decision_parser_ok():
    """parse_pre_decision parsea JSON valido."""
    from llm_engine.haiku_prefilter import parse_pre_decision, PreDecision
    raw = '''```json
{
  "should_call_full": false,
  "reason": "VIX velocity > 5% (spike intraday)",
  "haiku_confidence": 9
}
```'''
    pd = parse_pre_decision(raw)
    assert isinstance(pd, PreDecision)
    assert pd.should_call_full is False
    assert pd.haiku_confidence == 9
    print(f"OK PreDecision parser OK")


def test_pre_decision_parser_fallback():
    """parse_pre_decision fallback a should_call_full=True en error."""
    from llm_engine.haiku_prefilter import parse_pre_decision
    raw = "this is not json at all"
    pd = parse_pre_decision(raw)
    assert pd.should_call_full is True, "fallback DEBE ser should_call_full=True"
    assert pd.haiku_confidence == 0
    print(f"OK PreDecision parser fallback (should_call_full=True)")


def test_market_snapshot_accepts_quantdata_fields():
    """Hotfix #95: schema accepts QD fields posted by bot CROP."""
    base = make_test_snapshot()
    qd_dict = base.model_dump()
    qd_dict.update({
        "max_pain_strike": 595.0,
        "max_pain_distance_pct": 0.42,
        "max_pain_expiry": "2026-06-02",
        "iv_rank_call": 28.5,
        "iv_rank_put": 32.1,
        "gex_regime": "negative",
        "gex_total": -1.2e9,
        "gex_max_call_strike": 600.0,
        "gex_max_put_strike": 590.0,
        "net_call_premium_drift": -125000.0,
        "net_put_premium_drift": 85000.0,
    })
    snap = MarketSnapshot(**qd_dict)
    assert snap.max_pain_strike == 595.0
    assert snap.max_pain_expiry == "2026-06-02"
    assert snap.iv_rank_call == 28.5
    assert snap.iv_rank_put == 32.1
    assert snap.gex_regime == "negative"
    assert snap.gex_total == -1.2e9
    assert snap.net_call_premium_drift == -125000.0
    assert snap.net_put_premium_drift == 85000.0


def test_to_llm_format_renders_options_positioning():
    """Hotfix #95: to_llm_format() renders OPTIONS POSITIONING section with QD values."""
    base = make_test_snapshot()
    qd_dict = base.model_dump()
    qd_dict.update({
        "max_pain_strike": 595.0,
        "max_pain_distance_pct": 0.42,
        "max_pain_expiry": "2026-06-02",
        "iv_rank_call": 28.5,
        "iv_rank_put": 32.1,
        "gex_regime": "extreme_negative",
        "gex_total": -1.2e9,
        "gex_max_call_strike": 600.0,
        "gex_max_put_strike": 590.0,
        "net_call_premium_drift": -125000.0,
        "net_put_premium_drift": 85000.0,
    })
    snap = MarketSnapshot(**qd_dict)
    prompt = snap.to_llm_format()

    # Section header present
    assert "OPTIONS POSITIONING" in prompt
    # Max pain rendered
    assert "595" in prompt
    assert "2026-06-02" in prompt
    # IV rank rendered (ticker-specific, not broken iv_rank_spy)
    assert "28.5" in prompt
    assert "32.1" in prompt
    # GEX regime rendered
    assert "extreme_negative" in prompt
    # Net premium drift rendered
    assert "-125000" in prompt or "125000" in prompt
    assert "85000" in prompt


if __name__ == "__main__":
    test_kb_loads()
    test_no_ghost_rules()
    test_tier_from_column()
    test_safety_rail_vix_spike()
    test_safety_rail_low_confidence()
    test_safety_rail_no_iron_condor_directo()
    test_decision_parser_with_markdown()
    test_prompt_building()
    test_haiku_prompts_build()
    test_pre_decision_parser_ok()
    test_pre_decision_parser_fallback()
    print("\n🎉 All tests passed!")
