# Sprint S3 — Backend Edit + Persistence (in-memory)

**Period:** 2026-05-13 (1 día)
**Status:** ✅ COMPLETO
**Strategy:** In-memory only (Firestore deferred a S3.X por V1 paralelo)

## Bloques deployados

| Bloque | Componente | Resultado |
|--------|-----------|-----------|
| S3.A | POST /api/state/edit Flask endpoint | Allowlist + threading.Lock + all-or-nothing |
| S3.B | Override layer (Opción C híbrido) | _strategy_overrides dict + _strategy_params() merge |
| S3.C | Refactor _compute_theta_dtes | Cierra deuda B7 — bot respeta DTEs editados |
| S3.D | Validation cross-field server-side | 3 capas (allowlist + range + cross-field) |
| S3.E | Frontend Save + readonly UI | saveEdits + 3 overlaps readonly + path consistency |
| S3.F | Final: commit + canary + promote + tag | Sprint cerrado |

## Architectural decisions

### Decisión 1: In-memory only (sin Firestore)
**Razón:** Otra sesión Claude trabajando con V1 en Firestore en paralelo. Sprint S3 evita Firestore para prevenir colisiones temporales con la otra sesión.

**Trade-off:** Restart del bot pierde overrides. User re-aplica desde dashboard.

**Resolution:** Sprint S3.X (cuando V1 termine) agrega Firestore persistence con namespace `crop_*` exclusivo.

### Decisión 2: Override layer híbrido (Opción C, no B)
**Razón:** Refactor de 50-100 callsites (Opción B) = 12-15h. Demasiado para una sesión.

**Implementación Opción C:** Refactor solo `_strategy_params()` (lo que sirve a UI) y `_compute_theta_dtes()` (cierra B7). Resto de callsites siguen leyendo constantes module-level.

**Consecuencia:** Solo DTE Schedule afecta funcionalmente al bot post-S3. Resto de overrides son visibles en /api/state pero no afectan callsites originales hasta Sprint S3.1+.

**Tech debt explícita:** Sprint S3.1+ refactor callsites STOP_LOSS_MULT, PROFIT_TARGET_PCT, etc.

### Decisión 3: 3 overlaps readonly UI
Los campos `entry_hour_et`, `max_positions`, `daily_loss_cap_pct` viven en `/api/config` (endpoint existente con Firestore). Para evitar divergencia silenciosa:
- Backend `/api/state/edit` rechaza estos paths (allowlist exclusion)
- Frontend renderiza readonly + tooltip "Configure en modal Theta Harvest"
- Path consistency E2E verificada (UI ↔ backend blocklist)

### Decisión 4: Flask, no FastAPI
Hallazgo durante exploración: `eolo-crop/main.py` usa Flask. El plan original de FastAPI estaba incorrecto.
- `threading.Lock` para concurrencia con bot asyncio loop
- Flask handler en thread separado del bot loop

### Decisión 5: All-or-nothing validation
Si algún path en el payload falla validation, rechaza todos. Evita estados parciales confusos.

## Tests E2E pasados

| Test | Result | Notes |
|------|--------|-------|
| POST {} empty | 200 OK | applied:0 |
| POST invalid path | 422 | error claro |
| POST valid stop_loss_mult=1.5 | 200 | /api/state refleja 1.5 |
| POST T0>T1 (cross-field) | 422 | "T0 must be < T1" |
| POST entry_hour_et (blocklist) | 422 | "not in allowlist" |
| Path consistency UI ↔ backend | ✅ | grep matches |
| 24 smoke tests _validate_value_range | 24/24 ✅ | |
| 6 smoke tests cross-field | 6/6 ✅ | |
| 6 smoke tests _set_path | 6/6 ✅ | |
| 17 smoke tests _is_allowed_path | 17/17 ✅ | |

## Estado de funcionalidad por bloque S2.3

| Bloque | UI editable | Backend funcional | Notas |
|--------|-------------|-------------------|-------|
| B3 Exits Advanced (15) | 14 (entry_hour_et readonly) | UI-only (override visible en /api/state, callsites NO usan) | Sprint S3.1 refactor callsites |
| B4 Delta by Risk (8) | 8 | UI-only | Sprint S3.2 refactor pivot_analysis.py |
| B5 Ticker Config (20) | 20 | UI-only | Sprint S3.3 refactor theta_harvest_strategy.py |
| B6 VIX Credit Table (30) | 30 | UI-only | Sprint S3.4 refactor |
| B7 DTE Schedule (7 fieldIds) | 7 | **FUNCIONAL ✅** | _compute_theta_dtes refactored |
| S2.1 (2) | 0 (ambos readonly) | Via /api/config (existente) | No es scope de Sprint S3 |

## Stats

