# Reconciliation Memo — Master Plan v2.1 ↔ Master 06-01

**Fecha:** 2026-06-01 (sesión nocturna)
**Autor:** Co-diseño Juan + Claude
**Status:** Borrador para revisión, base para Master Plan v2.2
**Source:** Verificación directa contra `~/PycharmProjects/eolo` HEAD `231c361`

---

## 1. Resumen ejecutivo

El Master Plan v2.1 (10 sprints / 12 semanas) y el Master 06-01 (estado actual) fueron escritos el mismo día (1-jun-2026) pero parten de framings distintos del sistema. Este memo reconcilia ambos contra el código real y propone un Master Plan v2.2 con roadmap realista para las próximas 4-6 semanas.

**Conclusiones materiales:**

1. **Sprint 1 (QuantData ingest) está PARCIALMENTE DONE** — 4 endpoints LIVE (`max_pain`, `iv_rank`, `gex_regime` vía `/exposure-by-strike`, `net_premium_drift`). El v2.1 hablaba de "15 campos" sin enumerarlos. Decisión necesaria: ¿cerrar Sprint 1 acá o extender a charm / vanna / dealer_positioning / net_call_premium / net_put_premium?
2. **`net_premium_drift` puede estar wire-incompleto** — el header del cliente Quant Data tiene `TODO: validar response antes de wire a snapshot`. El Master 06-01 lo afirma como LIVE. Verificación pendiente.
3. **Sprints 3, 4, 5, 6 (parte in-process), 7 (manual close), 8, 10, 11 están TO DO** sin código existente.
4. **Sprint 5 (backtest engine) está bloqueado por dependencia no resuelta** — el cliente Quant Data no expone endpoint de backlog histórico (sin matches `backlog|365|historical` en `external_data_quantdata.py`).
5. **El Master 06-01 contiene un error factual** — afirma "Flask" para LLM Engine (sec 8.1) cuando en realidad es FastAPI (`service.py` confirmado).
6. **Push status indeterminado** — local `origin/main` ref está 2 commits behind HEAD; cierre nocturno asumía push completado.

---

## 2. Metodología

Comparación tripartita:
1. **Master Plan v2.1** (`Eolo_Crop_Master_Plan_v2_1.docx`) — plan estratégico 10 sprints
2. **Master 06-01** (`EOLO_CROP_MASTER_2026-06-01.docx`) — estado actual + backlog
3. **Código real** (`~/PycharmProjects/eolo` HEAD `231c361`) — verificación con greps puntuales esta sesión

Donde docs y código difieren, el código gana. Donde docs entre sí difieren, se priorizó el más reciente (06-01) salvo cuando el código contradice (caso FastAPI).

---

## 3. Hallazgos verificados (verificación directa esta sesión)

| # | Probe | Resultado | Implicación |
|---|---|---|---|
| 1 | Framework LLM Engine | **FastAPI** confirmado (`service.py` línea 15: `from fastapi import FastAPI`) | Master 06-01 sec 8.1 **incorrecto** — necesita patch a "FastAPI" |
| 2 | Framework Bot CROP | **Flask** confirmado (`main.py` línea 19) | Master 06-01 correcto |
| 3 | `rule_evaluation_trace` | **No existe** (cero matches en `llm_engine/` y `llm_gate/`) | Sprint 3 v2.1 es genuino TO DO |
| 4 | Endpoint `/juan/suggest` | **No existe** | Sprint 4 v2.1 es genuino TO DO |
| 5 | Orchestrator / APScheduler | **No existe** in-process (ni dir `orchestrator/` ni imports) | Sprint 6 v2.1 es TO DO. Scheduled tasks viven en Cloud Scheduler externo |
| 6 | QuantData endpoints | **4 LIVE**: `get_max_pain`, `get_iv_rank`, `get_gex_regime`, `get_net_premium_drift`. Pero header del archivo: `net-drift (TODO: validar response antes de wire a snapshot)` | Sprint 1 v2.1 parcial. Wiring de net-drift sospechoso — verificar en `snapshot.py` |
| 7 | Backtest engine | **No existe** ningún dir `backtest/` | Sprint 5 v2.1 es TO DO completo |
| 8 | KB version + `kb_loader.py` | KB `v1.2.xlsx` LIVE. `kb_loader.py` **sin constante KB_VERSION** ni refs inline a "v1.2" | Master 06-01 correcto en KB version. Fix #92 sigue defensible: aunque patch a `kb_loader.py` sea no-op por ausencia del string, el flag `update_code_refs` protege `tools/kb_schema.py` que **sí** tiene la ref |
| 9 | Last commits + origin diff | HEAD `231c361`. origin/main local ref `a37588a`. **2 ahead**. | Push del cierre nocturno **no se refleja localmente**. Necesita `git fetch` o `git push` real |
| 10 | Quant Data backlog histórico | **No existe** (cero matches `backlog\|365\|historical` en cliente) | Sprint 5 (backtest 365d) tiene dependencia no resuelta |

