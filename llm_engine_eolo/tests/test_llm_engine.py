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
# below are version-specific — bump them on each KB version bump.
import re as _re
_kb_files = list((PROJECT_ROOT / "kb").glob("EOLO_ThetaHarvest_v*.xlsx"))
assert _kb_files, f"No KB Excel found in {PROJECT_ROOT / 'kb'}"
KB_PATH = str(sorted(_kb_files, key=lambda p: tuple(int(g) for g in _re.search(r"v(\d+)\.(\d+)", p.stem).groups()))[-1])

from llm_engine.kb_loader import KBLoader
from llm_engine.market_snapshot import MarketSnapshot

# Sprint 3 A.1: module-level KB instance so trace tests can pass kb_loader=kb.
kb = KBLoader(KB_PATH)
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
    """KB Excel v1.4 FULL (Sprint INTRADAY-THETA-PIVOT-FULL) carga OK."""
    kb = KBLoader(KB_PATH)
    stats = kb.stats()

    # v1.4 BUNDLED: 80 reglas (77 + 3 nuevas TR-Juan-078/079/080)
    assert stats["total_rules"] == 80, f"Expected 80 rules, got {stats['total_rules']}"
    # Cases: 6 SILVER + 3 GOLD (007, 008, 009 today) = 9
    assert stats["total_cases"] >= 9, f"Expected >=9 cases, got {stats['total_cases']}"

    tiers = stats["rules_by_tier"]
    assert tiers.get("AXIOMA", 0) == 4, f"AXIOMA count wrong: {tiers}"
    assert tiers.get("PROHIBITIVA", 0) == 6, f"PROHIBITIVA count wrong: {tiers}"
    assert tiers.get("MAESTRA", 0) == 13, f"MAESTRA count wrong: {tiers}"
    # +1 TR-Juan-079 BUNDLED → 9
    assert tiers.get("PROTOCOLO", 0) == 9, f"PROTOCOLO count wrong: {tiers}"
    # +2 TR-Juan-078, TR-Juan-080 → 25
    assert tiers.get("TACTICAL_PLUS", 0) == 25, f"TACTICAL_PLUS count wrong: {tiers}"
    assert tiers.get("TACTICAL", 0) == 23, f"TACTICAL count wrong: {tiers}"

    # Verificar que TR-019 a TR-022 existen (fix v1.0)
    for rule_num in [19, 20, 21, 22]:
        rule = kb.get_rule_by_id(f"TR-Juan-{rule_num:03d}")
        assert rule is not None, f"TR-Juan-{rule_num:03d} missing"

    # Verificar que R001-R014 fueron migradas (fix v1.1)
    for rule_num in range(48, 62):
        rule = kb.get_rule_by_id(f"TR-Juan-{rule_num:03d}")
        assert rule is not None, f"TR-Juan-{rule_num:03d} (migrated from R) missing"

    # v1.3: 10 nuevas reglas QD-aware
    for rule_num in range(62, 72):
        rule = kb.get_rule_by_id(f"TR-Juan-{rule_num:03d}")
        assert rule is not None, f"TR-Juan-{rule_num:03d} (v1.3 QD-aware) missing"

    # TR-Juan-042 ahora es MAESTRA (no AXIOMA)
    rule_042 = kb.get_rule_by_id("TR-Juan-042")
    assert rule_042 is not None
    assert rule_042.tier == "MAESTRA", f"TR-042 should be MAESTRA in v1.3, got {rule_042.tier}"

    print(f"✅ KB v1.3 loaded: {stats}")


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


def test_rule_evaluation_trace_populated_from_tacit_rules():
    """Sprint 3 A.1: trace decorates tacit_rules_applied with tier lookup + verdict AFFIRMED."""
    from llm_engine.decision_parser import safe_decision_pipeline, RuleEvaluation
    raw = '{"verdict": "SELL_PUT", "confidence": 8, "main_reason": "test", "tacit_rules_applied": ["TR-Juan-001"], "strikes": {"put_strike": 500.0}, "deltas": {"put_delta": 0.20}, "dte_target": 1, "profit_target_pct": 50}'
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    assert decision.rule_evaluation_trace, "trace should be populated"
    affirmed = [e for e in decision.rule_evaluation_trace if e.verdict == "AFFIRMED"]
    assert any(e.rule_id == "TR-Juan-001" for e in affirmed), f"TR-Juan-001 not in affirmed trace: {[e.rule_id for e in affirmed]}"


