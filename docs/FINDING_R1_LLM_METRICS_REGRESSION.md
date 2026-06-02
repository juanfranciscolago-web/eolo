# Finding R1: llm_metrics regression — Fallback 1 del /api/state handler omite inject

**Descubierto:** 2026-06-02 ~10:02 ET durante validation #77 runtime.
**Severidad:** Alta (observability), Bajo blast radius (read-only).
**Fix planeado:** Post-cierre mercado 16:00 ET hoy (no mid-market redeploy).

## El bug

`eolo-crop/main.py` handler `/api/state` tiene 3 fallback paths:
- **Fallback 1** (líneas 743-757): lee `crop_state.json` local si existe, retorna early
- **Fallback 2**: leer Firestore cached state
- **Fallback 3** (líneas 835-846): build from `bot_instance` live state

El inject `state["stats"]["llm_metrics"] = bot._llm_metrics.stats()` (Sprint 11)
existe SOLO en Fallback 3. Cuando `crop_state.json` existe, Fallback 1 retorna
sin ejecutar el inject → `llm_metrics` ausente en response.

**Mismo bug aplica a `llm_cache`** (Sprint 8.B) — inject solo en Fallback 3.

## Evidencia

- Live test 3 polls /api/state (10:00-10:01 ET): `llm_metrics: NOT PRESENT` consistente
- Response tiene `_source: "local_state_file"` → confirma Fallback 1 activo
- `record_call` SÍ se ejecuta en crop_main.py:1361 — counters bot._llm_metrics están incrementándose
- Sin warnings `record_call failed` ni `Could not read llm_metrics stats`

## Fix (~5 LOC en main.py)

ANTES de `return jsonify(state), 200` en Fallback 1 (~línea 757):

```python
# Sprint 11: enrich file-based state with live counters from bot
if bot is not None:
    try:
        llm_metrics = getattr(bot, "_llm_metrics", None)
        if llm_metrics is not None and hasattr(llm_metrics, "stats"):
            state.setdefault("stats", {})["llm_metrics"] = llm_metrics.stats()
    except Exception as me:
        logger.debug(f"[API /state] Could not read llm_metrics stats (fallback 1): {me}")
```

Misma logica para `llm_cache`.

## Refactor opcional (R1.B)

Extraer helper `_enrich_state_with_bot(state, bot)` que se llame en los 3 fallbacks.
Dedup + previene esta regresión a futuro.

## Tasks

- R1.A: hotfix Fallback 1 inject — **NIGHT SESSION 2-jun post-16:00 ET**
- R1.B: refactor helper común — opcional, próxima sesión
- Tech debt para captura en MEMORY.md cierre noche

## Cross-refs

- main.py handler /api/state líneas 730-860
- crop_main.py LLMMetrics init línea 382, record_call línea 1361
- metrics.py LLMMetrics class
- Sprint 11 wire history (originally injected only in build-from-bot path)
- Validation evidence: /api/state polls 2026-06-02 10:00-10:01 ET
