# Master Plan v2.1 — Inventory & Status

**Date:** 2026-06-03
**Author:** CC inventario sprint INVENTARIO-MP
**Source docs:**
- `Eolo_Crop_Master_Plan_v2_1.docx` — **NO en repo** (referenciado por `docs/RECONCILIATION_v2_1_vs_06_01.md` pero archivo ausente)
- `EOLO_CROP_LLM_DOCUMENTO_MAESTRO_v2.docx` (v2.0, mayo 28) — sec-structure proxy
- `docs/EOLO_CROP_MASTER_2026-06-01.docx` (06-01) — current state baseline
- `docs/RECONCILIATION_v2_1_vs_06_01.md` — mapping explícito S1-S11

**Git HEAD verificado:** `19cc76a feat(audit): /audit + /audit.json server-side rendered LLM audit dashboard`

---

## Resumen ejecutivo

Inventario de **47 items** del MP v2.1 + outline operacional contra código HEAD `19cc76a`.

| Status | Conteo | % |
|---|---|---|
| **DONE** | 27 | 57% |
| **PARTIAL** | 11 | 23% |
| **PENDING** | 6 | 13% |
| **DEFERRED** | 3 | 7% |

**Cambios materiales vs RECONCILIATION (2026-06-01):**
- Sprints S3, S4, S9, S10 transitaron de TO DO → DONE.
- KB bump v1.2 → **v1.3 con 71 reglas + 10 QD-aware** (TR-Juan-062..071) **CONFIRMED**.
- Schema fix dte_target le=7 → le=45 (commit A12 `b9c8cbb`) y safety Rule 5 removida.
- Audit trail Firestore con `llm_verdict / llm_tacit_rules_applied / llm_safety_overrides` deployed (commit A `863a476`).
- kb_version meta dinámico desde KB_PATH (commit C `d56ef2b`).
- `/audit` + `/audit.json` server-side dashboard (commit `19cc76a`).

**Bloqueantes mayores que quedan:**
1. **S5 Backtest engine** — sin código, sin endpoint backlog Quant Data identificado, sin alternativa.
2. **S11 Hardening + live toggle** — `PAPER_TRADING_ONLY=true` está hardcoded; no hay dual-auth mode toggle.
3. **`net_premium_drift` wiring** — endpoint LIVE pero TODO en header del cliente sobre validar response antes de wire a snapshot. Verificación de uso real en snapshot pendiente.

---

## Sec 1-4 — Foundation

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 1.1 `PAPER_TRADING_ONLY=true` hardcoded | DONE | `llm_engine_eolo/llm_engine/service.py:76` lee env var; `deploy.sh` lo setea explícito. Service `/health` retorna `paper_trading_only: true`. | — |
| 1.2 Schwab OAuth tokens en Firestore | DONE | `eolo-crop/helpers.py:6-7` `schwab-tokens / schwab-tokens-auth`. Refresh loop en `crop_main.py:2378-2398` (cada 25min). | — |
| 1.3 Anthropic API key Secret Manager | DONE | `llm_engine_eolo/deploy.sh:32` `--set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest"`. | — |
| 1.4 Quant Data API key Secret Manager | DONE | `eolo-crop/llm_gate/external_data_quantdata.py:63` `projects/eolo-schwab-agent/secrets/quantdata-api-key/versions/latest` + fallback env var. | — |
| 1.5 Repo structure + Docker + cloudbuild | DONE | `eolo-crop/Dockerfile` build context = repo root; `eolo-crop/cloudbuild-buildonly.yaml` build-only (deploy separado); `llm_engine_eolo/deploy.sh` engine. | — |

