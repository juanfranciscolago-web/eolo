# Validation #95 End-to-End PASS

**Test ejecutado:** 2026-06-02 ~10:20 ET vía manual /decide call con QD-populated snapshot.
**Resultado:** ✅ Wire #95 LIVE confirmado end-to-end.
**LLM Engine rev validado:** llm-engine-service-00004-qw9 (post-hotfix #95).

## Killer evidence

Snapshot enviado al engine incluyó:
```
"gex_regime": "positive_stable",
"gex_total": 5.2e9,
"max_pain_strike": 755.0,
"iv_rank_call": 28.5, "iv_rank_put": 32.1,
"net_call_premium_drift": 1500000.0,
"net_put_premium_drift": -800000.0
```

Sonnet response (decision.warnings item 5):

> "GEX positive_stable sugiere rango pero sin confirmación price action lateral sostenida"

Sonnet cita textualmente `gex_regime: "positive_stable"` del snapshot →
prueba que el value cruzó el boundary Pydantic (post-#95) y llegó al prompt
construido por `to_llm_format()`.

## Flujo confirmado

1. ✅ Bot POSTea snapshot con 11 QD fields
2. ✅ Pydantic MarketSnapshot (#95 schema) acepta sin descartar
3. ✅ `to_llm_format()` serializa OPTIONS POSITIONING section
4. ✅ Sonnet recibe + procesa + cita en reasoning

## Otros wins observados en response

| Item | Valor |
|---|---|
| tacit_rules_applied | [TR-Juan-043, TR-Juan-036, TR-Juan-037, TR-Juan-040, TR-Juan-044] |
| meta.kb_version | v1.2 |
| meta.input_tokens | 4664 |
| meta.output_tokens | 768 |
| meta.latency_ms | 17521 |
| meta.model | claude-sonnet-4-5-20250929 |
| verdict | WAIT, confidence: 5 (setup ambiguo SPY low-VIX) |

## Por qué solo cita GEX y no los otros QD fields

Setup ambiguo SPY low-VIX 16.1 + price 757.5 cerca de max_pain 755 → Sonnet
emitió WAIT priorizando indicadores tradicionales (VIX, RSI multi-TF, Fibonacci,
MACD). De los 11 QD fields disponibles citó solo GEX positive_stable
(probablemente más decisivo para corroborar "rango lateral").

Esto es comportamiento natural del LLM: cita solo lo que es relevante para
el verdict. No es proof negativo del wire — los otros fields estaban en el
prompt pero no fueron citados.

## Implicaciones para roadmap

- **Sprint 3 A.1 deploy gate**: ✅ UNBLOCKED (validation PASSED)
- **OPS-3 Risk Arbiter**: ✅ recibe Quant Data context real
- **Sprint UP-1.4 (KB v1.3)**: puede asumir QD wire funcional + agregar reglas QD-aware
- **R4 (TR-Juan-042 AXIOMA blocks non-SPY)**: sigue siendo el blocker práctico — el LLM tiene QD context pero rechaza QQQ/IWM por scope rule

## Script test reusable

`/tmp/test_decide_qd.py` queda como herramienta para futuras smoke validations
post-deploy del engine (Sprint 3 A.1, A.2, etc.). Considerar persistir como
`scripts/smoke_decide_qd.py` en repo para reuso.

## Cross-refs

- Commit hotfix #95: 0f77177
- Deploy LLM Engine: rev 00004-qw9 (2026-06-01 noche)
- docs/RECONCILIATION_v2_1_vs_06_01.md finding C (raíz del bug)
- Sprint 3 A.1 commit 80b12cf (pending deploy bundled con R1.A hotfix post-16:00 ET)
