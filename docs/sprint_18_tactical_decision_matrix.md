# Sprint 18 — TACTICAL Tier Decision Matrix (KB v1.3)

**Fecha:** 2026-05-31
**Autor:** Claude (audit dry-run via tools/kb_editor.py)
**Estado:** Draft — listo para ejecutar cuando UP-1.2 fase 2 (add-rule/edit-rule) esté mergeado
**Source:** `llm_engine_eolo/kb/EOLO_ThetaHarvest_v1.2.xlsx` con base c93efc0

---

## Resumen ejecutivo

| Tier | KB v1.2 actual | KB v1.3 propuesto | Delta |
|---|---|---|---|
| AXIOMA | 2 | 2 | 0 |
| PROHIBITIVA | 5 | 5 | 0 |
| MAESTRA | 11 | 12 | +1 (promote TR-023) |
| PROTOCOLO | 6 | 6 | 0 |
| TACTICAL_PLUS | 13 | 18 | +5 (promotes from TACTICAL) |
| **TACTICAL** | **24** | **0** | **-24** (colapso total) |
| **Total** | **61** | **43** | **-18** |

**Objetivo final:** Eliminar el tier TACTICAL completamente. Las reglas con valor concreto se convierten en TACTICAL_PLUS (con triggers schema-friendly tipo `rsi_zone = oversold_bouncing` en lugar de "RSI haciendo top"). Las redundantes se mergean en MAESTRA/AXIOMA. Las puramente conceptuales se demoten a notes inline.

**Beneficios esperados:**
- KB ~30% más concisa (43 vs 61 reglas) → prompts más cortos → menos tokens → menos costo
- Triggers schema-driven → LLM puede aplicar reglas con menos ambigüedad
- Sin más tier que el LLM debe priorizar mentalmente (TACTICAL siempre fue ambiguo)

---

## Citation analysis (con sample de 6 SILVER cases)

| Status | Count | Reglas |
|---|---|---|
| 🟢 Cited ≥2 veces | 5 | TR-002, TR-003, TR-006, TR-025, TR-026, TR-027 |
| 🟡 Cited 1 vez | 8 | TR-001, TR-007, TR-021, TR-023, TR-024, TR-029, TR-030, TR-032, TR-035 |
| 🔴 Cited 0 veces | 11 | TR-004, TR-005, TR-008, TR-009, TR-013, TR-016, TR-017, TR-018, TR-028 |

**Caveat:** La sample es chica (6 cases). Cited=0 no significa "dead en producción LLM" — significa "no anotada en cases curados manualmente". Para Sprint 18 validation real, esperar a tener data del primer mes de Sprint 21 deployed (input_tokens/output_tokens trackeados desde 31-may en rev `llm-engine-service-00003-sqn`).

---

## Decision matrix — 24 reglas TACTICAL

### Categoría 1: DELETE (4 reglas) — redundantes con MAESTRA/AXIOMA

| ID | Razón | Cubierto por |
|---|---|---|
| **TR-Juan-005** | "ATR decrec + VIX decrec → theta excelente" — caso particular del axioma "theta siempre a favor" | TR-Juan-014 MAESTRA |
| **TR-Juan-008** | "VIX velocity+ + decay visible → mantener" — caso particular del mismo axioma | TR-Juan-014 MAESTRA |
| **TR-Juan-013** | "VIX bajo + spike-free → sesión ideal Theta Harvest" — duplicada literal del AXIOMA #2 | TR-Juan-043 AXIOMA |
| **TR-Juan-018** | "Divergencia \|RSI - VIX-Inv\| > 15 → fade" — requiere VIX-Inv que no está en snapshot. Sin uso real | (orphan — no replacement) |

### Categoría 2: MERGE (10 reglas) — consolidar en TACTICAL_PLUS existentes

