# Sprint 2 CROP — fix exit_price (resolve close debit from chain)

**Period:** 2026-05-14 (1 día)
**Status:** ✅ COMPLETO Y EN PRODUCCIÓN
**Author:** Claude Code session paralela (sister session)
**Doc created retroactively:** 2026-05-14 ~15:15 ART by main coordination session

## Overview

Fix del bug `exit_price` en bot eolo-bot-crop (Theta Harvest). Mismo patrón aplicado al Sprint 1 V2 (eolo-options) que ya estaba en main. Hasta este sprint, los cierres del bot CROP registraban `limit_price=0/null` y `pnl=None` por falta de resolución del net_debit desde el chain de Schwab.

## Bloques deployados

| Bloque | Componente | Líneas | Notas |
|--------|-----------|--------|-------|
| 2a | `_resolve_close_limit` helper | options_trader.py | Auto-resolve bid desde chain cuando limit=None |
| 2b | `_resolve_spread_close_debit` helper | options_trader.py | Auto-resolve net_debit desde chain cuando net_debit=None |
| 2c.1-3 | Refactor `close_spread`, `close_long_call`, `close_long_put` | options_trader.py | Integración con resolvers + snapshot completo |
| 2d | `chain_fetcher` wiring | crop_main.py | +3 líneas — inyección al trader |

## Cambios funcionales

1. **Auto-resolve net_debit desde chain** cuando `close_spread` recibe `net_debit=None`
2. **Auto-resolve bid desde chain** cuando `close_long_call/put` recibe `limit=None`
3. **Snapshot completo persistido a Firestore** por cierre:
   - single_leg: 9 keys (quote_bid/ask/mid/last/mark/spot/iv/fetched_at/source)
   - spread: 15 keys (quote_short_*/quote_long_* + spot/fetched_at/source)
   - `snapshot_schema` field explícito ("single_leg" | "spread")
4. **data_quality flag** persistido ("n/a" | "quote_resolved" | "quote_unavailable")
5. **pnl=None honesto** cuando quote falla (vs pnl=0 engañoso pre-fix)
6. **_paper_positions cleanup** al cerrar spread (fix zombie positions)
7. **option_type derivado correctamente** de spread_type (era bug "always call")

## Tests E2E

Smoke test (`scripts/smoke/resolve_close_limit_crop.py`) con Schwab live data, market hours 2026-05-14:

| Test | Result | Detail |
|------|--------|--------|
| single_leg SPY 0DTE CALL ATM | ✅ | bid=0.88, quote_source=schwab_chain, snapshot_schema="single_leg" |
| spread SPY 0DTE put 748/744 | ✅ | net_debit=0.93, quote_source=schwab_chain, snapshot_schema="spread", 16 keys |

## Sanity post-deploy

- ✅ 0 errors severity≥ERROR
- ✅ chain_fetcher inyectado y activo ([CHAIN] SPY/QQQ/IWM/TQQQ fetching)
- ✅ WebSocket conectado + Login OK
- ✅ [TRADER] Modo: 📄 PAPER

## Validación con trade real: PENDIENTE

Hoy ventana apertura ya cerrada (13:20 ET > 11:50 ET) + macro filter off por dashboard. 0 trades esperados.

Próximo close natural: viernes 2026-05-15 o lunes.

Esperado en Firestore al primer close:
- `limit_price > 0` (no más 0/null)
- `pnl_usd != None` (con valor calculado)
- `quote_source = "schwab_chain"`
- `data_quality = "quote_resolved"`
- `snapshot_schema ∈ {"single_leg", "spread"}`

## Stats

| File | Lines |
|------|-------|
| eolo-crop/crop_main.py | +3 (chain_fetcher wiring) |
| eolo-crop/execution/options_trader.py | +424 / -13 (core fix) |
| eolo-crop/cloudbuild-buildonly.yaml | +48 (NEW, replica patrón V2) |
| scripts/smoke/resolve_close_limit_crop.py | +196 (NEW smoke test) |
| **Total** | **+660 / -13** |

## Cloud Run

- **Revision:** `eolo-bot-crop-fix-exit-price-20260514-1404` @ 100%
- **Image:** `gcr.io/eolo-schwab-agent/eolo-bot-crop:fix-exit-price`
- **Image digest:** `sha256:51276e29fb0bd530ee6f320e5bbbff8ea7317dfc3cf2a176b8b78354dfbbf1fa`
- **Build ID:** `b3241c58-830c-4d36-837b-5a73541854f5` (SUCCESS 1m22s)
- **Salvavidas:** `eolo-bot-crop-s3-5-complete-20260514-1212` @ 0% (preserved)

## Git

