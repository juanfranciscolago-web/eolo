# Sprint 3 A.2 — Design Doc

**Status:** Designed 2026-06-02 morning. Implementation queued post-validation #77.
**Predecessor:** Sprint 3 A.1 (commit 80b12cf) — schema + derived trace shipped.
**Deploy gate:** validation #77 + #95 runtime confirmation today 9:30 ET.

## Objetivo

LLM emite trace enriquecida con `evidence` per rule. Layered on top de A.1
(que sigue funcionando como fallback derivation).

## Diff vs A.1

| A.1 (shipped 2026-06-02) | A.2 (this design) |
|---|---|
| LLM emite `tacit_rules_applied: List[str]` (current behavior) | LLM emite `rule_evaluation_trace: List[RuleEvaluation]` con evidence |
| Parser DECORA con tier + verdict inferred | Parser ACCEPTS LLM-authored + MERGES con safety rails |
| `confidence_impact = None`, `evidence = None` | Both populated by LLM |
| `source = "LLM"` (semantic incorrect — was derived) | `source = "LLM"` (now correct) + `source = "DERIVED"` for A.1 fallback path |

## Files afectados (~95 LOC, 1 commit)

| File | Cambio | LOC |
|---|---|---|
| `llm_engine_eolo/llm_engine/prompt_builder.py` | Nueva sección RULE EVALUATION TRACE en system prompt instruyendo formato | +30 |
| `llm_engine_eolo/llm_engine/decision_parser.py` | Modificar `build_rule_evaluation_trace` para MERGE LLM-emitted + safety rails. LLM entries take precedence sobre derived | +25 |
| `llm_engine_eolo/tests/test_llm_engine.py` | 3 tests: LLM-emitted passes through / merge con safety rails / LLM takes precedence over derived | +50 |

## Prompt change (prompt_builder.py)

Append section a system prompt template (probably después de TACTICAL rules section):

```
=== RULE EVALUATION TRACE (Sprint 3 A.2) ===

In your JSON Decision output, you MUST include "rule_evaluation_trace" as a list
of the 5-10 most decisive rule evaluations driving this decision.

Each entry schema:
{
  "rule_id": "TR-Juan-XXX",
  "tier": "AXIOMA|PROHIBITIVA|MAESTRA|PROTOCOLO|TACTICAL_PLUS|TACTICAL",
  "verdict": "AFFIRMED|BLOCKED|NEUTRAL|NOT_APPLICABLE",
  "confidence_impact": <int 0-10>,
  "source": "LLM",
  "evidence": "<1-2 sentence WHY this rule scored this way given snapshot>"
}

Constraints:
- LIMIT to 5-10 entries (compact mode). Most decisive only.
- rule_id must reference real KB rule (TR-Juan-001 to TR-Juan-061), NOT invented.
- evidence MUST cite specific snapshot fields.
- Safety rails (SR-XXX) are appended by parser; do NOT emit them.

Also include "decision_path": 1-2 sentence narrative summary of reasoning flow.
```

## Parser change (decision_parser.py)

Modify `build_rule_evaluation_trace`:

```python
def build_rule_evaluation_trace(decision_dict: dict, kb_loader=None) -> List[RuleEvaluation]:
    """A.2: accept LLM-emitted trace + merge derived safety rail entries.

    LLM-authored entries (source="LLM" with evidence) take precedence over
    A.1-style derivation. Safety rails (source="SAFETY_RAIL") always appended.
    """
    trace: List[RuleEvaluation] = []
    seen_rule_ids = set()

    # 1. LLM-emitted entries (A.2): pass through if valid
    llm_emitted = decision_dict.get("rule_evaluation_trace", [])
    for entry in llm_emitted:
        if isinstance(entry, dict) and entry.get("source") == "LLM":
            try:
                re_obj = RuleEvaluation(**entry)
                trace.append(re_obj)
                seen_rule_ids.add(re_obj.rule_id)
            except Exception as e:
                logger.warning(f"LLM emitted invalid trace entry: {entry} — {e}")

    # 2. Fallback A.1 derivation for tacit_rules_applied NOT already in LLM trace
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
            source="DERIVED",  # CHANGED: was "LLM" in A.1, now "DERIVED" semantically correct
        ))

    # 3. Safety rail entries (parser-side, always appended)
    for code in decision_dict.get("safety_overrides", []):
        rule_id = next(
            (rid for prefix, rid in _SAFETY_RAIL_PREFIX_TO_RULE_ID if code.startswith(prefix)),
            f"SR-UNKNOWN-{code}",
        )
        verdict = "NEUTRAL" if any(code.startswith(p) for p in _SR_NEUTRAL_PREFIXES) else "BLOCKED"
        trace.append(RuleEvaluation(
            rule_id=rule_id, tier="AXIOMA", verdict=verdict, source="SAFETY_RAIL",
        ))

    return trace
```

Note: A.2 changes `source` for A.1 fallback path from "LLM" to "DERIVED" (semantic correction).
A.1 tests will need update if they check `source="LLM"` for the tacit_rules path.

## Decisiones de design lockeadas

1. **`decision_path` LLM-emitted con auto-fallback** — LLM emite si puede; parser auto-genera si LLM no incluye o vacío.
2. **Cost impact validation post-deploy** — medir token usage diff en `/api/state.stats.llm_metrics`. Target <15% increase. Si supera, reducir prompt verbosity.
3. **Schema validation strictness: coerce + log warning** — si LLM emite `tier` con value no-Literal, coerce a None + log warning (graceful degradation, no reject).

## Tests (3 nuevos en test_llm_engine.py)

```python
def test_llm_emitted_trace_passes_through():
    """A.2: LLM emits structured trace with evidence; parser accepts."""
    # raw with rule_evaluation_trace[0]={rule_id=TR-Juan-012, source=LLM, evidence=...}
    # assert decision.rule_evaluation_trace contains TR-Juan-012 with evidence preserved

def test_llm_trace_merged_with_safety_rails():
    """A.2: low confidence still triggers SR-001 even with LLM trace present."""
    # raw with confidence=3 + LLM trace[0]
    # assert both LLM entry + SR-001 BLOCKED in final trace

def test_llm_trace_takes_precedence_over_derived():
    """A.2: if LLM emits TR-Juan-X, derived A.1 fallback skips that rule."""
    # raw with tacit_rules_applied=[TR-Juan-012] AND rule_evaluation_trace=[TR-Juan-012 LLM]
    # assert exactly 1 entry, source=LLM, no DERIVED dup
```

## Acceptance criteria

- 16/16 previous + 3 new = 19/19 tests PASS
- LLM Engine redeploy to rev 00005-xxx (post 00004-qw9 from #95)
- Live monitor cost impact <15% vs pre-A.2 baseline
- Sample decision in logs shows `rule_evaluation_trace` with evidence populated

## Open follow-ups (not blocking A.2 ship)

- Sprint 3 A.3: Dashboard 3-niveles UI (compact/expanded/full toggle, single-response client-side filter per Q3)
- Sprint 3 A.4: Bot CROP consume trace in `/api/state` + `/api/trades` responses
- Tech debt A.2: consider adding "MODIFIED" to verdict Literal enum for clamp rails

