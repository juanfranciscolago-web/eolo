# EOLO Project State Snapshot

**Última actualización:** Domingo 31-mayo 2026 (cierre sesión extendida 28-29-31 may)
**Propósito:** Source of truth para futuras sesiones (Claude Code o Cowork). Leelo primero antes de tocar cualquier código.

---

## Estado producción LIVE

| Servicio | Revisión activa | Región | Project |
|---|---|---|---|
| **eolo-bot-crop** | `eolo-bot-crop-00080-zbb` | us-east1 | eolo-schwab-agent |
| **llm-engine-service** | `llm-engine-service-00003-sqn` | us-central1 | eolo-schwab-agent |

Ambos `--no-allow-unauthenticated` (ID token auth via metadata server).
**PAPER_TRADING_ONLY=true** hardcoded en config del engine (verificado en /health).

---

## Sprints LIVE (orden cronológico)

| # | Sprint | Estado | Notas |
|---|---|---|---|
| 4.D HZ-2 | LLM scope SPY only | LIVE | QQQ/IWM/TQQQ siguen rule-based |
| 5 Fix B | REST polling reemplaza WebSocket | LIVE | WebSocket Schwab 8% uptime → REST cacheado |
| 5.B | L1 quotes polling tech debt #25 | LIVE | |
| 5.C | (variant) | LIVE | |
| 6 | (variant) | LIVE | |
| 7 | bounds #22 + vix_velocity_1d #20 | LIVE | |
| 8.A / 8.B | bugs latentes #1 + #2 (field-mapping + caching) | LIVE | |
| 9 | Logging estructurado trades UUID Firestore | LIVE | eolo-crop-trades collection |
| 10 | Fix wiring tacit_rules + similar_case + safety_overrides | LIVE | |
| 11 | LLM observability metrics (/api/state.stats.llm_metrics) | LIVE | metrics.py thread-safe counters |
| 12 | Fix sector field mapping (Bug A) | LIVE | netPercentChangeInDouble correcto |
| 13 | Fix STOP_LOSS espurio por marks stale | LIVE | + investigación Bug B (sector polling = falso positivo) |
| 14 | SPY_DROP anchor sanity guards | LIVE | |
| 15 | Ventana trading configurable + S3.X Firestore persistence + timezone consistency | LIVE | |
| 17 | Bug fix decision_meta vacío | LIVE | loguru integration |
| 21 | Token usage en LLM Engine decision.meta | LIVE | rev 00003-sqn (deployado 31-may 10:06 ET) |

**Bug Sprint 21 identificado en audit del 31-may:** client-side perdía Haiku tokens en flow layered. Fix en PR #28 pendiente deploy lunes.

---

## PRs draft pendientes (estado al cierre 31-may)

| PR | Branch | LOC | Status | Plan |
|---|---|---|---|---|
| **#24** | fix/sprint20-cleanup-datetime-deadcode | +7/-8 | draft | Mergear lunes 1-jun (3°) |
| **#25** | feat/up-1.2-kb-editor | +726 | draft | Mid-week — foundational para Sprint 18 |
| **#26** | feat/up-1.3-gold-cases | +446 | draft | Mid-week — 2 GOLD cases, fase 2 pendiente |
| **#27** | feat/up-2.2-llm-metrics-dashboard | +370 | draft | Mergear lunes 1-jun (2°) |
| **#28** | fix/sprint21-haiku-tokens-cost | +209/-8 | draft | Mergear lunes 1-jun (1°) — fix bug Haiku cost |

---

## Worktrees activos

```
/Users/JUAN/PycharmProjects/eolo               c93efc0 [main]
/Users/JUAN/PycharmProjects/eolo-up12          c88fad6 [feat/up-1.2-kb-editor]
/Users/JUAN/PycharmProjects/eolo-up13          1c93188 [feat/up-1.3-gold-cases]
/Users/JUAN/PycharmProjects/eolo-up22          9a0966f [feat/up-2.2-llm-metrics-dashboard]
/Users/JUAN/PycharmProjects/eolo-sprint21-fix  d2a8071 [fix/sprint21-haiku-tokens-cost]
```

**Cleanup post-merge mid-week:**
```bash
cd ~/PycharmProjects/eolo
git worktree remove ../eolo-up12 ../eolo-up13 ../eolo-up22 ../eolo-sprint21-fix
git branch -d feat/up-1.2-kb-editor feat/up-1.3-gold-cases feat/up-2.2-llm-metrics-dashboard fix/sprint21-haiku-tokens-cost
```

---

## Docs clave generados sesión 31-may