- **Main HEAD:** `49ef185` (merge commit, 2026-05-14 14:52 ART)
- **Fix commit:** `02f33f2` (2026-05-14 14:01 ART)
- **Branch:** `feat/crop-fix-exit-price` (preservada local + remote, no borrada por decisión del usuario)
- **Tag:** `sprint-2-fix-exit-price-2026-05-14` (creado retroactivamente por sesión coordinación)

## Rollback chain

```bash
# Rollback A (S3.5, último estable previo)
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --to-revisions=eolo-bot-crop-s3-5-complete-20260514-1212=100

# Rollback B (S3 sin S3.5)
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --to-revisions=eolo-bot-crop-s3-complete-20260513-2032=100

# Git rollback (revierte el merge si fuera necesario)
git revert -m 1 49ef185
```

Pre-Sprint-2 revisions disponibles en 0% traffic:
- `eolo-bot-crop-s3-5-complete-20260514-1212` — S3.5 sin el fix (rollback inmediato)
- `eolo-bot-crop-s3-complete-20260513-2032` — pre-S3.5
- `eolo-bot-crop-s2-3-b7-20260513-1509` — pre-S3

## Tech debt anotada (NO en scope Sprint 2)

Detectada durante este sprint pero deferida intencionalmente:

1. **LIVE path línea 882** en `options_trader.py`: mismo bug `exit_price` en el path live. **OBLIGATORIO fixearlo antes de pasar CROP a LIVE mode**. Hoy CROP corre solo en PAPER, no afecta.
2. **`counter_key="eolo_v2"` línea 875**: copy-paste wrong de V2. Debería ser `eolo_crop`. Solo afecta métricas de contadores (no funcional).
3. **`entry_price` recompute en `_update_paper_positions`**: pisa el `net_credit` original al primer current_price update post-open. No bloqueante para Sprint 2 (afecta historia de paper positions, no execution).

## Coordinación con otros sprints

Sprint 2 sucedió en paralelo a:
- **Sprint S3.5** (sesión coordinación) — Granular Position Sizing, completed mismo día ~12:28 ART
- **Eolo V2** (sesión paralela) — pausado por bug `claude_medium` no desplegado

**Orden de eventos:**
1. ~12:28 — S3.5 mergeado a main (commit `0042e10`)
2. ~14:01 — Sprint 2 fix committed (`02f33f2`) en branch
3. ~14:04 — Sprint 2 image build + deploy a producción @ 100%
4. ~14:52 — Sprint 2 merge a main (`49ef185`)
5. ~15:15 — Doc + tag retroactivos (este documento)

**Issue de housekeeping detectado:** entre ~14:04 y ~14:52, producción corría código que NO estaba en main (deploy adelantado al merge). Auditoría de ~14:34 detectó la inconsistencia. Resuelto cuando la sesión paralela hizo el merge ~14:52.

## Lecciones aprendidas

1. **Patrón "build then merge" tiene gap de housekeeping**: si se deploya antes de mergear, hay ventana donde producción y main están desincronizados. Mitigación futura: mergear ANTES o INMEDIATAMENTE después del deploy, no como paso final.

2. **Tag + doc deben ser parte del checklist de cierre**: la sesión paralela completó el deploy + merge pero omitió tag y doc. Memoria persistente del progreso ayudó a detectar el gap.

3. **Réplica de patrones es buen barómetro de tech debt**: este sprint replica el mismo fix de Sprint 1 V2 ya en main. Tener el sister fix sin aplicar en CROP era tech debt latente desde el launch.

## Backlog actualizado

| Sprint | Componente | Status |
|--------|-----------|--------|
| ✅ Sprint 2 CROP | fix exit_price | **CERRADO** (este sprint) |
| ⏸️ V2 unpause | Deploy `abae1a5` (`claude_medium` block) | Pausado en producción, requiere cherry-pick + redeploy |
| 🔜 CROP→LIVE prep | Fix LIVE path línea 882 + `counter_key` typo | Bloqueante para LIVE mode |
| 🔜 S3.X | Firestore persistence (consolida S3 + S3.5) | Cuando V1 termine en Firestore |
| 🔜 S3.1-4 | Refactor callsites STOP_LOSS_MULT etc | Cualquier momento |

## Conclusión

Sprint 2 cierra el bug crítico que dejó 1336/1336 closes con `limit_price=0` / `pnl=None` desde el launch de CROP. La estrategia ahora puede tracking real PnL en cierres paper (y en live cuando se haga el siguiente fix del LIVE path).

Producción operativa @ 100%, S3.5 features preservados (`position_sizing 100/100 keys`), 0 errors logs, smoke tests pass con datos Schwab reales en market hours.

Sprint 2 official close + housekeeping retroactivo completo.