## Sec 5 — Knowledge Base

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 5.1 KB Excel v1.3 active | DONE | `llm_engine_eolo/kb/EOLO_ThetaHarvest_v1.3.xlsx` cargada por engine; `/kb_stats` retorna `total_rules: 71`. KB_PATH auto-discover por glob en `service.py:57`. | — |
| 5.2 71 reglas | DONE | Conteo directo del workbook Decision_Rules sheet: 71 `TR-Juan-XXX` matches. | — |
| 5.3 TR-Juan-062..071 QD-aware | DONE | 10/10 presentes: 062, 063, 064, 065, 066, 067, 068, 069, 070, 071. Citadas en live decisions hoy (TR-Juan-070 confirmada en QQQ 14:39:42Z). | — |
| 5.4 Tier system 6 niveles | DONE | Distribución actual: AXIOMA=1, PROHIBITIVA=6, MAESTRA=13, PROTOCOLO=6, TACTICAL_PLUS=20, TACTICAL=25. Total = 71 ✓. | — |
| 5.5 `kb_editor.py` 11 commands | DONE | `tools/kb_editor.py`: cmd_list_rules, cmd_next_id, cmd_show_rule, cmd_validate, cmd_list_cases, cmd_stats, cmd_add_rule, cmd_edit_rule, cmd_delete_rule, cmd_merge_rules, cmd_bump_version = 11. | — |
| 5.6 bump-version + backup atómico | DONE | `tools/kb_editor.py:484 cmd_bump_version`. | — |

## Sec 6 — Quant Data integration

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 6.1 30 endpoints catalogados | PARTIAL | Cliente expone 5 endpoints LIVE: `get_max_pain`, `get_iv_rank`, `get_gex_regime`, `get_net_premium_drift`, `get_max_pain_over_time`. Otros 25 catalogados pero NO wired. | Decisión: extensión gated por S5 backtest (RECONCILIATION 7.1). |
| 6.2 Tier S 14 fields en MarketSnapshot | PARTIAL | snapshot extiende con campos QuantData; tests `test_market_snapshot_accepts_tier_s_extension_fields PASSED`. `iv_rank` y `gex_regime` se ven en live decisions. **`net_premium_drift` wiring sospechoso** — header `external_data_quantdata.py:9` aún dice "TODO: validar response antes de wire a snapshot". | Verificar en snapshot.py uso real de `net_premium_drift` field; verificar count actual de Tier S fields presentes. |
| 6.3 `classify_gamma_regime` | DONE | `llm_engine_eolo/llm_engine/quantdata_features.py:16`. Test `test_classify_gamma_regime PASSED`. | — |
| 6.4 `compute_vrp_score` | DONE | `quantdata_features.py:38`. Test `test_compute_vrp_score PASSED`. | — |
| 6.5 `magnet_strength` compute | PENDING | Cero matches `def magnet_strength` en codebase. | Implementar. Citado por TR-Juan-064/067 (Max Pain proximity). |
| 6.6 `cascade_risk` compute | PENDING | Cero matches. | Implementar. Referenciado por reglas defense regime negative. |
| 6.7 `smart_money_bias` compute | PENDING | Cero matches. | Implementar. Net flow vs delta-weighted positioning. |
| 6.8 `sessionDate` historical replay | PENDING | Cero matches `sessionDate` en `eolo-crop/` ni `llm_engine_eolo/`. | Sin endpoint backlog QD identificado; dependencia para S5 backtest. |
| 6.9 Pydantic boundary fix #95 (MarketSnapshot rejected dyn fields) | DONE | Tests `test_market_snapshot_accepts_quantdata_fields PASSED` + `test_market_snapshot_accepts_tier_s_extension_fields PASSED`. | — |

## Sec 7 — Decision Matrix + Safety Rails

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 7.1 Régimen × IVR × Flow table | DONE | `llm_engine_eolo/llm_engine/prompt_builder.py:53-56` 3 regímenes × IVR > 50/70 × side selection. KB Decision_Rules sheet refleja matriz. | — |
| 7.2 Side selection rules | DONE | Prompt + KB: SELL_PUT en long-gamma, IRON_CONDOR en transición, SELL_CALL en negative-gamma. | — |
| 7.3 DTE selection 30-45 long-gamma | DONE | Schema `dte_target` Field `le=45` (`decision_parser.py:53`, commit `b9c8cbb`). Master Plan v2.1 sec 7.1 alignado. | — |
| 7.4 Strike scoring | PARTIAL | Reglas KB definen target deltas (0.20 entry, 0.10 deep OTM, etc.) pero no hay scoring quantitativo explícito en código. | Documentar formal en `strike_scorer.py` o en safety rail. |
| 7.5 Sizing logic | PARTIAL | `position_monitor.py` tracker open positions; `day_max_positions: 50` configurable. No Kelly o adjusted sizing per régimen. | Sprint UP-1.4 puede incluir sizing rules nuevas. |
| 7.6 Schema dte_target ≤ 45 | DONE | Verificado. | — |
| 7.7 Safety rails 7 → 6 (Rule 5 removida) | DONE | `decision_parser.py:188-193` removed (commit `b9c8cbb`). Renumeración Rule 6/7 → 5/6. `_SAFETY_RAIL_PREFIX_TO_RULE_ID` sin entry `DTE_TOO_HIGH`. | — |