def test_rule_evaluation_trace_includes_safety_rails():
    """Sprint 3 A.1: low confidence triggers safety rail entry in trace with verdict BLOCKED."""
    from llm_engine.decision_parser import safe_decision_pipeline
    raw = '{"verdict": "SELL_PUT", "confidence": 3, "main_reason": "test low confidence", "tacit_rules_applied": [], "strikes": {"put_strike": 500.0}, "deltas": {"put_delta": 0.20}, "dte_target": 1, "profit_target_pct": 50}'
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    sr_entries = [e for e in decision.rule_evaluation_trace if e.source == "SAFETY_RAIL"]
    assert sr_entries, f"should have at least 1 safety rail entry, got: {decision.rule_evaluation_trace}"
    assert any(e.verdict == "BLOCKED" for e in sr_entries), "at least 1 safety rail should be BLOCKED"


def test_decision_path_narrative_summary():
    """Sprint 3 A.1: decision_path is human-readable string with verdict + rule counts."""
    from llm_engine.decision_parser import safe_decision_pipeline
    raw = '{"verdict": "WAIT", "confidence": 8, "main_reason": "test", "tacit_rules_applied": ["TR-Juan-001", "TR-Juan-002"], "strikes": {}, "deltas": {}, "dte_target": 0, "profit_target_pct": 50}'
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    assert decision.decision_path is not None
    assert "WAIT" in decision.decision_path
    assert "2 KB rule" in decision.decision_path


def test_llm_emitted_trace_passes_through():
    """Sprint 3 A.2: LLM emits structured trace with evidence; parser accepts."""
    from llm_engine.decision_parser import safe_decision_pipeline
    raw = (
        '{"verdict": "SELL_PUT", "confidence": 8, "main_reason": "test A.2 trace", '
        '"tacit_rules_applied": [], '
        '"strikes": {"put_strike": 500.0}, "deltas": {"put_delta": 0.20}, '
        '"dte_target": 1, "profit_target_pct": 55, '
        '"rule_evaluation_trace": [{'
        '"rule_id": "TR-Juan-012", "tier": "TACTICAL_PLUS", "verdict": "AFFIRMED", '
        '"confidence_impact": 2, "source": "LLM", '
        '"evidence": "vix_level=17.05 within stable band per snapshot"}]}'
    )
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    llm_entries = [e for e in decision.rule_evaluation_trace if e.source == "LLM"]
    assert any(e.rule_id == "TR-Juan-012" for e in llm_entries), (
        f"TR-Juan-012 LLM entry missing: {[(e.rule_id, e.source) for e in decision.rule_evaluation_trace]}"
    )
    tr012 = next(e for e in llm_entries if e.rule_id == "TR-Juan-012")
    assert tr012.evidence and "vix_level" in tr012.evidence
    assert tr012.confidence_impact == 2


def test_llm_trace_merged_with_safety_rails():
    """Sprint 3 A.2: low confidence still triggers SR-001 even with LLM trace present."""
    from llm_engine.decision_parser import safe_decision_pipeline
    raw = (
        '{"verdict": "SELL_PUT", "confidence": 3, "main_reason": "low conf with LLM trace", '
        '"tacit_rules_applied": [], '
        '"strikes": {"put_strike": 500.0}, "deltas": {"put_delta": 0.20}, '
        '"dte_target": 1, "profit_target_pct": 55, '
        '"rule_evaluation_trace": [{'
        '"rule_id": "TR-Juan-012", "tier": "TACTICAL_PLUS", "verdict": "NEUTRAL", '
        '"confidence_impact": 1, "source": "LLM", '
        '"evidence": "weak setup, low conviction"}]}'
    )
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    rule_ids = [e.rule_id for e in decision.rule_evaluation_trace]
    assert "TR-Juan-012" in rule_ids, f"LLM entry dropped: {rule_ids}"
    assert "SR-001" in rule_ids, f"SR-001 missing despite low confidence: {rule_ids}"
    sr_entry = next(e for e in decision.rule_evaluation_trace if e.rule_id == "SR-001")
    assert sr_entry.verdict == "BLOCKED"