| ID origen | Action | Merge target | Resultado en KB v1.3 |
|---|---|---|---|
| **TR-Juan-001** | MERGE | (nuevo TACTICAL_PLUS, ver promotes) | "Volume spike premarket + gap" → trigger schema |
| **TR-Juan-004** | MERGE | TR-Juan-050 / TR-Juan-051 | Rally + reversal cubierto por overbought_fading trigger |
| **TR-Juan-007** | MERGE | TR-Juan-019 / TR-Juan-020 | "VIX bottom + SPY top" = caso particular de "RSI cruza 60 abajo + overbought" |
| **TR-Juan-009** | MERGE | TR-Juan-001 (mergeada arriba) | "Primer rally VIX 9:30-9:50" parte del setup early-session |
| **TR-Juan-016** | MERGE | TR-Juan-019 | "DOBLE OVERBOUGHT" caso particular de RSI cruza 60 |
| **TR-Juan-017** | MERGE | TR-Juan-020 | "DOBLE OVERSOLD" caso particular de RSI cruza 30 |
| **TR-Juan-028** | MERGE | TR-Juan-022 / TR-Juan-031 | "VWAP+Fibo range-bound" añade dimensión a IC setup existente |
| **TR-Juan-031** ⚠️ | MERGE | TR-Juan-022 | TR-031 y TR-022 son virtualmente idénticas (ambas RSI lateral + MACD plano + low vol → IC) — duplicación de Juan al agregar la TR-031 después |
| **TR-Juan-035** | MERGE | TR-Juan-024 (luego promote — ver abajo) | "Mediodia sin 50-60% → recompra parcial" + "Mediodia + buen profit → toma proactiva" = una sola regla mediodia discipline |
| **TR-Juan-024** | MERGE+PROMOTE | (nuevo TACTICAL_PLUS) | Ver promotes — la nueva regla mediodia consolida 024+035 |

### Categoría 3: PROMOTE TO MAESTRA (1 regla) — promoción de tier

| ID | Razón | Tier destino |
|---|---|---|
| **TR-Juan-023** | "50-60% profit → cerrar 50% (partial close)" — sus propias notes la marcan como "REGLA DE EXIT principal". Es la estrategia de exit más usada. MAESTRA TR-Juan-053 dice "cerrar 100%" cuando >50%, pero la práctica real es partial close primero. Esto la convierte en complemento estructural de TR-053, no en duplicada | MAESTRA |

### Categoría 4: PROMOTE TO TACTICAL_PLUS (5 reglas nuevas) — reformular con schema concreto

Las 13 TACTICAL_PLUS existentes usan triggers schema-driven (`rsi_zone = oversold_bouncing`). Las 24 TACTICAL originales usan lenguaje natural ("RSI haciendo top"). Para colapsar el tier, reformulamos las TACTICAL útiles con la misma sintaxis.

| ID origen | Nueva ID propuesta | Trigger reformulado (schema) | Action |
|---|---|---|---|
| TR-Juan-001 + TR-Juan-009 | **TR-Juan-062** | `session_label = AM_open AND price_action_pattern includes ['gap', 'volume_spike_premarket']` | "Esperar 30min para ver señal exhaustion antes de entry" |
| TR-Juan-002 + TR-Juan-006 + TR-Juan-021 | **TR-Juan-063** | `rsi_zone in ['overbought_fading', 'oversold_bouncing'] AND price_action_pattern includes 'reversal_candle'` | "Trust pattern over absolute RSI number — entry confirmation" |
| TR-Juan-003 + TR-Juan-029 | **TR-Juan-064** | `atr_value trending_down AND ema_distance compressing` | "Exhaustion confirmation — momentum agotado" |
| TR-Juan-024 + TR-Juan-035 | **TR-Juan-065** | `session_label = mid_morning_to_lunch AND time_to_close > 3h AND (open_position_profit_pct ∈ [40, 80] OR (strategy=IC AND profit_pct < 50))` | "Take profit proactively before lunch chop — discipline > greed" |
| TR-Juan-032 | **TR-Juan-066** | `key_levels_visible includes fibonacci_levels AND price respecting levels intraday` | "Use Fibonacci S/R as primary level system for entries" |

### Categoría 5: KEEP AS-IS (renombrar a TACTICAL_PLUS) (4 reglas) — únicas y concretas

| ID | Razón | Acción |
|---|---|---|
| **TR-Juan-025** | "Entry 9:30-11:00, después prima pobre" — timing window structural, 4 citas (más citada de TACTICAL) | Mover tier de TACTICAL → TACTICAL_PLUS sin cambios |
| **TR-Juan-026** | "Identificar A+ → ESCALAR multiples entries" — sizing rule estructural, 3 citas | Mover tier sin cambios |
| **TR-Juan-027** | "IC en día range → SECUENCIAL (CALL primero, PUT después)" — operativa concreta, 2 citas | Mover tier sin cambios — refuerza PROHIBITIVA TR-047 (no IC simultáneo) |
| **TR-Juan-030** | "VIX spike intraday tarde (>13:00) → monitorear riesgo cierre" — late-session risk mgmt, 1 cita | Mover tier sin cambios |

---

## Resumen final

### Reglas TACTICAL después de Sprint 18: **0**

