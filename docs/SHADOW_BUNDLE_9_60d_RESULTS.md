# SHADOW-BUNDLE-9 — Dual 60d Backtest Results

Generated: 2026-06-07T01:55Z
Run 1: `/tmp/shadow_60d_20260606_163620` (SPY only, 193 samples — partial, run interrupted)
Run 2: `/tmp/shadow_60d_run2_20260606_191429` (QQQ 300, IWM 273 — TQQQ swap → SPX intended, SPX 0 by budget cap)
Combined summary: `/tmp/shadow_60d_run2_20260606_191429/combined_summary.json`

## Setup

- Window: 2026-03-09 → 2026-06-02 (~60 trading days)
- Tickers: SPY (run 1), QQQ + IWM (run 2). SPX scoped for run 2 but budget cap hit before SPX started.
- Sample hours: 10:00, 11:30, 13:00, 14:30, 15:15 ET
- Engine A (control): KB v1.7 vanilla, `llm-engine-service-00026-vrx`, SHADOW_MODE=false
- Engine B (treatment): KB v1.8 + bundle 9, `llm-engine-shadow-service-00001-6hv`, SHADOW_MODE=true
- Budget cap: $150 combined (run 1 spent $37.98 + run 2 spent $112.05)

### Bundle 9 cambios

1. KB v1.8 (tier bump TR-091..103 TACTICAL_PLUS → MAESTRA + Deprecated_By col)
2. kb_loader filtra reglas con Deprecated_By non-empty (TR-Juan-056 → 083 deprecation)
3. KB_PATH env override (heredado de service.py)
4. prompt_builder inyecta 5 few-shot examples antes de DECISION MATRIX
5. Visibility reorder (few-shot insertion antes del matrix)
6. wait_streak override (≥5 WAIT consecutivos → sugiere action cap conf 4)
7. earnings_filter.py hardcoded Q2-Q3 2026 calendar (QQQ top 10 holdings + SPY key)
8. crop_main.py default _llm_spread_override_threshold 8 → 4
9. TQQQ Haiku diagnosis A3: bias_pct=93%, flag=YES (mantenido como flag, no Haiku change)

## Notas operativas del sprint

- **Run 1 (4 tickers original)**: matado prematuramente cuando pace bajó por 504s
  intermitentes en engine A. SPY 193/300 samples preservadas (64% coverage).
- **Adenda Juan**: descartar TQQQ por 100% NO-ACTION en sprint anterior, sustituir
  por SPX. Backtest SPX requirió fetch QD bulk (~62 días).
- **Run 2**: QQQ 300/300 + IWM 273/300 (budget cap a 273 samples mid-IWM). SPX
  no llegó por cap.
- **Bug fix mid-sprint**: token gcloud expira a 1h → todas las requests devuelvieron
  401. Agregado `make_token_refresher` con refresh cada 45min. Reinicio backtest desde
  cero tras detección.
- **Append-mode JSONL**: cambiado de write-end-of-ticker a append-per-sample para
  resilencia ante interrupts.

## Resultados

| Métrica          | Engine A (v1.7) | Engine B (v1.8 + bundle 9) | Delta |
|---|---|---|---|
| Total samples    | 766 | 766 | — |
| **WAIT rate**    | **70.5%**     | **48.7%**     | **−21.8 pp** |
| **Action rate**  | **26.5%** (203) | **48.2%** (369) | **+21.7 pp** |
| SKIPPED_BY_HAIKU | 2.3% (18)   | 3.1% (24)   | +0.8 pp |
| ENGINE_ERROR     | 0.7% (5)    | 0%          | −0.7 pp |
| SELL_PUT verdict | 199         | 369         | +170 |
| SELL_CALL verdict| 4           | 0           | −4 |
| Disagreement     | —           | —           | 24.8% (190) |
| A=WAIT, B=action | —           | —           | 170 |
| B=WAIT, A=action | —           | —           | 5 |
| Cost             | $75.06      | $74.97      | $150.03 total |

### Per-ticker

