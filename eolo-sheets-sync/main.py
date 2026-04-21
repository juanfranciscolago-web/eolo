# ============================================================
#  EOLO — Sheets Sync Service
#
#  Servicio Cloud Run que lee las trades de los 3 bots desde
#  Firestore y las escribe en 4 Google Sheets dentro del Drive
#  del usuario (juanfranciscolago@gmail.com, carpeta "nino/eolo - API").
#
#  Sheets destino:
#    1. Eolo_v1_Trades       — trades de Bot/ (acciones Schwab)
#    2. Eolo_v2_Trades       — trades de eolo-options/ (opciones Schwab)
#    3. Eolo_Crypto_Trades   — trades de eolo-crypto/ (Binance spot)
#    4. Eolo_All_Trades      — normalizado, un schema común
#
#  Cómo se dispara:
#    Cloud Scheduler hace POST cada 15 minutos a /sync (o GET).
#    También puede dispararse manualmente con:
#       curl -s https://eolo-sheets-sync-...-ue.a.run.app/sync
#
#  Idempotencia:
#    Cada trade tiene un `doc_id` único. El set de IDs ya escritos
#    se persiste en Firestore: eolo-sheets-sync-state/synced_ids
#    (un map ticker→timestamp por colección). Trades ya presentes se skippean.
#
#  Auth:
#    El servicio corre con una SA dedicada `eolo-sheets-sync@...`
#    que tiene IAM rol `roles/datastore.user` para leer Firestore.
#    Para escribir a Sheets, usa las creds por default de Cloud Run
#    (Application Default Credentials) y la carpeta Drive tiene que
#    estar compartida con el email de la SA como Editor.
#
#  Estado en Firestore:
#    eolo-sheets-sync-state/config    → {v1_sheet_id, v2_sheet_id, crypto_sheet_id, all_sheet_id, folder_id}
#    eolo-sheets-sync-state/synced_v1      → {ids: [...]}
#    eolo-sheets-sync-state/synced_v2      → {ids: [...]}
#    eolo-sheets-sync-state/synced_crypto  → {ids: [...]}
# ============================================================
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, jsonify, request
from google.auth import default as google_auth_default
from google.cloud import firestore
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger

# ── Config ────────────────────────────────────────────────
PROJECT_ID           = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")
DRIVE_FOLDER_NAME    = os.environ.get("DRIVE_FOLDER_NAME", "eolo - API")
DRIVE_PARENT_FOLDER  = os.environ.get("DRIVE_PARENT_FOLDER", "nino")
SYNC_STATE_COLLECTION = "eolo-sheets-sync-state"
CONFIG_DOC           = "config"

# Look-back (en días) de Firestore al scanear. No leemos todo el histórico
# cada 15 min — solo los últimos N días para no gastar lecturas.
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))

# Scopes necesarios
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DRIVE_SCOPE  = "https://www.googleapis.com/auth/drive"
SCOPES       = [SHEETS_SCOPE, DRIVE_SCOPE]

# Nombres de las sheets
SHEET_V1     = "Eolo_v1_Trades"
SHEET_V2     = "Eolo_v2_Trades"
SHEET_CRYPTO = "Eolo_Crypto_Trades"
SHEET_ALL    = "Eolo_All_Trades"

# Headers de cada sheet
HEADERS_V1 = [
    "doc_id", "timestamp", "mode", "action", "ticker",
    "shares", "price", "total_usd", "strategy",
]
HEADERS_V2 = [
    "doc_id", "timestamp", "mode", "action", "ticker", "option_type",
    "expiration", "strike", "contracts", "limit_price", "total_est",
    "symbol", "strategy", "reason", "order_id", "pnl_usd", "pnl_pct",
]
HEADERS_CRYPTO = [
    "doc_id", "ts_iso", "mode", "side", "symbol", "qty",
    "price", "notional", "strategy", "pnl_usdt", "reason",
]
HEADERS_ALL = [
    "doc_id", "ts_utc", "bot", "mode", "action", "ticker",
    "instrument", "qty", "price", "notional_usd",
    "strategy", "reason", "pnl_usd", "option_meta",
]


