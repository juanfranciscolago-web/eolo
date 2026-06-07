# SPX Sprint — Bundle 9 metric validation (post-promote)

Generated: 2026-06-07T14:30Z
Output: `/tmp/spx_sprint_20260607_115430`

## Setup
- Window: 2026-03-09 → 2026-06-02 (~60 trading days)
- Ticker: SPX (single — engine A = bundle 9 post-promote, no dual)
- Sample hours: 10:00, 11:30, 13:00, 14:30, 15:15 ET
- Engine: `llm-engine-service-00027-5zt` (KB v1.8 + bundle 9, SHADOW_MODE=true)
- Budget cap: $25 (hit at 248/300 samples = 83% coverage)
- Cost: $25.05

## Resultado: OUTLIER

```
SPX:  n=248  WAIT=100.0%  Action=0.0%  health=OUTLIER
```

**Comparativo con shadow bundle 9 (run anterior, mismo engine B settings)**:

| Ticker | n | WAIT % | Action % |
|---|---|---|---|
| SPY    | 193 | 54.4% | 43.0% |
| QQQ    | 300 | 34.7% | 59.0% |
| IWM    | 273 | 60.1% | 39.9% |
| **SPX** | **248** | **100.0%** | **0.0%** |

SPX rechaza bundle 9 sistemáticamente — verdict health OUTLIER (esperado
35-60% WAIT range, observado 100%).

## Top rules cited

| Rule | Cites | %  |
|---|---|---|
| TR-Juan-077 | 237 | 96% |
| TR-Juan-088 | 232 | 94% |
| TR-Juan-079 | 177 | 71% |
| TR-Juan-036 | 161 | 65% |
| TR-Juan-105 | 114 | 46% |
| TR-Juan-072 | 71 | 29% |
| TR-Juan-104 | 69 | 28% |
| TR-Juan-040 | 66 | 27% |

TR-Juan-088 (anti-WAIT philosophy) citada en 94% de los casos pero NO
unblock al engine — el LLM la cita como guidance pero defaultea a WAIT.

## Regime distribution

- `unknown`: 243/248 (98%)
- `flip_zone`: 5/248 (2%)

El engine clasifica casi todos los snapshots SPX como `unknown` — sin
régimen detectable. Esto explica el 100% WAIT: sin clasificación de
régimen, las reglas de entry (TR-Juan-091..103) no disparan.

## Root cause análisis

Los snapshots SPX tienen 38 fields con valores no-None (price ~$6800,
VIX, RSI, fib, max_pain, OI, etc), pero el engine no logra mapear a
régimen GEX. Posibles causas:

1. **Mapping QD → spot**: snapshot tiene `price` pero el engine espera
   `spot` para SPX (vs SPY donde sí coincide). Investigar
   `snapshot_replay.reconstruct_snapshot` para SPX.
2. **TR-Juan-090 (proxy mapping)**: SPX requiere VXN como proxy de VIX,
   pero el snapshot trae VIX literal (16.0 default). El engine no aplica
   el mapping y trata el contexto como `unknown`.
3. **Cushions absolutos descalibrados**: bundle 9 prompts mencionan
   "SPX $10, SPY $1" — si los strikes/Δ son evaluados con números
   relativos sin ajuste, todos quedan fuera de rango operable.
4. **OI / max_pain en miles**: SPX max_pain=7395, dist=-0.56%. Posible
   que el engine interprete el max_pain como outlier vs spot=6796 sin
   reconocer la escala ×10.

## Próximos pasos

1. **NO desplegar SPX live** con bundle 9 actual.
2. **Sprint dedicado SPX**: revisar `snapshot_replay` para confirmar que
   el campo correcto se está enviando (`spot` vs `price`). Si efectivamente
   falta `spot`, parche en el reconstructor.
3. **TR-Juan-090 validation**: confirmar mapping VIX→VXN para SPX, y si
   el engine lo aplica correctamente.
4. **Calibration test mínimo**: 10 samples manuales SPX con snapshots
   parcheados para verificar antes de 60d full re-run.
5. SPY/QQQ/IWM bundle 9 sigue valido para live trading (paper trading
   ya en curso post-promote).

## Artefactos

- Raw decisions: `/tmp/spx_sprint_20260607_115430/decisions_SPX_*.jsonl`
- Summary: `/tmp/spx_sprint_20260607_115430/spx_summary.json`
- Report.md: `/tmp/spx_sprint_20260607_115430/report.md`
- Engine revision: `llm-engine-service-00027-5zt` (bundle 9 live)
- Rollback rev kept warm: `llm-engine-service-00026-vrx`
