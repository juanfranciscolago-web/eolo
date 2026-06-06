# Backtest Saturday 2026-06-06 — Triple comparativo v1.5 → v1.6 → v1.7

## Setup

- **Window**: 2026-05-19 → 2026-06-02 (10 trading days)
- **Tickers**: SPY, QQQ, IWM, TQQQ
- **Sample hours**: 10:00, 14:00 ET (2/día = 88 max)
- **Pre-screen (Haiku)**: ON
- **Budget cap**: $12 per run
- **Engine**: `llm-engine-service` us-central1, revs `00024`/`00025`/`00026-vrx`

## Comparativo triple

| Métrica | v1.5 baseline | v1.6 post-edits | v1.7 disaggregated | Δ v1.6→v1.7 | Δ v1.5→v1.7 |
|---|---|---|---|---|---|
| **WAIT** | 74 (84.1%) | 58 (65.9%) | **55 (62.5%)** | −3.4 pp | **−21.6 pp** |
| **SELL_PUT (action)** | 0 (0.0%) | 14 (15.9%) | **19 (21.6%)** | +5.7 pp | **+21.6 pp** |
| SKIPPED_BY_HAIKU | 14 (15.9%) | 16 (18.2%) | 14 (15.9%) | −2.3 pp | 0.0 pp |
| Cost | $7.49 | $7.29 | $7.49 | +$0.20 | $0.00 |
| Wall time | ~50 min | ~52 min | ~49 min | −3 min | −1 min |
| N decisions | 88/88 | 88/88 | 88/88 | — | — |

## Verdicts per ticker (v1.7)

| Ticker | WAIT | SKIPPED | SELL_PUT | v1.6→v1.7 action delta |
|---|---|---|---|---|
| SPY | 18 | 0 | 4 | 2 → 4 (+2) |
| **QQQ** | 11 | 0 | **11** | 8 → 11 (+3) **50% action** |
| IWM | 18 | 0 | 4 | 4 → 4 (flat) |
| TQQQ | 8 | 14 | 0 | 0 → 0 (Haiku pre-filter) |

## Top reglas citadas (v1.7)

`TR-Juan-088` **(64, ≈v1.6)** · `TR-Juan-072` (46) · **`TR-Juan-104` (37 — NUEVA top-3)** · `TR-Juan-001` (35) · `TR-Juan-077` (32) · `TR-Juan-079` (29) · `TR-Juan-008` (19) · `TR-Juan-062` (17) · `TR-Juan-082` (17) · `TR-Juan-070` (14) · `TR-Juan-040` (14) · `TR-Juan-074` (10) · **`TR-Juan-105` (9 — NUEVA)** · `TR-Juan-064` (8) · `TR-Juan-068` (6).

## Adopción de sub-reglas (091-108)

| Sub-regla | Citas | Router parent | Status |
|---|---|---|---|
| **TR-Juan-104** (VIX<15 max action) | **37** | 043 (VIX) | ✅ Top-3 cited, sub-rule path completamente adoptado |
| **TR-Juan-105** (VIX 15-20 normal) | 9 | 043 (VIX) | ✅ Adoptado |
| **TR-Juan-106** (VIX 20-25 defensive) | 1 | 043 (VIX) | ⚠ Marginal (régimen poco frecuente en muestra) |
| TR-Juan-091 (VRP cheap+IV>=12%) | 1 | 063 (VRP) | ⚠ Marginal |
| TR-Juan-099 (Pin low magnet watch) | 1 | 067 (Pin) | ⚠ Marginal |
| 092, 093, 094 (VRP) | 0 | 063 (VRP) | ❌ Sin disparos |
| 095, 096, 097, 098 (Pin) | 0 | 067 (Pin) | ❌ Sin disparos |
| 100, 101, 102, 103 (Range) | 0 | 022 (Range) | ❌ Sin disparos |
| 107, 108 (VIX high/panic) | 0 | 043 (VIX) | ✅ Esperado (sin panic en muestra) |