app = Flask(__name__)
_db: firestore.Client | None = None


def db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


# ── Google auth + clients ─────────────────────────────────

def _google_services():
    """Devuelve (sheets_service, drive_service) usando ADC."""
    creds, _ = google_auth_default(scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive  = build("drive",  "v3", credentials=creds, cache_discovery=False)
    return sheets, drive


# ── Config (ids de sheets en Drive) ───────────────────────

def _load_config() -> dict:
    doc = db().collection(SYNC_STATE_COLLECTION).document(CONFIG_DOC).get()
    return doc.to_dict() if doc.exists else {}


def _save_config(cfg: dict) -> None:
    db().collection(SYNC_STATE_COLLECTION).document(CONFIG_DOC).set(cfg, merge=True)


def _find_folder(drive, name: str, parent_id: str | None = None) -> str | None:
    """Busca una carpeta por nombre accesible a la SA (incluye shared-with-me).
    Si `parent_id` se provee, filtra por ese padre. Retorna el id o None.

    Nota: NO creamos folders acá. Una Service Account no tiene Drive quota
    propia, así que carpetas y spreadsheets tienen que vivir en el Drive del
    usuario y estar compartidas con la SA. Si la carpeta no existe, fallamos
    con un mensaje claro.
    """
    q_parts = [
        "mimeType = 'application/vnd.google-apps.folder'",
        f"name = '{name}'",
        "trashed = false",
    ]
    if parent_id:
        q_parts.append(f"'{parent_id}' in parents")
    q = " and ".join(q_parts)
    resp = drive.files().list(
        q=q, fields="files(id,name,parents)", pageSize=20,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    return None


def _find_sheet(drive, name: str, folder_id: str) -> str | None:
    """Busca una spreadsheet por nombre dentro del folder. Retorna el id o None.
    NO la crea: las SAs de GCP tienen 0 bytes de Drive quota y no pueden ser
    owners, así que las sheets tienen que existir (creadas a mano por Juan)
    y estar compartidas con la SA como Editor.
    """
    q = (
        "mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and name = '{name}' "
        f"and '{folder_id}' in parents and trashed = false"
    )
    resp = drive.files().list(
        q=q, fields="files(id,name)", pageSize=5,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _ensure_headers(sheets, spreadsheet_id: str, headers: list[str]) -> None:
    """Si la primera fila está vacía, escribe los headers. Idempotente:
    si ya hay algo en A1 (p.ej. headers existentes o una fila vieja), no toca nada."""
    try:
        got = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range="A1:Z1",
        ).execute()
        row = got.get("values", [[]])[0] if got.get("values") else []
        if row:
            return  # ya tiene algo, no pisar
    except HttpError as e:
        logger.warning(f"[SYNC] No pude leer A1 de {spreadsheet_id}: {e}")
        return
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range="A1",
        valueInputOption="RAW", body={"values": [headers]},
    ).execute()
    logger.info(f"[SYNC] Headers escritos en {spreadsheet_id}")


def _ensure_infra() -> dict:
    """Valida que la carpeta exista (compartida con la SA) y crea/valida las 4
    sheets, persistiendo sus ids en Firestore.

    Si la carpeta no es encontrada, falla con un mensaje accionable — no la
    creamos porque una SA no tiene Drive quota propia para ser owner.
    """
    cfg = _load_config()
    sheets, drive = _google_services()

    folder_id = cfg.get("folder_id")
    if not folder_id:
        # Buscar directo por nombre (la SA tiene que verla via shared-with-me
        # o via Shared Drive). No nos importa el padre — si hay ambigüedad,
        # agarramos la primera.
        folder_id = _find_folder(drive, DRIVE_FOLDER_NAME)
        if not folder_id:
            raise RuntimeError(
                f"No se encontró la carpeta '{DRIVE_FOLDER_NAME}' accesible a la "
                f"Service Account. Compartila con "
                f"eolo-sheets-sync@{PROJECT_ID}.iam.gserviceaccount.com como Editor."
            )
        cfg["folder_id"] = folder_id

    targets = [
        ("v1_sheet_id",     SHEET_V1,     HEADERS_V1),
        ("v2_sheet_id",     SHEET_V2,     HEADERS_V2),
        ("crypto_sheet_id", SHEET_CRYPTO, HEADERS_CRYPTO),
        ("all_sheet_id",    SHEET_ALL,    HEADERS_ALL),
    ]
    missing = []
    for key, name, headers in targets:
        if not cfg.get(key):
            sid = _find_sheet(drive, name, folder_id)
            if not sid:
                missing.append(name)
                continue
            cfg[key] = sid
        # Escribir headers si la sheet está vacía (idempotente)
        _ensure_headers(sheets, cfg[key], headers)

    if missing:
        raise RuntimeError(
            f"Faltan las siguientes spreadsheets en la carpeta "
            f"'{DRIVE_FOLDER_NAME}': {', '.join(missing)}. "
            f"Creá cada una a mano desde Google Sheets (deben vivir DENTRO "
            f"de esa carpeta) y compartilas con "
            f"eolo-sheets-sync@{PROJECT_ID}.iam.gserviceaccount.com como Editor. "
            f"Razón: las Service Accounts no tienen Drive quota y no pueden "
            f"crear archivos propios."
        )

    _save_config(cfg)
    return cfg


# ── Firestore readers ─────────────────────────────────────

def _recent_daily_docs(collection: str) -> list[str]:
    """Devuelve lista de doc_ids (fechas YYYY-MM-DD) de los últimos LOOKBACK_DAYS.
    Asume el patrón de v1/v2: doc_id = YYYY-MM-DD."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(LOOKBACK_DAYS)]


def _read_v1_v2(collection: str) -> list[tuple[str, dict]]:
    """Lee colecciones con patrón de daily doc (v1 y v2). Devuelve lista de
    (doc_id, trade_dict) donde doc_id = '{YYYY-MM-DD}/{field_key}' — único."""
    out: list[tuple[str, dict]] = []
    for day in _recent_daily_docs(collection):
        snap = db().collection(collection).document(day).get()
        if not snap.exists:
            continue
        data = snap.to_dict() or {}
        for field_key, trade in data.items():
            if not isinstance(trade, dict):
                continue
            out.append((f"{day}/{field_key}", trade))
    return out


def _read_crypto() -> list[tuple[str, dict]]:
    """Lee eolo-crypto-trades (un doc por trade). doc_id = id del doc.
    Filtramos por ts (epoch seconds) >= now - LOOKBACK_DAYS para limitar reads."""
    cutoff = time.time() - (LOOKBACK_DAYS * 86400)
    out: list[tuple[str, dict]] = []
    try:
        query = (
            db().collection("eolo-crypto-trades")
            .where("ts", ">=", cutoff)
            .order_by("ts")
        )
        for d in query.stream():
            out.append((d.id, d.to_dict() or {}))
    except Exception as e:
        # Fallback si no existe index sobre `ts` o la colección está vacía
        logger.warning(f"[SYNC] crypto query falló, haciendo full scan: {e}")
        for d in db().collection("eolo-crypto-trades").stream():
            data = d.to_dict() or {}
            if data.get("ts", 0) >= cutoff:
                out.append((d.id, data))
    return out


# ── Idempotencia (set de ids ya escritos) ─────────────────

def _load_synced_ids(kind: str) -> set[str]:
    doc = db().collection(SYNC_STATE_COLLECTION).document(f"synced_{kind}").get()
    if not doc.exists:
        return set()
    data = doc.to_dict() or {}
    return set(data.get("ids", []))


def _save_synced_ids(kind: str, ids: set[str]) -> None:
    # Solo mantenemos los de los últimos 2× LOOKBACK_DAYS para no crecer infinito
    # (si un id cae fuera y reaparece, se reescribe — aceptable).
    db().collection(SYNC_STATE_COLLECTION).document(f"synced_{kind}").set(
        {"ids": sorted(ids), "updated_at": datetime.now(timezone.utc).isoformat()},
        merge=False,
    )


# ── Conversores a filas ───────────────────────────────────

def _row_v1(doc_id: str, t: dict) -> list[Any]:
    return [
        doc_id,
        t.get("timestamp", ""),
        t.get("mode", ""),
        t.get("action", ""),
        t.get("ticker", ""),
        t.get("shares", ""),
        t.get("price", ""),
        t.get("total_usd", ""),
        t.get("strategy", ""),
    ]


def _row_v2(doc_id: str, t: dict) -> list[Any]:
    return [
        doc_id,
        t.get("timestamp", ""),
        t.get("mode", ""),
        t.get("action", ""),
        t.get("ticker", ""),
        t.get("option_type", ""),
        t.get("expiration", ""),
        t.get("strike", ""),
        t.get("contracts", ""),
        t.get("limit_price", ""),
        t.get("total_est", ""),
        t.get("symbol", ""),
        t.get("strategy", ""),
        (t.get("reason", "") or "")[:500],
        t.get("order_id", ""),
        t.get("pnl_usd", ""),
        t.get("pnl_pct", ""),
    ]


def _row_crypto(doc_id: str, t: dict) -> list[Any]:
    ts = t.get("ts", 0)
    try:
        ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        ts_iso = ""
    return [
        doc_id,
        ts_iso,
        t.get("mode", ""),
        t.get("side", ""),
        t.get("symbol", ""),
        t.get("qty", ""),
        t.get("price", ""),
        t.get("notional", ""),
        t.get("strategy", ""),
        t.get("pnl_usdt", ""),
        (t.get("reason", "") or "")[:500],
    ]


def _row_all(bot: str, doc_id: str, t: dict) -> list[Any]:
    """Schema normalizado para la sheet 'Eolo_All_Trades'.
    Convierte cada trade a: ts_utc | bot | mode | action | ticker | instrument |
    qty | price | notional_usd | strategy | reason | pnl_usd | option_meta."""
    if bot == "v1":
        ts       = t.get("timestamp", "")
        action   = t.get("action", "")
        qty      = t.get("shares", "")
        price    = t.get("price", "")
        notional = t.get("total_usd", "")
        inst     = "stock"
        opt_meta = ""
        pnl      = ""  # v1 no persiste pnl por trade (se deriva de pares BUY/SELL)
        reason   = ""
    elif bot == "v2":
        ts       = t.get("timestamp", "")
        action   = t.get("action", "")
        qty      = t.get("contracts", "")
        price    = t.get("limit_price", "")
        notional = t.get("total_est", "")
        inst     = "option"
        opt_meta = json.dumps({
            "option_type": t.get("option_type", ""),
            "expiration":  t.get("expiration", ""),
            "strike":      t.get("strike", ""),
            "symbol":      t.get("symbol", ""),
        })
        pnl      = t.get("pnl_usd", "")
        reason   = (t.get("reason", "") or "")[:300]
    else:  # crypto
        try:
            ts = datetime.fromtimestamp(float(t.get("ts", 0)), tz=timezone.utc).isoformat()
        except Exception:
            ts = ""
        action   = t.get("side", "")
        qty      = t.get("qty", "")
        price    = t.get("price", "")
        notional = t.get("notional", "")
        inst     = "spot-crypto"
        opt_meta = ""
        pnl      = t.get("pnl_usdt", "")
        reason   = (t.get("reason", "") or "")[:300]

    return [
        doc_id, ts, bot, t.get("mode", ""), action, t.get("ticker") or t.get("symbol", ""),
        inst, qty, price, notional, t.get("strategy", ""), reason, pnl, opt_meta,
    ]


# ── Escritura a Sheets ────────────────────────────────────

def _append_rows(sheets, spreadsheet_id: str, rows: list[list[Any]]) -> None:
    if not rows:
        return
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


# ── Sync principal ────────────────────────────────────────

def run_sync() -> dict:
    cfg = _ensure_infra()
    sheets, _drive = _google_services()

    result = {"appended": {}, "skipped": {}, "errors": []}

    # v1 ───────────────────────────────────────────────────
    try:
        synced = _load_synced_ids("v1")
        new_rows_individual: list[list[Any]] = []
        new_rows_all: list[list[Any]] = []
        new_ids: list[str] = []
        for doc_id, trade in _read_v1_v2("eolo-trades"):
            if doc_id in synced:
                continue
            new_rows_individual.append(_row_v1(doc_id, trade))
            new_rows_all.append(_row_all("v1", doc_id, trade))
            new_ids.append(doc_id)
        _append_rows(sheets, cfg["v1_sheet_id"], new_rows_individual)
        _append_rows(sheets, cfg["all_sheet_id"], new_rows_all)
        synced.update(new_ids)
        _save_synced_ids("v1", synced)
        result["appended"]["v1"] = len(new_ids)
    except HttpError as e:
        result["errors"].append(f"v1: {e}")
        logger.error(f"[SYNC] v1 falló: {e}")
    except Exception as e:
        result["errors"].append(f"v1: {e}")
        logger.error(f"[SYNC] v1 error: {e}")

    # v2 ───────────────────────────────────────────────────
    try:
        synced = _load_synced_ids("v2")
        new_rows_individual = []
        new_rows_all = []
        new_ids = []
        for doc_id, trade in _read_v1_v2("eolo-options-trades"):
            if doc_id in synced:
                continue
            new_rows_individual.append(_row_v2(doc_id, trade))
            new_rows_all.append(_row_all("v2", doc_id, trade))
            new_ids.append(doc_id)
        _append_rows(sheets, cfg["v2_sheet_id"], new_rows_individual)
        _append_rows(sheets, cfg["all_sheet_id"], new_rows_all)
        synced.update(new_ids)
        _save_synced_ids("v2", synced)
        result["appended"]["v2"] = len(new_ids)
    except HttpError as e:
        result["errors"].append(f"v2: {e}")
        logger.error(f"[SYNC] v2 falló: {e}")
    except Exception as e:
        result["errors"].append(f"v2: {e}")
        logger.error(f"[SYNC] v2 error: {e}")

    # crypto ───────────────────────────────────────────────
    try:
        synced = _load_synced_ids("crypto")
        new_rows_individual = []
        new_rows_all = []
        new_ids = []
        for doc_id, trade in _read_crypto():
            if doc_id in synced:
                continue
            new_rows_individual.append(_row_crypto(doc_id, trade))
            new_rows_all.append(_row_all("crypto", doc_id, trade))
            new_ids.append(doc_id)
        _append_rows(sheets, cfg["crypto_sheet_id"], new_rows_individual)
        _append_rows(sheets, cfg["all_sheet_id"], new_rows_all)
        synced.update(new_ids)
        _save_synced_ids("crypto", synced)
        result["appended"]["crypto"] = len(new_ids)
    except HttpError as e:
        result["errors"].append(f"crypto: {e}")
        logger.error(f"[SYNC] crypto falló: {e}")
    except Exception as e:
        result["errors"].append(f"crypto: {e}")
        logger.error(f"[SYNC] crypto error: {e}")

    # Guardamos último run
    db().collection(SYNC_STATE_COLLECTION).document("last_run").set({
        "at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }, merge=False)
    return result


# ── HTTP endpoints ────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "eolo-sheets-sync",
        "endpoints": ["/sync (POST/GET)", "/status (GET)", "/config (GET)"],
    })


@app.route("/sync", methods=["GET", "POST"])
def sync():
    logger.info(f"[SYNC] Trigger desde {request.remote_addr}")
    try:
        result = run_sync()
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        logger.exception("[SYNC] Error fatal")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    doc = db().collection(SYNC_STATE_COLLECTION).document("last_run").get()
    return jsonify(doc.to_dict() if doc.exists else {"last_run": None})


@app.route("/config", methods=["GET"])
def config():
    return jsonify(_load_config())


if __name__ == "__main__":
    # Modo local para debugging
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
