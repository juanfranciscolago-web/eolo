# Sprint 21 — Wiring Audit End-to-End (Pre-flight Lunes 1-Jun)

**Fecha:** Domingo 31-mayo 2026
**Trigger:** Pre-flight check antes del deploy Lunes 1-jun + validation Sprint 21
**Resultado:** **🟡 BUG identificado** — Sprint 21 cost tracking incompleto en layered flow

---

## TL;DR

Sprint 21 deployado a las 10:06 ET de hoy en LLM Engine rev `llm-engine-service-00003-sqn`. Wiring **engine-side está completo** (ambos `/decide` y `/pre_decide` retornan tokens en `meta`). **Wiring client-side está incompleto** en el flow layered (Haiku pre-filter → Sonnet decide).

**Impact:** `cost_estimate_usd` está **systematically understated**:
- `haiku_skip` (Haiku rechaza con alta confidence): **100% del cost de Haiku se pierde** (cost = $0)
- `haiku_pass` / `haiku_low_conf` (Sonnet se llama): **cost de Haiku se pierde** (~5-10% del total real)
- Solo `LLM_UNKNOWN` (sin pre-filter, deprecated path) trackea cost correctamente

**Validation Monday criterion #5 (`cost > 0`):**
- Si primer ciclo es `haiku_pass` → criterion PASA pero valor wrong (~10% bajo)
- Si primer ciclo es `haiku_skip` → criterion **FALLA** (cost = $0 pese a haber hecho la llamada Haiku)

---

## Audit por layer

### Layer 1: LLM Engine `/decide` (Sonnet) — ✅ OK

`llm_engine_eolo/llm_engine/service.py:138-188` — Sprint 21 cambios:

```python
response = anthropic_client.messages.create(...)
_usage = getattr(response, "usage", None)
input_tokens  = int(getattr(_usage, "input_tokens", 0) or 0)
output_tokens = int(getattr(_usage, "output_tokens", 0) or 0)
...
result["meta"] = {
    "request_id": request_id,
    "latency_ms": total_latency_ms,
    "model": CONFIG["LLM_MODEL"],
    "kb_version": "v1.2",
    "input_tokens":  input_tokens,
    "output_tokens": output_tokens,
}
return result
```

**Verdict:** Defensive con `getattr` doble. Si SDK cambia, fallback a 0. Tokens van en `meta.input_tokens` / `meta.output_tokens`. ✅

### Layer 2: LLM Engine `/pre_decide` (Haiku) — ✅ OK

`llm_engine_eolo/llm_engine/service.py:234-279` — mismo patrón:

```python
response = anthropic_client.messages.create(model=HAIKU_MODEL, ...)
_usage = getattr(response, "usage", None)
input_tokens  = int(getattr(_usage, "input_tokens", 0) or 0)
output_tokens = int(getattr(_usage, "output_tokens", 0) or 0)
...
result["meta"] = {
    ...
    "model": CONFIG["HAIKU_MODEL"],
    "input_tokens":  input_tokens,
    "output_tokens": output_tokens,
}
return result
```

**Verdict:** Idéntico al `/decide`. ✅

### Layer 3: Bot CROP `client.consult()` — ❌ BUG

`eolo-crop/llm_gate/client.py:163-194` — el flow layered combina Haiku + Sonnet:

```python
def consult(self, snapshot):
    pre = self.pre_decide(snapshot)  # Returns {..., "meta": {"input_tokens": N, "output_tokens": M, "model": "haiku"}}
    should_call = pre.get("should_call_full", True)
    conf = pre.get("haiku_confidence", 0)

    # Haiku NO_GO con confidence alta -> skip Sonnet
    if not should_call and conf >= self.haiku_confidence_threshold:
        return {
            "verdict": "WAIT",
            "confidence": conf,
            "main_reason": f"haiku_skip: ...",
            "tacit_rules_applied": [],
            "safety_overrides": ["HAIKU_PREFILTER_SKIP"],
            "layered_path": "haiku_skip",
            "pre_decision": pre,  # ← Haiku meta vive aquí
            # ❌ NO incluye top-level "meta" — bot client lee decision.meta
            # que es None → 0 tokens contados → cost = $0
        }

    # Sonnet path
    decision = self.decide(snapshot)  # Has meta with Sonnet tokens
    decision["layered_path"] = "haiku_pass" if should_call else "haiku_low_conf"
    decision["pre_decision"] = pre  # ← Haiku meta también acá pero...
    return decision
    # ❌ decision["meta"] = Sonnet's meta only. Haiku tokens en
    # decision["pre_decision"]["meta"] NUNCA son sumados al cost.
```

