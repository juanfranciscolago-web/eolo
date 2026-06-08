# SPRINT A — TR-Juan-109 NOT_PROMOTE (hallazgo arquitectural)

Fecha: 2026-06-08
Status: **NOT_PROMOTE** — TR-109 unreachable por gate arquitectural upstream

## Resumen ejecutivo

TR-Juan-109 (VIX_SPIKE_DIRECTIONAL_BIAS) NO se promueve. La razón no es PnL inferior — es que la regla nunca llega a ejecutarse en los 145 samples del backtest sobre 20 eventos VIX spike. Engine A (sin TR-109) y Engine C (con TR-109) produjeron **verdicts 100% idénticos**.

## Root cause

**TR-Juan-088** (TRADE_FREQUENCY_OVERRIDE_WAIT_PHILOSOPHY, MAESTRA) define "evento extremo" como `VIX velocity 30m > +10%` y permite WAIT.

El prescreen Haiku interpreta esto literalmente y bloquea (`SKIPPED_BY_HAIKU`) precisamente los samples con VIX velocity alta — que son exactamente los samples que TR-109 fue diseñada para resolver.

Cuando Sonnet sí evalúa (vel +5% a +10%, fuera del gate de Haiku), elige TR-107 (VIX_HIGH_CONDITIONAL_ACTION → WAIT) o TR-106 (VIX_MEDIUM_DEFENSIVE_OP → defensive) como rules dominantes. TR-109 nunca aparece en `rules_b`.

## Métricas

| Métrica | Valor |
|---|---|
| Total samples (20 eventos × 2 tickers × ~5 horas) | 145 |
| Verdicts WAIT (engine A) | 55 |
| Verdicts SKIPPED_BY_HAIKU (engine A) | 90 |
| Verdicts no-WAIT (engine A) | **0** |
| Verdicts no-WAIT (engine C con TR-109) | **0** |
| Samples con vix_velocity_30m_pct > +5% | 90 (62%) |
| Samples con \|vel\| > 5% | 105 (72%) |
| Referencias a TR-109 en rules_b | **0** |
| Referencias a TR-088 en rules_b | 49 |
| Verdicts idénticos A/C | **145/145** |

## TR-088 (el blocker)

```
Trigger: TRADE_FREQUENCY_OVERRIDE_WAIT_PHILOSOPHY
Action: WAIT requiere [...] O evento extremo: (a) VIX velocity 30m > +10%, (b) FOMC/CPI/NFP confirmado <60min, (c) Circuit breaker, (d) API down.
Tier: MAESTRA
```

## Caminos posibles para destrabar TR-109

1. **Modificar TR-088** para excluir explícitamente el caso de TR-109: "VIX velocity > +10% PERO con price_change_30m < -0.5% → CONSULTAR TR-109". Riesgo: rebalancear todo el régimen de spikes.

2. **Promover TR-109 a override explícito de TR-107**: Action text dice "Override sobre TR-105/106/107" pero Sonnet no lo respeta porque TR-088 ya gateo.

3. **Mover TR-109 lógica a capa Haiku**: en lugar de pasar por Sonnet, el prescreen evalúa la condición y emite directamente. Más invasivo.

4. **Aceptar status quo**: el sistema actual prefiere WAIT en spikes violentos, lo cual es defensivamente razonable. TR-109 puede ser "letra muerta" intencional.

## Recomendación

No cargar batalla por TR-109 hasta resolver la pregunta de filosofía: ¿queremos actuar direccionalmente en spikes (TR-109) o preferir prudencia (TR-088 actual)? Esto debería ser una decisión consciente, no un side-effect de prioridades de reglas.

## Sí vale por sí solo: VIX fetcher (FASE 0)

`backtest/vix_fetcher.py` + patch a `snapshot_replay.py` son **independientemente útiles**:
- Habilita cualquier sprint futuro VIX-related (data real, no DEFAULT_VIX_LEVEL=16.0)
- Cache local con yfinance ^VIX, fallback a fetch on-the-fly
- vix_velocity_30m_pct calculado day-over-day como proxy

Pendiente decisión usuario: cherry-pick FASE 0 a main aunque TR-109 no promueva.

## Sprint cost

Backtest validate: 145 samples × $0.10 promedio = ~$15.
Engine A prod intacto en `llm-engine-service-00029-t4q`.
Engine C shadow (`llm-engine-spike-test-service`) queda activo para análisis manual.
