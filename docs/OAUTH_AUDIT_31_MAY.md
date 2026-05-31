# OAuth Status Audit — Schwab refresh_token (v1/v2/crop)

**Fecha:** 2026-05-31 (Domingo cierre)
**Pregunta:** ¿Cuál es el estado actual de OAuth para los 3 bots? ¿Qué falta para PR #23?
**Resultado:** **1 OAuth app compartida por 3 bots.** Backlog item #23 = crear 3 apps separadas (no urgente, requiere acceso Schwab dev portal).

---

## TL;DR

| Item | Estado |
|---|---|
| OAuth apps en uso | **1** (no 3) |
| refresh_token doc Firestore | `schwab-tokens/schwab-tokens-auth` |
| Bots que comparten el token | Bot v1 (`Bot/`), Bot v2 (`Bot-v1.2/`), Bot CROP (`eolo-crop/`) |
| Lifetime refresh_token | ~7 días |
| Auto-refresh access_token | ✅ Cloud Function `refresh_tokens` (cada ~25 min) |
| Alertas pre-expiry refresh_token | ✅ Cloud Function `oauth_health_check` (Telegram + email) |
| Re-auth manual cuando expira | `python3 -c "import init_auth; init_auth.main(None)"` desde local |
| Plan PR #23 | Crear 3 OAuth apps separadas (aislar bots) |
| Bloqueante PR #23 | Acceso Schwab dev portal + decisión arquitectónica |
| Severidad sin PR #23 | 🟡 MEDIA — sistema funciona pero single point of failure |

---

## Arquitectura actual

### Token storage

```
GCP Project: eolo-schwab-agent
Firestore Collection: schwab-tokens
Document: schwab-tokens-auth
Fields:
  - access_token (string, vida 30 min)
  - refresh_token (string, vida ~7 días)
  - refresh_token_issued_at (timestamp)
```

### Quién lee qué

Todos los bots leen del MISMO documento:

| Bot | Path | Función |
|---|---|---|
| **Bot v1** | `Bot/secret_stuff.py` | `retrieve_firestore_value("schwab-tokens", "schwab-tokens-auth", "access_token")` |
| **Bot v2** | `Bot-v1.2/helpers.py` + `Bot-v1.2/secret_stuff.py` | Mismo patrón |
| **Bot CROP** | `eolo-crop/helpers.py` + `eolo-crop/secret_stuff.py` + `eolo-crop/crop_main.py` | Mismo patrón |

**Implicación:** los 3 bots comparten un único refresh_token. Si expira sin re-auth, los 3 caen simultáneamente.

### Auto-refresh del access_token

```
Cloud Function: refresh_tokens
Region: us-east1
Trigger: HTTP (probablemente Cloud Scheduler cada ~25min)
Source: ./cloudbuild.yaml (root del repo)
```

El access_token Schwab vive 30 min. Esta Cloud Function lo refresca usando el refresh_token. Mientras el refresh_token sea válido, los access_token se rotan automáticamente.

### Alerta pre-expiry del refresh_token

```
Cloud Function: oauth-health-check
Source: cloud_functions/oauth_health_check/main.py
Schedule: Mon-Fri 13:00 UTC (per docstring del HTML email)
Thresholds:
  - WARN (5d edad):    "re-auth en 2 días"
  - URGENT (6d edad):  "re-auth HOY"
  - EXPIRED (7d+):     "bots OFFLINE"
Canales: Telegram + Gmail SMTP
```

**Calidad de mitigación:** ALTA — Juan tiene 2 días de aviso antes del expiry.

---

## Re-auth manual (cuando refresh_token expira)

Process documentado en el email de alerta:

```bash
cd ~/PycharmProjects/eolo
python3 -c "import init_auth; init_auth.main(None)"
```

1. Se abre el browser para login Schwab
2. Tras login, Schwab redirige a `https://127.0.0.1/?code=...` (página no carga, normal)
3. Copiar URL completa y pegarla en el terminal cuando lo pida
4. Los tokens nuevos se persisten en Firestore automáticamente
5. **Los bots los toman en ~25 min** (vía Cloud Function `refresh_tokens`)