**Verdict:** Bug claro. Ambas ramas pierden los Haiku tokens.

### Layer 4: Bot CROP `crop_main.py:record_call()` — 🟡 Receives incomplete data

`eolo-crop/crop_main.py:1322-1357`:

```python
_meta = decision.get("meta") or {}
self._llm_metrics.record_call(
    verdict=decision.get("verdict"),
    latency_ms=_llm_latency_ms,
    decision_source=_source,
    input_tokens=int(_meta.get("input_tokens") or 0),
    output_tokens=int(_meta.get("output_tokens") or 0),
    model=_model,
)
```

Solo lee `decision.meta`. No mira `decision.pre_decision.meta`.

**Bonus finding:** Comment líneas 1343-1346 está **stale** — predates Sprint 21:

```python
# tokens: el cliente actual no los expone explícitamente.
# Dejamos 0 hasta que decision.meta los incluya. La
# estimación de costo quedará en 0 por ahora — fix
# cuando el engine devuelva usage stats.
```

El engine YA los expone (Sprint 21 hoy). Comment debe actualizarse a "Sprint 21 — tokens de decision.meta. NOTA: Haiku tokens en pre_decision.meta NO se suman, ver bug doc."

### Layer 5: `metrics.py` `_compute_cost()` — ✅ OK

`eolo-crop/llm_gate/metrics.py:150-164`:

```python
@staticmethod
def _compute_cost(input_tokens, output_tokens, model):
    if (model or "").lower() == "haiku":
        in_rate, out_rate = HAIKU_INPUT_PER_1M, HAIKU_OUTPUT_PER_1M
    else:
        in_rate, out_rate = SONNET_INPUT_PER_1M, SONNET_OUTPUT_PER_1M
    return (in_tok / 1M * in_rate) + (out_tok / 1M * out_rate)
```

**Verdict:** Distingue Haiku vs Sonnet correctamente. Si recibe los tokens correctos, calcula bien. ✅

### Layer 6: Dashboard UP-2.2 — ✅ OK