---

## 4. Discrepancias críticas

### 4.1 Master 06-01 vs realidad — FastAPI/Flask

| Doc | Afirmación | Realidad |
|---|---|---|
| Master 06-01 sec 8.1 tabla "Web framework LLM Engine" | "Flask" | **FastAPI** (verificado en `service.py`) |
| Master 06-01 sec 8.1 tabla "Web framework bot" | "Flask" | Flask (correcto) |
| v2.1 sec 3.1 "service.py — FastAPI app" | "FastAPI" | **FastAPI** (correcto) |

**Acción:** patchar Master 06-01 sec 8.1 al regenerar.

### 4.2 Master 06-01 vs realidad — `net_premium_drift` LIVE?

Master 06-01 sec 10.4 dice "4 endpoints LIVE" incluyendo `net_drift`. Pero header de `eolo-crop/llm_gate/external_data_quantdata.py` línea 9:

```
/v1/options/tool/net-drift         (TODO: validar response antes de wire a snapshot)
```

**Riesgo:** posible que el fetch funcione pero el valor no esté entrando al snapshot que ve el LLM. Si es así, el LLM toma decisiones sin uno de los 4 inputs Quant Data.

**Acción:** verificación pendiente — grep en `snapshot.py` por uso de `get_net_premium_drift` o key `net_drift`.

### 4.3 v2.1 vs realidad — Sprint 1 "QuantData ingest" parcial

v2.1 sec 12.2 (Sprint 1):
- Objetivo: "Que MarketSnapshot tenga campos QuantData"
- Acceptance criteria: "test_snapshot_with_quantdata_fields passes"
- Estima 15 campos

Realidad: 4 endpoints, no 15. Pero v2.1 nunca enumera los 15 — solo menciona charm, vanna, dealer_positioning, net_call_premium, net_put_premium como "futuros" en sec 15.2.

**Acción:** decisión estratégica (sec 7 abajo) — cerrar Sprint 1 a 4 endpoints o extender.

### 4.4 v2.1 vs realidad — KB version

v2.1 sec 3.1 dice "KB v1.1 (61 reglas, 6 casos)". Realidad: KB v1.2 LIVE. Diff cosmético (mismo conteo de reglas y casos), pero refleja que v2.1 fue redactado con un snapshot mental ligeramente anterior.

### 4.5 Sprint 18 (master 06-01) ⇄ Sprint 2 (v2.1) — overlap

Sprint 18 plan (master 06-01 sec 14.1): "TACTICAL tier rediseño KB v1.3", target ~8-jun, audita 24 reglas TACTICAL existentes con data real.

Sprint 2 plan (v2.1 sec 12.3): "Decision matrix + 10 reglas nuevas TR-Juan-062 a 071" usando QuantData fields.

**Solapamiento:** ambos bumpean KB de v1.2 a v1.3, ambos tocan Decision_Rules sheet. Hacerlos secuenciales (S2 → S18) produce churn (Sprint 18 podría deprecar reglas Sprint 2 acabadas de agregar).

**Acción:** consolidar como un único Sprint KB-v1.3 que combina (a) consolidación TACTICAL existentes + (b) agregar 10 reglas QuantData-aware. Renombrar a Sprint UP-1.4 o similar.

