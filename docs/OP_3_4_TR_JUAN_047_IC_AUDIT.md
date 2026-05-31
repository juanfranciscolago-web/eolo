# OP-3.4 — TR-Juan-047 (IC PROHIBITIVA) Audit

**Fecha:** 2026-05-31 (Domingo cierre)
**Backlog ref:** OP-3.4 MEDIO — "TR-Juan-047 dead: marcar como N/A si IC deshabilitado"
**Verdict:** **KEEP TR-Juan-047 sin cambios. Notes update opcional en Sprint 18.**

---

## TL;DR

| Pregunta | Respuesta |
|---|---|
| ¿IC está deshabilitado en bot CROP? | **Sí, default `self._iron_condor_enabled = False`** |
| ¿IC se usó alguna vez en producción? | No (no hay flip Firestore documentado) |
| ¿TR-Juan-047 se cita en decisiones? | 0 citas (consistente con IC disabled) |
| ¿Marcar TR-Juan-047 como N/A? | **NO.** Es PROHIBITIVA — guard rail para futuro |
| ¿Eliminar TR-Juan-047 del KB? | **NO.** Sigue siendo relevante semántica |
| ¿Acción inmediata? | **Ninguna.** Quizás Notes update en Sprint 18 KB v1.3 |

---

## Hallazgos del audit

### 1. IC está disabled por default (verificado en código)

**`eolo-crop/crop_main.py:511`:**
```python
# Iron Condor flag — Paso 5 backlog v2.
# False (default): guard activo, NO se permite PUT+CALL en mismo ticker.
# True: guard se saltea, permite Iron Condors (eval futura).
self._iron_condor_enabled: bool = False
```

**`eolo-crop/crop_main.py:1437`:**
```python
# ── Guard Iron Condor — Paso 5 backlog v2 ──
# No abrir CALL si hay PUT abierto en mismo ticker (y viceversa).
# Bypass via Firestore flag iron_condor_enabled (default False).
if not self._iron_condor_enabled:
    opposite_side = "call" if "put" in (spread_type or "") else "put"
    opposite_open = any(
        p.get("ticker") == ticker
        and opposite_side in (p.get("spread_type") or "")
        for p in self._theta_positions
    )
    if opposite_open:
        logger.info(f"... guard Iron Condor: hay {opposite_side} spread abierto ...")
        continue
```

**Cómo se podría habilitar:** POST `/api/config` con `iron_condor_enabled=true` → guarda en Firestore + bot lee con `_poll_settings()` cada ~25min → `self._iron_condor_enabled = True` → bypass guard.

**Estado en producción:** sin evidencia de que se haya habilitado nunca. Bot CROP siempre opera con IC bloqueado.

### 2. LLM CAN verdict IRON_CONDOR_SEQUENTIAL

**`eolo-crop/llm_gate/integration.py:140`:**
```python
"IRON_CONDOR_SEQUENTIAL": "iron_condor",
```

**`eolo-crop/llm_gate/integration.py:162`:**
```python
if verdict == "IRON_CONDOR_SEQUENTIAL":
    # IC: ambos strikes/deltas relevantes
    params["llm_put_strike"] = strikes.get("put_strike")
    params["llm_call_strike"] = strikes.get("call_strike")
    params["llm_put_delta"] = deltas.get("put_delta")
    params["llm_call_delta"] = deltas.get("call_delta")
```

**Implicación:** el LLM tiene en su vocabulario el verdict `IRON_CONDOR_SEQUENTIAL`. Si el LLM lo sugiere, los params se construyen para ambas patas. Pero el guard de `_iron_condor_enabled=False` rechaza el open antes de ejecutar.

**TR-Juan-047 es relevante para el LLM,** porque le indica:
- Si vas a sugerir IC, hacelo SECUENCIAL (no simultaneous)
- "NUNCA hacer IC directo. SIEMPRE secuencial: vender PUT o CALL primero, esperar rebote, vender otra punta en rebote opuesto."