def test_llm_trace_takes_precedence_over_derived():
    """Sprint 3 A.2: if LLM emits TR-Juan-X, derived A.1 fallback skips that rule."""
    from llm_engine.decision_parser import safe_decision_pipeline
    raw = (
        '{"verdict": "SELL_PUT", "confidence": 8, "main_reason": "precedence test", '
        '"tacit_rules_applied": ["TR-Juan-012"], '
        '"strikes": {"put_strike": 500.0}, "deltas": {"put_delta": 0.20}, '
        '"dte_target": 1, "profit_target_pct": 55, '
        '"rule_evaluation_trace": [{'
        '"rule_id": "TR-Juan-012", "tier": "TACTICAL_PLUS", "verdict": "AFFIRMED", '
        '"confidence_impact": 3, "source": "LLM", '
        '"evidence": "explicit LLM-authored entry should win over derived"}]}'
    )
    snapshot = make_test_snapshot()
    decision = safe_decision_pipeline(raw, snapshot, kb_loader=kb)
    tr012_entries = [e for e in decision.rule_evaluation_trace if e.rule_id == "TR-Juan-012"]
    assert len(tr012_entries) == 1, (
        f"Expected exactly 1 TR-Juan-012 entry, got {len(tr012_entries)}: "
        f"{[(e.source, e.evidence) for e in tr012_entries]}"
    )
    assert tr012_entries[0].source == "LLM"
    assert tr012_entries[0].evidence is not None


def test_market_snapshot_accepts_tier_s_extension_fields():
    """T1.A: schema accepts new Tier S fields without errors."""
    base = make_test_snapshot()
    qd_dict = base.model_dump()
    qd_dict.update({
        "vrp_value": 0.08,
        "vrp_score": "fair",
        "put_skew_25d": 0.045,
        "call_skew_25d": 0.025,
        "atm_iv": 0.18,
        "ts_iv_7d": 0.16,
        "ts_iv_30d": 0.18,
        "ts_iv_60d": 0.20,
        "term_slope_60d_7d": 0.04,
        "oi_max_call_strike": 760.0,
        "oi_max_put_strike": 750.0,
        "max_pain_trend_7d": 1.5,
        "gamma_regime_v2": "long",
        "gamma_zero_strike": 755.0,
    })
    snap = MarketSnapshot(**qd_dict)
    assert snap.vrp_value == 0.08
    assert snap.vrp_score == "fair"
    assert snap.gamma_regime_v2 == "long"
    prompt = snap.to_llm_format()
    assert "OPTIONS POSITIONING ADVANCED" in prompt
    assert "VRP: fair" in prompt
    assert "Gamma Regime: long" in prompt


def test_classify_gamma_regime():
    """T1.A: compute layer classifier."""
    from llm_engine.quantdata_features import classify_gamma_regime
    assert classify_gamma_regime(760.0, 750.0) == "long"  # 1.3% above
    assert classify_gamma_regime(745.0, 750.0) == "negative"  # 0.7% below
    assert classify_gamma_regime(751.0, 750.0) == "transition"  # within 0.5%
    assert classify_gamma_regime(None, 750.0) is None
    assert classify_gamma_regime(750.0, None) is None


def test_compute_vrp_score():
    """T1.A: compute layer VRP scoring."""
    from llm_engine.quantdata_features import compute_vrp_score
    assert compute_vrp_score(80.0) == "rich"
    assert compute_vrp_score(50.0) == "fair"
    assert compute_vrp_score(20.0) == "cheap"
    assert compute_vrp_score(None) is None


def test_compute_magnet_strength_high_confluence():
    """TERMINATOR Sub-B: magnet_strength TR-Juan-067 high pin probability."""
    from llm_engine.quantdata_features import compute_magnet_strength
    # spot 750.5, max_pain 750.0 → 0.067% distance → base 70
    # gex_zero 750.5 + oi_max_call 750.2 + oi_max_put 749.8 all within 0.5% of 750 → +30
    r = compute_magnet_strength(750.5, 750.0, 750.5, 750.2, 749.8)
    assert r["score"] == 100
    assert r["magnet_strike"] == 750.0
    assert r["components"]["max_pain_distance_pct"] < 0.3
    assert r["components"]["gamma_zero"] == "confluent"