---

## 5. Sprint-by-sprint reconciliation

Mantengo el numeración v2.1 (S1-S11) por trazabilidad. Cada sprint con estado real + acción propuesta.

| Sprint v2.1 | Scope original | Estado real verificado | Esfuerzo restante | Acción propuesta v2.2 |
|---|---|---|---|---|
| **S1** QuantData ingest | 15 fields, MarketSnapshot extendido | **PARCIAL**: 4 endpoints LIVE (max_pain, iv_rank, gex_regime, net_drift). 11 sin implementar. Net-drift wiring sospechoso. | Verificación net-drift wiring (~1h) + decisión add 11 more (TBD) | **Cerrar S1 con 4 endpoints** + sub-tarea "verificar net-drift wiring". 11 endpoints adicionales pasan a S1.B (futuro, post-backtest justifica cuáles agregar) |
| **S2** Decision matrix + 10 rules TR-Juan-062 a 071 | KB v1.2 + rules nuevas QuantData-aware | **TO DO**, **overlaps Sprint 18** | 1-2 sem (combinado con Sprint 18) | **Mergear con Sprint 18** → Sprint UP-1.4 unificado |
| **S3** Rule Evaluation Trace | trace estructurado en Decision + UI 3 niveles | **TO DO completo** | 1 sem | **PRIORITARIO** — habilitar antes de Sprint 18 audit (más data útil para decision matrix) |
| **S4** Juan ↔ LLM `/juan/suggest` | endpoint 4 tipos + dashboard tab | **TO DO completo** | 1 sem | Después de S3 (depende de trace + prompt builder) |
| **S5** Backtest engine | 365d data validar 71 reglas | **TO DO completo + BLOQUEADO** (no backlog endpoint en cliente Quant Data) | 2 sem + N días unblock | **Pre-tarea: investigar disponibilidad Quant Data backlog API** antes de schedule. Si no disponible, evaluar Schwab historical o alternativa free (Yahoo Finance limitado a precios, no GEX/IV) |
| **S6** Orchestrator | APScheduler + watchlist + 6 fases | **TO DO** (Cloud Scheduler externo cubre algo pero no estructura v2.1) | 1 sem | Lower priority — bot ya corre autónomo via loop. Estructura formal vale cuando hayan más fases (S7 close + S9 journal lo motivan más) |
| **S7** Manual close + Firestore backup | endpoints close_one/all/filter + backup auto | **PARCIAL**: Firestore writes SÍ (trades + S3 overrides), endpoints close_* NO | 4-6 días | Mover up en prioridad si Juan va a operar más activo. Útil incluso pre-live |
| **S8** Outcome tracking + RAG balanced | auto-case generation + balanced sampling | **TO DO**. `classify_trades.py` standalone existe (Task #68) sin loop automático | 1 sem | Depende de cases volume — diferir hasta tener 20+ cases reales post-OPS-3 |
| **S9** Learning loop básico | nightly journal + weekly review | **PARCIAL**: scheduled task `eolo-daily-analysis 18:32` enabled pero contenido v2.1 sin implementar | 1 sem | Sigue S7 — necesita outcome tracking maduro |
| **S10** Chat feedback nocturno | sesiones interactivas + artifacts | **TO DO completo** | 1 sem | Sprint final útil — depende de S3 (trace para discutir) + S8 (cases para revisar) + S9 (journal como input) |
| **S11** Hardening + DR + go-live | QA + DR + mode toggle doble auth | **TO DO**. PAPER_TRADING_ONLY=true hardcoded | 1 sem | Sprint final — gating step antes de live. Sin schedule firme hasta que S5 backtest valide edge |

**Resumen estado:**
- 1 sprint PARCIAL (S1)
- 3 sprints PARCIAL (S6, S7, S9)
- 7 sprints TO DO (S2, S3, S4, S5, S8, S10, S11)

---

## 6. Cross-cutting issues

### 6.1 Push status del cierre nocturno

Local `origin/main` ref está en `a37588a`, HEAD en `231c361` (2 ahead). Esto significa una de dos cosas:
- (a) El `git push origin main` que recomendé al cierre nocturno **no se ejecutó**
- (b) Se ejecutó pero el ref local de origin está stale (caché git no actualizada — improbable porque git mueve el ref como side effect del push)

**Acción inmediata:** verificar y, si falta, pushear. Bloque al final de este memo.

### 6.2 Schwab REST polling estabilidad

Master 06-01 sec 7.2 + 8 menciona REST poller post-WebSocket migration (Sprint 5 Fix B + Sprint 5.B). No verifiqué en esta sesión health del poller — worth chequear en validation 09:30 ET mañana.

### 6.3 Sprint 18 timing

Master 06-01 dice "target ~8-jun" para Sprint 18 con bloqueante "+1 sem data". Si arrancamos S3 esta semana (no data-dep), Sprint 18 puede correr en paralelo con S3 a partir de ~5-6-jun. Pero si quedamos en serial, Sprint 18 se mueve a ~10-12-jun.

### 6.4 LLM cost trending

Master 06-01 sec 12.3 estima $50-150/mo Anthropic. Con scope expandido (SPY + QQQ + IWM) + Quant Data wire + posible S3 (que aumenta tokens del prompt con trace), el cost real puede subir. Worth tracking en `/api/state.stats.llm_metrics.cost_estimate_usd` semanalmente.

### 6.5 Pre-live milestones

v2.1 sec 1 dice "métrica final: combinación Juan + LLM > cualquier en aislamiento". Eso requiere baseline. Baseline solo se obtiene con backtest (S5). Sin S5, no hay path objetivo a live. Sprint 11 hardening sin S5 = gating en criterio cualitativo solamente.

---

## 7. Decisiones necesarias de Juan

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| 7.1 | Cerrar Sprint 1 a 4 endpoints o extender? | A) Cerrar a 4 (recomendado). B) Add 5 más (charm, vanna, dealer_positioning, net_call/put_premium) ahora. C) Add después de S5 backtest decida cuáles aportan edge. | **C** — agregar endpoints sin backtest = más vapor en KB. Cerrar S1 ahora y reabrir post-S5 con criterio objetivo. |
| 7.2 | Sprint 2 mergea con Sprint 18 (KB v1.3 unificado)? | A) Mergear (recomendado). B) Sprint 2 standalone primero, después Sprint 18. C) Skip Sprint 2 — Sprint 18 audit decide reglas QuantData. | **A** — un solo bump KB v1.2 → v1.3 con TACTICAL consolidación + 10 reglas QuantData. Sprint compuesto pero más limpio que serial. |
| 7.3 | Próximo sprint a arrancar (esta semana)? | A) S3 Rule Evaluation Trace (no data-dep). B) S7 Manual close + Firestore backup (útil pre-live). C) Investigación Quant Data backlog API (unblock S5). D) Pre-stage Sprint 18 audit framework. | **A** — S3 habilita observabilidad que mejora Sprint 18 audit. Sin data-dep. Foundation para S4 + S9 + S10. **+ paralelo C** (investigación, no-dev work). |
| 7.4 | Verificación `net_premium_drift` wiring (1h)? | A) Hacer ahora (read snapshot.py). B) Diferir hasta validation 09:30 ET. | **A** — bloque rápido próximo turno. |
| 7.5 | Master 06-01 doc updates? | A) Patchar FastAPI en sec 8.1 ahora. B) Regenerar master completo post-S3. C) Dejar como snapshot histórico, generar v2.2 desde cero. | **C** — Master 06-01 queda como snapshot 1-jun. Este memo + el output de las próximas decisiones → input para v2.2 oficial cuando esté lista la arquitectura post-S3. |
| 7.6 | Push status acción inmediata? | A) Verificar y pushear si falta (1 bloque). B) Ignorar hasta cierre próxima sesión. | **A** — bloque listo abajo. |

