# Sprint — LLM Engine v0.2 (Layered Haiku Prefilter)

- **Fecha cierre:** 2026-05-27
- **Autor:** Juan + Claude
- **Status:** DONE
- **Branch:** `feat/llm-engine-v0.2-haiku-prefilter`

## Scope

Agregar layered approach al LLM Engine: nuevo endpoint `/pre_decide` con
Haiku 4.5 que decide `should_call_full` antes de invocar Sonnet 4.6 en `/decide`.
Objetivo: reducir 60-70% de calls a Sonnet (filtra los WAIT obvios — VIX spike,
macro events, fuera de ventana, etc).

## Cambios

- `llm_engine_eolo/llm_engine/haiku_prefilter.py` (NUEVO, ~113 LOC):
  - `PreDecision` pydantic model
  - `build_haiku_prompts(kb, snapshot)` — prompts cortos (~2050 chars system + 304 user)
  - `parse_pre_decision(raw)` con fallback `should_call_full=True` en error
- `llm_engine_eolo/llm_engine/service.py` (MODIFY +79 LOC):
  - Import haiku_prefilter
  - `CONFIG["HAIKU_MODEL"]` = `"claude-haiku-4-5-20251001"`
  - Nuevo endpoint `POST /pre_decide`
- `llm_engine_eolo/llm_engine/__init__.py` (MODIFY):
  - `__version__` bumped a `0.2.0`
  - Re-exports: `Decision` + `PreDecision` desde package root
- `llm_engine_eolo/tests/test_llm_engine.py` (MODIFY +45 LOC):
  - `test_haiku_prompts_build`
  - `test_pre_decision_parser_ok`
  - `test_pre_decision_parser_fallback`
- `llm_engine_eolo/README.md` (MODIFY):
  - Sección nueva "Layered approach (v0.2)"
  - Endpoint `POST /pre_decide` documentado
  - Counts de KB stats actualizados a v1.2 (61 reglas, 6 tiers)
  - Roadmap v0.2 marca Layered como done

Total: **1 NUEVO + 4 MODIFY**, ~250 LOC.

## Validación

- **Tests 11/11 verde** (8 originales + 3 nuevos para Haiku).
- **Smoke 2/2 expected matches**:
  - Neutral setup → `should_call_full=True` confidence=8 latency=1881ms
    *(Haiku usó la jerga "GOLDEN TICKET" del AXIOMA TR-Juan-043 — indicación de que leyó la regla, aunque no citó el ID. Reconoció ventana óptima y delegó a Sonnet)*
  - VIX spike +7.5% → `should_call_full=False` confidence=9 latency=1443ms
    *(Haiku citó TR-Juan-058 PROHIBITIVA explícitamente)*
- Refactor no rompió tests existentes.

## Comparativa Haiku vs Sonnet

| Métrica | Haiku 4.5 | Sonnet 4.6 | Ratio |
|---|---|---|---|
| System prompt chars | ~2050 | ~9615 | 4.7x más corto |
| User prompt chars | ~304 | ~1970 | 6.5x más corto |
| Latencia típica | 1.4-1.9s | 14-18s | ~10x más rápido |
| Cost por call | ~$0.003 | ~$0.02 | ~7x más barato |

## Decisiones tomadas

1. **`format_rule` reutilizado** de `prompt_builder` (no duplicar formato de reglas).
2. **`/pre_decide` SIN `paper_trading_only` gate**: el endpoint no ejecuta nada, solo recomienda. El gate sigue en `/decide` (L111-112).
3. **`PreDecision` exportada desde `__init__.py`** (no re-export a mitad de módulo en `decision_parser.py`).
4. **`HAIKU_MODEL` en `CONFIG`** agrupado con `LLM_MODEL` (entre L42 y L43 del lifespan).
5. **Threshold de confidence = 7** para gate (decisión del **cliente** eolo-crop, no del servidor): Haiku NO_GO con confidence ≥7 → skip Sonnet. <7 → llamar Sonnet (conservador).
6. **Fallback siempre `should_call_full=True`** en cualquier error de Haiku (mejor pasar a Sonnet que perder oportunidad).

## Cost estimado con layered (proyección)

Asumiendo 60-70% de calls filtrados por Haiku, en operación full driver eolo-crop:

| Setup | Cost mensual proyectado |
|---|---|
| Antes (solo Sonnet) | ~$300-450/mes |
| Con layered | ~$125-200/mes |
| **Ahorro** | **~50-60% del cost total** |

## Tech debt

- Heredada del bootstrap (items 1-11 de `SPRINT_LLM_ENGINE_BOOTSTRAP_COMPLETE.md`), todas vigentes.
- **Item 12 (nuevo)**: el `HAIKU_MODEL` default es hardcoded en `service.py` CONFIG. Si Anthropic bumpea Haiku a una versión nueva (ej. 4.6), hay que editar el default — aunque ya tiene fallback via `os.getenv("HAIKU_MODEL", ...)` para override en deploy.

## Salvavidas (rollback)

Si el layered approach se considera no viable y hay que revertir solo este sprint:

```bash
git revert <merge_commit_sha>
# o si todavía no mergeado:
git checkout main
git branch -D feat/llm-engine-v0.2-haiku-prefilter
```

El LLM Engine vuelve a v0.1 sin endpoint `/pre_decide`. Cliente externo que llame a `/pre_decide` post-rollback recibe **404** — comportamiento explícito de "feature removed". `/decide` queda intacto, no afecta integración existente.

## Next

- **4.D**: re-deploy LLM Engine v0.2 a Cloud Run (bump version, re-build image, redeploy).
- **Fase 2 del Bloque 4** (eolo-crop integration): cliente HTTP que use `/pre_decide` + `/decide` con threshold de confidence 7. Insertion point: `_run_theta_harvest` en `crop_main.py` (ver audit 4.0.3).
- **Fase 4 (post-Bloque 4 v0.1)**: hot-reload del KB + logging trades + workflow iteración del prompt según fallos en producción.

## Commits del sprint en `feat/llm-engine-v0.2-haiku-prefilter`

```
<commit_2_sha> docs(llm-engine): sprint v0.2 layered approach
<commit_1_sha> feat(llm-engine): v0.2 layered Haiku prefilter
```
*(SHAs reemplazados después del commit real)*
