# Finding: TR-Juan-042 AXIOMA bloquea LLM scope expansion a QQQ/IWM

**Descubierto:** 2026-06-02 ~09:48 ET durante validation #77 runtime.
**Severidad:** Strategic ALTA. Cambia priorities Sprint UP-1.4.
**Bloquea fix:** KB v1.3 (Sprint UP-1.4, target ~8-jun).

## El problema

OPS-1 (PR #32) + OPS-1b (PR #33) expandieron `LLM_ENABLED_TICKERS = {SPY, QQQ, IWM}`
con allowlist en `crop_main.py` upstream + `integration.py` Rule 0.

Arquitectónicamente el LLM recibe snapshots para los 3 tickers y emite verdicts.
En la práctica, la regla `TR-Juan-042` (tier AXIOMA, inviolable) del KB v1.2
afirma "sistema Juan diseñado exclusivamente para SPY". El LLM respeta el AXIOMA
y devuelve `verdict=WAIT` para QQQ/IWM citando explícitamente TR-Juan-042.

## Evidencia en logs (validation 2-jun)

```
13:48:21 [llm] IWM verdict=WAIT conf=4 path=haiku_pass
  reason=Setup rechazado por múltiples factores críticos:
  (1) Ticker IWM no es SPY - sistema Juan disenado exclusivamente para SPY
  con correlacion inversa VIX documentada [TR-Juan-042]...
```

3 de 3 verdicts non-SPY del día (QQQ + IWM) bloqueados por esta regla.

## Net effect

- LLM Risk Arbiter solo es útil para SPY hoy
- OPS-1/1b/OPS-3 scope expansion = arquitectura, no funcionalidad
- Quant Data fetches para QQQ/IWM corren pero output ignorado en práctica
- Verdicts QQQ/IWM siempre WAIT, sin edge agregado

## Opciones de fix

A) **Esperar Sprint UP-1.4** (KB v1.3 con reglas QQQ/IWM specific + TR-Juan-042 update). Pros: workflow correcto, KB review riguroso. Cons: ~1 sem delay.

B) **Quick hotfix TR-Juan-042**: cambiar tier=AXIOMA a tier=MAESTRA con scope=SPY. KB v1.2.1 minor bump. Pros: habilita LLM para QQQ/IWM esta semana. Cons: KB content change sin audit completo; bypasea el process del AXIOMA tier.

C) **Override hardcoded en prompt**: instruir LLM a evaluar TR-Juan-042 SOLO para SPY. No KB change. Cons: hack, no escalable, ofende design del KB.

**Recomendación: A** (esperar Sprint UP-1.4) con priorización ALTA esa semana. El AXIOMA tier existe por una razón (inviolabilidad); cambiar sin audit completo es riesgoso.

## Tasks siguientes

- Capturar como input PRINCIPAL del Sprint UP-1.4 audit
- Pre-stage borrador TR-Juan-042 v2 con tier=MAESTRA + scope=SPY + nuevas reglas QQQ/IWM-specific
- Considerar log estructurado de verdicts WAIT-por-TR-Juan-042 para audit

## Cross-refs

- docs/RECONCILIATION_v2_1_vs_06_01.md Sprint UP-1.4 (W2 del roadmap v2.2)
- docs/sprint_18_tactical_decision_matrix.md (plan original, pending update)
- Master 06-01 sec 10.1 LLM scope description
- Validation evidence: bot CROP logs 2026-06-02 13:48 UTC (09:48 ET)