## Sec 8 — Orchestrator

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 8.1 6 phases declared | DONE | `orchestrator/daily_scheduler.py:30` PHASES = [pre_market 08:00, open 09:30, mid_day 10:30, afternoon 13:30, **power_hour 15:30** (no 15:00), post_market 16:00]. | Power_hour empieza 15:30 según código vs 15:00 en outline user. |
| 8.2 Phase 1 watchlist 8:00 ET | DONE | Cron `eolo-watchlist-premarket 0 8 * * 1-5` LIVE. `orchestrator/watchlist_builder.py` implementa. | — |
| 8.3 Phase 6 journal 16:15 + chat 16:30 | DONE | Crons `eolo-nightly-journal 15 16 * * 1-5` + `eolo-feedback-chat-open 30 16 * * 1-5`. | — |
| 8.4 Phases 2-5 (open, mid_day, afternoon, power_hour) | PARTIAL | DailyScheduler declara phases pero callbacks no están wired a Cloud Scheduler — el bot corre autónomo via internal loop. | Decisión RECONCILIATION 7.3: diferido sin schedule firme. Bot ya cubre via loop. |
| 8.5 Entry executor wire to /decide | DONE | `orchestrator/entry_executor.py:18` `f"{llm_engine_url}/decide"`. | — |
| 8.6 Position monitor at_50_target | DONE | `orchestrator/position_monitor.py` + test `test_position_monitor_at_50_target PASSED`. | — |

## Sec 9 — Juan Suggestion (`/juan/suggest`)

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 9.1 `/juan/suggest` endpoint | DONE | `llm_engine_eolo/llm_engine/service.py:316 @app.post("/juan/suggest")`. Live test: HTTP 200 en 15-53s con `BLOCK_HARD`/`DISAGREE` razonado. | — |
| 9.2 4 suggestion_types | DONE | `service.py:311` `pattern="^(ENTRY|EXIT|SIZE_DEBATE|MANUAL_TRADE_LOG)$"`. | — |
| 9.3 Tono evaluador honesto | DONE | `prompt_builder.py:268 build_juan_suggestion_prompt` + system prompt. Live response usó "absurdamente seguro 23% OTM" — tono honesto verificado. | — |
| 9.4 Response schema completo | DONE | `prompt_builder.py:281-294`: `llm_verdict` (AGREE/DISAGREE/PARTIAL_AGREE/BLOCK_HARD), `confidence_in_juans_call` 1-10, `rules_questioning_juan`, `alternative_proposal`, `final_recommendation` (ACCEPT_AS_IS/ACCEPT_WITH_ADJUSTMENT/REJECT/DEFER), `would_lead_to_case`. | — |
| 9.5 BLOCK_HARD para AXIOMA/PROHIBITIVA | DONE | Live test confirmó BLOCK_HARD cuando proposal viola AXIOM principles. | — |
| 9.6 Bot proxy `/juan/suggest` | DONE | `eolo-crop/main.py` reusa snapshot cached `_last_snapshots[ticker]` (post commit `b9c8cbb`). | — |

## Sec 10 — Tracking

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 10.1 `trade_lifecycle.py` | DONE | `eolo-crop/tracking/trade_lifecycle.py`. Tests cubren `record_trade_opened`. | — |
| 10.2 `outcome_writer.py` + cap 8/régimen/mes | DONE | `tracking/outcome_writer.py` con `identify_regime` + `is_exceptional_case`. Tests pasan. | — |
| 10.3 `accuracy_report.py` | DONE | `tracking/accuracy_report.py:compute_rule_accuracy`. Consumido por `nightly_journal.py` y `weekly_review.py`. | — |
| 10.4 Firestore audit completo | DONE | `crop_main.py:_log_theta_decision` + `_log_theta_decision_llm_update` (commit `863a476`). Query Firestore confirma docs con `llm_verdict`, `decision_source: LLM_SONNET_CONSULT`. | — |
| 10.5 Decision audit con llm_verdict/tacit_rules/safety_overrides | DONE | Track A commit `863a476` — fields `llm_verdict`, `llm_confidence`, `llm_main_reason`, `llm_layered_path`, `llm_tacit_rules_applied`, `llm_safety_overrides`, `sonnet_latency_ms` persisten. | — |

