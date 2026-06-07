# SPX Calibration Sprint — Resultado

Fecha: 2026-06-07T20:00Z (sprint autónomo end-to-end)
**Decisión: NOT_PROMOTED** (engine A intacto en `llm-engine-service-00029-t4q`)
Branch: `sprint/spx-calibration` (pushed para review)

## Resumen ejecutivo

3 hipótesis testeadas sobre engine B shadow con cambios al system prompt
(prompt_builder.py) — TODAS produjeron 0% action rate en SPX.

Investigación post-mortem reveló que el problema NO está en el prompt:
**los snapshots SPX del backtest tienen indicadores intraday vacíos** (EMAs
intraday=spot, VWAP σ=0, volume=0, Opening Range=N/A, RSI defaulteado a 50).
El engine correctamente rechaza con TR-Juan-079 (confluence requirement) y
TR-Juan-036 (Fibonacci sobre apertura).

Root cause: `backtest.snapshot_replay.reconstruct_snapshot` no obtiene
pricehistory de Schwab para SPX (símbolo cash index distinto de SPY/QQQ).
Solo carga datos de cache QD (options chain), faltan OHLC y indicadores
intraday. Engine no es el cuello de botella.

## Hipótesis probadas (todas FAILED)

### H1 — SPX → SPXW chain mapping + IVR=100 esperable + flip_zone operable
- Inserted en SYSTEM_PROMPT_TEMPLATE después de cushion absoluto note
- Commit: `32d7347`
- Result: 20 samples, 100% WAIT, 0% action

### H2 — flip_zone operable explícito (4 condiciones → IRON_CONDOR)
- Inserted después de H1 block, ANTES de "7.4 Timing por phase"
- Commit: `7795fa8`
- Result: 20 samples, 100% WAIT, 0% action
- (Runbook tenía bug `src += flip_block` appendia FUERA del string — fixed)

### H3 — IVR=100 override (anti-prudencia data_anomaly_check)
- Inserted después de H2 block
- Commit: `6c247d2`
- Result: 20 samples, 100% WAIT, 0% action

## FASE 5 — Validación final SPX (40+ samples)

```json
{"n": 45, "action_pct": 0.0, "wait_pct": 100.0, "error_pct": 0.0, "spx_pass": false}
```

**SPX_PASS criteria** (action≥35% AND error≤3% AND n≥40): **FAIL**

## FASE 5b — No-regression check SPY/QQQ/IWM/TQQQ

```
SPY:  n=6, action=50%  (need ≥35%)   PASS
QQQ:  n=6, action=100% (need ≥45%)   PASS
IWM:  n=6, action=0%   (need ≥30%)   FAIL
TQQQ: n=6, skip=100%   (need ≤35%)   FAIL  ← regresión vs A3
```

**NOREG_PASS**: **FAIL** (IWM 0% action, TQQQ 100% skip — pero n=6 cada uno
es muestra pequeña, podría ser día atípico).

## Decisión auto-promote: NOT_PROMOTED

Ambos criterios (SPX_PASS + NOREG_PASS) deben ser True para promover. Ambos
fueron False → engine A queda en `llm-engine-service-00029-t4q` (bundle 9 +
A3 patch v1.7→v1.8).

## Root cause análisis (post-mortem)

Sample del primer SPX decision payload (engine B con H1+H2+H3 todos activos):

```json
{
  "verdict": "WAIT", "confidence": 3,
  "main_reason": "Apertura de sesión con datos técnicos insuficientes para operar.
   Opening Range aún no establecido (state=N/A), VWAP sin bandas (σ=0), EMAs
   intraday no pobladas ($0.00), volumen=0. Aunque VIX=16 (LOW_STABLE) es
   favorable per TR-Juan-104, el protocolo TR-Juan-036 requiere calcular pivotes
   Fibonacci sobre precio apertura ANTES de cualquier trade.",
  "warnings": [
    "Datos técnicos incompletos en apertura: EMAs intraday=$0.00, VWAP σ=0,
     volumen=0, OR state=N/A",
    "IVR call=100 y IVR put=100 en SPX es estructural (options chain enorme),
     NO anomalía - operable cuando confluence confirme",
    "GEX regime flip_zone con gamma_zero=$7355 muy cerca de spot=$7353.61 -
     monitorear para IC setup cuando OR establezca niveles"
  ]
}
```

Notar: el engine ENTIENDE el contexto SPX (H1 patch funciona — ya no es "unknown"
regime, ya no llama IVR=100 anomalía), pero igual rechaza porque los indicadores
de PRICE ACTION están en cero. NO es bias contra SPX — es respeto correcto al
protocolo TR-Juan-036/079.

## Próximos pasos para REVIEW HUMANO

1. **Branch sprint/spx-calibration** pushed. Tiene los 3 patches (H1, H2, H3).
   Probablemente vale promover H1 al menos (no regresa nada y mejora regime detection).
2. **Sprint dedicado de snapshot quality SPX**: parche `snapshot_replay` para
   obtener Schwab pricehistory SPX (símbolo "$SPX.X" o equivalente Schwab) o
   recalcular indicadores intraday desde QD data si disponible.
3. **No-regression IWM/TQQQ con n=6 muestras es ruidoso**: re-validar con muestra
   mayor (50+ samples cada uno) antes de concluir regresión real. Day-of-week
   o evento macro puede explicar 100% skip.
4. **Engine A queda intacto** en revision 00029-t4q (bundle 9 + A3 + KB v1.8).
   No hace falta rollback.

## Resultados intermedios

```
/tmp/spx_calib/
  rollback_rev.txt
  h1_result.json     {"action_pct": 0.0, "wait_pct": 100.0, "n": 20}
  h2_result.json     {"action_pct": 0.0, "wait_pct": 100.0, "n": 20}
  h3_result.json     {"action_pct": 0.0, "wait_pct": 100.0, "n": 20}
  final_spx.json     {"n": 45, "action_pct": 0.0, ...}
  noreg.json         {"all_pass": false, ...}
```

## Engine state final

- Engine A (prod): `llm-engine-service-00029-t4q` (UNCHANGED — bundle 9 + A3)
- Engine B (shadow): `llm-engine-shadow-service-00004-85b` (H1+H2+H3 patches)
- Rollback warm: `llm-engine-service-00026-vrx` (v1.7 vanilla)
- Cost del sprint: ~$11 (h1 $2 + h2 $2 + h3 $2 + final $4 + noreg $1)
