"""
Prompt Builder - Construye prompts para Claude basados en el KB de Juan.

Estrategia:
- System prompt fijo con AXIOMAS, PROHIBITIVAS, MAESTRAS, PROTOCOLO
- User prompt dinámico con MarketSnapshot + similar cases
- Output esperado: JSON estructurado
"""
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