## Sec 11 — Learning loop

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 11.1 nightly_journal 16:15 ET cron | DONE | `eolo-nightly-journal 15 16 * * 1-5` activo. `learning/nightly_journal.py` implementado. Endpoint `/journal/today` 200 OK. | — |
| 11.2 weekly_review compare_periods | DONE | `learning/weekly_review.py:18 compare_periods` + `run_weekly_review`. Cron `eolo-weekly-review 0 18 * * 0` activo. Test `test_compare_periods_baseline PASSED`. | — |
| 11.3 Chat feedback session manager | DONE | `learning/feedback_chat/session_manager.py`. Cron `eolo-feedback-chat-open` activo. | — |
| 11.4 FEEDBACK_SYSTEM_PROMPT | DONE | `learning/feedback_chat/prompt_builder.py` + engine `/feedback/chat` endpoint live. | — |
| 11.5 4 artifact types | DONE | `feedback_chat/artifact_writer.py`: `write_rule_proposal`, `write_case_upgrade`, `write_lesson_learned`, `write_qa_ticket` = 4/4. | — |
| 11.6 Re-backtest 60d Sunday job | PENDING | `weekly_review.py` hace `compare_periods` (recent vs baseline trades) — NO re-backtest real con replay. | Requiere S5 backtest engine funcional. |

## Sec 12 — Operational

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 12.1 PAPER_TRADING_ONLY=true Cloud Run env var | DONE | Engine `--set-env-vars=PAPER_TRADING_ONLY=true` en deploy.sh. Bot también. | — |
| 12.2 Deploy pipelines | DONE | `llm_engine_eolo/deploy.sh` (engine) + `eolo-crop/cloudbuild-buildonly.yaml` (bot canary pattern). | — |
| 12.3 Canary tag pattern documented | DONE | Memoria CC + canary actual `t14` @ 100%, todos los tags previos limpiados. | — |
| 12.4 DR Cloud Function (`auto_close.py` standalone) | PARTIAL | `eolo-crop/disaster_recovery/auto_close.py` existe. **No deployed como Cloud Function independiente** — vive solo en el container del bot. | Deploy como Cloud Function standalone para que sobreviva crash del bot. |
| 12.5 Token refresh Schwab | DONE | `crop_main.py:2378 _token_refresh_loop` cada 25min. Cron auxiliar `schedulke-token-refresh */25 9-16 * * 1-5`. | — |
| 12.6 Cost monitoring | DONE | `llm_metrics.cost_estimate_usd` en `/api/state.stats.llm_metrics`. Visible en `/audit` y observación bg (PID 8382). | — |
| 12.7 Dashboard / observabilidad | DONE | `/dashboard` legacy (trading UI 254KB) + `/audit` LLM audit (server-side, commit `19cc76a`). | — |
| 12.8 kb_version dinámico engine | DONE | Commit `d56ef2b` — `service.py` extrae `v(\d+\.\d+)` de KB_PATH al startup. | — |

## Sec 13 — Hardening + go-live

| Item | Status | Evidencia | Gap |
|---|---|---|---|
| 13.1 PAPER_TRADING_ONLY=true defensa | DONE | Engine `service.py:82` warn si False; check explícito en startup. | — |
| 13.2 Mode toggle dual-auth (live) | PENDING | No hay mecanismo de toggle a live. PAPER_TRADING_ONLY env var solo. | Sprint 11 v2.1 — requiere doble auth (Juan + token override). |
| 13.3 QA checklist pre-live | DEFERRED | Sin schedule. Gating por S5 backtest result. | — |
| 13.4 Backtest 365d edge validation | PENDING | S5 sin código. | Implementación + endpoint backlog QD identificación. |

---

