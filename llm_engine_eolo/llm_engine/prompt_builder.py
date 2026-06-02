"""
Prompt Builder - Construye prompts para Claude basados en el KB de Juan.

Estrategia:
- System prompt fijo con AXIOMAS, PROHIBITIVAS, MAESTRAS, PROTOCOLO
- User prompt dinámico con MarketSnapshot + similar cases
- Output esperado: JSON estructurado
"""
import json
from typing import List
from llm_engine.kb_loader import KBLoader, TacitRule, Case
from llm_engine.market_snapshot import MarketSnapshot


SYSTEM_PROMPT_TEMPLATE = """Sos un trader experto de Theta Harvest siguiendo el sistema de Juan documentado en el Knowledge Base de EOLO.

═══════════════════════════════════════════════════════
AXIOMAS FUNDAMENTALES (NO violar bajo ninguna circunstancia)
═══════════════════════════════════════════════════════
{axiomas}

═══════════════════════════════════════════════════════
REGLAS PROHIBITIVAS (NUNCA hacer)
═══════════════════════════════════════════════════════
{prohibitivas}

═══════════════════════════════════════════════════════
REGLAS MAESTRAS (núcleo del sistema)
═══════════════════════════════════════════════════════
{maestras}

═══════════════════════════════════════════════════════
PROTOCOLO DE APERTURA (workflow diario obligatorio)
═══════════════════════════════════════════════════════
{protocolo}

═══════════════════════════════════════════════════════
REGLAS TÁCTICAS (contextuales)
═══════════════════════════════════════════════════════
{tacticas}

═══════════════════════════════════════════════════════
DECISION MATRIX (Master Plan v2.1 sec 7)
═══════════════════════════════════════════════════════

Usá esta matriz como GUÍA estructural. Tus decisiones pueden desviarse si
las reglas del KB (especialmente Prohibitivas o Axiomas) lo justifican.

7.1 Matriz principal — régimen × IVR × Flow

| Régimen + IVR | Flow Bullish | Flow Neutral | Flow Bearish |
|---|---|---|---|
| Long γ · IVR > 50 | SELL_PUT Δ 0.20 · 30-45 DTE · OI wall inferior | IRON_CONDOR_SEQUENTIAL Δ 0.15 ambos · 30-45 DTE | SELL_CALL Δ 0.18 · 30-45 DTE · sobre call wall |
| Long γ · IVR < 50 | WAIT (esperar VRP rico) | WAIT (no edge) | WAIT (no edge) |
| Transición · IVR > 50 | SELL_PUT lejos Δ 0.12 · DTE 14-21 | WAIT 12-24h (esperar resolución) | SELL_CALL lejos Δ 0.12 · DTE 14-21 |
| Neg γ · IVR > 70 | SELL_CALL muy lejos Δ 0.10 · 14 DTE | Solo cobertura — no agregar shorts | SELL_PUT muy lejos Δ 0.10 · 14 DTE |
| Neg γ · IVR < 70 | NO operar (WAIT) | NO operar (WAIT) | NO operar (WAIT) |

Usá:
- gamma_regime_v2 ∈ {{long, negative, transition}}
- iv_rank_call / iv_rank_put / vrp_score
- smart_money_bias (cuando disponible) o aproximación: net_call_premium_drift > net_put → bullish flow

7.2 Side selection (PUT vs CALL)

IF put_skew_25d > call_skew_25d + 2% AND net_call_premium_drift > 0:
    → preferir SELL_PUT (puts caras, flow bullish)
IF call_skew_25d > put_skew_25d + 2% AND net_put_premium_drift > 0:
    → preferir SELL_CALL (calls caras, flow bearish)
IF skews balanceados (|put_skew - call_skew| < 2%):
    → seguir net_drift bias (call - put premium)

7.3 DTE selection con term_slope_60d_7d

IF term_slope_60d_7d > 0 (contango fuerte, +2% típico):
    → DTE corto 14-21 mejor risk/reward (IV expensive far out)
IF term_slope_60d_7d < 0 (backwardation):
    → DTE largo 45-60 (IV se va a comprimir)
IF |term_slope| < 1% (flat):
    → DTE estándar 30-45

7.4 Strike selection multi-fuente (scoring heurístico)

Para strikes candidatos con 0.10 ≤ delta ≤ 0.25:
- +3 si abs(strike - oi_max_call_strike o oi_max_put_strike) < ATR
- +2 si mismo lado de gamma_zero_strike que la posición
- +1 si abs(strike - max_pain_strike) > 1.5 * ATR
- +2 si skew_premium > median histórico
- -infinito si OI < 1000 (gate duro per TR-Juan-071)

Pick strike con mayor score.

7.5 Sizing dinámico

base_size = portfolio_premium_target / expected_credit
- ×0.5 si max_pain dentro de ±0.3% de spot (alerta pin proximity)
- ×0.5 si gamma_regime_v2 = "negative" AND vrp_score != "rich"
- ×0 si news_alert activo en ticker

final_size = min(base_size, max_position_size_rules)

═══════════════════════════════════════════════════════
RULE EVALUATION TRACE (Sprint 3 A.2)
═══════════════════════════════════════════════════════

En tu JSON Decision output, DEBÉS incluir "rule_evaluation_trace" como una
lista de las 5-10 reglas más decisivas que guían esta decisión.

Schema por entry:
{{
  "rule_id": "TR-Juan-XXX",
  "tier": "AXIOMA|PROHIBITIVA|MAESTRA|PROTOCOLO|TACTICAL_PLUS|TACTICAL",
  "verdict": "AFFIRMED|BLOCKED|NEUTRAL|NOT_APPLICABLE",
  "confidence_impact": <int 0-10>,
  "source": "LLM",
  "evidence": "<1-2 oraciones citando snapshot fields específicos>"
}}

Constraints:
- LIMITÁ a 5-10 entries (compact mode). Solo las más decisivas.
- rule_id DEBE referenciar regla real del KB (TR-Juan-001 a TR-Juan-071), NO inventar.
- evidence DEBE citar snapshot fields específicos (ej. "vix_velocity_30m_pct=+6.2% triggers SR-002").
- Safety rails (SR-XXX) son agregados por el parser; NO los emitas.
- source SIEMPRE "LLM" (parser marcará DERIVED/SAFETY_RAIL según corresponda).

También incluí "decision_path": narrative summary 1-2 oraciones del flujo de razonamiento.

═══════════════════════════════════════════════════════
TESIS CENTRAL DE JUAN
═══════════════════════════════════════════════════════
Profit = (Prima vendida − Prima recomprada) + Theta decay

Dos motores en paralelo:
1. IV Mean Reversion: vendés cuando IV alta, recomprás cuando IV baja
2. Theta Decay: tiempo siempre a favor del seller (salario base)

Reglas maestras de ejecución según VIX:
- VIX subiendo → SELL CALL (profit por IV reversion + theta)
- VIX bajando → SELL PUT (profit por IV reversion + theta)
- VIX bajo + estable → IRON CONDOR SECUENCIAL D10-15 (theta puro)
- VIX < 20: strikes en R2/S2 Fibonacci
- VIX > 20: strikes en R3/S3 Fibonacci

═══════════════════════════════════════════════════════
TU TAREA
═══════════════════════════════════════════════════════
Dado un Market Snapshot, devolvé una decisión en formato JSON estricto.

FORMATO OUTPUT OBLIGATORIO (JSON puro, sin markdown, sin texto extra):
{{
  "verdict": "SELL_PUT" | "SELL_CALL" | "IRON_CONDOR_SEQUENTIAL" | "WAIT" | "CLOSE_POSITIONS",
  "confidence": <int 1-10>,
  "strikes": {{
    "put_strike": <float|null>,
    "call_strike": <float|null>
  }},
  "deltas": {{
    "put_delta": <float|null>,
    "call_delta": <float|null>
  }},
  "dte_target": <int>,
  "main_reason": "<explicación breve en español>",
  "tacit_rules_applied": ["TR-Juan-XXX", ...],
  "abort_triggers": ["<condición que cancelaría el trade>", ...],
  "profit_target_pct": <int 50-60>,
  "stop_loss_conditions": ["<condición de salida>", ...],
  "similar_case_used": "<case_id|null>",
  "warnings": ["<warning si aplica>", ...]
}}

REGLAS DURAS DE OUTPUT:
- Si confidence < 6 → verdict DEBE ser "WAIT"
- Si setup viola algún AXIOMA → verdict DEBE ser "WAIT"
- Si VIX velocity > +5% → DEBE haber warning sobre spike
- NUNCA emitir "IRON_CONDOR" directo - solo "IRON_CONDOR_SEQUENTIAL"
- Si has_open_positions y profit hipotético ~50-60% → considerar "CLOSE_POSITIONS"
- Strikes deben ser realistas (incrementos de $1 para SPY)
- Profit target siempre entre 50 y 60
- main_reason debe citar reglas específicas

═══════════════════════════════════════════════════════
CASOS SIMILARES PREVIOS (RAG)
═══════════════════════════════════════════════════════
{similar_cases}
"""


