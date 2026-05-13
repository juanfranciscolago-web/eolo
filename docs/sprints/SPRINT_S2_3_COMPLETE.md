# Sprint S2.3 — Edit Mode + 6 Sub-Paneles Editables

**Period:** 2026-05-12 → 2026-05-13 (2 días)
**Status:** ✅ COMPLETO
**Total inputs editables UI:** 82/87 (94%)

## Bloques deployados

| Bloque | Componente | Inputs | Tag |
|--------|-----------|--------|-----|
| C1 | Foundation + S2.1 inputs | 2 | `sprint-s2-3-c1-2026-05-12` |
| B3 | Exits Advanced | 15 | `sprint-s2-3-b3-2026-05-13` |
| B4 | Delta by Risk | 8 | `sprint-s2-3-b4-2026-05-13` |
| B5 | Ticker Config | 20 | `sprint-s2-3-b5-2026-05-13` |
| B6 | VIX Credit Table | 30 | `sprint-s2-3-b6-2026-05-13` |
| B7 | DTE Schedule (UI-only) | 7 fieldIds (35 checkboxes) | `sprint-s2-3-b7-2026-05-13` |
| **B8** | **Final consolidation** | — | `sprint-s2-3-complete-2026-05-13` |

## Architectural decisions

### Decisión: `vix_ceil` NO editable (B6 Opción D)
Cambiar VIX bins es decisión arquitectónica. Afecta entry/risk/tranche logic.
Editar credits/payoff per band es operational tweak (permitido).

### Decisión: T2 tranche profit target editable como T0/T1
Todos los 3 inputs editables. Si actual es `null`, input arranca vacío con placeholder "EXP".

### Decisión: B7 backend hardcoded
DTE Schedule actualmente hardcoded en `_compute_theta_dtes()` (crop_main.py:3039).
B7 deployado como UI-only. Backend refactor pendiente para Sprint S3.

### Decisión: Validación rango individual sin pair-wise
Backend Sprint S3 enforce cross-field (`delta_min < delta_max`, etc).

### Patrones UX establecidos
- `_renderEditableNumber` para inputs numéricos simples
- `_renderEditableNumberWithSuffix` para inputs con unidad estática (%, x, s, min)
- `_renderEditableNumberLiveSuffix` para inputs con suffix live-computed (=X.X%)
- `_renderEditableArray` para arrays con índices [T0, T1, T2]
- Checkboxes (`.editable-checkbox`) para boolean toggles por celda (B7)
- `inputDraftValues` separado de `pendingEdits` (preserva UX en valores inválidos)

## Lecciones aprendidas

- **L52: Scope reveal durante exploración** — S2.3 estimado 15-20 inputs en C1, real 87 inputs. Checkpoint inteligente preserva calidad.
- **L54: Architectural vs operational tweaks** — `vix_ceil` + DTE schedule son architectural, no operational. Decisiones separadas.
- **L55: Verificar UI con tests visuales explícitos** — usuario pidió instrucciones más claras antes de aprobar B6 (test ✓/× con descripción paso a paso).
- **L56: Mockup interactivo para feature compleja** — Position Sizing S3.5 aprobado vía mockup HTML interactivo antes de implementar.

## Backlog actualizado

### Sprint S3 (Próximo, ETA 7-9h)
Backend Edit + Persistence:
- POST `/api/state/edit` endpoint
- Firestore persistence
- Refactor `_compute_theta_dtes()` para leer del state (cierra deuda B7)
- Validation cross-field (pair-wise, monotonicity)

### Sprint S3.5 (APROBADO vía mockup, ETA 4-6h)
Granular Sizing Control — Nivel 3 (Override por día de semana):
- 7mo sub-panel "💰 Position Sizing"
- Matriz Day × Ticker × DTE = 5 × 4 × 5 = 100 inputs
- Selector de día (tabs Lunes/Martes/.../Viernes)
- Presets: Conservador / Moderado / Agresivo / Solo 0DTE / OFF
- Botones: "Copiar día a todos" / "Reset día"
- Resumen: Total semanal + Capital expuesto estimado
- Backend: refactor `_compute_size()` para leer del state
- Pattern: misma arquitectura que sub-paneles existentes
- Requiere Sprint S3 primero (persistence)

### Sprint S2.4 (Mid)
Audit Log Drawer (1-2h):
- Track de cambios en `pendingEdits` con timestamps
- Drawer lateral mostrando historial
- "Revert this change" por entrada

### Sprint S4-S6
- S4: Alertas + Health Monitoring (7-9h)
- S5: Analytics + Backtesting (10-12h)
- S6: Multi-account & Advanced

## Stack final en producción

| Capa | Componente |
|---|---|
| Foundation | F2 cleanup (theta_harvest only) |
| Bug fixes | AC.1 + AE + AM + AN + AC.2 + Card3 fix |
| UI Sprint S1 | Strategy params 5 sub-panels (readonly) |
| UI Sprint S2.1 | Risk Management Panel (4 cards) |
| UI Sprint S2.2 | Active Positions Tracker + risk filter |
| UI Sprint S2.3 | Edit Mode foundation + 6 sub-panels editables |

**Revisión productiva final**: `eolo-bot-crop-s2-3-b7-20260513-1509` @ 100%
**Image**: `95cbd439-d3d1-4a7d-9d05-d46d23f62732`
**Git HEAD main**: `e228f2b`
**HTML size**: 193KB

## Rollback chain disponible

| Letra | Revisión | Estado |
|---|---|---|
| Current | `s2-3-b7-20260513-1509` | B7 (current) |
| A | `s2-3-b6-20260513-1427` | sin B7 |
| B | `s2-3-b5-20260513-1228` | sin B6+B7 |
| C | `s2-3-b4-20260513-0952` | sin B5+B6+B7 |
| D | `s2-3-b3-live-20260513-0915` | sin B4+B5+B6+B7 |
| E | `s2-3-c1-fix2-20260512-1738` | solo S2.1 inputs |
| F | `s2-2-risk-20260512-1604` | sin S2.3 entero |
| G+ | (earlier — see rollback plans of previous sprints) |

## Sprint metrics

- **Duración**: 2 días (12-may + 13-may)
- **Bloques deployados**: 8 (C1 + B3 + B4 + B5 + B6 + B7 + B8 final)
- **Deploys + Promotes**: ~16
- **Commits a main**: ~20
- **Tags milestone**: 7 (1 por bloque + B8 complete)
- **Inputs editables UI**: 82/87 (94%, los 5 restantes son `vix_ceil` rows readonly por decisión arquitectónica)
- **Backend persist**: Pendiente Sprint S3 (estado deseado)
- **Errors en producción**: 0
