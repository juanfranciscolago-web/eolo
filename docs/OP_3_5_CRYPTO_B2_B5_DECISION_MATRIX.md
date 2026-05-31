# OP-3.5 — Crypto Bot B2-B5 Decision Matrix

**Fecha:** 2026-05-31 (Domingo cierre)
**Backlog ref:** OP-3.5 MEDIO — "Crypto bot auditoría operacional"
**Source doc previo:** `CRYPTO_HALLAZGO_28_MAY.md` (workspace root)
**Verdict:** **MERGE TODAS las 4 branches a main. Riesgo bajo (TESTNET), beneficio alto.**

---

## TL;DR

| Branch | Commit | Tema | Risk merge | Esfuerzo | Decisión |
|---|---|---|---|---|---|
| **B2** | `e3035b2` | Whitelist rsi_sma200 ampliada + fix Haiku cost (3.75× overcount) | 🟢 BAJO | 15 min | **MERGE** |
| **B3** | `69a1247` | SL/TP automático con safety nets | 🟡 MEDIO | 1h validación | **MERGE** |
| **B4** | `a5ad258` | Daily loss cap enforcement | 🟡 MEDIO | 1h validación | **MERGE** |
| **B5** | `90a4ef3` | Apagar AutoRouter dead loop | 🟢 BAJO | 15 min | **MERGE** |

**Plan ejecutable:** cherry-pick los 4 a una nueva branch `feat/crypto-consolidate-B2-B5` → PR draft → review mid-week → merge → deploy crypto bot (TESTNET) → validate logs por 24h.

**Esfuerzo total:** 3-4h spread over 1-2 días.

**Riesgo financiero:** 0 (TESTNET).

---

## Contexto del crypto bot

| Item | Estado |
|---|---|
| Servicio | `eolo-bot-crypto` (southamerica-east1, rev 00054-kcc) |
| Mode | TESTNET (0 riesgo financiero) |
| Status overnight | running, healthy, 0 restarts, 18h uptime |
| Actividad | ALTA (1000+ log lines / 8h) |
| Trades hoy | 0 |
| **Trades últimos 30 días** | **0** ← problema operacional |
| Versión main | code de **abril 28 + mayo 7** (whitelist `{BTC, ETH}` solo) |
| Versión orphan B2-B5 | mayo 19 (whitelist ampliada + safety nets) |

**Bot está "vivo" pero no opera.** 9 strategies enabled, 8 sin emitir signals (boot-only). Solo `rsi_sma200` emite (500+/24h) pero ConsensusFilter MIN_STRATEGY_CONSENSUS=2 lo bloquea.

**Causa root de 0 trades:** combinación de versión vieja (whitelist limitada) + ConsensusFilter restrictivo + posible bug en multi-TF dispatch de las otras 8 strategies.

---

## Análisis por commit

### B2 — `e3035b2` (19-may) — Whitelist + Haiku cost fix

**Archivos:** `eolo-crypto/claude_bot_crypto.py`, `eolo-crypto/settings.py`

**Cambios:**
1. `STRATEGY_SYMBOL_WHITELIST['rsi_sma200']` ampliado de `{BTC, ETH}` a `{BTC, ETH, SOL, DOGE, BNB, XRP}`
2. Fix Haiku cost estimator — usaba precios Sonnet → 3.75× overcount

**Riesgo merge:** 🟢 BAJO
- Whitelist amplía pero NO cambia el flow (rsi_sma200 ya emite para esos tickers en logs)
- Cost estimator fix: solo logging, no afecta decisiones

**Beneficio merge:**
- Logs de cost reales (no 3.75× inflados) → métricas honestas
- Whitelist consistente con strategies que ya emiten (limpia inconsistencia "rsi_sma200 emite para SOL pero whitelist dice no")

**Veredicto:** **MERGE inmediato.**

### B3 — `69a1247` (19-may) — SL/TP automático

**Archivos:** `eolo-crypto/eolo_crypto_main.py`, `eolo-crypto/trading/binance_executor.py`

**Cambios:**
- DEFAULT_STOP_LOSS_PCT y DEFAULT_TAKE_PROFIT_PCT estaban declarados pero ningún caller los consumía
- Agrega campos sl_pct/tp_pct por posición
- Trigger automático sl_trigger/tp_trigger