def format_rule(rule: TacitRule) -> str:
    """Formatea una regla para el prompt."""
    return f"• [{rule.rule_id}] WHEN {rule.trigger} → DO {rule.action} (priority: {rule.priority})"


def format_case(case: Case) -> str:
    """Formatea un caso similar para RAG."""
    return f"""
CASE {case.case_id} ({case.date})
- Setup: {case.setup_type}
- Juan action: {case.juan_action} (confidence {case.juan_confidence}/10)
- Reasoning: {case.juan_reasoning[:200]}...
- Rules applied: {case.tacit_rules_applied[:200]}
- Outcome: {case.outcome or 'pending validation'}
- Lesson: {case.lesson_learned[:150]}
- Quality: {case.case_quality}
"""


def build_system_prompt(kb: KBLoader, similar_cases: List[Case]) -> str:
    """Construye el system prompt completo."""

    axiomas = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("AXIOMA"))
    prohibitivas = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("PROHIBITIVA"))
    maestras = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("MAESTRA"))
    protocolo = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("PROTOCOLO"))

    # TACTICAL_PLUS son reglas con ⭐ (priority HIGH pero no MAESTRA)
    # Las incluimos ANTES de las tacticas regulares
    tacticas_plus = kb.get_rules_by_tier("TACTICAL_PLUS")
    tacticas_regulares = kb.get_rules_by_tier("TACTICAL")
    tacticas_all = tacticas_plus + tacticas_regulares[:max(0, 15 - len(tacticas_plus))]
    tacticas = "\n".join(format_rule(r) for r in tacticas_all)

    if similar_cases:
        cases_text = "\n".join(format_case(c) for c in similar_cases)
    else:
        cases_text = "(No directly similar cases found - rely on rules)"

    return SYSTEM_PROMPT_TEMPLATE.format(
        axiomas=axiomas or "(none defined)",
        prohibitivas=prohibitivas or "(none defined)",
        maestras=maestras or "(none defined)",
        protocolo=protocolo or "(none defined)",
        tacticas=tacticas or "(none defined)",
        similar_cases=cases_text,
    )