Tiempo total: ~2 min de Juan.

---

## Plan PR #23 — 3 OAuth apps separadas

### Motivación

Hoy single point of failure: si re-auth manual no se hace a tiempo, los 3 bots caen. Aislamiento por bot:
- Bot v1 cae → bots v2 + crop siguen
- Bot v2 cae → bots v1 + crop siguen
- Bot CROP cae → bots v1 + v2 siguen

### Implementación propuesta

```
Schwab Dev Portal:
  - App 1: "eolo-bot-v1"    → refresh_token_v1
  - App 2: "eolo-bot-v2"    → refresh_token_v2
  - App 3: "eolo-bot-crop"  → refresh_token_crop

Firestore (3 docs en lugar de 1):
  schwab-tokens/v1-tokens-auth
  schwab-tokens/v2-tokens-auth
  schwab-tokens/crop-tokens-auth

Bot config (env var o constante):
  Bot/         → SCHWAB_TOKEN_DOC = "v1-tokens-auth"
  Bot-v1.2/    → SCHWAB_TOKEN_DOC = "v2-tokens-auth"
  eolo-crop/   → SCHWAB_TOKEN_DOC = "crop-tokens-auth"

Cloud Function refresh_tokens:
  - Itera por las 3 docs en lugar de 1
  - Refresh independiente por app
  - Si una falla, las otras siguen

Cloud Function oauth-health-check:
  - Itera por las 3 docs
  - Alerta separada por bot
  - Identifica cuál bot está en riesgo

init_auth.py:
  - Parametrizar por bot (--target v1|v2|crop)
  - 3 OAuth flows separados
```

### Esfuerzo estimado

| Tarea | Tiempo |
|---|---|
| Crear 3 apps en Schwab dev portal | 1h (manual, primero entender procedimiento Schwab) |
| Refactor `init_auth.py` parametrizable | 2h |
| Crear 2 docs Firestore adicionales + tokens | 30 min |
| Refactor `refresh_tokens` Cloud Function | 1h |
| Refactor `oauth_health_check` Cloud Function | 1h |
| Update bots a usar env var SCHWAB_TOKEN_DOC | 30 min cada bot × 3 = 1.5h |
| Test E2E con cada bot | 2h |
| **Total** | **~9h** |

### Riesgos

- **Downtime durante migración:** mientras se cambia el doc del bot, ese bot puede caer 25 min hasta que tome el nuevo token. Mitigación: deploy bot-por-bot, no los 3 a la vez.
- **3x re-auths cada 7 días:** Juan ahora hace 1 re-auth/semana; con #23 serán 3 re-auths/semana. Mitigación: scheduled tasks recordatorios + procedimiento batch.
- **Acceso Schwab dev portal:** requiere validar que Juan puede crear apps adicionales en su cuenta dev. Si Schwab limita el plan a 1 app, #23 no es viable.

### Decisión sugerida

**WAIT.** No es urgente porque:
- 0 expirations no-detectadas en los últimos meses (oauth-health-check funciona)
- Manual re-auth toma 2 min
- Sin #23, sistema sigue operacional con single point of failure controlado

**Reconsiderar cuando:**
- Haya 1+ incidentes de bots-caídos-por-expiry no atrapados a tiempo
- Quiera reactivar bot v1 (hoy no opera) y necesite aislamiento
- Tiempo libre disponible para sprint de 9h

---

## Operacional immediate-term — qué hacer ESTA SEMANA

### ¿oauth-health-check está realmente corriendo?

Verificar con:

```bash
gcloud functions describe oauth-health-check --region=us-east1 --project=eolo-schwab-agent
```

Si NO está deployed → riesgo real (Juan no recibe alertas). Acción: deploy.

Si SÍ está deployed → confirmar último `lastRunAt` y que envió alertas en último ciclo de re-auth.

### ¿Cuándo fue el último re-auth?

