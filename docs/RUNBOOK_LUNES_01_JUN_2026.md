# Runbook Lunes 1-Junio-2026 — Deploy PR #24 + #27 + #28 + Validación Sprint 21

**Generado:** Domingo 31-mayo 2026 (mercados cerrados)
**Updated:** Domingo 31-mayo 2026 noche — agregado PR #28 (Sprint 21 fix Haiku cost) tras audit
**Estado al cierre del domingo:**
- Bot CROP: rev `eolo-bot-crop-00080-zbb` (us-east1) — Sprint 5-17 LIVE
- LLM Engine: rev `llm-engine-service-00003-sqn` (us-central1) — Sprint 21 LIVE desde 10:06 ET hoy
- PRs draft listos para merge: **#24** (Sprint 20), **#25** (UP-1.2), **#26** (UP-1.3), **#27** (UP-2.2), **#28** (Sprint 21 fix Haiku tokens)

---

## TL;DR — Plan lunes

| Hora ART | Acción | Duración |
|---|---|---|
| 09:00-09:15 | Verificar bot + LLM Engine estables overnight | 15 min |
| 09:15-09:35 | Merge PRs #28 → #27 → #24 a main (orden importa) | 20 min |
| 09:35-09:55 | Deploy bot CROP con PR #24 + #27 + #28 combinados | 20 min |
| 09:55-10:30 | Smoke checks + monitor logs | 35 min |
| 10:30 ART | Market open (09:30 ET) | — |
| 10:30-10:45 | Validar primer ciclo LLM + tokens + cost + dashboard | 15 min |
| 10:45+ | Free time / monitor casual | — |

**Total work:** ~95 min de atención activa antes de market open.

**No merge mid-week (todavía):** PR #25 + PR #26 quedan draft. Razón: ambos son foundational para UP-1.2 fase 2 / Sprint 18, mejor mergearlos después de validar 1 semana de prod data del Sprint 21.

**PR #28 (Sprint 21 fix Haiku cost) PRIORIDAD ALTA:** sin este fix, validation criterion #5 falla si primer ciclo es `haiku_skip` (cost = $0 pese a llamada Haiku). Con #28, validation pasa con cost real desde el primer ciclo. Ver `docs/SPRINT_21_WIRING_AUDIT_31_MAY.md` para detalle del bug.

---

## 09:00 ART — Verificación pre-market

### Checklist 1 — Bot CROP healthy overnight

```bash
gcloud run services describe eolo-bot-crop \
  --region=us-east1 \
  --project=eolo-schwab-agent \
  --format='value(status.url,status.traffic[0].revisionName,status.conditions[0].status)'
```

**Esperás:** `eolo-bot-crop-00080-zbb` con `True`. Si no, **STOP** y diagnosticar antes de cualquier deploy.

### Checklist 2 — LLM Engine healthy

```bash
gcloud run services describe llm-engine-service \
  --region=us-central1 \
  --project=eolo-schwab-agent \
  --format='value(status.url,status.traffic[0].revisionName)'
```

**Esperás:** `llm-engine-service-00003-sqn` @ 100% tráfico (la del deploy de domingo).

### Checklist 3 — /health endpoint del LLM Engine

```bash
LLM_URL=$(gcloud run services describe llm-engine-service \
  --region=us-central1 --project=eolo-schwab-agent --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOKEN" "${LLM_URL}/health" | python3 -m json.tool
```

**Esperás:**
```json
{
  "status": "ok",
  "model": "claude-sonnet-4-5-20250929",
  "kb_loaded": true,
  "paper_trading_only": true
}
```

Si `paper_trading_only != true` → **STOP**, no continuar deploy.

### Checklist 4 — Logs sin errores 5xx overnight

```bash
gcloud logging read 'resource.labels.service_name="eolo-bot-crop" AND severity>=ERROR' \
  --project=eolo-schwab-agent \
  --limit=20 \
  --freshness=12h \
  --format='value(timestamp,textPayload)'
```

**Esperás:** Cero entries o solo warnings conocidos (timezone normalization, polling cache misses esperados).

---

## 09:15 ART — Merge PRs #28 → #27 → #24

### Decisión de merge order

**ORDEN:** mergear en orden inverso de riesgo. Frontend puro primero, fix de bug segundo, cleanup de código tercero. Si algún paso falla, los previos siguen aplicables al deploy.