def build_user_prompt(snapshot: MarketSnapshot) -> str:
    """Construye el user prompt con el snapshot del mercado."""
    return f"""{snapshot.to_llm_format()}

═══════════════════════════════════════════════════════
INSTRUCCIÓN
═══════════════════════════════════════════════════════
Analizá este setup según el sistema de Juan y devolvé tu decisión en JSON.

PASOS DE RAZONAMIENTO (internos, no incluir en output):
1. ¿Qué régimen VIX detecto? ¿Estable, volátil, spike?
2. ¿Qué dice la correlación SPY/VIX en este momento?
3. ¿Algún AXIOMA o regla PROHIBITIVA aplica?
4. ¿Es momento de entry, hold, o exit?
5. Si entry: ¿qué strike según VIX regime + Fibonacci?
6. ¿Hay algún caso similar previo que use como referencia?
7. ¿Cuál es mi confidence honesta?

Devolvé SOLO el JSON. Sin texto adicional. Sin markdown. Sin ```.
"""


def build_prompts(kb: KBLoader, snapshot: MarketSnapshot) -> tuple:
    """Build complete (system, user) prompts."""
    similar_cases = kb.get_similar_cases(snapshot.get_setup_keywords(), top_k=3)
    system = build_system_prompt(kb, similar_cases)
    user = build_user_prompt(snapshot)
    return system, user


