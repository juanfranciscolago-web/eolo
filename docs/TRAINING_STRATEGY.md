# Training Strategy — Camino más rápido a autonomía LLM exitosa

**Fecha:** 2026-05-31
**Pregunta de Juan:** "¿Cómo entrenar la LLM para que sea 100% autónoma lo más rápido posible? ¿Con datos pasados, preguntas específicas, o datos disponibles?"
**Objetivo:** Plan de 30 días para llevar el LLM de "decisiones conservadoras subóptimas" a "decisiones autónomas accionables y exitosas".

---

## Concepto fundamental

### ❌ Lo que NO podemos hacer

- **Fine-tuning de Claude:** Anthropic NO permite fine-tunear sus modelos foundation
- **Re-entrenar desde scratch:** no aplica para LLM API-based
- **Reinforcement learning con outcomes:** tampoco disponible directamente

### ✅ Lo que SÍ podemos hacer

Lo que se llama "training" en este contexto es realmente **iterar 4 sistemas** que rodean al LLM:

```
            ┌─────────────────────┐
            │   Knowledge Base    │ ← Palanca #1 (cases curados)
            │   (Excel + RAG)     │
            └──────────┬──────────┘
                       │
                       ▼
   ┌──────────────────────────────────────┐
   │     LLM Engine (Sonnet 4.5)          │
   │     ├─ System prompt                 │ ← Palanca #3 (prompt eng.)
   │     ├─ Snapshot (input)              │
   │     └─ Decision (output)             │
   └─────────────┬────────────────────────┘
                 │
                 ▼
   ┌──────────────────────────────────────┐
   │  Snapshot enrichment                 │ ← Palanca #2 (data fields)
   │  (VIX, RSI, P/C, GEX, macro, etc.)   │
   └──────────────────────────────────────┘

                  + Feedback loop
   ┌──────────────────────────────────────┐
   │  Trade outcomes → case curation      │ ← Palanca #4 (loops)
   │  GOLD/SILVER/BRONZE classifier       │
   └──────────────────────────────────────┘
```

**Las 4 palancas, en orden de ROI:**

1. **Knowledge Base** (cases curados + reglas refinadas)
2. **Snapshot enrichment** (más data fields → mejor contexto)
3. **Prompt engineering** (sistema + few-shots)
4. **Feedback loops** (outcomes → KB updates automático)

---

## Estado actual del sistema (baseline)

### KB v1.2
- 61 reglas (24 TACTICAL + 13 TACTICAL_PLUS + 11 MAESTRA + 6 PROTOCOLO + 5 PROHIBITIVA + 2 AXIOMA)
- **0 GOLD cases**, 6 SILVER cases, 0 BRONZE cases
- Sprint 18 planeado: colapsar TACTICAL → reduce ruido

### Snapshot
- ~30 fields: VIX, RSI 2m/15m, MACD, EMAs, ATR, BVP/SVP, sector, key_levels
- **Falta:** GEX, put/call ratio, macro calendar, VIX term structure, sentiment

### Prompts
- System prompt KB-based
- Snapshot inyectado como user message
- RAG sobre Cases sheet
- Sin few-shot examples explícitos

### Feedback loops
- Trades logged a Firestore con decision_meta + outcome (Sprint 9+10)
- **Pero:** sin pipeline automático para identificar mejores cases
- Sprint 19 planeado: 3-5 GOLD cases manuales

### Métricas baseline (proyectadas post Sprint 21 fix)
- LLM calls/día: ~10
- Sonnet vs Haiku-skip: 30/70
- Cost/día: ~$0.06
- Confidence promedio: probable 5-7 (calibración conservadora)
- % verdicts decisive (no WAIT): probable 30-50%

---

## Las 3 palancas en detalle

### Palanca 1 — Knowledge Base curated cases (TU MAYOR ROI)

#### El gap actual

Solo 6 SILVER cases en KB v1.2. El LLM hace RAG sobre ellos pero la sample es chica → reasoning genérico.

#### Por qué importa más que todo lo demás