#### 1° Merge: PR #28 (Sprint 21 fix Haiku cost)

```bash
cd ~/PycharmProjects/eolo
gh pr ready 28
gh pr merge 28 --merge --delete-branch
```

Razón: backward compatible (kwargs default 0), modifica solo 2 archivos + agrega 1 test. Es FIX puro de bug — no agrega features.

#### 2° Merge: PR #27 (UP-2.2 dashboard frontend)

```bash
gh pr ready 27
gh pr merge 27 --merge --delete-branch
```

Razón: 100% frontend, cero riesgo backend. Solo `dashboard-crop.html`. Sin conflicts con #28.

#### 3° Merge: PR #24 (Sprint 20 cleanup datetime + dead code)

```bash
gh pr ready 24
gh pr merge 24 --merge --delete-branch
```

Razón: modifica `crop_main.py` + `execution/options_trader.py`. POTENTIAL CONFLICT con PR #28 (también toca crop_main.py). Si gh pr merge falla por conflict:

```bash
gh pr view 24 --json mergeStateStatus
# Si dirty → conflict
git checkout fix/sprint20-cleanup-datetime-deadcode
git pull origin fix/sprint20-cleanup-datetime-deadcode
git merge main  # rebase contra main que ya tiene #28
# Resolver conflicts en crop_main.py manualmente
git add eolo-crop/crop_main.py
git commit -m "merge main into sprint20 cleanup — resolved conflict with #28"
git push origin fix/sprint20-cleanup-datetime-deadcode
gh pr merge 24 --merge --delete-branch
```

(NO usar `--squash` en ninguno para preservar commit messages.)

### Verificar main local actualizado

```bash
git checkout main
git pull origin main
git log -5 --oneline
```

**Esperás:** Top 5 commits = (1) merge #24, (2) merge #27, (3) merge #28, (4) c93efc0 (merge #23 Sprint 21), (5) older.

---

## 09:30 ART — Deploy bot CROP

### Deploy comando único

```bash
cd ~/PycharmProjects/eolo
gcloud builds submit \
  --config eolo-crop/cloudbuild-crop.yaml . \
  --project eolo-schwab-agent
```

**Duración esperada:** 3-5 min (build Docker + push + deploy Cloud Run).

**Output a observar:**
- `STATUS: SUCCESS` al final
- Nueva revisión `eolo-bot-crop-000XX-YYY` listada en gcloud

### Verificar revisión activa

```bash
gcloud run revisions list \
  --service=eolo-bot-crop \
  --region=us-east1 \
  --project=eolo-schwab-agent \
  --limit=3 \
  --format='table(name,creation_timestamp,status.conditions[0].status,traffic.percent)'
```

**Esperás:** Nueva rev al top, `True`, 100% tráfico. Anteriores en 0%.

### Anotar nueva revision name

Capturar el nombre exacto para el smoke + posible rollback:

```bash
NEW_REV=$(gcloud run services describe eolo-bot-crop \
  --region=us-east1 --project=eolo-schwab-agent \
  --format='value(status.traffic[0].revisionName)')
echo "Nueva revision: $NEW_REV"
echo "Rollback target (Sprint 17 estable): eolo-bot-crop-00080-zbb"
```

---

## 09:50 ART — Smoke checks post-deploy

### Smoke 1 — Boot limpio sin errors

```bash
gcloud logging read "resource.labels.service_name=\"eolo-bot-crop\" AND resource.labels.revision_name=\"$NEW_REV\" AND severity>=WARNING" \
  --project=eolo-schwab-agent \
  --limit=20 \
  --freshness=10m \
  --format='value(timestamp,textPayload)'
```

**Esperás:** Cero entries (boot limpio).

### Smoke 2 — Sprint 20 changes activos

Sprint 20 cambió `datetime.now()` por `datetime.now(ZoneInfo("America/New_York"))` en 3 lugares. Buscar evidencia en logs:

```bash
gcloud logging read "resource.labels.service_name=\"eolo-bot-crop\" AND resource.labels.revision_name=\"$NEW_REV\" AND textPayload:\"timestamp\"" \
  --project=eolo-schwab-agent \
  --limit=5 \
  --freshness=10m
```

**Esperás:** timestamps con `-04:00` o `-05:00` (ET offset según DST), NO `+00:00` UTC.

