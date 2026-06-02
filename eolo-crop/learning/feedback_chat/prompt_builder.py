"""Dedicated LLM prompt para chat feedback nocturno.

Master Plan v2.1 sec 11.4 — protocolo del LLM dedicado distinto al de /decide.
"""


FEEDBACK_SYSTEM_PROMPT = """Sos el LLM de feedback nocturno de Eolo Crop. Tu rol es
distinto al LLM de decisión.

Reglas operativas:

1. PRIORIDAD de trades a revisar:
   (a) Overrides manuales del LLM (Juan cerró antes que el sistema)
   (b) Juan suggested + LLM disagreed
   (c) Trades con mayor P/L absoluto
   (d) Primera vez que dispara una regla TR en 30+ días
   (e) Trades del régimen menos visto este mes

2. Preguntas estructuradas:
   - Validación del setup (¿la regla aplicada era correcta?)
   - Evaluación de timing (¿entry/exit fueron óptimos?)
   - Evaluación de sizing (¿el size capturó el edge?)
   - Detección de patterns no capturados en KB

3. NO HACÉS:
   - Celebrar wins
   - Pedir disculpas por losses
   - Preguntas retóricas
   - Resumir el journal (no repetir, sumar perspectiva)

4. OUTPUT siempre estructurado al cierre de sesión:
   - Propuestas de reglas nuevas o modificadas
   - Casos a upgrade a GOLD
   - Lessons learned para el KB
   - Tickets de QA si hay bugs detectados

5. Si Juan adjunta gráfico, leelo con vision y citá específicamente lo que ves.
"""


def build_feedback_user_prompt(trades_today: list[dict], journal: dict) -> str:
    """Build user prompt for first message of feedback session."""
    if not trades_today:
        return f"""No hubo trades hoy ({journal['date']}).

Journal summary: {journal['summary']}

¿Querés revisar algún setup que el sistema descartó? ¿O reglas que NO se chequearon en 30+ días ({len(journal['rules_unused_30d'])} pending)?"""

    # Prioritize overrides + Juan-suggested + high abs PNL
    sorted_trades = sorted(
        trades_today,
        key=lambda t: (
            t.get("manual_override", False),
            t.get("juan_suggested", False),
            abs(t.get("pnl_dollars", 0)),
        ),
        reverse=True,
    )
    top = sorted_trades[:3]
    lines = [
        f"Tenemos {len(trades_today)} trades para revisar de hoy ({journal['date']}).",
        f"P/L total: ${journal['total_pnl_dollars']:.0f} · Win rate: {journal['win_rate']:.0%}",
        "",
        "Top 3 por prioridad de review:",
    ]
    for i, t in enumerate(top, 1):
        flags = []
        if t.get("manual_override"):
            flags.append("MANUAL_OVERRIDE")
        if t.get("juan_suggested"):
            flags.append("JUAN_SUGGESTED")
        flags_str = f" [{','.join(flags)}]" if flags else ""
        lines.append(
            f"{i}. {t.get('trade_id', '?')} {t.get('ticker', '?')} "
            f"P/L ${t.get('pnl_dollars', 0):.0f}{flags_str} "
            f"Rules: {','.join(t.get('rules_applied', []))[:80]}"
        )
    lines.append("")
    lines.append("¿Empezamos por el más controvertido o por orden cronológico?")
    return "\n".join(lines)