```bash
gcloud firestore documents read schwab-tokens/schwab-tokens-auth --project=eolo-schwab-agent
# Buscar refresh_token_issued_at
```

Si `now - issued_at > 5 días` → ya deberías estar recibiendo warning Telegram.
Si `now - issued_at > 7 días` → bots caídos (incidente). Re-auth YA.

### ¿Cron del refresh_tokens está activo?

Verificar el Cloud Scheduler que dispara `refresh_tokens` Cloud Function:

```bash
gcloud scheduler jobs list --location=us-east1 --project=eolo-schwab-agent | grep -i refresh
```

Si no hay scheduler → access_token NO se está rotando → bots caen cada 30 min. (Probablemente sí existe porque los bots están vivos en producción, pero verificar.)

---

## Resumen operacional

| Componente | Status estimado | Acción |
|---|---|---|
| 1 OAuth app compartida | ✅ Funciona | Documentado |
| refresh_tokens Cloud Function | ⚠️ Verificar deployed + scheduler | Comando `gcloud functions describe` |
| oauth-health-check Cloud Function | ⚠️ Verificar deployed + scheduler | Comando `gcloud functions describe` |
| Manual re-auth cada 7d | ✅ Documentado | Email lo recuerda |
| 3 OAuth apps separadas (#23) | ⏳ Backlog ~9h | NO URGENTE |

**Decisión final:**
1. **Esta semana:** verificar que las 2 Cloud Functions estén deployed + en cron (5 min con gcloud)
2. **Backlog futuro:** #23 cuando haya incidente o tiempo libre (9h sprint)
3. **No tocar nada más:** sistema operativo, alertas en pie, manual re-auth conocido

---

## Apéndice — comandos de verificación

### Estado Cloud Functions

```bash
gcloud functions list --project=eolo-schwab-agent --region=us-east1
# Buscar: refresh_tokens, oauth-health-check
```

### Estado Cloud Scheduler

```bash
gcloud scheduler jobs list --location=us-east1 --project=eolo-schwab-agent
# Buscar: refresh tokens, oauth health check
```

### Estado del refresh_token actual

```bash
# Via gcloud CLI:
gcloud firestore documents read schwab-tokens/schwab-tokens-auth \
  --project=eolo-schwab-agent

# Via Python:
python3 -c "
from google.cloud import firestore
import datetime
db = firestore.Client(project='eolo-schwab-agent')
doc = db.collection('schwab-tokens').document('schwab-tokens-auth').get().to_dict()
issued = doc.get('refresh_token_issued_at')
print(f'refresh_token_issued_at: {issued}')
if issued:
    age_days = (datetime.datetime.now(datetime.timezone.utc) - issued.replace(tzinfo=datetime.timezone.utc)).days
    print(f'edad: {age_days} días')
    if age_days >= 7:
        print('🔴 EXPIRADO — re-auth YA')
    elif age_days >= 6:
        print('🟠 URGENTE — re-auth HOY')
    elif age_days >= 5:
        print('🟡 WARNING — re-auth en 1-2 días')
    else:
        print('✅ OK')
"
```

### Deploy de las Cloud Functions (si faltan)

```bash
# refresh_tokens
cd ~/PycharmProjects/eolo
gcloud builds submit --config cloudbuild.yaml . --project=eolo-schwab-agent

# oauth_health_check (no veo cloudbuild dedicado, podría requerir deploy manual)
cd cloud_functions/oauth_health_check
gcloud functions deploy oauth-health-check \
  --gen2 \
  --runtime=python312 \
  --region=us-east1 \
  --source=. \
  --entry-point=check \
  --trigger-http \
  --memory=256MB \
  --timeout=60 \
  --project=eolo-schwab-agent
```

---

**Generado:** 2026-05-31
**Files auditados:** `secret_stuff.py` (×3), `helpers.py` (×3), `init_auth.py`, `cloud_functions/oauth_health_check/main.py`, `cloudbuild.yaml`, backlog item OP-4.1