### Smoke 3 — Dashboard UP-2.2 renderiza

```bash
BOT_URL=$(gcloud run services describe eolo-bot-crop \
  --region=us-east1 --project=eolo-schwab-agent \
  --format='value(status.url)')
echo "Dashboard URL: $BOT_URL"
```

Abrir el URL en browser. Verificar:
- Carga sin error
- Section nueva "🧠 LLM Metrics" visible (entre Paper Trades y Risk Management)
- 4 cards en "—" (correcto, pre-market = sin calls)
- 3 charts vacíos (correcto)
- Footer "Última reset: —" (correcto, sin actividad LLM aún)

Si la section no aparece → cache del navegador, hacer hard refresh (Cmd+Shift+R).

### Smoke 4 — /api/state endpoint funciona

```bash
curl -s "$BOT_URL/api/state" | python3 -c "import sys,json; d=json.load(sys.stdin); print('llm_metrics keys:', list(d.get('stats',{}).get('llm_metrics',{}).keys()))"
```

**Esperás:**
```
llm_metrics keys: ['total_calls', 'calls_per_hour', 'verdicts', 'pre_filter_skips', 'decision_sources', 'errors', 'latency_ms', 'cost_estimate_usd', 'last_reset_at', 'elapsed_hours']
```

Si falta `cost_estimate_usd` → Sprint 11 no inicializó bien, investigar.

---

## 10:30 ART — Market open — Validación Sprint 21 + UP-2.2

### Esperar primer ciclo LLM

El bot tiene `entry_window` configurada (Sprint 15 lo hizo configurable). El primer Sonnet consult debería ocurrir entre 09:30-09:35 ET (10:30-10:35 ART) si el pre-filter pasa.

### Validar Sprint 21 — tokens en logs del LLM Engine

```bash
gcloud logging read 'resource.labels.service_name="llm-engine-service" AND textPayload:"LLM response"' \
  --project=eolo-schwab-agent \
  --limit=5 \
  --freshness=15m \
  --format='value(timestamp,textPayload)'
```

**Esperás:** Lines tipo:
```
[req-abc123] LLM response in 1850ms (in=2100 out=320)
```

Si `in=0 out=0` → Sprint 21 wiring rompió algo. Investigar (probable: response.usage es None en algunas respuestas).

### Validar Sprint 21 — cost en /api/state (con PR #28 fix aplicado)

```bash
curl -s "$BOT_URL/api/state" | python3 -c "import sys,json; d=json.load(sys.stdin); m=d.get('stats',{}).get('llm_metrics',{}); print('total_calls:', m.get('total_calls'), 'cost:', m.get('cost_estimate_usd'))"
```