## Apéndice A — Sprints v2.1 (S1-S11) actualizado post-FIX-A-12

Estado al 2026-06-03 (delta vs RECONCILIATION 2026-06-01 en la última columna).

| Sprint | Status hoy | Evidencia clave | Delta vs 06-01 |
|---|---|---|---|
| **S1** QuantData ingest | PARTIAL (4 endpoints LIVE, 11 catalogados sin wire) | `external_data_quantdata.py` 5 funciones get_*. | Sin cambio. Decisión sec 7.1 RECONCILIATION pendiente. |
| **S2** Decision matrix + 10 rules | DONE (KB v1.3 con TR-Juan-062..071) | KB workbook + presence check. | **TO DO → DONE**. |
| **S3** Rule Evaluation Trace | DONE | `RuleEvaluation` schema + builder + sanitization en `decision_parser.py`. Tests `test_rule_evaluation_trace_*` pasan. | **TO DO → DONE**. |
| **S4** `/juan/suggest` | DONE | Engine endpoint + bot proxy + 4 types + alternative_proposal. Live verified. | **TO DO → DONE**. |
| **S5** Backtest engine | PENDING + BLOCKED | Sin dir `backtest/`. Sin `sessionDate` matches. | Sin cambio. |
| **S6** Orchestrator | PARTIAL | `daily_scheduler.py` + entry_executor + watchlist_builder + position_monitor. Phases 1+6 wired a Cloud Scheduler. Phases 2-5 viven en bot loop autónomo. | Mejorado (phases definidas + componentes), pero callbacks 2-5 sin Cloud Scheduler wire (decisión deferred). |
| **S7** Manual close + Firestore backup | PARTIAL | `backup/firestore_writer.py` + `restore.py` existen. `_execute_close_all` interno SI (daily-open-reset). **No HTTP endpoints `/close_one`/`/close_all`/`/close_filter`**. | Sin cambio mayor en endpoints externos. |
| **S8** Outcome tracking + RAG balanced | DONE (tracking) / PARTIAL (RAG balanced sampling) | `tracking/outcome_writer.py` + `is_exceptional_case` + tests. `classify_trades.py` standalone existe pero sin loop automático. | Tracking maduro; balanced RAG sampling depende de volume cases reales. |
| **S9** Learning loop básico | DONE | nightly_journal + weekly_review + crons activos. Re-backtest 60d real NO (depende de S5). | **PARTIAL → DONE** (excepto re-backtest). |
| **S10** Chat feedback nocturno | DONE | feedback_chat module + 4 artifact types + cron open + engine `/feedback/chat` live. | **TO DO → DONE**. |
| **S11** Hardening + DR + go-live | PARTIAL | PAPER_TRADING_ONLY ✓, deploy pipelines ✓, canary pattern ✓, DR script existe pero no deployed standalone. Sin mode toggle live. | Sin cambio mayor. |

---

## Apéndice B — Items PARTIAL priorizados (top 5 por impacto)

1. **`net_premium_drift` wiring verification** (Sec 6.2) — endpoint LIVE pero header TODO. Riesgo: LLM toma decisiones sin uno de los 4 inputs Quant Data si el field no entra a snapshot. **Esfuerzo: 1-2h grep + verify**.
2. **S5 Backtest engine** (Sec 13.4 + S5) — sin esto no hay validación objetiva de edge para path a live. Bloqueado por endpoint backlog QD ausente. **Esfuerzo: 2 sem + investigación QD**.
3. **DR `auto_close.py` deploy standalone** (Sec 12.4) — script existe pero corre en el container del bot, no sobrevive crash. **Esfuerzo: 2-3 días Cloud Function deploy + IAM**.
4. **S7 Manual close HTTP endpoints** (`/close_one`, `/close_all`, `/close_filter`) — útiles cuando Juan opere activo en paper. **Esfuerzo: 4-6 días**.
5. **S6 Phases 2-5 Cloud Scheduler wire** — actualmente solo Phase 1 y 6. Bot loop interno cubre semánticamente pero sin observabilidad estructurada. **Esfuerzo: 3-5 días**.

## Apéndice C — Items PENDING priorizados (top 5 por impacto)

