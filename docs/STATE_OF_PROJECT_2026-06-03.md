# Eolo Crop — State of Project 2026-06-03

## Executive summary
Master Plan v2.1 ~85% complete técnicamente. Sistema operando en PAPER mode con
Cloud Run llm-engine-service (us-central1) + eolo-bot-crop (us-east1) + DR Cloud
Function standalone. KB v1.3 con 71 reglas (10 QD-aware) influenciando decisiones
reales en producción.

## Hoy (2026-06-03) — Trabajo realizado

### Sprint T10+T11 (mañana)
- Cloud Scheduler Phase 6 (16:15 journal, 16:30 chat, weekly review, watchlist)
- LLM full integration `/juan/suggest` + `/feedback/chat` endpoints

### Sprint A12 + Audit + Dashboard (mediodía)
- Schema `dte le=45`, `_last_snapshots` wired, LLM timeout 120s
- Audit Firestore: `llm_verdict` + `tacit_rules` persisted (raíz #4 INVESTIGATE)
- KB version dinámico desde KB_PATH
- Dashboard `/audit` con Chart.js (server-side, 4 KPI + 2 charts + 5 tablas)

### Sprint TERMINATOR (tarde temprana)
- Sub-A: tech debt cleanup (scheduler dep + archive HTMLs + QD backlog research → S5 unblocked)
- Sub-B: 3 compute functions (`magnet_strength`, `cascade_risk`, `smart_money_bias`)
- Sub-C: DR Cloud Function standalone, mode toggle Firestore-driven, dashboard cross-link

### Sprint S5 + MEGATERMINATOR (tarde)
- S5 backtest scaffolding completo + dry run validated
- Snapshot quality con Schwab OHLC + indicators reales (Sub-A)
- Bot lee `trading_mode` Firestore en polling, fail-safe paper constraint (Sub-B)
- Weekly review real handler (Sub-C)
- 4 phase checkpoints Cloud Scheduler (Sub-D)
- UI close buttons `/dashboard` legacy (Sub-E)
- `refresh_token_issued_at` preservation (Sub-F)

### Sprint BACKTEST-COMPLETO (noche temprana)
- Fix `helpers.get_access_token` (era `get_schwab_access_token` inexistente)
- SPY 23d rerun: confidence 4.00→4.83 con indicators reales
- Fetch QD QQQ/IWM/TQQQ 60d historical
- Full 4-ticker × 23d backtest: 92 decisions, $8.99, 0 action (período flip_zone-heavy)

### Sprint REGRESSION-FIX (noche)
- **Bug crítico**: `_BACKUP_DB="eolo-backups"` no existe en GCP → 5 paths silent fail
- Fix: usar default DB
- Restaurado: chat feedback session, nightly journal persist, system_events,
  trade lifecycle backup, kb_snapshots

## Arquitectura (alto nivel)

```
[Schwab API + QuantData API]
         ↓
   [eolo-bot-crop t17 us-east1] ←→ [Cloud Scheduler 11 jobs]
         ↓                              ↓
   [llm-engine-service t15 us-central1] [DR Cloud Function]
         ↓                              ↓
   [Firestore (default DB)]      [Audit + Feedback artifacts]
         ↓
   [Dashboard /audit + /dashboard legacy]
```

## Deploy state actual (EOD 2026-06-03)
- Engine: `llm-engine-service-00015-c4h`
- Bot: `eolo-bot-crop-00190-zuy` (tag t17 @ 100%)
- DR: `disaster-recovery-auto-close` ACTIVE
- Cloud Scheduler jobs: 11 activos

## Estado del Master Plan v2.1
- **DONE: ~85%**
- **PARTIAL: ~10%**
- **DEFERRED (humano): ~5%**

## Pendientes calendar / human
- LIVE mode flip (decisión humana + backtest satisfactorio)
- KB iteration via feedback (proceso continuo, primer trigger esta noche 16:30 ET)
- 11 endpoints QD adicionales (gated por backtest result)
