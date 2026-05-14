# Sprint S3.5 — Granular Position Sizing (in-memory)

**Period:** 2026-05-14 (1 día)
**Status:** ✅ COMPLETO
**Strategy:** In-memory only (Firestore deferred a S3.X, consistente con S3)

## Bloques deployados

| Bloque | Componente | Resultado |
|--------|-----------|-----------|
| S3.5.A | Backend foundation | _compute_size creado + hook en crop_main.py:1148 |
| S3.5.B | Endpoint + validation + state | Allowlist + int 0-50 + default matrix 100 valores |
| S3.5.C | Override layer | Side effect de S3.B (reusa S3.B helpers) |
| S3.5.D | Frontend UI completo | Sub-panel 7mo con tabs/presets/actions/summary |
| S3.5.E | Final wiring + budget hint | Opción B fix UX + info-hint en budget_per_trade |
| S3.5.F | Commit + canary + promote + tag | Sprint cerrado |

## Architectural decisions

### Decisión 1: qty = contracts POR TRANCHE
**Razón:** El bot abre cada spread en 3 tranches (T0/T1/T2). Internamente más simple: `signal.to_decision(contracts=qty)` × 3 tranches automáticamente.

**UX:** Mostrar hint "×3 trc = N" debajo de cada input editado para evitar confusión.

**Implicación:** qty=10 en UI → 30 contratos abiertos en broker (10 × 3 tranches).

### Decisión 2: In-memory only (sin Firestore)
**Razón:** Otra sesión Claude trabajando con V1 en Firestore (otro proyecto GCP). Consistente con S3 — evitar colisiones temporales.

**Trade-off:** Restart del bot pierde overrides.

**Resolution:** Sprint S3.X (cuando V1 termine) agrega Firestore persistence para S3 + S3.5 juntos.

### Decisión 3: _compute_size CREADA (no refactor)
**Hallazgo durante investigación:** `_compute_size` no existía. Solo había `contracts=1` hardcoded en crop_main.py:1146.

**Implementación:** Crear método from scratch en CropBotTheta con 4 layers defensivos de fallback a 1 (weekend, ticker desconocido, dte inválido, override inválido).

### Decisión 4: Default matrix todo en 1
**Razón:** Preservar comportamiento original del bot pre-S3.5.

**Implementación:** Dict comprehension genera 5 días × 4 tickers × 5 DTEs = 100 valores en 1.

### Decisión 5: Cross-field warning, NO block
**Razón:** User puede aceptar exposure alta deliberadamente.

**Implementación:** `logger.warning` si `total_exposure > $5000 nominal` estimado, sin bloquear el Save.

### Decisión 6: 5 presets simétricos
**Conservador 1-1-1-1-1, Moderado 2-2-2-2-2, Agresivo 5-5-5-5-5, Solo 0DTE 1-0-0-0-0, OFF 0-0-0-0-0**

**Razón:** Simple, predictable, fácil de modificar después.

### Decisión 7: budget_per_trade NO bloqueado
**Razón:** Sigue siendo necesario como fallback global y para cálculo de `daily_loss_cap` nominal_equity.

**Implementación:** Solo hint visual ⓘ con tooltip explicando que Position Sizing lo precede cuando hay overrides.

### Decisión 8: Opción B aplicada (fix UX preexistente)
**Razón:** Issue latente de S2.3 — `setEditMode` y `discardEdits` solo llamaban `updateRiskManagement`, no `updateStrategyMatrices`. Esto causaba lag visual hasta 60s en toggle Edit Mode.

**Implementación:** +2 líneas — `updateStrategyMatrices()` agregado a ambas funciones. Beneficia a TODOS los sub-paneles (B3-B7 + Position Sizing).

## Tests E2E pasados

| Test | Result | Notes |
|------|--------|-------|
| _compute_size functional smoke | 12/12 ✅ | Default + overrides + edge cases |
| _validate_value_range position_sizing | 8/8 ✅ | Range + type + bool gotcha |
| _apply_overrides E2E backend | 6/6 ✅ | Paths 4 niveles + untouched preserved |
| POST Mon.SPY.dte0=5 (canary) | 200 ✅ | applied:1, /api/state refleja |
| POST out-of-range 51 (canary) | 422 ✅ | "value 51 out of range [0, 50]" |
| Cleanup reset (canary) | 200 ✅ | Default 1 restored |
| Matrix completa expuesta | ✅ | 100/100 keys generated |
| Stack S2.3+S3 preserved | ✅ | All sub-paneles funcionan |
| Path consistency UI ↔ backend | ✅ | grep matches |
| HTML balance + JS syntax | ✅ | node --check pass |

