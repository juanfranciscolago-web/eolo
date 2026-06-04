"""
Decision Parser - Parsea y valida el output JSON del LLM.

Aplica safety rails:
- Confidence threshold
- VIX spike override
- Strike sanity checks
- Iron Condor rules
"""
import json
import re
import logging
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Literal
from llm_engine.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)


class StrikesModel(BaseModel):
    put_strike: Optional[float] = None
    call_strike: Optional[float] = None


class DeltasModel(BaseModel):
    put_delta: Optional[float] = None
    call_delta: Optional[float] = None


class RuleEvaluation(BaseModel):
    """Structured trace entry per rule evaluated in a Decision.

    Sprint 3 A.1 (2026-06-02): schema-only. Trace populated by
    build_rule_evaluation_trace() from existing tacit_rules_applied +
    safety_overrides — no LLM prompt change yet.
    Sprint 3 A.2 (later): LLM emits enriched trace with evidence per rule directly.
    """
    rule_id: str
    tier: Optional[str] = None
    verdict: Literal["AFFIRMED", "BLOCKED", "NEUTRAL", "NOT_APPLICABLE"]
    confidence_impact: Optional[int] = Field(default=None, ge=0, le=10)
    source: Literal["LLM", "SAFETY_RAIL", "DERIVED"]
    evidence: Optional[str] = None


class Decision(BaseModel):
    """Decisión parseada y validada del LLM."""
    verdict: Literal["SELL_PUT", "SELL_CALL", "IRON_CONDOR_SEQUENTIAL",
                     "WAIT", "CLOSE_POSITIONS"]
    confidence: int = Field(ge=0, le=10)
    strikes: StrikesModel = Field(default_factory=StrikesModel)
    deltas: DeltasModel = Field(default_factory=DeltasModel)
    dte_target: int = Field(default=0, ge=0, le=4)
    main_reason: str
    tacit_rules_applied: List[str] = Field(default_factory=list)
    abort_triggers: List[str] = Field(default_factory=list)
    profit_target_pct: int = Field(default=50, ge=40, le=80)
    stop_loss_conditions: List[str] = Field(default_factory=list)
    similar_case_used: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    safety_overrides: List[str] = Field(default_factory=list)

    # === Sprint 3 (W1 A.1, 2026-06-02): structured rule trace ===
    rule_evaluation_trace: List[RuleEvaluation] = Field(default_factory=list)
    decision_path: Optional[str] = None