- main.py: +407 líneas
- crop_main.py: +5 líneas
- dashboard-crop.html: +126 / -14
- **Total: +542 / -14**

## Stack final en producción

```
Service:     eolo-bot-crop (us-east1)
Revision:    eolo-bot-crop-s3-complete-20260513-2032
Image:       gcr.io/eolo-schwab-agent/eolo-bot-crop:s3-complete-20260513-2022
Traffic:     100%
Git HEAD:    52a78a6 (main)  -- Merge Sprint S3
Tag:         sprint-s3-complete-2026-05-13
```

## Rollback paths

Si surge un issue crítico post-promote:

```bash
# Rollback A (último estable previo, S2.3)
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --to-revisions=eolo-bot-crop-s2-3-b7-20260513-1509=100
```

Pre-S3 revisions disponibles (en 0% traffic):
- `eolo-bot-crop-s2-3-b7-20260513-1509` — rollback inmediato (S2.3 complete)
- `eolo-bot-crop-s2-3-c1-fix2-20260512-1738` — fallback 2 (pre-S2.3 frontend)
- `eolo-bot-crop-sprint-s1-20260512-1053` — fallback 3 (pre-S2)

## Lecciones aprendidas

### Hallazgos durante exploración

1. **Stack web es Flask, no FastAPI**: el plan inicial asumía FastAPI. La realidad del repo se descubre durante PASO 1 de cada sprint — siempre verificar antes de codear.

2. **`/api/config` ya existe**: maneja 14 campos planos (budget, max_pos, daily_cap, kill switches, etc) y persiste a Firestore `eolo-crop-config/settings`. **Está activo** (7 POSTs últimos 7 días desde Chrome residencial). Sprint S3 coexiste con él, no lo modifica.

3. **3 overlaps con `/api/config`**: descubiertos durante investigación, no en el plan inicial. `entry_hour_et`, `max_positions`, `daily_loss_cap_pct` viven en ambos. Resolución: readonly en UI + blocklist en backend.

### Bugs evitados

1. **Regex script fragility**: PASO 3 de S3.B usaba regex `return\s+(\w+)\s*$` que falla con `return {...}` (dict literal). Detectado antes de ejecutar, fix manual con Edit tool. **Lección**: para refactors de funciones existentes, Edit tool es más robusto que regex sed/python.

2. **`bool` is subclass of `int`** (Python gotcha): `isinstance(True, int) == True`. Validation S3.D rechaza `bool` explícitamente antes de check numeric, sino True/False pasaban como 1/0.

3. **UTC consistency**: `_compute_theta_dtes` original usaba `datetime.now(timezone.utc).weekday()`. Plan inicial proponía `datetime.now()` (local). Decisión: preservar UTC para consistencia con resto del bot.

### Edge case en deploy

**Cleanup state durante canary→100%**: smoke tests dejaron `stop_loss_mult: 1.5` overriding 1.25. Cloud Run scaling momentáneo durante traffic transition dejó instancia con el override. Reset post-promote vía POST a default 1.25.

**Mitigación futura**: incluir cleanup explícito como último paso de canary validation, o documentar que canary tests deben ejecutar contra una instancia separada.

## Backlog actualizado

| Sprint | Componente | ETA | Prioridad |
|--------|-----------|-----|-----------|
| S3.X | Firestore persistence | 2-3h | Cuando V1 termine |
| S3.1 | Refactor callsites STOP_LOSS_MULT et al en crop_main.py | 3-4h | Media |
| S3.2 | Refactor callsites delta_by_risk en pivot_analysis.py | 2-3h | Media |
| S3.3 | Refactor callsites TICKER_CONFIG | 3-4h | Media |
| S3.4 | Refactor callsites VIX_CREDIT_TABLE | 2-3h | Media |
| S3.5 | Granular Position Sizing | 4-6h | Mockup aprobado |
| S2.4 | Audit Log Drawer | 1-2h | Baja |
| S4 | Alertas + Health monitoring | 7-9h | Alta |
| S5 | Analytics + Backtesting | 10-12h | Alta |

## Conclusión

Sprint S3 cierra el ciclo de integración UI ↔ backend iniciado en S2.3. Los inputs ahora pueden:
- Editarse desde el dashboard (82/87 inputs)
- Persistirse al bot in-memory vía POST /api/state/edit
- Validarse en 3 capas (allowlist, range, cross-field) con feedback claro al frontend
- Reflejarse inmediatamente en /api/state para confirmación visual

Solo el bloque B7 (DTE Schedule) afecta funcionalmente al bot post-S3. Los demás overrides quedan visibles en state pero requieren Sprint S3.1+ para que los callsites originales los respeten.

Firestore persistence se agrega en S3.X cuando V1 termine en Firestore para evitar colisiones de namespace.