`eolo-crop/dashboard-crop.html:3253-3326` (PR #27 draft) — `renderLLMMetrics()`:

```javascript
const cost = Number(m.cost_estimate_usd) || 0;
if (calls > 0) {
    _setText('llm-cost-usd', `$${cost.toFixed(4)}`);
    _setText('llm-cost-usd-sub', `avg $${(cost / calls).toFixed(4)}/call`);
}
```

Muestra `cost_estimate_usd` tal cual viene del backend. Si backend reporta underestimate, dashboard muestra underestimate. **Visual es correcto relativo a data, pero data es wrong.** ✅ (no es el bug)

---

## Quantification del bug

### Pricing references (Anthropic.com actual)

| Modelo | Input ($/1M tok) | Output ($/1M tok) |
|---|---|---|
| Sonnet 4.5 | $3.00 | $15.00 |
| Haiku 4.5 | $0.80 | $4.00 |

### Per-call estimates (típicos del Theta Harvest bot)

| Call type | Input tokens | Output tokens | Cost real |
|---|---|---|---|
| Haiku pre-filter | ~500 | ~200 | $500/1M*$0.8 + $200/1M*$4 = $0.0004 + $0.0008 = **$0.0012** |
| Sonnet full decide | ~3000 | ~400 | $3000/1M*$3 + $400/1M*$15 = $0.009 + $0.006 = **$0.015** |

### Daily impact (assumption: 20 ciclos LLM/día)

Distribución típica del layered flow:
- **haiku_skip 70%** (Haiku rechaza con alta confidence) → 14 ciclos
- **haiku_pass 25%** (Haiku acepta, Sonnet decide) → 5 ciclos
- **haiku_low_conf 5%** (Haiku duda, Sonnet decide) → 1 ciclo

Cost real vs cost trackeado:

| Path | Count | Real cost/ciclo | Real cost total | Trackeado/ciclo | Trackeado total | Pérdida |
|---|---|---|---|---|---|---|
| haiku_skip | 14 | $0.0012 (solo Haiku) | $0.0168 | **$0.0000** | $0.0000 | **$0.0168** |
| haiku_pass | 5 | $0.0012 + $0.015 = $0.0162 | $0.0810 | $0.015 (solo Sonnet) | $0.075 | $0.006 |
| haiku_low_conf | 1 | $0.0162 | $0.0162 | $0.015 | $0.015 | $0.0012 |
| **Total** | 20 | — | **$0.114/día** | — | **$0.090/día** | **$0.024/día** |

**Pérdida porcentual:** ~21% del cost real (cost trackeado es $0.090 vs real $0.114).

**Annual:** ~$8.50/año understated. Money-wise no es crítico. **Pero la métrica de cost está rota como herramienta de observabilidad.**

### Severity: 🟡 MEDIO

- ✗ No rompe funcionalidad (verdicts correct)
- ✗ No bloquea deploy lunes
- ✓ Validation criterion #5 ("cost > 0") puede FALLAR si primer ciclo es haiku_skip
- ✓ Cost tracking sistemáticamente bajo → dashboard UP-2.2 muestra valores wrong

---

## Fix propuesto

### Opción A — Quick fix client-side (recomendado)

**Single PR, 2 archivos modificados, ~15 LOC:**

#### Fix 1: `metrics.py:record_call` — add Haiku tokens kwargs (backward compat)

```python
def record_call(
    self,
    verdict: Optional[str],
    latency_ms: float,
    decision_source: str = "UNKNOWN",
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "sonnet",
    haiku_input_tokens: int = 0,   # ← NEW (Sprint 21 fix)
    haiku_output_tokens: int = 0,  # ← NEW
) -> None:
    """Registra una llamada al LLM (post-pre-filter, llegó a consult).

    Sprint 21 fix: haiku_input_tokens/haiku_output_tokens se suman al cost
    cuando hubo pre-filter Haiku (layered_path = haiku_skip / haiku_pass / haiku_low_conf).
    """
    try:
        latency = float(latency_ms)
    except (TypeError, ValueError):
        latency = 0.0
    cost = self._compute_cost(input_tokens, output_tokens, model)
    # Sprint 21: add Haiku cost si hubo pre-filter
    if haiku_input_tokens or haiku_output_tokens:
        cost += self._compute_cost(haiku_input_tokens, haiku_output_tokens, "haiku")
    with self._lock:
        self._total_calls += 1
        if verdict:
            self._verdicts[verdict] += 1
        self._latencies_ms.append(latency)
        self._decision_sources[decision_source or "UNKNOWN"] += 1
        self._cost_estimate_usd += cost
```

#### Fix 2: `crop_main.py:1346` — pass Haiku tokens

```python
_meta = decision.get("meta") or {}
_pre_meta = (decision.get("pre_decision") or {}).get("meta") or {}

self._llm_metrics.record_call(
    verdict=decision.get("verdict"),
    latency_ms=_llm_latency_ms,
    decision_source=_source,
    input_tokens=int(_meta.get("input_tokens") or 0),
    output_tokens=int(_meta.get("output_tokens") or 0),
    model=_model,
    haiku_input_tokens=int(_pre_meta.get("input_tokens") or 0),   # ← NEW
    haiku_output_tokens=int(_pre_meta.get("output_tokens") or 0),  # ← NEW
)
```

#### Fix 3: Stale comment cleanup

Eliminar líneas 1343-1346 del comment stale ("Dejamos 0 hasta que decision.meta los incluya").
Reemplazar con:

```python
# Sprint 21 wire: tokens de decision.meta (Sonnet/Haiku según path).
# Si hubo pre-filter, Haiku tokens van en pre_decision.meta y se suman
# al cost via record_call kwargs (Sprint 21 fix).
```

#### Total LOC fix: ~12 LOC

#### Deploy: incluido en el próximo deploy bot CROP (Lunes con PR #24 + #27)

#### Backward compat: Sí (kwargs default 0). Si crop_main.py no pasa los kwargs, comportamiento idéntico al actual.

### Opción B — Reportar como tech debt, fix mid-week

Documentar este file como deuda, fixearlo mid-week sin urgencia. Validation Monday criterion #5 acepta que cost puede ser $0 en haiku_skip paths.

**Trade-off:** dashboard UP-2.2 muestra cost wrong por una semana. Pero plan lunes ya está blindado.

### Opción C — Documentar + workaround dashboard

Modificar `renderLLMMetrics()` para mostrar "(estimación parcial)" en el sub-label de cost USD card. Honest disclosure sin fix backend.

**Trade-off:** No arregla el bug, solo lo comunica.

---

## Recomendación: Opción A — Fix tonight

Razones:
1. Fix es chico (~12 LOC, 2 archivos)
2. Backward compatible (cero risk de breaking change)
3. Sin deploy adicional — incluido en el deploy lunes ya planeado
4. Validation criterion #5 pasa con valor correcto en lugar de "pass with wrong number" o "fail"
5. Dashboard UP-2.2 muestra cost real desde día 1
6. Cero contention con PRs draft existentes (toca código diferente)

Riesgos del fix:
- Bajo. metrics.py es pure compute. crop_main.py cambia 2 líneas en un try-except defensive.

---

## Si optás por Opción A — Plan ejecución

1. **Worktree nuevo:** `git worktree add ../eolo-sprint21-fix -b fix/sprint21-haiku-tokens-cost`
2. **Pegar a Claude en terminal:** prompt con los 3 fixes específicos (yo lo armo)
3. **Smoke test:** test unit que verifique cost computation con Haiku + Sonnet combinados
4. **Commit + PR draft**
5. **Merge lunes** junto con PR #24 + PR #27

Tiempo estimado: 30-45 min.

---

## Si optás por Opción B/C — Plan documentación

1. Crear `docs/tech_debt.md` con entry "TD-26: Sprint 21 Haiku cost tracking"
2. Actualizar runbook lunes — modificar validation criterion #5 para aceptar "cost > 0 si layered_path != haiku_skip"
3. Tarea de mid-week: aplicar Opción A

---

## Hallazgos secundarios del audit (no bugs, observaciones)

1. **`record_call` cuenta haiku_skip como call.** Esto está bien — fue una llamada al API (Haiku). Pero cost no se trackea (es lo que arregla Opción A).

2. **No hay test unit de cost calculation.** Para Sprint 21 fix sería bueno agregar:
   ```python
   def test_cost_haiku_plus_sonnet():
       m = LLMMetrics()
       m.record_call(verdict="SELL_PUT", latency_ms=1500,
                     input_tokens=2000, output_tokens=300, model="sonnet",
                     haiku_input_tokens=500, haiku_output_tokens=200)
       expected = (2000/1e6*3) + (300/1e6*15) + (500/1e6*0.8) + (200/1e6*4)
       assert abs(m.stats()['cost_estimate_usd'] - expected) < 1e-6
   ```

3. **Decision source "LLM_HAIKU_SKIP"** suena raro — fue un haiku_skip, "skip" implica no se llamó. Mejor naming: "LLM_HAIKU_PREFILTER_REJECTED". Pero romper backward compat por naming no vale.

4. **`pre_filter_skips` (Rule 0) vs `decision_sources.LLM_HAIKU_SKIP`** son métricas distintas pero similares:
   - `pre_filter_skips`: rules antes de cualquier LLM call (outside_entry_window, non_spy_ticker, etc) — cero cost
   - `LLM_HAIKU_SKIP`: pasó pre_filter pero Haiku rechazó — cost Haiku
   - Dashboard UP-2.2 los muestra en lugares diferentes, OK.

5. **PR #25 UP-1.2 KB Editor** no afecta este bug (tools, no engine/client).

---

## Si la opción es B/C — qué cambia en el runbook lunes

Actualizar `docs/RUNBOOK_LUNES_01_JUN_2026.md` sección "10:30 ART — Validación Sprint 21":

```markdown
### Validar Sprint 21 — cost en /api/state

⚠️ **TD-26 conocido (audit 31-may):** Cost trackeado puede ser $0 si el primer
ciclo es `haiku_skip` (Haiku rechaza con alta confidence sin llamar a Sonnet).
Si esto pasa, es esperado por bug en client.consult() — NO es regression.

Validación robusta:
- Si layered_path = "haiku_pass" o "haiku_low_conf" → esperás cost > 0 (Sonnet trackeado)
- Si layered_path = "haiku_skip" → cost puede ser $0 (TD-26 pendiente fix mid-week)

curl -s "$BOT_URL/api/state" | python3 -c "import sys,json,...; print(layered_path, cost)"
```

---

**Generado por:** Claude (audit dry-run via grep + file reads)
**Tiempo:** ~30 min
**Files auditados:** `service.py`, `client.py`, `crop_main.py`, `metrics.py`, `integration.py`, `dashboard-crop.html`