**Riesgo merge:** 🟡 MEDIO
- Modifica execution path
- Si los thresholds están mal configurados, podría cerrar posiciones prematuramente
- En TESTNET el riesgo financiero es 0, pero el behavior change requires validation

**Beneficio merge:**
- Safety net real (hoy posiciones dependen 100% de manual close)
- Aplicable a LIVE cuando se migre de TESTNET

**Veredicto:** **MERGE + validar 24h en TESTNET antes de mover a LIVE.**

### B4 — `a5ad258` (19-may) — Daily loss cap enforcement

**Archivos:** `eolo-crypto/eolo_crypto_main.py`, `eolo-crypto/firestore_state.py`, `eolo-crypto/trading/binance_executor.py`

**Cambios:**
- DAILY_LOSS_CAP_PCT estaba declarado pero no enforced
- `firestore_state.set_daily_pnl` existía pero nunca se llamaba
- `daily_pnl_usdt` quedaba en 0.0 → bot podía sangrar sin freno

**Riesgo merge:** 🟡 MEDIO
- Cambio en state machine (daily_pnl mantenido en Firestore)
- Hard stop si pierde >threshold% en el día
- Validar threshold default razonable (probablemente 5-10%)

**Beneficio merge:**
- Hard safety stop (crítico para LIVE)
- Sin esto, un día malo podría liquidar capital
- TESTNET 0 financial risk pero el bug existe igual

**Veredicto:** **MERGE + validar threshold + simular trigger en TESTNET.**

### B5 — `90a4ef3` (19-may) — AutoRouter dead loop cleanup

**Archivos:** `eolo-crypto/eolo_crypto_main.py`

**Cambios:**
- `_auto_router_crypto` era dead loop (update() con save_firestore=False, return _new_t descartado, ningún consumer aplicaba toggles)
- Apaga el loop muerto

**Riesgo merge:** 🟢 BAJO
- Solo remueve código que NO se usa
- Si hay regression es porque alguien empezó a depender del dead code (improbable)

**Beneficio merge:**
- Limpieza arquitectónica
- Reduce log noise (loop dead loggeaba a debug pero igual ocupaba CPU)

**Veredicto:** **MERGE inmediato.**

---

## Plan de ejecución

### Fase 1 — Cherry-pick + PR (30 min)

```bash
cd ~/PycharmProjects/eolo
git checkout main
git pull origin main
git checkout -b feat/crypto-consolidate-B2-B5

# Cherry-pick en orden cronológico
git cherry-pick e3035b2  # B2
git cherry-pick 69a1247  # B3
git cherry-pick a5ad258  # B4
git cherry-pick 90a4ef3  # B5

# Si conflicts (improbable, los 4 commits son secuenciales en el mismo bot):
# - Resolver manualmente
# - git cherry-pick --continue

git log --oneline -5
# Esperás: 4 commits B2-B5 + base main

git push -u origin feat/crypto-consolidate-B2-B5

gh pr create --base main --head feat/crypto-consolidate-B2-B5 --draft \
  --title "feat(crypto): consolidar B2-B5 orphan commits" \
  --body "Consolida 4 commits crypto bot del 19-may que quedaron huérfanos.

## Cambios
- B2 (e3035b2): whitelist rsi_sma200 ampliada + fix Haiku cost (3.75× overcount)
- B3 (69a1247): SL/TP automático con safety nets
- B4 (a5ad258): daily loss cap enforcement
- B5 (90a4ef3): apagar AutoRouter dead loop

## Audit completo
docs/OP_3_5_CRYPTO_B2_B5_DECISION_MATRIX.md (commit bea2c0a)

## Risk
Bot está en TESTNET, 0 riesgo financiero. Cambios validados en source branches del 19-may.

## Validation post-deploy
- 24h en TESTNET monitoreando: trades, SL/TP triggers, daily_pnl tracking
- Si todo verde: considerar migration a LIVE (decisión separada)
"
```

### Fase 2 — Review + merge (15 min, mid-week)

Review propio del PR (cambios YA validados en source, low risk).

```bash
gh pr ready <PR_NUM>
gh pr merge <PR_NUM> --merge --delete-branch
```

### Fase 3 — Deploy crypto bot (5 min)