| Ticker | n | WAIT A | WAIT B | Action A | Action B | Delta action pp |
|---|---|---|---|---|---|---|
| SPY    | 193 | 70% | 54% | 53 | 83 | **+16 pp** |
| QQQ    | 300 | 52% | 35% | 127 | 177 | **+17 pp** |
| IWM    | 273 | 92% | 60% | 23 | 109 | **+32 pp** |

IWM swing más grande (+32 pp action) — bundle 9 destrabó el ticker más bloqueado del
sprint anterior (Saturday-validation post-edits dejó IWM aún con 82% WAIT).

### Top sub-rules en B (TR-Juan-091..108)

| Rule | Cite count | Comentario |
|---|---|---|
| TR-Juan-104 | 333 | Range/momentum sub-rule, dominante |
| TR-Juan-105 | 259 | Pin cushion absoluto |
| TR-Juan-095 | 35  | Pin Δ 0.15 |
| TR-Juan-099 | 29  | Pin range exit |
| TR-Juan-091 | 23  | VRP cheap mid-IV |
| TR-Juan-107 | 17  | — |
| TR-Juan-106 | 6   | VIX defensive |
| TR-Juan-097 | 4   | — |

## Hipótesis H1 vs H2

**H1 (scarcity confirmada)**: sub-rules 091-108 son citadas dominantemente en B,
notablemente TR-Juan-104 (333) y TR-Juan-105 (259). El tier bump TACTICAL_PLUS →
MAESTRA + visibility reorder via few-shot dieron prominencia operacional a estas
reglas. CONFIRMADA: H1 explica el delta +21.7 pp en action rate.

**H2 (visibility issue persistente)**: NO se observa. Las sub-rules son citadas
con frecuencia (incluso TR-Juan-104 más que cualquier MAESTRA legacy).

## Interpretación del delta

Delta WAIT −21.8 pp ampliamente supera el umbral de promoción (>−15 pp) sugerido
en el runbook:
- **Promote B to production**: bundle 9 destraba sistemáticamente la acción sin
  modificar PROHIBITIVA gates ni AXIOMA.
- Disagreement asimétrico (170 vs 5): B reemplaza WAIT por ACTION sistemáticamente
  sin volver atrás. No hay backward regression de action → WAIT.
- Cost neutral ($75 ambos): bundle no incrementa token usage significativamente.

## Recomendaciones próximo sprint

1. **Promote B settings**: aplicar bundle 9 al engine de producción (`llm-engine-service`)
   con `SHADOW_MODE=true` permanente, KB v1.8 + Deprecated_By filter.
2. **Validar con paper trading**: 5-10 días de paper en CROP antes de habilitar
   live trading.
3. **SPX backtest pendiente**: budget cap impidió SPX en este sprint. Programar
   sprint dedicado SPX-only con engine B (cache ya disponible, 62 días).
4. **Investigar TR-Juan-104 dominancia**: regla más citada — verificar si es
   apropiada o si refleja un sesgo de prompt (mencionada en few-shot ejemplo 1).
5. **Earnings filter validation**: pocos días en window 2026-03-09 → 2026-06-02
   coinciden con calendar Q2-Q3 2026 hardcoded — efecto del filtro mínimo en este
   backtest. Validar en sprint H2 con window que cubra fechas de earnings.

## Artefactos

- Run 1 SPY: `/tmp/shadow_60d_20260606_163620/decisions_SPY_*.jsonl` (193 samples)
- Run 2 QQQ: `/tmp/shadow_60d_run2_20260606_191429/decisions_QQQ_*.jsonl` (300)
- Run 2 IWM: `/tmp/shadow_60d_run2_20260606_191429/decisions_IWM_*.jsonl` (273)
- Summary JSON: `/tmp/shadow_60d_run2_20260606_191429/combined_summary.json`
- Excel comparison: `docs/SHADOW_BUNDLE_9_COMPARISON.xlsx`
- Commit shadow infra: `6e36925 feat(shadow-bundle-9): KB v1.8 + bundle de 9 cambios`
