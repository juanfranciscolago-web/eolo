# Sprint — LLM Engine Bloque 4 Fase 2 (Integración eolo-crop)

- **Fecha cierre:** 2026-05-28
- **Autor:** Juan + Claude
- **Status:** DONE (código) — pendiente activación feature flag (4.C.3)
- **Branches mergeadas:**
  - `feat/eolo-crop-llm-integration-v0.1` (PR #5)
  - `feat/eolo-crop-llm-cache-and-wiring` (PR #6)
  - `fix/eolo-crop-vix-history-buffer` (PR #7)
  - `feat/eolo-crop-llm-sanity-validator` (PR #8)

## Scope

Integración del LLM Engine v0.2 (deployado en Cloud Run en Fase 1) con el bot eolo-crop. Approach: **full driver con layered Haiku+Sonnet**. El bot consulta al LLM antes del scan rule-based; el LLM decide verdict + strikes; el scan valida en chain y ejecuta.

Feature flag `strategy_params.llm_engine.enabled` default OFF. Activación en 4.C.3.

## Sub-bloques completados

### 4.A — Package `eolo-crop/llm_gate/` base (PR #5, +998 LOC)

5 archivos nuevos + smoke + requirements. Cliente HTTP + integration helpers + snapshot builder.

- `client.py` (220 LOC): `LLMGateClient` con auth ID token vía metadata server (Cloud Run SA) + fallback gcloud (dev local). `pre_decide` + `decide` + `consult()` layered. Fallback WAIT en cualquier error.
- `indicators.py` (132 LOC): copia literal de `market_data_collector.py` del LLM Engine. **Tech debt**: extraer a paquete compartido en el futuro.
- `snapshot.py` (344 LOC): `build_market_snapshot_from_crop` con 56 fields. Resample CandleBuffer 1-min a 2m/15m vía `BufferMarketData`. Defaults documentados para campos sin data en v0.1.
- `integration.py` (139 LOC): `should_call_llm` pre-filter + `llm_decision_to_scan_params` converter Decision → params para scan.
- `scripts/smoke/llm_integration_crop.py`: smoke real validado contra Cloud Run (Haiku 1.7s + Sonnet 20s, verdict WAIT con snapshot mock-vacío, comportamiento defensivo correcto).
- `eolo-crop/requirements.txt`: + `httpx>=0.25,<0.28` (consistente con LLM Engine pin).

### 4.B.1 — `DecisionCache` (parte de PR #6, +160 LOC)

Archivo nuevo `cache.py`. Cache thread-safe (`threading.Lock`) con TTL configurable (default 30s). Invalidación automática por:
- TTL expirado
- VIX velocity 30m delta >2%
- Cambio de `has_open_positions` o `open_positions_summary`

Stats con `hit_rate`, evictions, invalidations por tipo.

**Bug fix encontrado en tests sintéticos**: `has_open_positions` estaba en `_CACHE_KEY_FIELDS`, lo que hacía que cambios de positions generaran misses por key diferente — el path `positions_invalidations` nunca incrementaba. Fix aplicado: cache key solo `["ticker", "session_phase"]`, check explícito de bool+summary en `get()`. Tests 5/5 verde post-fix.

### 4.B.2 — Wiring en `crop_main.py` (parte de PR #6, +153 LOC)

Feature flag default OFF garantiza no-op operacional al mergear.

- Imports `llm_gate` (4 líneas)
- `__init__`: 8 atributos LLM (`enabled`, `url`, `threshold`, `cache_ttl_seconds`, `tickers_enabled` dict, `max_positions`, lazy `_llm_client` + `_llm_cache`)
- `_apply_strategy_overrides_to_instance_vars`: 5 keys nuevas con prefix `strategy_params.llm_engine.*` con try/except defensivo
- Helpers `_close_theta_positions_for_ticker` async + `_format_open_positions_summary` sync
- **Wiring en `_run_theta_harvest` L1135** (POST `_log_theta_decision` para preservar observability Firestore):
  * Skip si `pivot_result` o `vix` is None
  * Lazy init de client + cache
  * Build snapshot vía `build_market_snapshot_from_crop`
  * `should_call_llm` pre-filter
  * Cache check primero, `consult()` si miss
  * `asyncio.to_thread` para no bloquear event loop con httpx sync
  * Handle WAIT / CLOSE_POSITIONS / SELL_X verdicts
  * `try/except` wrap: si LLM falla, continúa flow rule-based

### Tech debt #18 RESUELTA — VIX velocity buffer (PR #7, +37/-2 LOC)

Pattern demand-driven idéntico al `_spy_price_history` existente. Auto-record en `_theta_get_macro_context()`.

- `self._vix_price_history: list[(ts_unix, vix_value)]`
- Helper `_record_vix_history(vix)`: append + cleanup >35min
- Helper `_compute_vix_velocity_30m()`: busca sample más viejo en ventana 30min, calcula delta % vs ahora. Safe defaults (0.0 si <2 samples, buffer vacío, o `vix_old=0`).
- Wiring LLM ahora pasa velocity real en vez de `0.0` hardcoded.
- Tests sintéticos 7/7 verde.

**RESUELTA = el Haiku ahora SÍ detecta spikes VIX intradía desde el bot real.** TR-Juan-058 PROHIBITIVA efectiva. Safety rail `VIX_SPIKE` del decision_parser efectivo cuando el bot esté en producción con flag activo.

### Tech debt #19 RESUELTA — LLM strike hint + spread threshold (PR #8, +91/-8 LOC)

2 archivos modificados. **2 thresholds distintos por intención**:

- `LLM_HINT_THRESHOLD = 7` en `strategy.py` (strike hint, ajuste fino dentro del mismo spread_type, menos sensible)
- `_llm_spread_override_threshold = 8` en `crop_main.py` (direccionalidad cambia entre SELL_PUT y SELL_CALL, más conservador)

**Logic en `scan_theta_harvest`**:
- Si LLM hint con confidence ≥ 7 Y strike existe en chain Y delta dentro del rango pivot expanded (±0.05): usa el strike del LLM
- Sino: fallback rule-based (`_find_best_strike`)
- WARNING logs en fallbacks para tracking de hints sub-óptimos sistemáticos (señal para iterar KB en Fase 4)

**Logic en `crop_main` wiring**:
- Si LLM verdict ≠ spread_type del sector Y confidence ≥ 8: override (WARNING log)
- Sino: keep sector spread_type (INFO log)
- Populate hint vars (que se pasan al scan) **solo** si spread_type final matchea con el LLM

Flow validado mentalmente con 3 escenarios A/B/C (ver PR #8 body).

## Decisiones arquitectónicas

1. **Full driver** elegido sobre shadow/gate: LLM decide todo (verdict + strikes), scan rule-based valida sanity en chain. Coherente con el approach del KB v1.2 (LLM internaliza reglas).
2. **Layered Haiku + Sonnet**: pre-filter Haiku (~$0.003) decide si vale la pena llamar Sonnet (~$0.02). Ahorro estimado 50-60%.
3. **Cache TTL 30s + invalidación VIX/positions**: reduce calls redundantes en ciclos consecutivos donde nada cambió. Cache key solo identidad estable.
4. **Pre-filter rule-based en cliente** (no en servidor): ticker disabled, ventana 9:30-12:00 ET, VIX spike, macro event ≤1d, max positions. Filtra antes de gastar $ en LLM.
5. **Feature flag default OFF**: PRs mergeables a main sin riesgo operacional. Activación deliberada vía Firestore (4.C.3).
6. **`asyncio.to_thread` para httpx sync**: el cliente sync no bloquea event loop del bot (Sonnet ~17s latency total).

## Cost proyectado

Con todo activo (post-4.C.3):

| Componente | Detalle | Cost/día |
|---|---|---|
| Loop entries (9:30-12:00 ET) | 4 tickers × ~150 ciclos × $0.003 (Haiku) | ~$1.80 |
| Sonnet pass-through (~40% de Haiku) | 0.4 × 600 × $0.02 | ~$4.80 |
| Exits (con positions abiertas) | Cache hit ~70-80%, calls residuales | ~$1-2 |
| **Total esperado** | | **~$8-10/día → ~$170-220/mes** |

vs estimado original sin layered ($300-450/mes). **Ahorro ~50%** confirmado en proyección.

## Validación

- Tests sintéticos `DecisionCache` 5/5 verde (hit, miss, vix invalidation, positions invalidation, TTL eviction) con assertions
- Tests sintéticos VIX velocity 7/7 verde (empty, 1 sample, 30m up/down, short buffer, vix_old=0, cleanup)
- Smoke real `LLMGateClient.consult()` contra Cloud Run (`/pre_decide` + `/decide`, ~$0.023 total)
- Syntax checks (`ast.parse`) OK en `crop_main.py` y `theta_harvest_strategy.py`
- 4/4 PRs con `mergeable=MERGEABLE, mergeStateStatus=CLEAN`

## Estado del flow end-to-end (con flag activo)

```
_run_theta_harvest(ticker, chain)
    │
    ├─ Gates rule-based (NYSE, news, Tick/AD)
    │
    ├─ vix, vvix = _theta_get_macro_context()  ← auto-record VIX history
    ├─ pivot_result = _theta_get_pivot(ticker, ...)
    ├─ spread_type = sector_analysis or signals
    ├─ _log_theta_decision(...)                  ← observability preserved
    │
    └─ LLM Wiring (if enabled):
       ├─ skip if pivot or vix None
       ├─ build_market_snapshot_from_crop(... vix_velocity_30m=_compute(...))
       ├─ should_call_llm() pre-filter rule-based → skip if False
       ├─ cache.get() → HIT? use cached decision
       │             → MISS? consult() vía asyncio.to_thread
       │
       ├─ WAIT verdict → return
       ├─ CLOSE_POSITIONS → _close_theta_positions_for_ticker + return
       └─ SELL_X verdict:
          ├─ if confidence ≥ 8: override spread_type del sector
          ├─ if spread_type final matchea LLM: populate hint vars (strike/delta)
          │
          └─ continúa al loop por DTE
             └─ scan_theta_harvest_tranches(... llm_short_strike=hint, llm_confidence=...)
                ├─ if confidence ≥ 7 + strike en chain + delta válido: usa strike LLM
                └─ sino: _find_best_strike() rule-based fallback
                
                Returns signals → trader.open_spread() ejecuta
```

## Tech debt status

| # | Severidad | Status | Item |
|---|---|---|---|
| #15 | ALTA | ⏳ Pending | Daily indicators (rsi_daily, ema_*_daily) defaulteados a neutral. REST cacheada en v0.2. |
| #16 | MEDIA | ⏳ Pending | Bumpear `CANDLE_BUFFER_SIZE` 100→500 (BVP/SVP intraday completo). |
| #17 | BAJA | ⏳ Pending | MACD 15m warning si buffer <30 candles. |
| **#18** | **ALTA BLOQUEANTE** | ✅ **RESUELTA** (PR #7) | VIX velocity 30m buffer. |
| **#19** | MEDIA | ✅ **RESUELTA** (PR #8) | LLM hint restricción strike/delta. |
| #20 | MEDIA | ⏳ Pending | `vix_velocity_1d_pct` REST startup para contexto multi-día. |
| #21 | BAJA | ⏳ Pending | Docstring helper "ventana parcial intencional". |

**Bloqueantes para activar flag**: ninguno (#18 RESUELTA). Las pending son mejoras de calidad de datos, no impiden operar.

## Salvavidas (rollback)

**Opción 1 — Rollback código** (si bug post-activación):
```bash
gh pr revert 8  # 4.C.1 strike hint
gh pr revert 7  # fix #18 VIX
gh pr revert 6  # cache + wiring
gh pr revert 5  # llm_gate base
```
Cada revert es independiente; secuencial restaura main a estado pre-Fase 2.

**Opción 2 — Disable runtime** (más seguro, NO rollback de código):
```bash
# Via Firestore override (no requiere redeploy):
POST /api/state/edit
{"strategy_params.llm_engine.enabled": false}
```
Bot vuelve a flow rule-based puro instantáneamente. Código LLM queda en main pero dormido.

**Recomendación**: Opción 2 primero (instant). Opción 1 solo si hay bug que afecta flow rule-based (no debería — el wiring está dentro del feature flag).

## Next — 4.C.3 (activación + monitoreo)

Checklist para próxima sesión:

1. **Pre-activación**: confirmar PR #7 + #8 desplegados en bot prod (no solo main). Verificar Cloud Run revision tiene los últimos commits.
2. **Activar feature flag** vía Firestore override:
   ```bash
   POST /api/state/edit
   {"strategy_params.llm_engine.enabled": true,
    "strategy_params.llm_engine.url": "https://llm-engine-service-nmjz4iwcea-uc.a.run.app"}
   ```
3. **Monitorear primeros 5-10 trades** del día siguiente operativo:
   - `[llm]` log entries en GCP Cloud Logging
   - Verdict distribution (WAIT vs SELL_X vs CLOSE)
   - Cache hit rate (target >50%)
   - Cost real vs proyectado ($170-220/mes)
   - Latency p95 (target <25s end-to-end)
4. **Decisión 1-2 días post-activación**:
   - Si OK → Bloque 4 v0.1 DONE
   - Si problemas → rollback con Opción 2 + iterar

## Roadmap Fase 3+ (post-activación)

- Resolver tech debt #15 (daily indicators REST), #16 (CANDLE_BUFFER_SIZE), #20 (velocity_1d)
- Hot-reload del KB (sin restart de Cloud Run para iterar reglas)
- Logging correlación: LLM decision → trade outcome → KB rule attribution
- Workflow iteración del prompt según outcomes en producción
- Eventualmente: A/B test rule-based vs LLM-driver con metric comparison

## Commits del sprint (4 commits + 4 merges en main)

```
3aee298 Merge pull request #8 from .../feat/eolo-crop-llm-sanity-validator
04ee858 feat(eolo-crop): LLM strike hint + spread override threshold (4.C.1)
c13d98e Merge pull request #7 from .../fix/eolo-crop-vix-history-buffer
643e50e fix(eolo-crop): VIX velocity_30m buffer for LLM snapshot (tech debt #18)
0bf0c45 Merge pull request #6 from .../feat/eolo-crop-llm-cache-and-wiring
9a544b1 feat(eolo-crop): wire LLM Engine into crop_main + DecisionCache
28b48c3 Merge pull request #5 from .../feat/eolo-crop-llm-integration-v0.1
db185d9 feat(eolo-crop): llm_gate package — cliente HTTP + layered approach
```

Total: **+1,260 / -11 LOC en 11 archivos** sobre 4 PRs + 1 doc (este sprint cierra).