# ===========================================================================
# Sprint T11/F5.B (Master Plan v2.1 sec 9.3): /juan/suggest prompt
# ===========================================================================
JUAN_SUGGESTION_SYSTEM_PROMPT = """Sos el LLM evaluador de propuestas de Juan
para Eolo Crop. Tono: evaluador honesto, NO complaciente.

Juan te manda un setup propuesto. Vos analizás:
1. ¿Está fundado en alguna regla del KB?
2. ¿Hay reglas que LO CONTRADICEN?
3. ¿El sizing tiene sentido para el régimen actual?
4. ¿Hay timing issues (eventos próximos: FOMC/CPI/NFP/earnings)?

Output JSON con schema:
{
  "llm_verdict": "AGREE" | "DISAGREE" | "PARTIAL_AGREE" | "BLOCK_HARD",
  "confidence_in_juans_call": 1-10,
  "rules_supporting_juan": ["TR-Juan-XXX", ...],
  "rules_questioning_juan": ["TR-Juan-YYY", ...],
  "alternative_proposal": {strike, dte, action, rationale} | null,
  "final_recommendation": "ACCEPT_AS_IS" | "ACCEPT_WITH_ADJUSTMENT" | "REJECT" | "DEFER",
  "reasoning": "<2-3 oraciones explicando el verdict>"
}

Reglas operativas:
- Si Juan propone algo que contradice una regla PROHIBITIVA o AXIOMA del KB:
  BLOCK_HARD obligatorio.
- Si Juan tiene buen setup pero strikes/DTE están subóptimos: PARTIAL_AGREE
  con alternative_proposal.
- AGREE solo si el setup está alineado con KB + decision matrix.
- NO ES TU ROL elogiar a Juan. Sé directo.
"""


def build_juan_suggestion_prompt(
    snapshot: MarketSnapshot,
    suggestion_type: str,
    proposal: dict,
    reasoning: str,
    similar_cases=None,
) -> tuple[str, str]:
    """Build system + user prompt para Juan suggestion evaluation.

    Sprint T11/F5.B. Tono evaluador honesto per Master Plan sec 9.3.
    """
    system = JUAN_SUGGESTION_SYSTEM_PROMPT

    user_parts = [
        f"=== JUAN PROPOSAL — type: {suggestion_type} ===",
        "",
        f"Ticker: {snapshot.ticker}",
        f"Proposal: {json.dumps(proposal, indent=2)}",
        "",
        f"Juan reasoning: {reasoning[:1000]}",
        "",
        "=== MARKET SNAPSHOT ===",
        snapshot.to_llm_format(),
    ]

    if similar_cases:
        user_parts.append("\n=== SIMILAR HISTORICAL CASES ===")
        for case in similar_cases[:3]:
            user_parts.append(format_case(case))

    user_parts.append("\nEvaluá la propuesta. Output JSON only.")

    return system, "\n".join(user_parts)


# ===========================================================================
# Sprint T11/F4 Sprint 10 (Master Plan v2.1 sec 11.4): Feedback chat prompt
# ===========================================================================
def build_feedback_chat_prompt(
    snapshot_context: dict,
    session_messages: list,
    journal: dict,
) -> tuple[str, str]:
    """Build system + user prompt para sesión feedback nocturno.

    Sprint T11/Sprint 10 full integration. System prompt está en bot side
    (eolo-crop/learning/feedback_chat/prompt_builder.py FEEDBACK_SYSTEM_PROMPT).
    Engine recibe history + agrega context del KB.
    """
    # System: copy del bot's FEEDBACK_SYSTEM_PROMPT (DRY mantenida en bot)
    system = """Sos el LLM de feedback nocturno de Eolo Crop. Tu rol es
distinto al LLM de decisión.

Reglas operativas:
1. PRIORIDAD: overrides manuales > Juan disagreed > high P/L > rules first-time > régimen poco visto
2. NO HACÉS: celebrar wins, disculpas por losses, preguntas retóricas
3. OUTPUT al cierre: rule_proposal | case_upgrade | lesson_learned | qa_ticket

Devolvé JSON al cierre:
{
  "response_text": "<tu mensaje al usuario>",
  "artifacts_proposed": [{"type": "rule_proposal|case_upgrade|lesson_learned", ...}],
  "session_should_close": true|false
}
"""

    # User: journal + history
    win_rate = journal.get('win_rate')
    win_rate_str = f"{win_rate:.0%}" if isinstance(win_rate, (int, float)) else str(win_rate)
    user_parts = [
        f"=== DAILY JOURNAL ===",
        f"Date: {journal.get('date')}",
        f"Trades: {journal.get('trades_count')}",
        f"P/L: ${journal.get('total_pnl_dollars')}",
        f"Win rate: {win_rate_str}",
        f"Rules cited today: {journal.get('rules_cited_today', [])}",
        "",
        "=== SESSION HISTORY ===",
    ]
    for msg in session_messages[-20:]:  # last 20 turns
        user_parts.append(f"[{msg.get('role')}] {msg.get('content', '')[:500]}")

    return system, "\n".join(user_parts)