**Esperás (con PR #28 mergeado y deployado):**

Caso A — `haiku_pass` (Haiku duda, Sonnet decide):
```
total_calls: 1  cost: 0.0124
```
(Sonnet ~$0.011 + Haiku pre-filter ~$0.0013 = $0.0124)

Caso B — `haiku_skip` (Haiku rechaza con alta confidence, no se llama Sonnet):
```
total_calls: 1  cost: 0.0013
```
(Solo Haiku pre-filter ~$0.0013. **Antes del fix PR #28, este caso reportaba $0.0000 que era un bug.**)

Caso C — `haiku_low_conf` (Haiku baja confianza, Sonnet decide):
```
total_calls: 1  cost: 0.0124
```
(Mismo que caso A)

**Si total_calls > 0 pero cost == 0:**
- PR #28 NO se mergeó/deployó → fix no aplicado, esperado pre-fix
- O PR #28 mergeó pero deploy falló → revisar gcloud builds list
- O bug nuevo distinto → investigar `llm_gate/metrics.py:record_call` y `crop_main.py:1346`

### Validar UP-2.2 — Dashboard muestra cost

Refresh el dashboard en browser. Verificar:
- Card "TOTAL CALLS" muestra `1` (o N si hubo más)
- Card "COST USD" muestra `$0.0072` (o similar > 0)
- Card "CALLS / HOUR" muestra `X.X`
- Card "ERROR RATE" muestra `0.0%`
- Donut "Verdict distribution" tiene 1+ slice coloreado (verde si SELL_PUT, amarillo si WAIT)
- Bar "Latency p50/p95/p99" muestra valores en ms
- Pie "Decision sources" tiene 1+ slice (HAIKU_PASS o SONNET_CONSULT)
- Tables "Pre-filter skips" y "Errors" pueden estar vacías o con valores
- Footer "Última reset: 2026-06-01 09:30:00 ET · elapsed: 0.0h" (o lo que sea desde el reset)

**Success criteria:** los 4 cards muestran valores numéricos coherentes + al menos 1 chart con data.

---

## Rollback procedures

### Rollback bot CROP

Si algo se rompe post-deploy, restaurar a la rev pre-deploy:

```bash
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 \
  --project=eolo-schwab-agent \
  --to-revisions=eolo-bot-crop-00080-zbb=100
```

Confirmar:
```bash
gcloud run services describe eolo-bot-crop \
  --region=us-east1 --project=eolo-schwab-agent \
  --format='value(status.traffic[0].revisionName)'
```

**Esperás:** `eolo-bot-crop-00080-zbb` activa.

### Rollback LLM Engine (si Sprint 21 trajo regression)

```bash
gcloud run services update-traffic llm-engine-service \
  --region=us-central1 \
  --project=eolo-schwab-agent \
  --to-revisions=llm-engine-service-00001-mk8=100
```

(00001-mk8 es pre-Sprint-21, Bloque 4 v0.2 estable.)

### Revert merge en GitHub

Si necesitás revertir un PR mergeado:

```bash
cd ~/PycharmProjects/eolo
git checkout main
git pull origin main
git revert HEAD --no-edit  # revertir el merge más reciente
git push origin main
# Re-deploy con cloudbuild submit
```

---

## Criterios de éxito del lunes

| Criterio | OK si |
|---|---|
| 1. Bot CROP nuevo deploy live | Nueva rev al 100% tráfico, /api/state responde 200 |
| 2. Sprint 20 (datetime cleanup) activo | Logs sin timestamps UTC, AUTO_CLOSE_HOUR/MINUTE removed (grep en código) |
| 3. UP-2.2 dashboard renderiza | Section "🧠 LLM Metrics" visible, cards pueblan con primer ciclo LLM |
| 4. Sprint 21 LLM Engine token tracking | Logs muestran `(in=NNNN out=MMM)`, in>0 y out>0 |
| 5. Sprint 21 cost en bot CROP | `/api/state.stats.llm_metrics.cost_estimate_usd > 0` post primer call |
| 6. Cero errors 5xx | Logs filtered ERROR severity, 0 entries en primera hora |
| 7. Latency razonable | p50 < 2000ms, p95 < 3500ms en primera muestra |

Si los 7 criterios pasan → Sprint 20 + Sprint 21 + UP-2.2 validados END-TO-END.

---

## Failure modes esperables + responses

### Failure 1: Cloud Build falla en step "Build"

**Síntoma:** `STATUS: FAILURE` en gcloud builds submit. Logs muestran error de import o sintaxis.

**Response:** Probable issue en PR #24 (cambios datetime). Verificar:
```bash
cd ~/PycharmProjects/eolo
python3 -m py_compile eolo-crop/crop_main.py eolo-crop/execution/options_trader.py
```

Si compile OK pero build falla, ver logs del build:
```bash
gcloud builds list --limit=1 --project=eolo-schwab-agent
gcloud builds log <BUILD_ID> --project=eolo-schwab-agent
```

### Failure 2: Deploy succeed pero bot no boota

**Síntoma:** Nueva revision aparece pero status = `False` o `health-check fails`.

**Response:**
```bash
gcloud logging read "resource.labels.service_name=\"eolo-bot-crop\" AND resource.labels.revision_name=\"$NEW_REV\"" \
  --project=eolo-schwab-agent \
  --limit=50 \
  --freshness=10m
```

Buscar Python tracebacks. Si es importable → rollback inmediato. Si es config (env var faltante) → check `cloudbuild-crop.yaml` `--set-env-vars`.

### Failure 3: Dashboard no carga la nueva section

**Síntoma:** Browser muestra dashboard pero sin section "🧠 LLM Metrics".

**Response:** Cache del browser. Hard refresh (Cmd+Shift+R). Si persiste, verificar que el deploy realmente incluyó el HTML modificado:
```bash
gcloud builds list --limit=1 --project=eolo-schwab-agent --format='value(images)'
```

Y compare con el commit hash de main. Si diff → re-deploy.

### Failure 4: Primer LLM call falla (5xx del LLM Engine)

**Síntoma:** Logs del bot muestran `LLM call failed: 500` o similar.

**Response:**
```bash
gcloud logging read 'resource.labels.service_name="llm-engine-service" AND severity>=ERROR' \
  --project=eolo-schwab-agent \
  --limit=20 \
  --freshness=10m
```

Si el error es del Anthropic API (timeout, rate limit) → no es nuestro problema, esperar y monitorear `cost_estimate_usd` en próximo ciclo.

Si el error es interno del engine (Sprint 21 wiring) → rollback LLM Engine a `00001-mk8`.

### Failure 5: Sprint 21 tokens son in=0 out=0 en LOGS

**Síntoma:** Logs del LLM Engine muestran `LLM response in Xms (in=0 out=0)`.

**Response:** Sprint 21 wiring obtuvo `response.usage = None`. Anthropic API debería siempre devolver usage en responses successfull. Verificar:
```bash
gcloud logging read 'resource.labels.service_name="llm-engine-service" AND textPayload:"usage"' \
  --project=eolo-schwab-agent \
  --limit=5 \
  --freshness=10m
```

Si Anthropic está devolviendo None → bug del Sprint 21 engine-side (improbable, código defensive con `getattr`). Investigar después de market close, no urgente — la decisión LLM funciona, solo el tracking falla.

### Failure 6: cost = $0 en /api/state pero tokens > 0 en logs

**Síntoma:** Logs del LLM Engine muestran tokens (in=2000 out=300) pero `/api/state.stats.llm_metrics.cost_estimate_usd = 0`.

**Diagnóstico:**
- Significa que el bot CROP no está leyendo correctamente los tokens del response del engine
- Si PR #28 está mergeado y deployado, esto NO debería pasar
- Verificar la revision activa del bot incluye el commit de PR #28:
```bash
gcloud run revisions describe $NEW_REV \
  --region=us-east1 --project=eolo-schwab-agent \
  --format='value(spec.containers[0].image)'
# Comparar el digest con el último build
```

**Response:** Si PR #28 no está en la revision activa → re-deploy. Si sí está → bug nuevo distinto al identificado en audit del 31-may. Investigar `eolo-crop/llm_gate/metrics.py:record_call` y `eolo-crop/crop_main.py:1346-1369`.

---

## Post-mortem (al cierre del día)

Si todo OK:
- Actualizar `MEMORY.md` con "Sprint 20 + UP-2.2 LIVE, Sprint 21 cost real validated"
- Cerrar tasks #45, #46, #47, #51 si quedaron pendientes en alguna parte
- Esperar 1 semana de data prod antes de tocar Sprint 18 (TACTICAL rediseño)

Si rollback ejecutado:
- Documentar en `docs/incidents/2026-06-01_rollback.md` qué falló, qué se restauró
- PR fix para el bug en branch separada
- No re-deploy hasta tener fix verificado en local

---

## Quick reference card

```
SERVICE: eolo-bot-crop (us-east1) | LLM_ENGINE: llm-engine-service (us-central1)
PROJECT: eolo-schwab-agent
DEPLOY:  gcloud builds submit --config eolo-crop/cloudbuild-crop.yaml . --project eolo-schwab-agent
LOGS:    gcloud logging read 'resource.labels.service_name="eolo-bot-crop"' --project=eolo-schwab-agent --limit=20
ROLLBACK BOT:    gcloud run services update-traffic eolo-bot-crop --region=us-east1 --project=eolo-schwab-agent --to-revisions=eolo-bot-crop-00080-zbb=100
ROLLBACK ENGINE: gcloud run services update-traffic llm-engine-service --region=us-central1 --project=eolo-schwab-agent --to-revisions=llm-engine-service-00001-mk8=100

PRs A MERGEAR (orden):     #28 (Sprint 21 fix) → #27 (UP-2.2) → #24 (Sprint 20)
PRs A DEJAR DRAFT:         #25 (UP-1.2 fase 1), #26 (UP-1.3 fase 1) — review mid-week
```

---

**Fin del runbook.** Si llegás al final con todos los checks ✓ → Sprint 20 + UP-2.2 son LIVE, Sprint 21 está validado end-to-end con cost real visible en dashboard. Trabajo de 4 días (28-29-30-31-01) cerrado con éxito.