**Total sub-rule citations**: 49 / 88 decisiones (55.7%).

## Comportamiento de routers (parents)

| Router | v1.6 cites | v1.7 cites | Interpretación |
|---|---|---|---|
| TR-Juan-043 (VIX) | 30 | **2 (−93%)** | ✅ Disagg total — LLM cita sub-reglas en su lugar |
| TR-Juan-063 (VRP) | 10 | 3 (−70%) | ✅ Disagg parcial — VRP cheap poco frecuente en muestra |
| TR-Juan-067 (Pin) | 3 | 2 | ≈ Estable (pin conditions raras en muestra) |
| TR-Juan-022 (Range) | n/a | 1 | ⚠ Range sub-rules sin tracción (LLM no las invoca) |

## Conclusiones

1. **VIX disaggregation = home run**. TR-Juan-104 absorbió 37 citations + 105 (9) + 106 (1) = **47 citas combinadas**, contra **2** del parent router. La filosofía VIX-based con path concreto en cada régimen funcionó. QQQ saltó de 36% → 50% action rate.

2. **WAIT rate modesto pero direccional**. Bajó de 65.9% → 62.5% (−3.4pp). El target proyectado de 30-45% fue optimista — la muestra de 10 días tiene muchos `flip_zone` (54/88) donde TR-Juan-070 sigue capeando confianza pero ya no bloquea. La ganancia real vino del action rate: 0% → 16% → **21.6%**.

3. **Pin/Range/VRP sub-rules con tracción mínima**. Sólo 3/13 sub-reglas no-VIX dispararon (091, 099, una vez cada una). Hipótesis: el régimen de la ventana (mayoría flip_zone, IVR medio, sin pin claro) no genera las precondiciones de 095-098, 100-103. Necesita re-test con ventana que incluya range-bound days claros.

4. **Costo neutral**. $7.49 v1.7 = $7.49 v1.5. Las 18 sub-reglas no agregaron tokens netos (prompt builder probablemente filtra por relevancia).

5. **Routers cumplen su rol**. TR-Juan-043 cayó de 30 → 2 citas (−93%) — el LLM dejó de citar al parent y citó a la sub-regla correcta. Demuestra que el patrón ROUTER → sub-rule funciona cuando hay sub-reglas con suficiente cobertura.

## Próximos pasos

- **Ampliar ventana a 60 días × 4 tickers** para validar sub-reglas Pin/Range/VRP con setups efectivos en su sub-régimen. Si action rate ≥ 18% sostenido → promote v1.7 a producción CROP.
- **Investigar TQQQ-only 100% NO-ACTION** (Haiku pre-filter + WAIT, 0 SELL_PUT en las 3 versiones). Posible falsa exclusión por IVR baja en TQQQ.
- **Calibrar TR-Juan-104 strength**: confirmar que en VIX>20 days reales no se dispara incorrectamente.
- **Considerar disagg adicional** para TR-Juan-070 (flip_zone cap conf=7) y TR-Juan-088 (top-1 cited 64x) — son los próximos candidatos a desagregar si el WAIT residual de 62.5% sigue alto en ventana extendida.

## Artefactos

- KB v1.7 review: `docs/KB_v1.7_REVIEW.xlsx`
- v1.5 baseline: `/tmp/backtest_v15_baseline_20260606_100733`
- v1.6 post-edits: `/tmp/backtest_v16_postedits_20260606_110221`
- v1.7 disaggregated: `/tmp/backtest_v17_disagg_20260606_124800`
- Commit (local): `35c778f feat(KB-v1.7): desagregación TR-Juan-063/067/022/043 → 18 sub-reglas concretas`
- Engine revision: `llm-engine-service-00026-vrx`
- Firestore: `backtest_runs/backtest_v17_disagg_20260606_124800`
