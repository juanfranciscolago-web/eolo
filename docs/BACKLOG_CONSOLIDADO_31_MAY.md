# Backlog Consolidado — Estado al 31-May-2026 (cierre domingo)

**Source of truth:** runbook lunes + 8 docs operacionales en `docs/` + 7 PRs draft + backlog 29-may.

---

## 🔴 URGENTE — Mañana lunes 1-jun

| # | Item | Tiempo | Owner |
|---|---|---|---|
| 1 | **Deploy 3 PRs combinados** (#28 + #27 + #24) | 30 min | Juan via scheduled task |
| 2 | **Validar Sprint 21 cost real visible** post primer ciclo LLM | 15 min | Juan |
| 3 | **Validar UP-2.2 dashboard renderiza** con cost real | 5 min | Juan |
| 4 | **Verificar OAuth Cloud Functions activas** (refresh_tokens + oauth-health-check) | 5 min | Juan |

**Plan ejecutable:** `docs/RUNBOOK_LUNES_01_JUN_2026.md`

---

## 🟡 PRs DRAFT — Cargo cognitivo de la semana

| PR | Sprint | LOC | Cuándo mergear | Por qué esperar |
|---|---|---|---|---|
| **#24** | Sprint 20 cleanup datetime + dead code | +7/-8 | Lunes 1-jun pre-market | Runbook plan |
| **#27** | UP-2.2 LLM metrics dashboard | +370 | Lunes 1-jun pre-market | Runbook plan |
| **#28** | Sprint 21 fix Haiku cost tracking | +209/-8 | Lunes 1-jun pre-market | Runbook plan + fix critico |
| **#25** | UP-1.2 fase 1 KB Editor scaffold | +726 | Mid-week | Foundational para UP-1.2 fase 2 + Sprint 18 |
| **#26** | UP-1.3 fase 1 2 GOLD cases | +446 | Mid-week | Foundational para Sprint 19 |
| **#29** | OBS-1+OBS-2 backend (/api/version + /api/trades) | +337 | Sáb/dom | Espera frontend par |
| **#30** | OBS-2 frontend Trade Detail expandible | +398/-3 | Sáb/dom | Mergear junto a #29 para activar end-to-end |

**Cleanup post-merge:**
```bash
cd ~/PycharmProjects/eolo
git worktree remove ../eolo-up12 ../eolo-up13 ../eolo-up22 ../eolo-sprint21-fix ../eolo-be-stack-1 ../eolo-fe-trade-detail
git branch -d feat/up-1.2-kb-editor feat/up-1.3-gold-cases feat/up-2.2-llm-metrics-dashboard fix/sprint21-haiku-tokens-cost feat/be-version-trades-endpoints feat/fe-trade-detail-expandable
```

---

## 🟢 BACKLOG PARTE 1 — OPERATIVO (de backlog 29-may + audits)

### CRÍTICO

| # | Item | Estado | Esfuerzo | Bloqueante |
|---|---|---|---|---|
| OP-1.1 | Validación operativa real LLM (lunes 1-jun 9:30 ET) | ⏳ Mañana | Automated via scheduled task | — |
| OP-1.2 | Bug B Sprint 12: sector polling stop | ✅ FALSO POSITIVO | — | Cerrado en audit 29-may |

### ALTO

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OP-2.1 | Bug: 5 entries pre-11:00 ET NO entraron al wiring Sprint 9 | ✅ FALSO POSITIVO | Cerrado en audit 29-may |
| OP-2.2 | Token usage en decision.meta para Sprint 11 cost estimate | ✅ **LIVE** (Sprint 21) | — |
| OP-2.3 | Auditor TACTICAL re-run con muestra mayor (7-14 días) | ⏳ Post 1 sem prod data | 2-3h |
| OP-2.4 | Field-mapping LEVELONE_EQUITIES WS (v1/v2 latente) | ⏳ Backlog | 2h |

### MEDIO

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OP-3.1 | Datetime utcnow / naive cleanup (4 puntos backend) | ✅ **LIVE** (Sprint 20 PR #24) | Pendiente deploy mañana |
| OP-3.2 | Sprint 15 Opción B portable (ISO 8601 + dashboard render) | ⏳ Pendiente | 2h |
| OP-3.3 | Dead code AUTO_CLOSE_HOUR / AUTO_CLOSE_MINUTE | ✅ **LIVE** (Sprint 20 PR #24) | Pendiente deploy mañana |
| OP-3.4 | TR-Juan-047 dead: marcar N/A si IC deshabilitado | ⏳ Backlog | 30 min KB edit |
| OP-3.5 | Crypto bot auditoría operacional | ⏳ Backlog | 1-2h doc |

### BAJO

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OP-4.1 | PR #23 — 3 OAuth apps v1/v2/crop | ⏳ **WAIT** (audit 31-may) | 9h sprint |
| OP-4.2 | Documento maestro v3 post-sesión | ✅ Cubierto por PROJECT_STATE.md | — |
| OP-4.3 | Refactor `_cur_val_valid` para compartir `_are_marks_reliable` | ⏳ Backlog | 1-2h |

---

## 🔵 BACKLOG PARTE 2 — MEJORAS / UPGRADES

### ALTA

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| UP-1.1 | Sprint 18 TACTICAL tier rediseño (KB v1.3) | ⏳ **Decision matrix LISTO** (`docs/sprint_18_tactical_decision_matrix.md`), espera UP-1.2 fase 2 + 1 mes prod data | 2-4h ejecución |
| UP-1.2 fase 1 | KB Editor scaffold (PR #25) | ✅ **DONE** | — |
| UP-1.2 fase 2 | add-rule / edit-rule / merge-rules + integración Claude | ⏳ Próximo sprint | 4-6h |
| UP-1.3 fase 1 | 2 GOLD cases drafts (PR #26) | ✅ **DONE** | — |
| UP-1.3 fase 2 | 3 GOLD cases más (TR-054, TR-061, TR-047) | ⏳ Backlog | 6h (2h/caso) |

### MEDIA

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| UP-2.1 | Framework doc decisión LLM scope SPY vs todos | ⏳ Pre-req data lunes | 1h doc |
| UP-2.2 | Dashboard /llm_metrics frontend (PR #27) | ✅ **DONE** | — |
| UP-2.3 | Hot-reload KB sin redeploy | ⏳ Requires UP-1.2 fase 2 | 1.5h |

### BAJA

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| UP-3.1 | Quant Data API integración | ⏳ Sin scope, requires Juan input | TBD |
| UP-3.2 | Backtest engine ligero | ⏳ Requires UP-1.2 + UP-1.3 maduros | 4-5h |

---

## 🟣 BACKLOG OBS — Observability stack (del stack audit)

### Frontend gaps

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OBS-2.A | Trade Detail expandible (PR #30) | ✅ **DONE** | — |
| OBS-3.B | LLM decision history (lista últimas N decisiones) | ⏳ Backlog | 2-3h + backend |
| OBS-4.C | KB inspection inline (reglas citadas en última decision) | ⏳ Backlog | 2-3h + backend |
| OBS-5.D | Alerts panel (stop loss, EOD close, VIX spike) | ⏳ Backlog | 3-4h + backend |
| OBS-6.E | Cost trends chart (histórico intraday) | ⏳ Backlog | 1-2h |

### Backend endpoints faltantes

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OBS-1 | `/api/version` (PR #29) | ✅ **DONE** | — |
| OBS-2.A | `/api/trades` (PR #29) | ✅ **DONE** | — |
| OBS-3 | `/api/llm/history` (últimas N decisiones) | ⏳ Backlog | 2h |
| OBS-4 | `/api/positions` (snapshot estructurado) | ⏳ Backlog | 1h |
| OBS-5 | `/api/llm/reload_kb` (en LLM Engine, no bot) | ⏳ Requires UP-2.3 | 2h |
| OBS-6 | `/api/alerts` (event stream) | ⏳ Backlog | 3-4h |

### Mejoras adicionales sugeridas (stack audit)

| # | Item | Estado | Esfuerzo |
|---|---|---|---|
| OBS-7 | Comparación rule-based vs LLM (SPY vs QQQ/IWM/TQQQ) | ⏳ Backlog | 3-4h |
| OBS-8 | Sector dashboard (visualizar Sprint 12 SectorDir) | ⏳ Backlog | 2-3h |
| OBS-9 | Strategy override audit log (historial /api/state/edit) | ⏳ Backlog | 4-5h |
| OBS-10 | Backtest viewer | ⏳ Requires UP-3.2 | 4-5h post UP-3.2 |
| OBS-11 | KB Editor inline | ⏳ Requires UP-1.2 fase 2 | 4-6h |
| OBS-12 | `/api/health/llm` separate healthcheck | ⏳ Backlog | 1h |
| OBS-13 | WebSocket `/ws/state` (push vs polling) | ⏳ Backlog | 6-8h |

---

## ⚪ WONTFIX — Tech debts cerradas oficialmente

| # | Item | Razón |
|---|---|---|
| TD-15 | BVP/SVP + daily indicators warm-up | RESOLVED-AS-DESIGN — graceful fallback intencional |
| TD-17 | MACD 15m <30 candles | RESOLVED-AS-DESIGN — warn-once + defaults 0.0 |
| TD-21 | Ventana parcial 2m/15m | RESOLVED-AS-DESIGN — comments explícitos "CUÁNDO INDICA BUG" |
| TD-16 | LLM snapshot lookback bumpeado 100→500 | ✅ Resuelto Sprint 6 |
| TD-18 | VIX history buffer | ✅ Resuelto Sprint 6 |
| TD-20 | vix_yesterday_close REST | ✅ Resuelto Sprint 7 |
| TD-22 | LLM Engine numeric keys | ✅ Resuelto Sprint 7 |
| TD-23 | WS Schwab → REST polling | ✅ Resuelto Sprint 5 Fix B |
| TD-25 | L1 quotes polling | ✅ Resuelto Sprint 5.B |

**Tech debt activa pendiente: 0.**

---

## 📦 OTROS PROYECTOS — fuera del scope CROP

| Proyecto | Estado | Acción pendiente |
|---|---|---|
| Bot v1 (`Bot/`) | Inactivo | Ninguna |
| Bot v2 (`Bot-v1.2/`) | Inactivo (`Bot-v1.2/claude_bot.py` no opera) | Ninguna |
| Crypto bot (`eolo-crypto/`) | Monitor only | Audit B2-B5 branches stale (1-2h doc) |
| Crypto dashboard (`eolo-crypto-dashboard/`) | Operacional | Ninguna |
| eolo-sheets-sync | Operacional con `health_check.py` | Ninguna |
| Options bot (`eolo-options/`) | Deprecated | Ninguna |
| LLM Engine (`llm_engine_eolo/`) | LIVE en rev 00003-sqn | Sprint 21 LIVE |

---

## 🎯 Priorización sugerida próximas 2-4 semanas

### Semana 1 (jun 1-7)

| Día | Acción | Esfuerzo |
|---|---|---|
| Lun 1-jun | Deploy 3 PRs lunes + validar mercado | 1h |
| Mié 3-jun | Review + merge PR #25 + PR #26 (foundational) | 30 min |
| Jue 4-jun | Merge PR #29 + #30 + deploy (OBS stack visible) | 1h |
| Vie 5-jun | Verify OAuth Cloud Functions + check refresh_token edad | 5 min |
| Sáb-dom | Cleanup worktrees + descanso | 15 min |

### Semana 2 (jun 8-14)

| Item | Esfuerzo | Justificación |
|---|---|---|
| UP-1.2 fase 2 (add-rule/edit-rule + Claude integration) | 4-6h | Desbloquea Sprint 18 |
| UP-1.3 fase 2 (3 GOLD cases más) | 6h | Cierra Sprint 19 |
| UP-2.3 Hot-reload KB | 1.5h | Cierra UP-1.2 stack |

### Semana 3 (jun 15-21)

| Item | Esfuerzo | Justificación |
|---|---|---|
| Sprint 18 ejecución (KB v1.3) | 2-4h | Requires 2 sem prod data |
| OBS-6.E Cost trends chart | 1-2h | Self-contained |
| OBS-3.B + OBS-3 backend (LLM history) | 5h | Auditabilidad LLM |

### Semana 4+ (jun 22+)

| Item | Esfuerzo |
|---|---|
| OBS-4.C KB inspection inline | 5h |
| UP-2.1 framework doc LLM scope | 1h post-data |
| Crypto bot consolidation audit | 1-2h |
| Tech debt mejoras opcionales (indicators_ready flags) | 2-3h |

---

## 📊 Resumen ejecutivo

| Categoría | Activos | Closed | Total |
|---|---|---|---|
| Sprints/UPs entregables | 7 (PRs draft) | 27 (LIVE o cerrado) | 34 |
| PRs draft | 7 | — | — |
| Operativos PARTE 1 | 5 backlog | 9 (LIVE/closed) | 14 |
| Mejoras PARTE 2 | 5 backlog | 3 (DONE) | 8 |
| Observability OBS | 11 backlog | 3 (DONE) | 14 |
| Tech debts | 0 activas | 9 (incluye 3 WONTFIX) | 9 |
| Docs operacionales | — | 8 en `docs/` | 8 |

**Esfuerzo backlog activo:** ~50-60h spread over 4+ semanas.

**Próximo bottleneck:** UP-1.2 fase 2 (desbloquea Sprint 18 + UP-2.3).

---

**Última actualización:** 2026-05-31 (cierre domingo)
**Source files consultados:** `EOLO_CROP_BACKLOG_29_MAY_2026.docx`, `PROJECT_STATE.md`, `STACK_AUDIT_31_MAY.md`, `TECH_DEBT_AUDIT_31_MAY.md`, `OAUTH_AUDIT_31_MAY.md`, `sprint_18_tactical_decision_matrix.md`, PR list GitHub
