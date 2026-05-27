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


class Decision(BaseModel):
    """Decisión parseada y validada del LLM."""
    verdict: Literal["SELL_PUT", "SELL_CALL", "IRON_CONDOR_SEQUENTIAL",
                     "WAIT", "CLOSE_POSITIONS"]
    confidence: int = Field(ge=0, le=10)
    strikes: StrikesModel = Field(default_factory=StrikesModel)
    deltas: DeltasModel = Field(default_factory=DeltasModel)
    dte_target: int = Field(default=0, ge=0, le=7)
    main_reason: str
    tacit_rules_applied: List[str] = Field(default_factory=list)
    abort_triggers: List[str] = Field(default_factory=list)
    profit_target_pct: int = Field(default=50, ge=40, le=80)
    stop_loss_conditions: List[str] = Field(default_factory=list)
    similar_case_used: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    safety_overrides: List[str] = Field(default_factory=list)


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


def apply_safety_rails(decision_dict: dict, snapshot: MarketSnapshot) -> Decision:
    """
    Aplica safety rails sobre la decisión del LLM.
    Override agresivo si detecta condiciones de riesgo.

    Esto es CRÍTICO para paper trading - aprendemos qué overrides
    son necesarios viendo los logs.
    """
    overrides = []

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
    # RULE 5: DTE constraints (0-3 DTE only for Theta Harvest)
    # ─────────────────────────────────────────────────────
    if decision.verdict != "WAIT" and decision.dte_target > 4:
        overrides.append(f"DTE_TOO_HIGH_{decision.dte_target}")
        decision.dte_target = 1  # Force to 1 DTE
        decision.warnings.append("DTE forced to 1 - Theta Harvest requires 0-3 DTE")

    # ─────────────────────────────────────────────────────
    # RULE 6: Profit target sanity
    # Juan canonical range: 50-60% (TR-Juan-023, TR-Juan-035, Success_Metrics).
    # Si LLM emite fuera de [50, 60] -> clamp y log.
    # ─────────────────────────────────────────────────────
    if decision.profit_target_pct < 50 or decision.profit_target_pct > 60:
        overrides.append(f"PROFIT_TARGET_CLAMPED_{decision.profit_target_pct}")
        decision.profit_target_pct = max(50, min(60, decision.profit_target_pct))

    # ─────────────────────────────────────────────────────
    # RULE 7: Required strikes
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


def safe_decision_pipeline(raw_llm_output: str, snapshot: MarketSnapshot) -> Decision:
    """
    Pipeline completo: parse + validate + safety rails.
    Si algo falla, retorna WAIT seguro con razón explícita.
    """
    try:
        parsed = parse_llm_output(raw_llm_output)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse LLM output: {e}")
        return Decision(
            verdict="WAIT",
            confidence=0,
            main_reason=f"LLM output parsing failed: {str(e)[:150]}",
            safety_overrides=["PARSING_FAILED"]
        )

    return apply_safety_rails(parsed, snapshot)