Distribución de las 24 originales:

| Destino | Count | IDs |
|---|---|---|
| DELETE | 4 | TR-005, TR-008, TR-013, TR-018 |
| MERGE en TACTICAL_PLUS existentes | 6 | TR-004, TR-007, TR-016, TR-017, TR-028, TR-031 |
| MERGE en nuevas TACTICAL_PLUS | 7 | TR-001, TR-009, TR-002, TR-006, TR-021, TR-024, TR-035 (consolidadas en 4 nuevas IDs) |
| PROMOTE TO MAESTRA | 1 | TR-023 |
| RENAME TO TACTICAL_PLUS | 4 | TR-025, TR-026, TR-027, TR-030 |
| Otras (no listadas explícitamente) | 2 | TR-003, TR-029, TR-032 → consolidadas en TR-064/066 |
| **Total** | **24** | — |

### TACTICAL_PLUS después de Sprint 18: **18**

- 13 actuales (TR-012, TR-015, TR-019, TR-020, TR-022, TR-033, TR-034, TR-048, TR-050, TR-051, TR-052, TR-057)
- 4 promotes desde TACTICAL (TR-025, TR-026, TR-027, TR-030)
- 5 nuevas consolidadas (TR-062, TR-063, TR-064, TR-065, TR-066) — pero contando las merge-into existentes (TR-019, TR-020, TR-022, TR-050, TR-051) ya cubren parte, el delta neto es ~+5

### MAESTRA después de Sprint 18: **12** (+1: TR-023 promoted)

---

## Validación pre-ejecución

Antes de aplicar Sprint 18, ejecutar:

1. **Wait for 1 mes de prod data post-Sprint 21** — confirmar que las TACTICAL marcadas DELETE realmente no se citan en LLM logs reales (no solo en SILVER cases)
2. **Sprint 19 fase 1 cierra** (PR #26 con 2 GOLD cases mergeada + 3 GOLD adicionales) — los gold cases pueden surfaceear reglas TACTICAL útiles que esta audit no ve
3. **UP-1.2 fase 2 deployed** — `tools/kb_editor.py add-rule / edit-rule / merge-rules` para aplicar cambios sin tocar Excel a mano
4. **Backup KB v1.2** — `cp llm_engine_eolo/kb/EOLO_ThetaHarvest_v1.2.xlsx backups/kb_v1.2_pre_sprint18_$(date +%Y%m%d).xlsx`
5. **Test set de prompts pre/post** — armar 10 snapshots representativos, correr decisiones LLM con KB v1.2 y v1.3, comparar verdict + reasoning. Si v1.3 cambia decisiones materialmente, evaluar si es mejora o regresión

---

## Riesgos identificados

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Borrar TR-018 cuando en futuro agregamos VIX-Inv field al snapshot | Bajo | Documentar en commit que TR-018 está deleted por falta de field; reintroducir como TR-XXX si field se agrega |
| Mergear TR-031 ↔ TR-022 oculta que Juan las pensó separadas | Bajo | Notes de TR-022 deben citar "deriva de TR-031 mergeada en Sprint 18" |
| Demote TR-001 a parte de TR-062 pierde el matiz "premarket" | Medio | El trigger schema debe incluir `time_analysis < 09:35 ET` para preservar el matiz |
| Promote TR-023 a MAESTRA puede colisionar con TR-053 (close vs partial-close) | Medio | Notes de TR-023 nueva MAESTRA deben decir "complementa TR-053: partial close primero, full close en TR-053" |
| Sample chico de 6 cases | Alto | Esperar 1 mes data prod antes de ejecutar (ver validación pre-ejecución) |

---

## Próximos pasos

1. ✅ Audit completo — este documento
2. ⏳ UP-1.2 fase 2 — add-rule/edit-rule en tools/kb_editor.py
3. ⏳ Sprint 19 fase 2 — 3 GOLD cases más
4. ⏳ Mes de prod data — validar TACTICAL "dead" con LLM logs reales
5. ⏳ Sprint 18 ejecución — aplicar este matrix con tools/kb_editor.py merge-rules / delete-rule / promote-rule
6. ⏳ KB v1.3 deploy LLM Engine — bump version, redeploy, validar /health

---

**Generado:** 2026-05-31 (Domingo, mercados cerrados)
**Herramientas usadas:** `tools/kb_editor.py` (PR #25 draft, UP-1.2 fase 1)
**KB analizado:** `llm_engine_eolo/kb/EOLO_ThetaHarvest_v1.2.xlsx` @ c93efc0