Esto guía la calidad del verdict del LLM cuando el strategy de IC se reactive en el futuro.

### 3. ¿Por qué TR-Juan-047 tiene 0 citas?

Causa root: **IC está disabled** → el LLM no ve setups donde IC sería relevante → no cita la regla.

NO es un bug. NO es regla "dead". Es regla "**dormant pero strategically valuable**".

Cuando IC se reactive:
- LLM empieza a evaluar setups range-bound como candidatos para IC
- TR-Juan-047 dispara para prevenir IC simultaneous
- Citas aumentan significativamente

---

## Decisión recomendada

### Opción A — **KEEP TR-Juan-047 sin cambios (recomendada)**

Razones:
1. Es PROHIBITIVA (regla negativa de seguridad)
2. Cuando IC se habilite (futuro), es crítica
3. Eliminarla = perder guard rail
4. Marcarla N/A = perder semántica del KB

### Opción B — Actualizar Notes en Sprint 18 KB v1.3

Si en algún momento Juan ejecuta Sprint 18 (KB v1.3), modificar el Notes de TR-Juan-047:

```
Notes ANTES:
  REGLA HARD - NO violar bajo ninguna circunstancia

Notes DESPUÉS:
  REGLA HARD - NO violar bajo ninguna circunstancia.
  DORMANT (2026-05-31): IC actualmente disabled via _iron_condor_enabled=False.
  Regla se mantiene preventiva para futuro re-enable.
  Audit completo: docs/OP_3_4_TR_JUAN_047_IC_AUDIT.md
```

### Opción C — Eliminar regla (NO recomendada)

Costo:
- Perder guard rail futuro
- Re-añadirla cuando IC se reactive (~30 min)
- Inconsistencia conceptual del KB (regla PROHIBITIVA importante removed sin razón estructural)

Beneficio:
- 1 línea menos en el prompt del LLM (negligible)

**No vale el costo.**

---

## Backlog impact

OP-3.4 originalmente decía "marcar como N/A si IC deshabilitado". Después de este audit, **se reescribe a:**

```
OP-3.4 — TR-Juan-047 IC dormant — RESOLVED-AS-DESIGN
  Verdict: KEEP sin cambios. Regla preventiva valiosa.
  Acción opcional: Notes update en Sprint 18 KB v1.3.
  Audit: docs/OP_3_4_TR_JUAN_047_IC_AUDIT.md
```

**Esfuerzo restante: 0** (era 15-30 min, ahora cerrado).

---

## Sprint 18 impact

El decision matrix de Sprint 18 (`docs/sprint_18_tactical_decision_matrix.md`) lista TR-Juan-047 en categoría PROHIBITIVA — no en TACTICAL. La regla NO está en el scope de Sprint 18 (KB v1.3 colapsa solo TACTICAL).

**No hay impacto.** TR-Juan-047 sobrevive intacta en KB v1.3.

Si en KB v1.4+ Juan decide hacer rediseño de PROHIBITIVA o agregar tier "DORMANT" para reglas con strategy disabled, este audit aplica.

---

## Comandos verification

```bash
# Verificar estado actual del flag en producción
BOT_URL=$(gcloud run services describe eolo-bot-crop --region=us-east1 \
  --project=eolo-schwab-agent --format='value(status.url)')
curl -s "$BOT_URL/api/state" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ic = d.get('config', {}).get('iron_condor_enabled', None)
print(f'iron_condor_enabled (live): {ic}')
print('Si False/None → guard activo, IC disabled')
print('Si True → IC habilitado, TR-Juan-047 activa en LLM decisions')
"
```

Si en futuro Juan flippa el flag a True via dashboard, este audit deja de aplicar y TR-Juan-047 pasa a ser "active" en lugar de "dormant".

---

**Audit completo. Acción inmediata: ninguna. Backlog item OP-3.4 cerrado como RESOLVED-AS-DESIGN.**
