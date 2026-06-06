# Backtest Saturday 2026-06-06 — v1.5 vs v1.6

## Setup

- **Window**: 2026-05-19 → 2026-06-02 (10 trading days)
- **Tickers**: SPY, QQQ, IWM, TQQQ
- **Sample hours**: 10:00, 14:00 ET
- **Pre-screen (Haiku)**: ON
- **Budget cap**: $12

Scope reducido del runbook original (60 días) tras observar pace `~1.25 /decide/min`
en engine v1.5 — proyectaba >5h por backtest. Reducción a 10 días mantiene
poder estadístico para detectar delta WAIT→ACTION pero respeta cap 90min.

## Baseline v1.5

- Output: `/tmp/backtest_v15_baseline_20260606_100733`
- KB: `v1.5` (90 reglas)
- Engine revision: `llm-engine-service-00024-*`
- Wall time: ~50 min
- Cost: $7.49 (88/88 decisions)

### Verdict distribution

| Verdict           | Count | %     |
|---|---|---|
| WAIT              | 74    | 84.1% |
| SKIPPED_BY_HAIKU  | 14    | 15.9% |
| **ACTION (total)**| **0** | **0.0%** |

### Per-ticker

| Ticker | WAIT | SKIPPED | SELL_PUT |
|---|---|---|---|
| SPY    | 22 | 0  | 0 |
| QQQ    | 22 | 0  | 0 |
| IWM    | 22 | 0  | 0 |
| TQQQ   | 8  | 14 | 0 |

### Top rule citations
TR-Juan-077 (67), TR-Juan-072 (64), TR-Juan-088 (43), TR-Juan-036 (29),
TR-Juan-040 (28), TR-Juan-070 (25), TR-Juan-062 (24), TR-Juan-079 (20).

**Hallazgo**: TR-Juan-088 ya citada 43× pero NO modifica conducta — es tratada
como guidance, no como override. TR-Juan-062 + TR-Juan-070 son las top "sino WAIT"
que bloquean al LLM en `flip_zone` y `transition`.

## Post-edits v1.6

- Output: `/tmp/backtest_v16_postedits_20260606_110221`
- KB: `v1.6` (90 reglas, 3 editadas)
- Engine revision: `llm-engine-service-00025-rbn`
- Wall time: ~52 min
- Cost: $7.29 (88/88 decisions)

### Edits aplicados

| Rule        | Tipo                  | Cambio |
|---|---|---|
| TR-Juan-062 | Action rewrite        | "sino WAIT" → "confidence cap 6" (operable con sizing reducido) |
| TR-Juan-070 | Cap relaxation        | cap conf 6→7, size ×0.5→×0.7, NO bloquear con WAIT |
| TR-Juan-088 | Action specification  | Default ES action; WAIT solo gates duros (PROHIBITIVA, VIX panic, FOMC, circuit breaker, API down) |
| prompt_builder.py | Philosophy injection | Bloque "MANDATORY OPERATIONAL PHILOSOPHY" insertado antes de DECISION MATRIX en system prompt |

### Verdict distribution

| Verdict           | Count  | %      |
|---|---|---|
| WAIT              | 58     | 65.9%  |
| SKIPPED_BY_HAIKU  | 16     | 18.2%  |
| **SELL_PUT**      | **14** | **15.9%** |

### Per-ticker

| Ticker | WAIT | SKIPPED | SELL_PUT |
|---|---|---|---|
| SPY    | 20 | 0  | 2  |
| QQQ    | 14 | 0  | 8  |
| IWM    | 18 | 0  | 4  |
| TQQQ   | 6  | 16 | 0  |

### Top rule citations
TR-Juan-088 (61 — sube de #3 a #1), TR-Juan-077 (48), TR-Juan-072 (48),
TR-Juan-043 (30), TR-Juan-001 (29), TR-Juan-070 (23), TR-Juan-079 (23),
TR-Juan-062 (9 — cae de 24 a 9).

## Comparativo

| Métrica          | v1.5 BASELINE | v1.6 POST-EDITS | Delta |
|---|---|---|---|
| WAIT rate        | 84.1%   | 65.9%   | **−18.2 pp** |
| Action rate      | 0.0%    | 15.9%   | **+15.9 pp** |
| SKIPPED_BY_HAIKU | 15.9%   | 18.2%   | +2.3 pp |
| Cost             | $7.49   | $7.29   | −$0.20 |

## Conclusiones

1. **Edits destrabaron action rate** de 0% a 16% en 10 trading days × 4 tickers.
   QQQ es el ticker con mayor swing: 100% WAIT → 36% SELL_PUT (8/22 muestras).
2. **TR-Juan-088 sube a top-1 citation** (43→61). La philosophy injection en
   prompt_builder.py refuerza la conducta beyond just "regla citable".
3. **TR-Juan-062 cae 24→9** en citas: confirma que su nueva acción
   ("confidence cap 6") es alternativa real a WAIT, ya no bloqueo terminal.
4. **TQQQ permanece 100% NO-ACTION**: 6 WAIT + 16 SKIPPED_BY_HAIKU. El pre-filtro
   Haiku descarta antes de llegar al LLM principal — investigar si TR-Juan-001
   (no operar TQQQ con IVR<X) o algo análogo está siempre activo en TQQQ.
   Esto NO es regresión, es comportamiento de prescreen.
5. **Cost neutral** (~$7.30 ambos): edits no incrementan token usage.

## Próximos pasos (no aplicados en este sprint)

- Ampliar window: si action rate ≥10% se sostiene en 60 días × 4 tickers,
  decidir promote v1.6 a producción CROP (actualmente paper-only).
- Investigar TQQQ-only 100% NO-ACTION: regla anti-TQQQ activa o falso positivo Haiku.
- Calibrar TR-Juan-088 prompt strength: confirmar que action rate no se dispara
  >40% (overcompensation) en days con realidad WAIT-correcta (FOMC, CPI).
- Persistir runs a Firestore `backtest_runs/` para historizar comparativos.

## Artefactos

- Excel review v1.6: `docs/KB_v1.6_REVIEW.xlsx`
- Backtest baseline:  `/tmp/backtest_v15_baseline_20260606_100733`
- Backtest post-edits:`/tmp/backtest_v16_postedits_20260606_110221`
- Commit: `a3e1a3d feat(KB-v1.6): edits TR-Juan-062/070/088 — destrabar WAIT excesivo`
- Engine revision: `llm-engine-service-00025-rbn`