def parse_llm_output(raw_output: str) -> dict:
    """
    Parsea el JSON output del LLM. Maneja markdown wrappers
    y otros artefactos comunes.
    """
    cleaned = raw_output.strip()

    # Quitar markdown code fences
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    # Encontrar el primer { y el ultimo }
    start = cleaned.find('{')
    end = cleaned.rfind('}')

    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in output: {raw_output[:200]}")

    json_str = cleaned[start:end+1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        logger.error(f"Raw output: {raw_output}")
        raise


def _sanitize_llm_trace_entries(decision_dict: dict) -> None:
    """Sprint 3 A.2: drop malformed rule_evaluation_trace entries pre-validation.

    Mutates decision_dict in place. Invalid entries are logged + skipped so
    one bad LLM entry does not poison the whole Decision (would otherwise
    cascade to WAIT fallback).
    """
    raw = decision_dict.get("rule_evaluation_trace")
    if not raw or not isinstance(raw, list):
        return
    cleaned: list = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            RuleEvaluation(**entry)
        except Exception as e:
            logger.warning(f"Dropping invalid LLM trace entry pre-validation: {entry} — {e}")
            continue
        cleaned.append(entry)
    decision_dict["rule_evaluation_trace"] = cleaned


def apply_safety_rails(decision_dict: dict, snapshot: MarketSnapshot) -> Decision:
    """
    Aplica safety rails sobre la decisión del LLM.
    Override agresivo si detecta condiciones de riesgo.

    Esto es CRÍTICO para paper trading - aprendemos qué overrides
    son necesarios viendo los logs.
    """
    overrides = []

    _sanitize_llm_trace_entries(decision_dict)

    # Crear Decision con safe defaults si parsing falla
    try:
        decision = Decision(**decision_dict)
    except Exception as e:
        logger.error(f"Decision validation failed: {e}")
        return Decision(
            verdict="WAIT",
            confidence=0,
            main_reason=f"Decision validation failed: {str(e)[:200]}",
            safety_overrides=["INVALID_LLM_OUTPUT"]
        )

    # ─────────────────────────────────────────────────────
    # RULE 1: Confidence threshold
    # ─────────────────────────────────────────────────────
    if decision.confidence < 6 and decision.verdict not in ["WAIT", "CLOSE_POSITIONS"]:
        overrides.append(f"LOW_CONFIDENCE_{decision.confidence}")
        decision.verdict = "WAIT"

    # ─────────────────────────────────────────────────────
    # RULE 2: VIX spike override
    # ─────────────────────────────────────────────────────
    if abs(snapshot.vix_velocity_30m_pct) > 5:
        if decision.verdict not in ["WAIT", "CLOSE_POSITIONS"]:
            overrides.append(f"VIX_SPIKE_{snapshot.vix_velocity_30m_pct:+.1f}%")
            decision.verdict = "WAIT"
            decision.warnings.append(
                f"VIX velocity {snapshot.vix_velocity_30m_pct:+.1f}% triggered safety override"
            )

    # ─────────────────────────────────────────────────────
    # RULE 3: Iron Condor must be SEQUENTIAL (TR-Juan-047)
    # ─────────────────────────────────────────────────────
    if decision.verdict == "IRON_CONDOR":  # type: ignore
        overrides.append("IC_DIRECTO_PROHIBIDO")
        decision.verdict = "IRON_CONDOR_SEQUENTIAL"

    # ─────────────────────────────────────────────────────
    # RULE 4: Strike sanity for SPY (warning si > 5% OTM)
    # NOTA: 5% es el threshold elegido por Juan tras analisis.
    # Strikes >5% OTM en 0-1 DTE tipicamente tienen prima muy baja
    # y/o son setups defensivos (no nuestro estilo de Theta Harvest).
    # ─────────────────────────────────────────────────────
    if decision.verdict in ["SELL_PUT", "IRON_CONDOR_SEQUENTIAL"]:
        if decision.strikes.put_strike:
            put_otm_pct = (snapshot.price - decision.strikes.put_strike) / snapshot.price * 100
            if put_otm_pct > 5:
                overrides.append(f"PUT_TOO_FAR_OTM_{put_otm_pct:.1f}%")
                decision.warnings.append(f"PUT strike {put_otm_pct:.1f}% OTM seems excessive")

    if decision.verdict in ["SELL_CALL", "IRON_CONDOR_SEQUENTIAL"]:
        if decision.strikes.call_strike:
            call_otm_pct = (decision.strikes.call_strike - snapshot.price) / snapshot.price * 100
            if call_otm_pct > 5:
                overrides.append(f"CALL_TOO_FAR_OTM_{call_otm_pct:.1f}%")
                decision.warnings.append(f"CALL strike {call_otm_pct:.1f}% OTM seems excessive")

    # ─────────────────────────────────────────────────────
    # RULE 5: Profit target sanity
    # Juan canonical range: 50-60% (TR-Juan-023, TR-Juan-035, Success_Metrics).
    # Si LLM emite fuera de [50, 60] -> clamp y log.
    # ─────────────────────────────────────────────────────
    if decision.profit_target_pct < 50 or decision.profit_target_pct > 60:
        overrides.append(f"PROFIT_TARGET_CLAMPED_{decision.profit_target_pct}")
        decision.profit_target_pct = max(50, min(60, decision.profit_target_pct))

    # ─────────────────────────────────────────────────────
    # RULE 6: Required strikes
    # ─────────────────────────────────────────────────────
    if decision.verdict == "SELL_PUT" and not decision.strikes.put_strike:
        overrides.append("MISSING_PUT_STRIKE")
        decision.verdict = "WAIT"
        decision.warnings.append("Missing PUT strike - aborted")

    if decision.verdict == "SELL_CALL" and not decision.strikes.call_strike:
        overrides.append("MISSING_CALL_STRIKE")
        decision.verdict = "WAIT"
        decision.warnings.append("Missing CALL strike - aborted")

    if decision.verdict == "IRON_CONDOR_SEQUENTIAL":
        if not decision.strikes.put_strike or not decision.strikes.call_strike:
            overrides.append("IC_MISSING_STRIKES")
            decision.verdict = "WAIT"
            decision.warnings.append("Iron Condor missing strikes - aborted")

    decision.safety_overrides = overrides
    return decision


# Sprint 3 A.1: prefix-based mapping. Los códigos reales emitidos por
# apply_safety_rails son UPPER_CASE con data appended (ej. LOW_CONFIDENCE_3,
# VIX_SPIKE_+5.5%, PUT_TOO_FAR_OTM_5.5%). Matcheo por prefix para visibilizar
# los rails en el trace estructurado.
_SAFETY_RAIL_PREFIX_TO_RULE_ID = [
    ("LOW_CONFIDENCE", "SR-001"),
    ("VIX_SPIKE", "SR-002"),
    ("IC_DIRECTO_PROHIBIDO", "SR-003"),
    ("PUT_TOO_FAR_OTM", "SR-004"),
    ("CALL_TOO_FAR_OTM", "SR-004"),
    ("PROFIT_TARGET_CLAMPED", "SR-006"),
    ("MISSING_PUT_STRIKE", "SR-007"),
    ("MISSING_CALL_STRIKE", "SR-007"),
    ("IC_MISSING_STRIKES", "SR-007"),
    ("PARSING_FAILED", "SR-PARSE-FAIL"),
    ("INVALID_LLM_OUTPUT", "SR-VALIDATION-FAIL"),
]

# NEUTRAL = solo warning (no override hard ni clamp). BLOCKED = forced verdict
# change o clamp. NOTE: clamps (DTE / PROFIT_TARGET) técnicamente MODIFICAN
# pero el enum actual no tiene "MODIFIED" — BLOCKED es el label más cercano.
# Tech debt menor para Sprint 3 A.2: considerar agregar "MODIFIED" al Literal.
_SR_NEUTRAL_PREFIXES = ("PUT_TOO_FAR_OTM", "CALL_TOO_FAR_OTM")


def build_rule_evaluation_trace(
    decision_dict: dict,
    kb_loader=None,
) -> List[RuleEvaluation]:
    """Sprint 3 A.2: accept LLM-emitted trace + merge derived + safety rails.

    Layering:
      1. LLM-authored entries (source="LLM" with evidence) pass through if valid.
      2. tacit_rules_applied not already in LLM trace are appended as source="DERIVED".
      3. Safety rails (source="SAFETY_RAIL") always appended by parser.
    """
    trace: List[RuleEvaluation] = []
    seen_rule_ids = set()

    # 1. LLM-emitted entries (A.2): pass through if valid.
    llm_emitted = decision_dict.get("rule_evaluation_trace", [])
    for entry in llm_emitted:
        if not isinstance(entry, dict):
            continue
        # Accept entries where LLM did not explicitly tag source, defaulting to "LLM".
        entry_dict = dict(entry)
        entry_dict.setdefault("source", "LLM")
        if entry_dict.get("source") != "LLM":
            continue
        try:
            re_obj = RuleEvaluation(**entry_dict)
        except Exception as e:
            logger.warning(f"LLM emitted invalid trace entry: {entry} — {e}")
            continue
        trace.append(re_obj)
        seen_rule_ids.add(re_obj.rule_id)

    # 2. Fallback A.1 derivation for tacit_rules_applied not yet in LLM trace.
    rules_by_id = {}
    if kb_loader is not None and hasattr(kb_loader, "tacit_rules"):
        rules_by_id = {r.rule_id: r for r in kb_loader.tacit_rules}
    for rule_id in decision_dict.get("tacit_rules_applied", []):
        if rule_id in seen_rule_ids:
            continue
        rule_meta = rules_by_id.get(rule_id)
        trace.append(RuleEvaluation(
            rule_id=rule_id,
            tier=getattr(rule_meta, "tier", None) if rule_meta else None,
            verdict="AFFIRMED",
            source="DERIVED",
        ))
        seen_rule_ids.add(rule_id)

    # 3. Safety rail entries (prefix-based mapping to real apply_safety_rails codes).
    # BLOCKED = forced verdict change or clamp. NEUTRAL = warning only.
    for code in decision_dict.get("safety_overrides", []):
        rule_id = next(
            (rid for prefix, rid in _SAFETY_RAIL_PREFIX_TO_RULE_ID if code.startswith(prefix)),
            f"SR-UNKNOWN-{code}",
        )
        verdict = "NEUTRAL" if any(code.startswith(p) for p in _SR_NEUTRAL_PREFIXES) else "BLOCKED"
        trace.append(RuleEvaluation(
            rule_id=rule_id,
            tier="AXIOMA",
            verdict=verdict,
            source="SAFETY_RAIL",
        ))

    return trace


def build_decision_path(decision_dict: dict) -> str:
    """Sprint 3 A.1: minimal narrative summary of decision flow."""
    verdict = decision_dict.get("verdict", "UNKNOWN")
    confidence = decision_dict.get("confidence", 0)
    n_rules = len(decision_dict.get("tacit_rules_applied", []))
    n_safety = len(decision_dict.get("safety_overrides", []))
    parts = [f"Verdict {verdict} (confidence {confidence}/10)"]
    if n_rules:
        parts.append(f"based on {n_rules} KB rule(s) applied")
    if n_safety:
        parts.append(f"with {n_safety} safety rail(s) triggered")
    return ", ".join(parts)


def safe_decision_pipeline(raw_llm_output: str, snapshot: MarketSnapshot, kb_loader=None) -> Decision:
    """
    Pipeline completo: parse + validate + safety rails.
    Si algo falla, retorna WAIT seguro con razón explícita.
    """
    try:
        parsed = parse_llm_output(raw_llm_output)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse LLM output: {e}")
        fail_dict = {
            "verdict": "WAIT",
            "confidence": 0,
            "main_reason": f"LLM output parsing failed: {str(e)[:150]}",
            "safety_overrides": ["PARSING_FAILED"],
        }
        fail_dict["rule_evaluation_trace"] = [
            r.model_dump() for r in build_rule_evaluation_trace(fail_dict, kb_loader)
        ]
        fail_dict["decision_path"] = build_decision_path(fail_dict)
        return Decision(**fail_dict)

    decision = apply_safety_rails(parsed, snapshot)

    # Sprint 3 A.1: populate trace + decision_path post-rails (additive).
    decision_dict = decision.model_dump()
    decision_dict["rule_evaluation_trace"] = [
        r.model_dump() for r in build_rule_evaluation_trace(decision_dict, kb_loader)
    ]
    decision_dict["decision_path"] = build_decision_path(decision_dict)
    return Decision(**decision_dict)