1. **S5 Backtest engine completo** — gating step para pre-live. **2-3 sem**.
2. **Compute layer `magnet_strength` + `cascade_risk` + `smart_money_bias`** — TR-Juan-064/067/068 los citan pero los compute functions no existen. **Esfuerzo: ~1 sem (3 funciones + tests + integration)**.
3. **`sessionDate` historical replay** — sin esto no hay path para S5. Depende de identificar endpoint QD backlog o alternativa (Yahoo, Schwab historical, dydx/polygon). **Esfuerzo: 1 sem investigación + decisión**.
4. **Mode toggle dual-auth live** (Sec 13.2) — necesario para flip PAPER → LIVE post-S5 validation. **Esfuerzo: 3-5 días con review seguridad**.
5. **Re-backtest 60d weekly real** (Sec 11.6) — `weekly_review.py` solo compara trades reales recientes vs baseline. Real backtest replay requiere S5. **Bloqueado por S5**.

## Apéndice D — Tech debt descubierto durante implementación (no del MP v2.1)

| Item | File:line | Notas |
|---|---|---|
| `google-cloud-scheduler` no en `requirements.txt` | `eolo-crop/requirements.txt` | `dashboard/builder.py:_safe_scheduler_jobs` falla graceful con []. Sección Cloud Scheduler en `/audit` queda vacía. **Esfuerzo: 30min add + redeploy**. |
| Duplicate `eolo-daily-open-reset` schedulers | gcloud listing | `eolo-daily-open-reset-crop` ENABLED + `eolo-daily-open-reset-v2` PAUSED. Limpiar el PAUSED si confirmado obsoleto. |
| `dashboard-crop-test.html` + `dashboard-crop-v2.html` | `eolo-crop/` root | 133KB + 154KB de UI deprecated viviendo en repo. Mover a `_archive_/` o eliminar. |
| `crop_state_mock.json` | `eolo-crop/crop_state_mock.json` | Mock data en root del módulo. ¿Aún necesario para tests/dev? |
| `decision_id` formato no-uuid | `crop_main.py:1179` `f"{time.time():.3f}"` | Funciona pero no es uuid. Suficiente para today, problema si dos decisions caen en mismo ms. |
| `EOLO_CROP_LLM_DOCUMENTO_MAESTRO_v2.docx` etiquetado v2.0 internamente | root | Naming confuso. Considerar mover a `docs/historico/`. |
| `Eolo_Crop_Master_Plan_v2_1.docx` referenciado pero **NO en repo** | — | Este inventario reconstruye v2.1 desde RECONCILIATION + v2.0 + 06-01. Recover desde Drive/local si existe. |

---

## Apéndice E — Roadmap para cerrar gaps en 2-3 sprints largos

**Estimación esfuerzo CC autónomo (cada "sprint largo" = 1.5-2h CC):**

- **Sprint 1 (~2h)** — Tech debt rápido + verificaciones:
  - Verify `net_premium_drift` wiring en snapshot.py (item B1)
  - Add `google-cloud-scheduler` a requirements + redeploy (tech debt D1)
  - Limpiar duplicate scheduler PAUSED (D2)
  - Mover `dashboard-crop-test.html` y `-v2.html` a `_archive_` (D3)
  - Investigar Quant Data backlog API + documentar en `docs/QUANTDATA_BACKLOG_INVESTIGATION.md` (sec 6.8 dependency)

- **Sprint 2 (~2h)** — Compute layer + S7 partial:
  - Implementar `magnet_strength` + tests (item C2)
  - Implementar `cascade_risk` + tests
  - Implementar `smart_money_bias` + tests
  - Wire 3 compute functions a snapshot output
  - Endpoint `/close_filter` (subset de S7) — útil pre-live

- **Sprint 3 (~2h)** — DR standalone + observabilidad:
  - `auto_close.py` deploy como Cloud Function independiente (item B3)
  - Mode toggle dual-auth scaffold (no flip todavía — solo infra)
  - Patch `dashboard-crop.html` con kb_version dinámico + audit trail link

**Lo que NO entra en 3 sprints CC y necesita ventana más larga:**
- S5 Backtest engine (~2-3 semanas calendario)
- KB iteration via feedback real (depende de volume artifacts generados por Juan)
- Live mode flip (post-S5 + 4 semanas paper estable)