---

## 8. Roadmap v2.2 propuesto (4-6 semanas)

Asume todas las recomendaciones de sec 7. Subject to Juan's calls.

| Semana | Sprint | Entregable | Bloqueantes |
|---|---|---|---|
| **W1 (1-jun → 7-jun)** | **S3 Rule Eval Trace** + sub-tareas | rule_evaluation_trace schema + decision_parser + dashboard 3-niveles. Verificación net-drift wiring. Investigación QD backlog API. | Ninguno |
| **W2 (8-jun → 14-jun)** | **Sprint UP-1.4** (merge S2 + Sprint 18) | KB v1.3: TACTICAL consolidación (24 → ~15-20) + 10 reglas QuantData-aware nuevas (TR-Juan-062 a 071) | S3 done (mejor data audit) + 1 sem data acumulada (8-jun OK) |
| **W3 (15-jun → 21-jun)** | **S4 `/juan/suggest`** | Endpoint 4 tipos + dashboard tab + prompt builder Juan-mode | S3 done (trace en respuesta) |
| **W4-W5 (22-jun → 5-jul)** | **S5 Backtest engine** | Pull historical + simulator + metrics report 365d KB v1.3 | QD backlog API disponible (investigado en W1) o alternativa identificada |
| **W6 (6-jul → 12-jul)** | **S7 Manual close + Firestore backup** | Endpoints close_one/all/filter + backup automation | Ninguno (no depende de los anteriores) |
| **Buffer (13-jul →)** | S8 + S9 + S10 + S11 | Iterativo, con cadencia más floja | S5 informa qué priorizar |

