# eolo-sheets-sync

Servicio Cloud Run que lee las trades de los 3 bots desde Firestore y las carga
en 4 Google Sheets en el Drive de `juanfranciscolago@gmail.com`, carpeta
`nino/eolo - API`.

## Sheets destino (se crean solas la primera vez)

| Sheet                 | Fuente Firestore       | Filas                     |
|-----------------------|------------------------|---------------------------|
| `Eolo_v1_Trades`      | `eolo-trades`          | Acciones Schwab           |
| `Eolo_v2_Trades`      | `eolo-options-trades`  | Opciones Schwab           |
| `Eolo_Crypto_Trades`  | `eolo-crypto-trades`   | Binance spot              |
| `Eolo_All_Trades`     | los 3                  | Schema normalizado común  |

## Setup (paso a paso)

Todos los comandos corren en `eolo-schwab-agent`. Pararse en la RAÍZ del repo
(`cd /path/to/eolo`) antes de los builds.

### 1. Habilitar APIs

```bash
gcloud services enable \
    sheets.googleapis.com \
    drive.googleapis.com \
    firestore.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    --project=eolo-schwab-agent
```

### 2. Crear la Service Account del servicio

```bash
gcloud iam service-accounts create eolo-sheets-sync \
    --display-name="Eolo Sheets Sync" \
    --project=eolo-schwab-agent

# Permitir lectura de Firestore
gcloud projects add-iam-policy-binding eolo-schwab-agent \
    --member="serviceAccount:eolo-sheets-sync@eolo-schwab-agent.iam.gserviceaccount.com" \
    --role="roles/datastore.user"
```

### 3. Crear carpeta + 4 spreadsheets en Drive (manual, una sola vez)

**Por qué manual:** las Service Accounts de GCP tienen 0 bytes de Drive quota.
Cuando una SA crea un archivo (incluso dentro de un folder ajeno), la quota se
cuenta al creador → `storageQuotaExceeded`. En Workspace esto se resuelve con
Shared Drives, pero con Gmail personal la única opción viable es que el dueño
(Juan) cree los archivos, y la SA los toque como Editor.

En Drive:

1. Crear la carpeta `nino/eolo - API` (o reutilizarla si ya existe).
2. Dentro de esa carpeta, crear 4 Google Sheets vacías con estos **nombres
   exactos** (mayúsculas/minúsculas/underscore importan):
   - `Eolo_v1_Trades`
   - `Eolo_v2_Trades`
   - `Eolo_Crypto_Trades`
   - `Eolo_All_Trades`
3. Compartir la **carpeta** `eolo - API` con:
   - `eolo-sheets-sync@eolo-schwab-agent.iam.gserviceaccount.com`
   - Rol: **Editor**
   (Las 4 sheets heredan el share por estar dentro.)

El servicio no crea ni la carpeta ni las sheets. Si faltan, falla con un
mensaje claro indicando cuáles faltan. Los headers los escribe el servicio en
la primera corrida (idempotente: si la sheet ya tiene algo en A1, no lo pisa).

### 4. Build + deploy

```bash
cd /path/to/eolo
gcloud builds submit --config eolo-sheets-sync/cloudbuild.yaml . \
    --project=eolo-schwab-agent
```

Quedará en `us-central1` con `--no-allow-unauthenticated` (solo tokeneado).

### 5. Primera invocación manual (para crear las sheets)

```bash
URL=$(gcloud run services describe eolo-sheets-sync \
    --region=us-central1 --project=eolo-schwab-agent \
    --format='value(status.url)')

TOKEN=$(gcloud auth print-identity-token)

# Trigger y ver qué escribió
curl -s -X POST -H "Authorization: Bearer $TOKEN" "$URL/sync" | jq
```

Si la primera vez falla con `403 ...drive...`, revisá que la carpeta esté
compartida con el email de la SA.

### 6. Cloud Scheduler cada 15 min

```bash
# Crear la SA que invoca al servicio (separada de la que corre el servicio)
gcloud iam service-accounts create eolo-sheets-sync-invoker \
    --display-name="Eolo Sheets Sync Invoker" \
    --project=eolo-schwab-agent

# Darle permiso de invocar a Cloud Run
gcloud run services add-iam-policy-binding eolo-sheets-sync \
    --region=us-central1 \
    --member="serviceAccount:eolo-sheets-sync-invoker@eolo-schwab-agent.iam.gserviceaccount.com" \
    --role="roles/run.invoker" \
    --project=eolo-schwab-agent

# Crear el job
gcloud scheduler jobs create http eolo-sheets-sync-cron \
    --schedule="*/15 * * * *" \
    --uri="$URL/sync" \
    --http-method=POST \
    --oidc-service-account-email=eolo-sheets-sync-invoker@eolo-schwab-agent.iam.gserviceaccount.com \
    --oidc-token-audience="$URL" \
    --location=us-central1 \
    --project=eolo-schwab-agent
```

### 7. Verificar

```bash
# Ver config persistida (ids de sheets)
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
    "$URL/config" | jq

# Ver último run
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
    "$URL/status" | jq

# Trigger manual
gcloud scheduler jobs run eolo-sheets-sync-cron --location=us-central1
```

## Observaciones

- **Idempotencia**: el servicio trackea los `doc_id` ya escritos en
  `eolo-sheets-sync-state/synced_v1|v2|crypto`. Re-runs no duplican filas.
- **Look-back**: por default 7 días (`LOOKBACK_DAYS`). Los trades más viejos
  se ignoran — si querés backfill histórico, subir esta env var temporalmente
  y re-triggerear.
- **Costo**: ~1 MB egress/día, ~200 lecturas Firestore/día → free-tier.

## Modificar el schema `Eolo_All_Trades`

El mapping de cada bot → fila común vive en `main.py::_row_all`. Si cambiás
columnas, también actualizar `HEADERS_ALL` (pero ojo, los headers sólo se
escriben al crear la sheet — si ya existe hay que cambiarlos a mano o borrar
la sheet y dejar que el servicio la re-cree).