| Doc | Propósito |
|---|---|
| `docs/RUNBOOK_LUNES_01_JUN_2026.md` | Source of truth deploy lunes 9:00 ART |
| `docs/SPRINT_21_WIRING_AUDIT_31_MAY.md` | Audit que identificó bug Haiku cost (justifica PR #28) |
| `docs/sprint_18_tactical_decision_matrix.md` | Plan KB v1.3 — colapsar tier TACTICAL → TACTICAL_PLUS (24 reglas → 0) |
| `docs/EOLO_CROP_BACKLOG_29_MAY_2026.docx` | Backlog consolidado PARTE 1 operativos + PARTE 2 mejoras |

Plus en PR #26: `docs/gold_cases/GOLD_001_*.md` y `GOLD_002_*.md` (TR-Juan-043 + TR-Juan-014 case templates).

---

## Backlog próximas mejoras (de PARTE 2 + operativos)

### Hechas esta sesión 31-may
- ✅ UP-1.2 fase 1 — KB Editor CLI scaffold (PR #25)
- ✅ UP-1.3 fase 1 — 2 GOLD cases (PR #26)
- ✅ UP-2.2 — Dashboard frontend (PR #27)
- ✅ UP-1.1 dry-run — Decision matrix Sprint 18 (doc only)

### Próximas
- ⏳ **UP-1.2 fase 2** — add-rule / edit-rule / merge-rules + integración Claude (4-6h Claude Code)
- ⏳ **UP-1.3 fase 2** — 3 GOLD cases más (TR-Juan-054, TR-Juan-061, TR-Juan-047) ~6h
- ⏳ **UP-2.1** — Framework doc decisión LLM scope SPY only vs todas (1h, pre-req data lunes)
- ⏳ **UP-2.3** — Hot-reload KB sin redeploy (1.5h, requires UP-1.2 fase 2)
- ⏳ **Sprint 18 ejecución** — aplicar decision matrix (requires UP-1.2 fase 2 + 1 mes prod data)
- ⏳ **UP-3.1** — Quant Data API (sin scope, requires Juan input)
- ⏳ **UP-3.2** — Backtest engine ligero (4-5h, requires UP-1.2 + UP-1.3 maduros)
- ⏳ **Crypto bot B2-B5 branches consolidation** (different codebase, audit pendiente)
- ⏳ **PR #23 completo** — 3 OAuth apps for v1/v2/crop refresh tokens

---

## Arquitectura clave (referencia rápida)

### Bot CROP (eolo-crop/)
- Theta Harvest only (sin VRP/0DTE/Earnings/PutSkew)
- 1Gi/1CPU, max-instances=1, concurrency=1, --no-cpu-throttling (WS no genera tráfico HTTP)
- 24/7 (min-instances=1, never scale-to-zero)
- LLM_ENGINE_URL=https://llm-engine-service-nmjz4iwcea-uc.a.run.app
- Deploy: `gcloud builds submit --config eolo-crop/cloudbuild-crop.yaml . --project eolo-schwab-agent`

### LLM Engine (llm_engine_eolo/)
- claude-sonnet-4-5-20250929 (full decide)
- claude-haiku-4-5-20251001 (pre-filter)
- KB v1.2: EOLO_ThetaHarvest_v1.2.xlsx (8 sheets, 61 reglas, 6 SILVER cases)
- 1Gi/1CPU, timeout 60s, max-instances=3, concurrency=10
- Deploy: `cd llm_engine_eolo && bash deploy.sh` (Cloud Build implícito)

### KB tiers (61 reglas, post Sprint 18 → 43)
- AXIOMA (2): TR-042, TR-043
- PROHIBITIVA (5): IC simultaneous, breakouts, etc.
- MAESTRA (11 → 12 post-Sprint-18): VIX-driven rules
- PROTOCOLO (6): morning ritual + Fibonacci
- TACTICAL_PLUS (13 → 18): schema-driven triggers
- TACTICAL (24 → 0): natural language, mergear/eliminar

### Firestore collections
- `eolo-crop-trades`: UUID docs (Sprint 9+10 format) + day-doc legacy
- `eolo-crop-config/strategy_overrides`: Sprint S3 override layer

### Layered LLM flow
1. `should_call_llm` (Rule 0 pre-filter): bot decide si vale la pena llamar
2. `/pre_decide` (Haiku): segunda capa de filtro
3. `/decide` (Sonnet): decisión final si Haiku no skipped
4. `record_call` actualiza LLMMetrics (tokens + cost) — **fix Haiku cost en PR #28**

---

## Lecciones operacionales aprendidas

1. **Git worktree paralelización funciona.** 4 sesiones Claude Code paralelas el 31-may sin contaminación. Required: 1 worktree por sesión, prompts self-contained.

2. **Sesiones paralelas SIN worktree contaminan commits.** Lección 31-may temprano: commit ff6b0d3 mezcló Sprint 20 + Sprint 21 changes. Recovery requirió `git checkout commit -- archivo` + cleanup.

3. **Docs en worktree no se ven entre worktrees.** Si una sesión necesita ver un doc generado en otra, hay que commiterlo a una rama compartida o pasarlo manualmente. Lección del PR #28 dev (Sprint 21 fix worktree no vio el audit doc que estaba en main worktree's filesystem).

4. **Tests sin pytest framework.** `eolo-crop/` no tiene pytest configurado. Pattern `importlib.util.spec_from_file_location` permite testear módulos individuales sin trigger imports del package completo (que rompe por deps faltantes en dev env). Ver `eolo-crop/tests/test_metrics_sprint21_fix.py`.

5. **Audit doc previene failures.** El audit Sprint 21 de 30 min identificó bug que hubiera roto validation criterion #5 el lunes en producción. Fix en sesión adicional de 30 min vs diagnóstico bajo presión pre-market.

6. **Scheduled task se debe re-armar.** Las one-time tasks quedan disabled post-fire. Re-armar con nuevo fireAt cuando el plan cambia (lección del runbook lunes update con PR #28).

7. **TACTICAL tier KB es ambiguo.** Audit Sprint 18 reveló que el natural language de TACTICAL ("RSI haciendo top") es más difícil de operacionalizar que el schema de TACTICAL_PLUS ("rsi_zone = overbought_fading"). KB v1.3 colapsa el tier.

8. **Cost tracking layered es no-trivial.** El bot llama Haiku + Sonnet en sequence; sin tracking cuidadoso (PR #28), cost se subestima ~21%.

---

## Comandos de referencia rápida

```bash
# Verificar estado servicios
gcloud run services describe eolo-bot-crop --region=us-east1 --project=eolo-schwab-agent
gcloud run services describe llm-engine-service --region=us-central1 --project=eolo-schwab-agent

# /health checks
ENGINE_URL=$(gcloud run services describe llm-engine-service --region=us-central1 --project=eolo-schwab-agent --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
curl -sS -H "Authorization: Bearer $TOKEN" "$ENGINE_URL/health"

# Deploy bot CROP
cd ~/PycharmProjects/eolo
gcloud builds submit --config eolo-crop/cloudbuild-crop.yaml . --project eolo-schwab-agent

# Deploy LLM Engine
cd ~/PycharmProjects/eolo/llm_engine_eolo
bash deploy.sh

# Rollback bot CROP
gcloud run services update-traffic eolo-bot-crop --region=us-east1 --project=eolo-schwab-agent --to-revisions=eolo-bot-crop-00080-zbb=100

# Rollback LLM Engine (pre-Sprint-21)
gcloud run services update-traffic llm-engine-service --region=us-central1 --project=eolo-schwab-agent --to-revisions=llm-engine-service-00001-mk8=100

# Worktree list
git worktree list

# KB Editor (PR #25 fase 1)
cd ~/PycharmProjects/eolo  # o el worktree con PR #25 mergeado
python3 tools/kb_editor.py list-rules
python3 tools/kb_editor.py stats
python3 tools/kb_editor.py validate
```

---

## Scheduled tasks activas

| Task ID | Schedule | Estado | Próximo run |
|---|---|---|---|
| `eolo-daily-analysis` | 18:32 daily | enabled | 2026-06-01 18:32 |
| `eolo-crypto-health-check` | 00:02 + 12:02 daily | enabled | 2026-06-01 00:02 |
| `sprint-5-deploy-checklist-viernes` | One-time | **enabled** | **2026-06-01 09:00 ART** (re-armed con PR #28) |
| `eolo-daily-health-check` | 08:05 daily | disabled (redundante) | — |

---

## Política trading vigente

- **PAPER_TRADING_ONLY=true** en LLM Engine config (hardcoded, validar en /health)
- Bot reporta paper trades a Firestore + dashboard
- ANTHROPIC_API_KEY en Secret Manager como "anthropic-api-key"
- Cloud Run authenticated con ID tokens (--no-allow-unauthenticated en ambos servicios)
- Trading window configurable (Sprint 15) — actualmente entry_window según HH:MM regex bounds

---

**Cualquier futura sesión:** leé esto + `docs/RUNBOOK_LUNES_01_JUN_2026.md` antes de ejecutar comandos en producción. Si algo no coincide con el estado real (revisiones, PRs), updateá esta doc primero.