## Stats

| File | Lines |
|------|-------|
| main.py | +57 (allowlist + validation + cross-field + snapshot) |
| crop_main.py | +58 (_compute_size method + hook + skip logic) |
| dashboard-crop.html | +403 (CSS + HTML + JS + budget hint + Opción B) |
| **Total** | **+518 / -3** |

## Stack final en producción

```
Service:     eolo-bot-crop (us-east1)
Revision:    eolo-bot-crop-s3-5-complete-20260514-1212
Image:       gcr.io/eolo-schwab-agent/eolo-bot-crop:s3-5-complete-20260514-1205
Traffic:     100% ✅
Git HEAD:    8497ddd (Merge S3.5) sobre 02cf001 (feat S3.5)
Tag:         sprint-s3-5-complete-2026-05-14
Mode:        PAPER
```

## Rollback paths

```bash
# Rollback A (S3, último estable previo)
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --to-revisions=eolo-bot-crop-s3-complete-20260513-2032=100

# Rollback B (S2.3, pre-S3 — más conservador)
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --to-revisions=eolo-bot-crop-s2-3-b7-20260513-1509=100
```

## Lecciones aprendidas

### Hallazgos durante investigación

1. **`_compute_size` NO existía**. El plan inicial decía "refactor `_compute_size`" pero la realidad era `contracts=1` hardcoded en un único callsite. Lección: investigación previa siempre obligatoria — el plan puede asumir cosas falsas sobre el código real.

2. **Mockup S3.5 aprobado pero no en repo**: el mockup HTML que generó la spec original no estaba commiteado. Tuvimos que reconstruir el layout desde el spec documentado en SPRINT_S2_3_COMPLETE.md. Lección: si un mockup se aprueba como referencia para un sprint futuro, commitearlo al repo.

3. **3 tranches × N qty = sorpresa potencial**: si user pone qty=10 en UI sin entender que × 3 tranches da 30 contratos, podría haber surpresa. Mitigación: hint "×3 trc = 30" debajo de cada input editado.

### Bugs evitados

1. **`bool` is subclass of `int`** (recurring Python gotcha): mismo patrón que S3.D. `_compute_size` y `_validate_value_range` rechazan `bool` explícitamente antes de check int.

2. **UTC consistency**: `_compute_size` reuse del approach de `_compute_theta_dtes` (UTC weekday). Si hubiera usado local time, race condition en day boundary.

3. **`window._lastStrategyParams` cache**: necesario para que event handlers (tabs, presets, etc) tengan acceso al state actual sin re-fetch. Cache se refresca cada `renderPositionSizing` call.

### Decisión arquitectónica retroactiva: Opción B

Detectado durante PASO 5 de S3.5.E: pre-existing UX issue de S2.3 — toggle Edit Mode no rerendea sub-paneles inmediatamente, lag visual hasta 60s. Fix de 2 líneas (`updateStrategyMatrices()` en `setEditMode` + `discardEdits`) implementado como parte de S3.5. Beneficia retroactivamente a todos los sub-paneles previos.

## Backlog actualizado

| Sprint | Componente | ETA | Prioridad |
|--------|-----------|-----|-----------|
| S3.X | Firestore persistence (S3 + S3.5) | 3-4h | Cuando V1 termine |
| S3.1 | Refactor STOP_LOSS_MULT callsites | 3-4h | Media |
| S3.2 | Refactor delta_by_risk callsites | 2-3h | Media |
| S3.3 | Refactor TICKER_CONFIG callsites | 3-4h | Media |
| S3.4 | Refactor VIX_CREDIT_TABLE callsites | 2-3h | Media |
| S2.4 | Audit Log Drawer | 1-2h | Baja |
| S4 | Alertas + Health monitoring | 7-9h | Alta |
| S5 | Analytics + Backtesting | 10-12h | Alta |

## Conclusión

Sprint S3.5 es **el primer feature end-to-end completo** del dashboard que conecta UI editable → endpoint backend → state in-memory → función operacional del bot.

Diferencia clave vs S3: en S3 solo B7 (DTE Schedule) afectaba al bot. En S3.5, el módulo de sizing está completamente integrado: editar la matriz desde UI → bot abre N contratos por tranche según `_compute_size` en próxima señal.

Bot operativo PAPER con default qty=1 preservado. Override activo opcional vía POST endpoint o UI dashboard.

Firestore persistence se sigue difiriendo a S3.X cuando V1 termine en paralelo.