```bash
cd ~/PycharmProjects/eolo
gcloud builds submit --config eolo-crypto/cloudbuild.yaml . --project=eolo-schwab-agent
```

### Fase 4 — Validación 24h (passive)

Health check existente (`eolo-crypto-health-check` scheduled task cada 12h) monitorea. Si trigger errors, revisar logs.

Manual checks ocasionales:

```bash
# Logs últimos 30 min
gcloud logging read 'resource.labels.service_name="eolo-bot-crypto"' \
  --project=eolo-schwab-agent --limit=20 --freshness=30m

# Trade count
gcloud logging read 'resource.labels.service_name="eolo-bot-crypto" AND textPayload:"OPEN"' \
  --project=eolo-schwab-agent --limit=10 --freshness=24h

# SL/TP triggers
gcloud logging read 'resource.labels.service_name="eolo-bot-crypto" AND (textPayload:"sl_trigger" OR textPayload:"tp_trigger")' \
  --project=eolo-schwab-agent --limit=10 --freshness=24h

# Daily loss cap
gcloud logging read 'resource.labels.service_name="eolo-bot-crypto" AND textPayload:"daily_pnl"' \
  --project=eolo-schwab-agent --limit=5 --freshness=24h
```

---

## Riesgos identificados

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Cherry-pick conflicts (B3+B4 modifican mismos archivos) | 🟡 Bajo | Resolver manualmente; probable conflicto en `eolo_crypto_main.py` |
| SL/TP threshold default desbalanceado | 🟡 Bajo | Validar en TESTNET 24h antes de cualquier LIVE |
| Daily loss cap demasiado restrictivo | 🟡 Bajo | Default razonable + override Firestore disponible |
| Trades aún en 0 post-merge | 🔴 ALTO si esperamos trades | Ortogonal a B2-B5 — causa root es ConsensusFilter + multi-TF bug, no es lo que B2-B5 fixean |

**Importante:** B2-B5 NO arreglan "0 trades en 30 días" — eso es un bug separado (ConsensusFilter MIN_STRATEGY_CONSENSUS=2 + posible bug multi-TF dispatch). Los B2-B5 son safety nets + cleanup, no enable-trading.

**Acción post-merge:** sigue con investigación separada del bug "8 strategies no emiten signals". Tracking item nuevo:

```
OP-3.5.B — Investigar por qué 8/9 strategies crypto bot no emiten signals
  Hipótesis: multi-TF gate falla, bug en adapter, conditions muy estrictas régimen calmo
  Esfuerzo: 2-3h debug session
  Pre-req: B2-B5 mergeados (clean baseline)
```

---

## Decisión final

**MERGE TODAS las 4 branches** vía PR consolidado `feat/crypto-consolidate-B2-B5`.

**Cuándo:** mid-week (mar/mie 3-4 jun), después de validar deploy crop bot lunes (no agregar variables el mismo día).

**Owner del merge:** Juan, después de review propio del PR.

**Post-merge:** monitorear 24h en TESTNET. Si todo verde → considerar Sprint dedicado para investigar OP-3.5.B (8 strategies sin signals).

---

## Resumen ejecutivo

| Métrica | Valor |
|---|---|
| Branches huérfanas | 4 (B2/B3/B4/B5) |
| Edad | 12 días (commits 19-may) |
| Riesgo merge | Bajo (TESTNET + cambios validados en source) |
| Esfuerzo total | 3-4h spread over 1-2 días |
| Beneficio | Safety nets + bug fix Haiku cost + cleanup |
| Riesgo financiero | 0 (TESTNET) |
| Bloqueante validación lunes Crop | NO (proyecto separado) |
| Decisión | **MERGE ALL 4** |
| Cuándo | Mid-week post deploy Crop |

**OP-3.5 backlog item:** REESCRIBIR a:

```
OP-3.5 — Crypto bot B2-B5 consolidación
  Plan: cherry-pick 4 commits a main, PR consolidado, deploy TESTNET, 24h monitoring
  Esfuerzo: 3-4h spread mid-week
  Audit: docs/OP_3_5_CRYPTO_B2_B5_DECISION_MATRIX.md
  Owner: Juan post-review
  Decisión: MERGE ALL
```

---

**Audit completo. Backlog item OP-3.5 ready for execution.**