Investigación: **un solo GOLD case bien curado mejora el reasoning del LLM más que 10 reglas TACTICAL nuevas.** Razón: los casos contienen contexto end-to-end (setup + decisión + outcome + lesson) que las reglas no transmiten.

#### Plan de ejecución (4 semanas)

**Semana 1: Backfill auto-classify**
- Correr `scripts/classify_trades.py` sobre últimos 90 días de Firestore
- Output: ranking de candidates GOLD/SILVER/BRONZE
- Esperás 100-300 trades evaluables (asumiendo bot opera ~5-10 trades/día)

**Semana 2: Curation manual TOP 20**
- Juan revisa TOP 20 candidates (filtros: score ≥75, distintos setups)
- Marca 10 como definitivos GOLD candidates
- Llena `lesson_learned` para cada uno (~30 min/caso = ~5h total)

**Semana 3: Sprint 19 fase 2**
- Convierte los 10 candidates en GOLD cases formales (sigue formato `docs/gold_cases/`)
- Cubrir setups distintos:
  - Rally exhaustion + reversal (TR-Juan-002 family)
  - Low VIX golden ticket (TR-Juan-043 — ya hay 1 draft en PR #26)
  - Chop sideways (TR-Juan-022)
  - Pre-FOMC defensive
  - Post-VIX spike recovery
  - Trend continuation con momentum
  - Bottom pattern with confluences
  - Theta decay puro (TR-Juan-014 — ya hay 1 draft en PR #26)
  - Range-bound IC sequential
  - High-conviction SELL_CALL en weak rally

**Semana 4: Ingerir al KB v1.3**
- UP-1.2 fase 2 (add-rule/edit-rule script) debe estar mergeado
- Usar el script para insertar los 10 GOLD cases al Excel
- Bump KB a v1.3 + redeploy LLM Engine
- A/B test 1 semana vs v1.2 baseline

**Target post 30 días:**
- 20-25 GOLD/SILVER cases curados (vs 6 hoy)
- 10x más signal density para RAG

#### Heurística scoring del classifier

`scripts/classify_trades.py` ya está en repo. Cutoffs:

| Score | Class | Acción |
|---|---|---|
| ≥75 | GOLD candidate | Curate como caso formal en KB |
| 50-74 | SILVER candidate | Considera si cubre gap del KB |
| <50 | BRONZE | Lecciones de qué NO hacer (also valuable) |

Componentes (max 100):
- PnL pct (40): outcome quality
- Time efficiency (20): rápido captura mejor
- Exit clean (15): TAKE_PROFIT > STOP_LOSS
- LLM-driven (10): Sonnet > rule-based
- Meta completeness (10): rules + reason + confidence
- Confidence calibration (5): high conf + positive outcome bonus

#### ROI estimado

🟢 ALTÍSIMO. Esta es la palanca con mejor retorno marginal.

---

### Palanca 2 — Snapshot enrichment (DESBLOQUEA NUEVAS REGLAS)

#### Por qué importa

Más data fields en el snapshot = más dimensiones para que el LLM razone. Reglas tipo "VIX low + estable → GOLDEN TICKET" no se pueden activar sin esos fields.

#### Plan recomendado

Ver `docs/DATA_SOURCES_FREE.md` para evaluación completa de 10 sources gratis.

**Sprint OBS-Data-1 (Semana 1, ~3h):**

Integrar Top 3 sources al snapshot:

| Source | Field | Reglas desbloqueadas |
|---|---|---|
| CBOE Put/Call ratio | `put_call_ratio: float` | 4-6 contrarian setups |
| FRED macro calendar | `days_to_next_macro_event: int`, `next_macro_event_type: str` | 5-8 pre-event defensive |
| CBOE VIX term structure | `vix9d: float`, `vx2_vx1_ratio: float`, `term_structure_state: str` | 3-5 contango/backwardation |

Total: 6 fields nuevos → 12-19 reglas KB candidatas.

#### ROI estimado

🟢 ALTO. Cada field nuevo es una dimensión nueva de razonamiento.

---

### Palanca 3 — Confidence calibration (resultado natural de 1+2)

#### Por qué el LLM hoy es conservador

Sin GOLD cases que digan "este setup es A+, andá con confianza 9", el LLM por defecto modera su confidence. Output típico: confidence 5-7, muchos WAIT verdicts.

#### Cómo "siempre accionar" sin perder plata

❌ **MAL approach:** relajar las reglas o forzar confidence mínima → vas a perder plata
✅ **BIEN approach:** dar al LLM más razones para tener high confidence cuando el setup lo amerita

#### Mecánica concreta

1. **Más GOLD cases (Palanca 1)** → el LLM ve "este setup similar tuvo outcome +71%" → su confidence justificada sube
2. **Más data fields (Palanca 2)** → "VIX low + estable + P/C ratio 0.65 + 8 días a FOMC + contango fuerte" = 5 confluences → confidence 9 vs 5 con solo VIX
3. **Sprint 18 KB v1.3** → menos reglas TACTICAL ruidosas que dilute reasoning → más focus en MAESTRA/AXIOMA

#### Métricas a trackear

| Métrica | Baseline (hoy) | Target 30d |
|---|---|---|
| % verdicts no-WAIT | 30-50%? | 60-75% |
| Avg confidence en SELL_PUT/SELL_CALL | 5-7? | 7-9 |
| Win rate de trades >conf 7 | TBD | >75% |
| Sharpe ratio LLM-SPY | TBD | +0.2 vs rule-based |

#### ROI estimado

🟢 MEDIO-ALTO. Resultado emergente de las otras 2 palancas.

---

### Palanca 4 — Feedback loops (auto-iteration)

#### Estado actual

- Trades logged ✓ (Sprint 9+10)
- decision_meta poblado ✓ (Sprint 17)
- outcome capturado ✓ (Sprint 9+10)
- case_quality manual classification ⚠️ (script auto-classifier ya está, manual curation pendiente)
- Auto-ingest a KB ❌ (requires UP-1.2 fase 2)

#### Plan para cerrar el loop

1. **`scripts/classify_trades.py`** (✅ DONE — agregado tonight)
2. **Workflow semanal:**
   - Lunes: Juan corre `classify_trades.py --days 7` → ranking semana
   - Lunes-mar: Juan curates TOP 5 candidates de la semana → drafts gold cases
   - Miercoles: ingerir a KB via UP-1.2 fase 2
   - Jueves: redeploy LLM Engine con KB updated
   - Resto semana: monitor performance del LLM con el nuevo KB
3. **Long-term: pipeline automatizada**
   - Cron weekly que corre classifier + envía email con candidates
   - Juan aprueba/rechaza via simple UI
   - Auto-ingest a KB → Cloud Function deploy → bot toma KB nueva en ~25 min

#### ROI estimado

🟡 MEDIO. Compound effect — cada semana el KB mejora, el LLM toma mejores decisiones, los outcomes mejoran, más GOLD cases emergen, etc.

---

## Plan integrado de 30 días

### Semana 1 (Jun 1-7)

| Día | Acción | Esfuerzo | Owner |
|---|---|---|---|
| Lun 1 | Deploy PR #28 + #27 + #24 (runbook) | 1h | Juan |
| Lun 1 | Verificar Sprint 21 cost visible | 15 min | Juan |
| Mar 2 | Correr `classify_trades.py --days 60` | 5 min | Juan |
| Mar 2 | Review TOP 30 candidates → seleccionar TOP 15 GOLD candidates | 2h | Juan |
| Mié 3 | Merge PR #25 + #26 (UP-1.2 fase 1 + UP-1.3 fase 1) | 30 min | Juan |
| Jue 4 | Merge PR #29 + #30 (OBS-1/2 stack) | 1h | Juan |
| Vie 5 | Verify OAuth Cloud Functions | 5 min | Juan |

**Resultado semana 1:** 5 PRs LIVE, 15 GOLD candidates identified.

### Semana 2 (Jun 8-14)

| Día | Acción | Esfuerzo | Owner |
|---|---|---|---|
| Lun 8 | UP-1.2 fase 2 — add-rule/edit-rule script | 4-6h Claude Code | Pareja terminals |
| Mar 9 | Curar los 15 GOLD candidates → llenar lesson_learned | 6-8h Juan | Juan |
| Mié 10 | Sprint OBS-Data-1: integrar P/C ratio + FRED + VIX term | 3h Claude Code | Claude Code |
| Jue 11 | Deploy snapshot enrichment + verify | 30 min | Juan |
| Vie 12 | Convertir 5 GOLD candidates a casos formales en markdown | 5h Juan | Juan |

**Resultado semana 2:** 5 GOLD cases drafted, snapshot con 6 fields nuevos, KB editor v2 LIVE.

### Semana 3 (Jun 15-21)

| Día | Acción | Esfuerzo | Owner |
|---|---|---|---|
| Lun 15 | Convertir 10 GOLD candidates más a casos | 8-10h Juan (distribuir) | Juan |
| Mar 16 | Ingerir los 15 GOLD cases al KB Excel (vía editor) | 1-2h | Juan |
| Mié 17 | Sprint 18: KB v1.3 (colapsar TACTICAL → TACTICAL_PLUS) | 2-4h | Claude Code |
| Jue 18 | Bump KB a v1.3 + redeploy LLM Engine | 30 min | Juan |
| Vie 19 | A/B baseline metrics setup (LLM-v1.2 vs LLM-v1.3) | 1h | Juan |

**Resultado semana 3:** KB v1.3 LIVE con 15 GOLD cases + nuevos fields + nuevas reglas tier-clean.

### Semana 4 (Jun 22-28)

| Día | Acción | Esfuerzo | Owner |
|---|---|---|---|
| Todo | Monitor performance KB v1.3 vs v1.2 baseline | Passive | Juan |
| Mié 25 | Mid-week metrics review + ajustes prompt si necesario | 1h | Juan |
| Vie 27 | Comprehensive metrics doc: Sharpe, win rate, confidence distribution | 2h | Juan + Claude |

**Resultado semana 4:** Data para decidir UP-2.1 (LLM scope extension a otros tickers).

---

## Métricas de éxito post 30 días

### Operacional

| Métrica | Baseline (estimado) | Target | Cómo medir |
|---|---|---|---|
| GOLD cases en KB | 0 | 15 | `kb_editor.py stats` |
| Reglas KB | 61 (v1.2) | 43 (v1.3) | `kb_editor.py list-rules` |
| Snapshot fields | ~30 | ~36 | grep en `snapshot.py` |
| % verdicts no-WAIT | 30-50% | 60-75% | UP-2.2 dashboard verdict distribution |
| Avg confidence en actions | 5-7 | 7-9 | trade history meta analysis |
| LLM Engine revisions deployed | 1 (00003-sqn) | 3+ | gcloud run revisions list |

### Performance trading (TESTNET / Paper)

| Métrica | Baseline (Sprint 21 LIVE 31-may) | Target 30d |
|---|---|---|
| Trades LLM-SPY / semana | ~25-50 (estimado) | ~50-75 |
| Win rate trades >conf 7 | TBD (medir) | >75% |
| Avg PnL pct | TBD | +5-10% vs baseline |
| Cost LLM / mes | ~$1.70 | ~$2.50 (más actions = más Sonnet calls) |
| Sharpe LLM-SPY vs rule-based otros | TBD | +0.2 |

### Validation

- Semana 4: A/B doc comparing KB v1.2 baseline vs KB v1.3+GOLD performance
- Semana 6: re-evaluate UP-2.1 (extender LLM a QQQ/IWM/TQQQ?)

---

## Riesgos identificados

| Riesgo | Severidad | Mitigación |
|---|---|---|
| GOLD curation toma más tiempo del estimado (8h/sem) | 🟡 MED | Distribute en sub-batches; calidad > velocidad |
| Snapshot enrichment introduce bugs sutiles | 🟢 BAJO | Tests defensive, fetch fail → None gracefull |
| KB v1.3 degrada decisiones vs v1.2 | 🟡 MED | A/B test 1 semana antes de promote; rollback ready |
| LLM confidence calibración over-shoot (todo conf 9) | 🟡 MED | Monitor win rate por confidence bucket; PROHIBITIVA rules como guard rails |
| Cost LLM sube más de 2x | 🟢 BAJO | Hoy $1.70/mes, hasta $5/mes es trivial |
| Juan sobre-curate sesgos personales en GOLD cases | 🔴 ALTO | Diversificar setups; validar con métricas, no intuición |

---

## Lo que NO va a funcionar (anti-patrones)

### ❌ Forzar al LLM a "siempre accionar"

Si relajás reglas o caps confidence → trades de baja conviction → losing trades. Bad ROI.

### ❌ Pedirle al LLM "predict the market"

LLMs no predicen precios. Sí razonan sobre setups risk/reward. Mantén el prompt enfocado en "evaluá este setup" no "predecí dirección".

### ❌ Agregar 100 reglas TACTICAL nuevas

Más reglas = más ruido. Sprint 18 va al revés (colapsa TACTICAL). Calidad > cantidad.

### ❌ Cambiar de Sonnet a Haiku para "ahorrar cost"

Haiku tiene menos reasoning quality. Cost diff es ~$2-5/mes. No vale el trade-off.

### ❌ Backtest histórico con KB actual

KB es para inferencia en setups LIVE. Backtest con KB inyectado retrospectivamente induce lookahead bias.

### ❌ Pedirle a otros LLMs (GPT-4, Gemini) que compitan

Cada modelo tiene su style. Cambiar modelo invalida todo el KB tuning. Mantén Sonnet 4.5.

### ❌ Crear features ML clásicas (random forest, NN)

Si querés ML clásico, es otro sistema (no LLM). Esfuerzo enorme, benefit dudoso vs Sonnet bien tuned.

---

## Quick wins esta semana (sin esperar plan completo)

Si querés empezar HOY/mañana sin esperar las 4 semanas:

1. **HOY:** correr `classify_trades.py --days 30` (5 min) para ver cuántos GOLD candidates tenés
2. **Lunes pre-market:** después del deploy, mirar dashboard UP-2.2 — ver baseline real de confidence + verdict distribution
3. **Lunes post-market:** seleccionar 3 candidates GOLD del classifier output y empezar a llenar lesson_learned

Con eso ya tenés 3 GOLD cases sembrados en la primera semana, sin esperar UP-1.2 fase 2.

---

## Recursos creados esta noche

| Recurso | Propósito | Status |
|---|---|---|
| `scripts/classify_trades.py` | Auto-classifier trades → GOLD/SILVER/BRONZE candidates | ✅ DONE |
| `docs/DATA_SOURCES_FREE.md` | Evaluación 10 free data sources ranked | ✅ DONE |
| `docs/TRAINING_STRATEGY.md` | Este doc (plan 30 días) | ✅ DONE |

---

## Resumen ejecutivo

**Pregunta:** "¿Cómo entrenar el LLM para que sea 100% autónoma exitosa, lo más rápido posible?"

**Respuesta:** No se "entrena" en sentido ML. Se itera 4 palancas:
1. **Knowledge Base con GOLD cases curados** (TOP ROI)
2. **Snapshot enrichment** con free data sources
3. **Confidence calibration** (emergente)
4. **Feedback loops** outcomes → KB

**Plan 30 días:**
- Sem 1: Run classifier + identify 15 candidates + deploy 5 PRs draft
- Sem 2: Build UP-1.2 fase 2 + curate candidates + integrate Top 3 data sources
- Sem 3: 15 GOLD cases LIVE en KB v1.3 + Sprint 18 ejecutado
- Sem 4: A/B metrics + decisión UP-2.1 (multi-ticker)

**Resultado esperado:** % verdicts no-WAIT de 30-50% → 60-75%, avg confidence 5-7 → 7-9, Sharpe +0.2, 15 GOLD cases vs 0 baseline.

**Costo extra:** ~$3-4/mes adicional (cost LLM scale by usage).

**Mayor risk:** GOLD curation toma tiempo de Juan. Si no puede dedicar 15-20h en 4 semanas, plan se estira a 6-8 semanas.

---

**Generado:** 2026-05-31
**Para discutir:** lunes con data baseline real, ajustar plan basado en cuánto tiempo puede dedicar Juan en jun.