**Notas:**
- S6 (orchestrator estructurado) **diferido sin schedule firme**. Bot ya corre autónomo; estructura formal espera S7/S9 que la justifican mejor.
- S8 (outcome tracking + RAG balanced) **diferido** hasta tener 20+ cases reales post-OPS-3 (probable ~mid-julio si Juan opera activamente).
- S10 (chat feedback nocturno) bueno candidato post-S8 (cases reales para revisar).
- S11 (hardening + go-live) gating step. Criterio objetivo de live: S5 backtest > 60% win rate sobre 365d Y operación paper > 4 semanas continuas con métricas estables.

---

## 9. Follow-ups (no urgent, próxima sesión)

1. Regenerar Master Plan v2.2 oficial `.docx` post-decisiones secc 7 + outputs S3
2. Patchar Master 06-01 sec 8.1 (FastAPI) o marcar como deprecated en favor de v2.2
3. Documentar en `docs/QUANTDATA_API_EVALUATION.md` el resultado de la investigación Quant Data backlog (W1 paralelo)
4. Actualizar `docs/sprint_18_tactical_decision_matrix.md` con el merge en Sprint UP-1.4
5. `MEMORY.md` consolidación post-sesión incluyendo este memo
6. Considerar archivar v2.1 a `docs/historico/Eolo_Crop_Master_Plan_v2_1_ARCHIVED.docx` para evitar confusión futura

---

## 10. Bloque verificación push (ejecutar próximo turno)

```bash
cd ~/PycharmProjects/eolo

# 1. Fetch sin merge (solo updates refs locales)
git fetch origin

# 2. Comparar real
git log origin/main..HEAD --oneline
git status

# 3a. Si HEAD == origin/main (refs sincronizados, push ya estaba hecho):
#     output esperado: "Your branch is up to date with 'origin/main'."

# 3b. Si HEAD ahead de origin/main (push pendiente):
git push origin main
git status
```

---

## 11. Conclusiones para esta sesión

- Reconciliation done. v2.1 está parcialmente desactualizado, Master 06-01 tiene 1 error material (FastAPI/Flask), código tiene findings menores (net-drift TODO, kb_loader sin KB_VERSION).
- Sprint 1 cierra a 4 endpoints (per recomendación 7.1). 11 endpoints adicionales gated por backtest result.
- Sprint 2 + Sprint 18 mergean a Sprint UP-1.4 (KB v1.3 unificado).
- Roadmap v2.2 estimado 4-6 sem para llegar a backtest done + manual close + endpoint juan/suggest.
- 6 decisiones esperando vos (sec 7).
- 1 acción inmediata: verificar y completar push del cierre nocturno (sec 6.1, bloque sec 10).

**Próximo paso natural:** decisiones secc 7.1-7.6 + arrancar Sprint 3 (Rule Eval Trace) si OK.