def test_compute_magnet_strength_no_data():
    """Sub-B: graceful None when inputs missing."""
    from llm_engine.quantdata_features import compute_magnet_strength
    assert compute_magnet_strength(None, 750.0) is None
    assert compute_magnet_strength(750.0, None) is None
    assert compute_magnet_strength(0, 750.0) is None


def test_compute_magnet_strength_far_pain():
    """Sub-B: max_pain >1% away → base 10, no bonus."""
    from llm_engine.quantdata_features import compute_magnet_strength
    r = compute_magnet_strength(800.0, 750.0)
    assert r["score"] == 10
    assert r["components"]["max_pain_distance_pct"] > 1.0


def test_compute_cascade_risk_extreme():
    """TERMINATOR Sub-B: cascade_risk TR-Juan-064 extreme case."""
    from llm_engine.quantdata_features import compute_cascade_risk
    # spot 745, oi_max_put 750, ATR 8 → cascade_zone_top = 758, spot < zone → +50
    # negative GEX magnitude 3B → +20, IVR call 20 → +10, put_skew 6% → +10, VIX 25 → +10 = 100
    r = compute_cascade_risk(
        spot=745.0, oi_max_put_strike=750.0, atr_daily=8.0,
        gex_total=-3e9, iv_rank_call=20.0, put_skew_25d=6.0, vix_level=25.0,
    )
    assert r["risk_level"] == "extreme"
    assert r["score"] == 100
    assert any("cascade_zone_top" in d for d in r["drivers"])


def test_compute_cascade_risk_low():
    """Sub-B: cascade_risk safe regime."""
    from llm_engine.quantdata_features import compute_cascade_risk
    r = compute_cascade_risk(
        spot=800.0, oi_max_put_strike=750.0, atr_daily=5.0,
        gex_total=5e9, iv_rank_call=70.0, put_skew_25d=2.0, vix_level=15.0,
    )
    assert r["risk_level"] == "low"
    assert r["score"] == 0


def test_compute_smart_money_bias_bullish():
    """TERMINATOR Sub-B: smart_money_bias TR-Juan-066 bullish drift."""
    from llm_engine.quantdata_features import compute_smart_money_bias
    r = compute_smart_money_bias(
        net_call_premium_drift=10_000_000, net_put_premium_drift=2_000_000,
    )
    assert r["bias"] == "bullish"
    assert r["conviction"] > 0
    assert r["evidence"]["diff_usd"] == 8_000_000


def test_compute_smart_money_bias_neutral():
    """Sub-B: small drift → neutral."""
    from llm_engine.quantdata_features import compute_smart_money_bias
    r = compute_smart_money_bias(
        net_call_premium_drift=2_000_000, net_put_premium_drift=1_000_000,
    )
    assert r["bias"] == "neutral"


def test_compute_smart_money_bias_skew_amplifier():
    """Sub-B: TR-Juan-068 skew differential amplifies bullish conviction."""
    from llm_engine.quantdata_features import compute_smart_money_bias
    r = compute_smart_money_bias(
        net_call_premium_drift=10_000_000, net_put_premium_drift=2_000_000,
        put_skew_25d=8.0, call_skew_25d=4.0,  # diff = +4% > 2% → amplifier
    )
    assert r["bias"] == "bullish"
    assert "skew_amplifier" in r["evidence"]


def test_compute_smart_money_bias_no_data():
    """Sub-B: both drifts None → None."""
    from llm_engine.quantdata_features import compute_smart_money_bias
    assert compute_smart_money_bias(None, None) is None


def test_validate_rule_citations_valid():
    """Sprint ANTI-HALLUCINATION-FIX: citations existentes pasan sin overrides."""
    from llm_engine.decision_parser import validate_rule_citations_from_lists

    class StubKB:
        def get_all_rule_ids(self): return {"TR-Juan-001", "TR-Juan-072", "TR-Juan-077"}
        def get_all_case_ids(self): return {"CASE-Juan-001", "CASE-Juan-002"}

    overrides = validate_rule_citations_from_lists(
        rules_supporting=["TR-Juan-072", "TR-Juan-077"],
        rules_questioning=["TR-Juan-001"],
        kb_loader=StubKB(),
    )
    assert overrides == []


