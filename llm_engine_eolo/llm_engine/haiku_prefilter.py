"""
Haiku 4.5 Prefilter - Layered approach v0.2.

Decide si vale la pena llamar a Sonnet 4.6 para una decision detallada.
Reduce ~60-70% de calls a Sonnet (los WAIT obvios).

Modelo: claude-haiku-4-5-20251001
Cost: ~$0.003 por call (vs $0.02 de Sonnet)
Latency: ~1-2s
"""
import json
import re
import logging
from pydantic import BaseModel, Field

from llm_engine.kb_loader import KBLoader
from llm_engine.market_snapshot import MarketSnapshot
from llm_engine.prompt_builder import format_rule

logger = logging.getLogger(__name__)


class PreDecision(BaseModel):
    should_call_full: bool
    reason: str
    haiku_confidence: int = Field(ge=0, le=10)


HAIKU_SYSTEM_PROMPT_TEMPLATE = """Sos un pre-filtro del sistema EOLO Theta Harvest.
Tu unica tarea: decidir si vale la pena llamar al modelo grande (Sonnet) para
un analisis detallado, o si la respuesta obvia es NO_TRADE.

AXIOMAS (no violar):
{axiomas}

REGLAS PROHIBITIVAS (NO_TRADE inmediato si aplica):
{prohibitivas}

CRITERIOS DE NO_TRADE OBVIO:
- VIX velocity 30m > +5% (spike intraday)
- Macro event en <=1 dia (FOMC/CPI/NFP)
- Fuera de ventana 9:30-12:00 ET para entries (solo evaluar exits)
- Confidence baja (<6) por contexto ambiguo
- Alguna regla PROHIBITIVA aplica

Si NINGUNO aplica claramente, dejar should_call_full=True (que Sonnet decida).
Si CUALQUIERA aplica claramente, should_call_full=False.

OUTPUT JSON ESTRICTO:
{{
  "should_call_full": true|false,
  "reason": "<explicacion breve en espanol, max 150 chars>",
  "haiku_confidence": <int 1-10 sobre tu propia certeza>
}}
"""


def build_haiku_prompts(kb: KBLoader, snapshot: MarketSnapshot) -> tuple[str, str]:
    """Build (system, user) prompts cortos para Haiku."""
    axiomas = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("AXIOMA"))
    prohibitivas = "\n".join(format_rule(r) for r in kb.get_rules_by_tier("PROHIBITIVA"))

    system = HAIKU_SYSTEM_PROMPT_TEMPLATE.format(
        axiomas=axiomas or "(none)",
        prohibitivas=prohibitivas or "(none)",
    )

    pos_line = (
        f"Positions summary: {snapshot.open_positions_summary}"
        if snapshot.has_open_positions else ""
    )

    user = f"""MARKET SNAPSHOT — {snapshot.ticker} @ {snapshot.timestamp}
Session: {snapshot.session_phase}
Price: ${snapshot.price:.2f} (open ${snapshot.open_price:.2f}, prev close ${snapshot.prev_close:.2f})
VIX: {snapshot.vix_level:.2f} (velocity 30m: {snapshot.vix_velocity_30m_pct:+.2f}%, 1d: {snapshot.vix_velocity_1d_pct:+.2f}%)
VIX regime: {snapshot._classify_vix_regime()}
RSI 2m: {snapshot.rsi_2m:.1f}
Open positions: {snapshot.has_open_positions}
{pos_line}
Days to FOMC: {snapshot.days_to_next_fomc or 'N/A'}, CPI: {snapshot.days_to_next_cpi or 'N/A'}, NFP: {snapshot.days_to_next_nfp or 'N/A'}

Decide should_call_full. Output SOLO JSON.
"""
    return system, user


def parse_pre_decision(raw_output: str) -> PreDecision:
    """Parse Haiku output. Fallback a should_call_full=True si parsing falla."""
    cleaned = raw_output.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start == -1 or end == -1:
        logger.error(f"No JSON in Haiku output: {raw_output[:200]}")
        return PreDecision(
            should_call_full=True,
            reason="haiku_parse_failed",
            haiku_confidence=0,
        )

    try:
        parsed = json.loads(cleaned[start:end + 1])
        return PreDecision(**parsed)
    except Exception as e:
        logger.error(f"PreDecision parse error: {e}")
        return PreDecision(
            should_call_full=True,
            reason=f"haiku_parse_error: {str(e)[:80]}",
            haiku_confidence=0,
        )