def test_validate_rule_citations_hallucinated():
    """Sprint ANTI-HALLUCINATION-FIX: rule_id inventado genera INVALID_RULE_CITATION."""
    from llm_engine.decision_parser import validate_rule_citations_from_lists

    class StubKB:
        def get_all_rule_ids(self): return {"TR-Juan-001"}
        def get_all_case_ids(self): return set()

    overrides = validate_rule_citations_from_lists(
        rules_supporting=["TR-Juan-001", "TR-Juan-002"],  # 002 inventado
        rules_questioning=["TR-Juan-9999"],                # inventado
        kb_loader=StubKB(),
    )
    assert "INVALID_RULE_CITATION_TR-Juan-002" in overrides
    assert "INVALID_RULE_CITATION_TR-Juan-9999" in overrides
    assert len(overrides) == 2


def test_validate_case_citations_hallucinated():
    """Sprint ANTI-HALLUCINATION-FIX: case_id inventado genera INVALID_CASE_CITATION."""
    from llm_engine.decision_parser import validate_rule_citations_from_lists

    class StubKB:
        def get_all_rule_ids(self): return set()
        def get_all_case_ids(self): return {"CASE-Juan-001"}

    overrides = validate_rule_citations_from_lists(
        rules_supporting=["CASE-Juan-001", "CASE-Juan-999"],
        rules_questioning=[],
        kb_loader=StubKB(),
    )
    assert "INVALID_CASE_CITATION_CASE-Juan-999" in overrides
    assert len(overrides) == 1


def test_validate_rule_citations_strips_descriptive_suffix():
    """Sprint ANTI-HALLUCINATION-FIX: tolera sufijos descriptivos (LLM citation style)."""
    from llm_engine.decision_parser import validate_rule_citations_from_lists

    class StubKB:
        def get_all_rule_ids(self): return {"TR-Juan-073"}
        def get_all_case_ids(self): return set()

    overrides = validate_rule_citations_from_lists(
        rules_supporting=["TR-Juan-073 (cushion call side absolute)"],
        rules_questioning=[],
        kb_loader=StubKB(),
    )
    assert overrides == []


def test_kbloader_get_all_rule_ids_real():
    """Sprint ANTI-HALLUCINATION-FIX: real KBLoader retorna set con rule_ids reales."""
    kb = KBLoader(KB_PATH)
    rule_ids = kb.get_all_rule_ids()
    case_ids = kb.get_all_case_ids()
    assert isinstance(rule_ids, set)
    assert "TR-Juan-001" in rule_ids
    assert "TR-Juan-072" in rule_ids
    assert "TR-Juan-077" in rule_ids
    assert len(rule_ids) >= 70


def test_build_juan_suggestion_prompt():
    """T11.A: prompt builder for /juan/suggest."""
    from llm_engine.prompt_builder import build_juan_suggestion_prompt, JUAN_SUGGESTION_SYSTEM_PROMPT
    snapshot = make_test_snapshot()
    snapshot_obj = MarketSnapshot(**snapshot.model_dump())
    system, user = build_juan_suggestion_prompt(
        snapshot_obj, "ENTRY", {"action": "SELL_PUT", "strike": 750}, "Setup A+",
    )
    assert "evaluador honesto" in system
    assert "JUAN PROPOSAL" in user
    assert "SELL_PUT" in user


def test_build_feedback_chat_prompt():
    """T11.A: prompt builder for /feedback/chat."""
    from llm_engine.prompt_builder import build_feedback_chat_prompt
    journal = {"date": "2026-06-02", "trades_count": 3, "total_pnl_dollars": 150, "win_rate": 0.67}
    messages = [{"role": "user", "content": "El trade de SPY..."}]
    system, user = build_feedback_chat_prompt({}, messages, journal)
    assert "feedback nocturno" in system
    assert "2026-06-02" in user
    assert "El trade de SPY" in user


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
    test_build_juan_suggestion_prompt()
    test_build_feedback_chat_prompt()
    print("\n🎉 All tests passed!")
