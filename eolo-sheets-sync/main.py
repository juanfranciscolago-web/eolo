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
from collections import defaultdict
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

# Phase 5 (2026-04-21) — Strategy_Diagnostics
DIAGNOSTICS_COLLECTION = "eolo-strategy-diagnostics"
DIAGNOSTICS_TICKERS    = (
    "AAPL", "NVDA", "NVDL", "SOXL", "SPY", "TSLA", "TQQQ", "TSLL", "QQQ",
)
DIAGNOSTICS_INTERVAL = "5m"
DIAGNOSTICS_PERIOD   = "2d"
DIAGNOSTICS_TAB      = "Strategy_Diagnostics"

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

# ── Headers de cada sheet ─────────────────────────────────
#
# v1 y crypto usan un schema UNIFICADO de 11 columnas (+timeframe extra)
# para poder compararlos fácilmente. v2 mantiene las columnas específicas
# de opciones pero adopta los mismos nombres base (Time Stamp, side, symbol,
# pnl_usd, reason) para consistencia entre sheets.
#
# Phase 1 (2026-04-21) agregó 10 columnas de enriquecimiento que pueblan los
# bots via `eolo_common.trade_enrichment.build_enrichment()`. Rows viejos que
# no tienen estos campos rellenan con string vacío — sheets-sync tolera mixto.
ENRICH_COLS = [
    "entry_price", "hold_seconds",
    "vix_snapshot", "vix_bucket",
    "spy_ret_5d", "session_bucket",
    "slippage_bps", "trade_number_today",
    "entry_reason", "exit_reason",
]

HEADERS_V1 = [
    "doc_id", "Time Stamp", "mode", "side", "symbol", "qty",
    "price", "notional", "strategy", "pnl_usd", "reason", "timeframe",
] + ENRICH_COLS
HEADERS_V2 = [
    "doc_id", "Time Stamp", "mode", "side", "symbol", "option_type",
    "expiration", "strike", "qty", "price", "notional",
    "occ_symbol", "strategy", "pnl_usd", "pnl_pct", "reason",
    "order_id", "timeframe",
] + ENRICH_COLS
HEADERS_CRYPTO = [
    "doc_id", "Time Stamp", "mode", "side", "symbol", "qty",
    "price", "notional", "strategy", "pnl_usd", "reason", "timeframe",
] + ENRICH_COLS
HEADERS_ALL = [
    "doc_id", "Time Stamp", "bot", "mode", "side", "symbol",
    "instrument", "qty", "price", "notional", "strategy",
    "pnl_usd", "reason", "timeframe", "option_meta",
] + ENRICH_COLS

# Headers de la pestaña de análisis por estrategia — Phase 2 (2026-04-21)
# agrega avg_hold_seconds, profit_factor, expectancy, sharpe_simple, verdict.
HEADERS_STATS = [
    "strategy", "enabled", "executions", "buys", "sells",
    "wins", "losses", "breakeven", "win_rate",
    "total_pnl", "avg_pnl_per_sell", "best_pnl", "worst_pnl",
    "avg_hold_seconds", "profit_factor", "expectancy", "sharpe_simple",
    "verdict",
    "first_seen", "last_seen", "timeframes", "tickers",
]
STATS_TAB = "Strategy_Stats"

# ── Nuevas pestañas analíticas (Phase 2 — 2026-04-21) ─────
# Cada sync re-computa estas pestañas from scratch leyendo la tab principal.
COMPLETED_TRADES_TAB = "Completed_Trades"
HEADERS_COMPLETED = [
    "bot", "strategy", "symbol", "entry_ts", "exit_ts", "hold_seconds",
    "qty", "entry_price", "exit_price",
    "pnl_usd", "pnl_pct",
    "session_bucket_entry", "session_bucket_exit",
    "vix_bucket_entry", "vix_bucket_exit",
    "slippage_bps_entry", "slippage_bps_exit",
    "entry_reason", "exit_reason",
    "timeframe",
]

STRATEGY_X_TICKER_TAB = "Strategy_x_Ticker"
HEADERS_STRATEGY_X_TICKER = [
    "strategy", "symbol", "trades", "buys", "sells",
    "wins", "losses", "win_rate",
    "total_pnl", "avg_pnl", "best_pnl", "worst_pnl",
    "first_seen", "last_seen",
]

DAILY_SUMMARY_TAB = "Daily_Summary"
HEADERS_DAILY = [
    "date", "trades", "buys", "sells",
    "wins", "losses", "win_rate",
    "total_pnl", "avg_pnl_per_sell", "best_pnl", "worst_pnl",
    "tickers", "strategies_active",
]

REGIME_PERFORMANCE_TAB = "Regime_Performance"
HEADERS_REGIME = [
    "vix_bucket", "session_bucket",
    "trades", "wins", "losses", "win_rate",
    "total_pnl", "avg_pnl", "best_pnl", "worst_pnl",
    "avg_hold_seconds",
]

# ── Phase 3 (2026-04-21) — Verdict engine + Daily Recap ───
# Motor de veredictos configurable (reemplaza _heuristic_verdict) + generador
# de Daily_Recap.md por bot, persistido en Firestore y en pestaña dedicada.
DAILY_RECAP_TAB = "Daily_Recap"
HEADERS_DAILY_RECAP = ["line"]   # una columna, cada row = línea de markdown
MAX_DAILY_RECAPS_IN_TAB = 30     # últimos N recaps que quedan visibles en la tab

VERDICT_RULES_DOC      = "verdict_rules"
VERDICT_HISTORY_DOC    = "verdict_history"
DAILY_RECAP_COLLECTION = "eolo-daily-recaps"

# Reglas por defecto. Se pueden overridear con un doc en Firestore
# `eolo-sheets-sync-state/verdict_rules` que contenga cualquiera de estas
# keys; el merge es shallow (el doc reemplaza la key entera).
#
# Semántica:
#   - insufficient_data_threshold: si trades < N → "insufficient_data"
#     antes de evaluar reglas.
#   - cooldown_syncs: cuántos syncs consecutivos tiene que sostenerse un
#     veredicto nuevo antes de promoverse (solo aplica si la transición es
#     "strong", ver abajo). Si <= 1, los cambios son instantáneos.
#   - strong_transitions: veredictos "fuertes" que requieren cooldown. Si la
#     transición involucra (prev ∈ strong OR raw ∈ strong), el cambio espera.
#     Transiciones débiles (p.ej. watch ↔ tune) flipean instantáneo.
#   - rules: lista evaluada en orden. Primera match gana.
#     Claves soportadas por `when`: min_pf/max_pf, min_wr/max_wr,
#     min_exp/max_exp. max_X es estricto (<), min_X es no-estricto (>=).
#   - default_verdict: si ninguna regla matchea, este es el resultado.
DEFAULT_VERDICT_RULES: dict = {
    "insufficient_data_threshold": 10,
    "cooldown_syncs": 2,
    "strong_transitions": ["kill", "keep"],
    "rules": [
        {"when": {"max_pf": 0.8, "max_exp": 0.0}, "verdict": "kill"},
        {"when": {"max_pf": 1.0},                 "verdict": "tune"},
        {"when": {"min_pf": 1.5, "min_wr": 0.55}, "verdict": "keep"},
    ],
    "default_verdict": "watch",
}

# Dónde vive la config de estrategias habilitadas para cada bot.
#   v1     → eolo-config/strategies          (strategy keys como fields top-level)
#   v2     → eolo-options-config/settings    (bajo field `strategies_enabled`)
#   crypto → eolo-crypto-config/settings     (bajo field `strategies_enabled`)
# Tupla: (collection, doc_id, field_key | None)
STRATEGIES_CONFIG = {
    "v1":     ("eolo-config",         "strategies", None),
    "v2":     ("eolo-options-config", "settings",   "strategies_enabled"),
    "crypto": ("eolo-crypto-config",  "settings",   "strategies_enabled"),
}


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


def _ensure_headers(sheets, spreadsheet_id: str, headers: list[str],
                    tab_name: str | None = None) -> None:
    """Sincroniza la fila 1 con `headers`.

    - Si A1 está vacío → escribe los headers completos.
    - Si A1 tiene headers pero son un PREFIX del esperado (ej: agregamos columnas
      nuevas) → extiende la fila 1 con las columnas faltantes. Los rows de datos
      existentes quedan intactos (las celdas nuevas aparecen vacías).
    - Si A1 tiene headers incompatibles (no-prefix) → warn y deja como está
      para no pisar algo que el user pueda haber cambiado a mano.

    `tab_name` permite apuntar a una pestaña específica; por defecto la
    primera pestaña.
    """
    a1_range = f"'{tab_name}'!A1:ZZ1" if tab_name else "A1:ZZ1"
    write_range = f"'{tab_name}'!A1" if tab_name else "A1"
    try:
        got = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=a1_range,
        ).execute()
        current = got.get("values", [[]])[0] if got.get("values") else []
    except HttpError as e:
        logger.warning(f"[SYNC] No pude leer headers de {spreadsheet_id}: {e}")
        return

    if not current:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=write_range,
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
        logger.info(f"[SYNC] Headers nuevos escritos en {spreadsheet_id} ({tab_name or 'primera tab'})")
        return

    if current == headers:
        return  # ya está alineado

    # Prefix? si current es prefix de headers, extender.
    if len(current) < len(headers) and headers[: len(current)] == current:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=write_range,
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
        logger.info(
            f"[SYNC] Headers extendidos en {spreadsheet_id} ({tab_name or 'primera tab'}): "
            f"+{len(headers) - len(current)} cols"
        )
        return

    # Incompatible — no pisar
    logger.warning(
        f"[SYNC] Headers actuales en {spreadsheet_id} ({tab_name or 'primera tab'}) "
        f"no coinciden con schema esperado. Actual={current[:3]}... "
        f"Esperado={headers[:3]}... — deja como está."
    )


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

def _enrich_cells(t: dict) -> list[Any]:
    """Devuelve las 10 celdas de enrichment en orden ENRICH_COLS. Vacío → ''."""
    return [
        t.get("entry_price", ""),
        t.get("hold_seconds", ""),
        t.get("vix_snapshot", ""),
        t.get("vix_bucket", ""),
        t.get("spy_ret_5d", ""),
        t.get("session_bucket", ""),
        t.get("slippage_bps", ""),
        t.get("trade_number_today", ""),
        (t.get("entry_reason", "") or "")[:500],
        (t.get("exit_reason", "") or "")[:500],
    ]


def _row_v1(doc_id: str, t: dict) -> list[Any]:
    """Mapea el trade de Firestore (colección eolo-trades) al schema unificado.

    Campos de Firestore (bot_trader._log_trade v1):
        timestamp, mode, action, ticker, shares, price, total_usd,
        strategy, pnl_usd, timeframe, reason
        + enrichment (Phase 1): entry_price, hold_seconds, vix_snapshot,
          vix_bucket, spy_ret_5d, session_bucket, slippage_bps,
          trade_number_today, entry_reason, exit_reason
    """
    return [
        doc_id,
        t.get("timestamp", ""),          # Time Stamp
        t.get("mode", ""),
        t.get("action", ""),             # side (BUY/SELL)
        t.get("ticker", ""),             # symbol
        t.get("shares", ""),             # qty
        t.get("price", ""),
        t.get("total_usd", ""),          # notional
        t.get("strategy", ""),
        t.get("pnl_usd", ""),
        (t.get("reason", "") or "")[:500],
        t.get("timeframe", ""),
    ] + _enrich_cells(t)


def _row_v2(doc_id: str, t: dict) -> list[Any]:
    """Schema de opciones: mantiene columnas específicas (option_type, expiration,
    strike, occ_symbol) pero con nombres base alineados (Time Stamp, side, symbol).
    """
    return [
        doc_id,
        t.get("timestamp", ""),           # Time Stamp
        t.get("mode", ""),
        t.get("action", ""),              # side
        t.get("ticker", ""),              # symbol subyacente
        t.get("option_type", ""),
        t.get("expiration", ""),
        t.get("strike", ""),
        t.get("contracts", ""),           # qty
        t.get("limit_price", ""),         # price
        t.get("total_est", ""),           # notional
        t.get("symbol", ""),              # occ_symbol (OCC format)
        t.get("strategy", ""),
        t.get("pnl_usd", ""),
        t.get("pnl_pct", ""),
        (t.get("reason", "") or "")[:500],
        t.get("order_id", ""),
        t.get("timeframe", ""),
    ] + _enrich_cells(t)


def _row_crypto(doc_id: str, t: dict) -> list[Any]:
    """Crypto trade → schema unificado. El campo `ts` en Firestore es epoch float
    (time.time()) y lo convertimos a ISO-8601 UTC. `pnl_usdt` se renombra a
    `pnl_usd` en la sheet — son equivalentes (USDT ≈ USD 1:1).
    """
    ts = t.get("ts", 0)
    try:
        ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        ts_iso = ""
    return [
        doc_id,
        ts_iso,                          # Time Stamp
        t.get("mode", ""),
        t.get("side", ""),
        t.get("symbol", ""),
        t.get("qty", ""),
        t.get("price", ""),
        t.get("notional", ""),
        t.get("strategy", ""),
        t.get("pnl_usdt", ""),           # pnl_usd (USDT ≈ USD)
        (t.get("reason", "") or "")[:500],
        t.get("timeframe", ""),
    ] + _enrich_cells(t)


def _row_all(bot: str, doc_id: str, t: dict) -> list[Any]:
    """Schema normalizado para la sheet 'Eolo_All_Trades' (15 columnas).
    Orden: doc_id | Time Stamp | bot | mode | side | symbol | instrument |
           qty | price | notional | strategy | pnl_usd | reason | timeframe | option_meta
    """
    if bot == "v1":
        ts       = t.get("timestamp", "")
        side     = t.get("action", "")
        symbol   = t.get("ticker", "")
        qty      = t.get("shares", "")
        price    = t.get("price", "")
        notional = t.get("total_usd", "")
        inst     = "stock"
        opt_meta = ""
        pnl      = t.get("pnl_usd", "")
        reason   = (t.get("reason", "") or "")[:300]
        tf       = t.get("timeframe", "")
    elif bot == "v2":
        ts       = t.get("timestamp", "")
        side     = t.get("action", "")
        symbol   = t.get("ticker", "")  # subyacente
        qty      = t.get("contracts", "")
        price    = t.get("limit_price", "")
        notional = t.get("total_est", "")
        inst     = "option"
        opt_meta = json.dumps({
            "option_type": t.get("option_type", ""),
            "expiration":  t.get("expiration", ""),
            "strike":      t.get("strike", ""),
            "occ_symbol":  t.get("symbol", ""),
        })
        pnl      = t.get("pnl_usd", "")
        reason   = (t.get("reason", "") or "")[:300]
        tf       = t.get("timeframe", "")
    else:  # crypto
        try:
            ts = datetime.fromtimestamp(float(t.get("ts", 0)), tz=timezone.utc).isoformat()
        except Exception:
            ts = ""
        side     = t.get("side", "")
        symbol   = t.get("symbol", "")
        qty      = t.get("qty", "")
        price    = t.get("price", "")
        notional = t.get("notional", "")
        inst     = "spot-crypto"
        opt_meta = ""
        pnl      = t.get("pnl_usdt", "")
        reason   = (t.get("reason", "") or "")[:300]
        tf       = t.get("timeframe", "")

    return [
        doc_id, ts, bot, t.get("mode", ""), side, symbol,
        inst, qty, price, notional, t.get("strategy", ""),
        pnl, reason, tf, opt_meta,
    ] + _enrich_cells(t)


# ── Análisis por estrategia (pestaña Strategy_Stats) ──────
#
# Cada vez que corre un sync, leemos TODO el histórico de la pestaña principal
# de trades (no sólo la ventana LOOKBACK_DAYS) y agregamos por estrategia:
#   - counts: executions / buys / sells / wins / losses / breakeven
#   - performance: total_pnl / avg_pnl / best / worst / win_rate
#   - meta: timeframes usados, tickers tocados, primer/último trade
#
# Para v1, donde el pnl por trade puede no estar en los rows viejos (anterior
# a la task #38), computamos PnL vía FIFO (ticker, strategy): cada BUY encola
# qty+entry_price, cada SELL consume esa cola y realiza PnL.

def _first_tab_name(sheets, spreadsheet_id: str) -> str:
    """Devuelve el nombre de la primera pestaña (index=0) — allí viven los trades.
    Cae a 'Sheet1' si no puede leer metadata."""
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,index))",
        ).execute()
        tabs = sorted(meta.get("sheets", []),
                      key=lambda s: s["properties"].get("index", 0))
        if tabs:
            return tabs[0]["properties"]["title"]
    except HttpError as e:
        logger.warning(f"[STATS] No pude leer tabs de {spreadsheet_id}: {e}")
    return "Sheet1"


def _ensure_tab(sheets, spreadsheet_id: str, tab_name: str) -> bool:
    """Garantiza que exista una pestaña con `tab_name`. La crea si falta.
    Retorna True si existe (al final), False si falló."""
    try:
        meta = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties(title))",
        ).execute()
        for sh in meta.get("sheets", []):
            if sh["properties"].get("title") == tab_name:
                return True
    except HttpError as e:
        logger.error(f"[STATS] Metadata falló en {spreadsheet_id}: {e}")
        return False
    try:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        logger.info(f"[STATS] Tab '{tab_name}' creado en {spreadsheet_id}")
        return True
    except HttpError as e:
        logger.error(f"[STATS] No pude crear tab '{tab_name}': {e}")
        return False


def _read_trades_rows(sheets, spreadsheet_id: str) -> tuple[list[str], list[list[str]]]:
    """Lee (headers, rows) de la primera pestaña del spreadsheet."""
    tab = _first_tab_name(sheets, spreadsheet_id)
    try:
        got = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:Z",
        ).execute()
    except HttpError as e:
        logger.warning(f"[STATS] Lectura de '{tab}' falló en {spreadsheet_id}: {e}")
        return [], []
    values = got.get("values", [])
    if not values:
        return [], []
    return values[0], values[1:]


def _float_or_none(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _load_enabled_strategies(bot: str) -> dict[str, bool]:
    """Lee de Firestore el mapa `{strategy_key: enabled_bool}` del bot dado.

    Para bot='all' combina v1+v2+crypto (union; si alguna está habilitada en
    cualquier bot, queda True en el agregado). Retorna {} si no hay config.
    """
    if bot == "all":
        merged: dict[str, bool] = {}
        for b in ("v1", "v2", "crypto"):
            for k, v in _load_enabled_strategies(b).items():
                merged[k] = bool(v) or merged.get(k, False)
        return merged

    cfg = STRATEGIES_CONFIG.get(bot)
    if not cfg:
        return {}
    coll, doc_id, field = cfg
    try:
        doc = db().collection(coll).document(doc_id).get()
        if not doc.exists:
            logger.info(f"[STATS] Sin config de strategies para {bot} ({coll}/{doc_id})")
            return {}
        data = doc.to_dict() or {}
        if field:
            data = data.get(field) or {}
        if not isinstance(data, dict):
            return {}
        return {str(k): bool(v) for k, v in data.items()}
    except Exception as e:
        logger.warning(f"[STATS] No pude leer strategies de {bot}: {e}")
        return {}


def _compute_strategy_stats(
    headers: list[str], rows: list[list[str]], bot: str,
    known_enabled: dict[str, bool] | None = None,
    rules: dict | None = None,
    history: dict | None = None,
) -> tuple[list[list[Any]], list[dict]]:
    """Agrega los trades por estrategia. Retorna (filas HEADERS_STATS, verdict_info).

    Maneja múltiples variantes de nombres de columna (Time Stamp vs timestamp,
    side vs action, qty vs shares vs contracts, pnl_usd vs pnl_usdt) para que
    funcione tanto con sheets nuevos como legacy.

    Para bot='v1': si el row no trae pnl_usd (trades pre-#38), computamos pnl
    realizado por FIFO emparejando BUYs con SELLs dentro de (ticker, strategy).

    Phase 3: si se provee `rules` y `history`, usa motor configurable + cooldown.
    `history` se muta in-place: la entrada `{bot}::{strategy}` queda actualizada
    con {verdict, pending_verdict, pending_streak, last_sync_ts}. El caller es
    responsable de persistirla a Firestore una sola vez al final del sync.

    `verdict_info` es una lista de dicts por estrategia con forma:
        {strategy, raw, final, previous, pending, pending_streak,
         trades, total_pnl, profit_factor, win_rate}
    usada downstream por el Daily_Recap generator.
    """
    idx = {h: i for i, h in enumerate(headers)}

    def _get(row, *keys, default=""):
        for k in keys:
            i = idx.get(k)
            if i is not None and i < len(row):
                v = row[i]
                if v != "" and v is not None:
                    return v
        return default

    ts_keys      = ("Time Stamp", "timestamp", "ts_iso", "ts_utc")
    side_keys    = ("side", "action")
    symbol_keys  = ("symbol", "ticker")
    qty_keys     = ("qty", "shares", "contracts")
    price_keys   = ("price", "limit_price")
    pnl_keys     = ("pnl_usd", "pnl_usdt")
    tf_keys      = ("timeframe",)

    def _ts(row) -> str:
        return str(_get(row, *ts_keys))

    # Orden cronológico para FIFO determinista
    rows_sorted = sorted(rows, key=_ts)

    # Aggregator por estrategia
    agg: dict[str, dict] = defaultdict(lambda: {
        "executions": 0, "buys": 0, "sells": 0,
        "wins": 0, "losses": 0, "breakeven": 0,
        "pnls": [],
        "holds": [],              # hold_seconds por SELL (para avg_hold)
        "first_seen": "", "last_seen": "",
        "timeframes": set(), "tickers": set(),
    })
    # Colas de posición (ticker, strategy) → [[qty, entry_price], ...] para FIFO v1
    pos_queue: dict[tuple[str, str], list[list[float]]] = defaultdict(list)

    hold_keys = ("hold_seconds",)

    for row in rows_sorted:
        strategy = str(_get(row, "strategy"))
        if not strategy:
            continue

        side   = str(_get(row, *side_keys)).upper()
        ticker = str(_get(row, *symbol_keys))
        qty    = _float_or_none(_get(row, *qty_keys)) or 0.0
        price  = _float_or_none(_get(row, *price_keys)) or 0.0
        pnl    = _float_or_none(_get(row, *pnl_keys))
        tf     = str(_get(row, *tf_keys))
        ts     = _ts(row)
        hold   = _float_or_none(_get(row, *hold_keys))

        s = agg[strategy]
        s["executions"] += 1
        if ts:
            if not s["first_seen"] or ts < s["first_seen"]:
                s["first_seen"] = ts
            if not s["last_seen"] or ts > s["last_seen"]:
                s["last_seen"] = ts
        if tf:
            s["timeframes"].add(tf)
        if ticker:
            s["tickers"].add(ticker)

        if side == "BUY":
            s["buys"] += 1
            if bot == "v1" and qty > 0 and price > 0:
                pos_queue[(ticker, strategy)].append([qty, price])
        elif side == "SELL":
            s["sells"] += 1

            # Fallback FIFO para v1 trades viejos sin pnl_usd
            if pnl is None and bot == "v1" and qty > 0 and price > 0:
                q = pos_queue[(ticker, strategy)]
                remaining = qty
                realized  = 0.0
                matched   = 0.0
                while remaining > 0 and q:
                    lot_qty, lot_entry = q[0]
                    take = min(lot_qty, remaining)
                    realized += (price - lot_entry) * take
                    matched  += take
                    lot_qty  -= take
                    remaining -= take
                    if lot_qty <= 0:
                        q.pop(0)
                    else:
                        q[0][0] = lot_qty
                # Sólo contamos como realizado si emparejamos todo el SELL
                if matched > 0 and remaining == 0:
                    pnl = round(realized, 2)

            if pnl is not None:
                s["pnls"].append(pnl)
                if pnl > 0:
                    s["wins"] += 1
                elif pnl < 0:
                    s["losses"] += 1
                else:
                    s["breakeven"] += 1
            # hold_seconds se reporta en el SELL (cuando build_enrichment lo
            # calcula desde opened_at_ts). Lo acumulamos para avg_hold.
            if hold is not None and hold > 0:
                s["holds"].append(hold)

    # Union con estrategias habilitadas en Firestore (incluso sin trades)
    known_enabled = known_enabled or {}
    all_strategies = set(agg.keys()) | set(known_enabled.keys())

    # Formateo a filas (estrategias sin trades aparecen con 0s — útil para
    # detectar estrategias habilitadas que no están activando señales).
    out: list[list[Any]] = []
    verdict_info: list[dict] = []
    for strategy in sorted(all_strategies):
        s = agg.get(strategy) or {
            "executions": 0, "buys": 0, "sells": 0,
            "wins": 0, "losses": 0, "breakeven": 0,
            "pnls": [], "holds": [], "first_seen": "", "last_seen": "",
            "timeframes": set(), "tickers": set(),
        }
        pnls = s["pnls"]
        total    = round(sum(pnls), 2) if pnls else ""
        avg      = round(sum(pnls) / len(pnls), 2) if pnls else ""
        best     = round(max(pnls), 2) if pnls else ""
        worst    = round(min(pnls), 2) if pnls else ""
        decisive = s["wins"] + s["losses"]
        wr       = round(s["wins"] / decisive, 4) if decisive else ""

        # ── Métricas avanzadas (Phase 2 — 2026-04-21) ──
        # avg_hold_seconds: promedio de hold entre SELLs que trajeron hold_seconds.
        holds       = s.get("holds", [])
        avg_hold    = round(sum(holds) / len(holds), 1) if holds else ""

        # profit_factor = sum(wins) / |sum(losses)|. Undefined si no hay pérdidas.
        gross_win  = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        if gross_loss > 0:
            profit_factor = round(gross_win / gross_loss, 2)
        elif gross_win > 0:
            profit_factor = "inf"   # ninguna pérdida registrada
        else:
            profit_factor = ""

        # expectancy $ per trade = avg pnl * (win_rate estimada). Acá usamos
        # la forma estándar: avg(win) * wr - avg(loss) * (1-wr).
        if s["wins"] and s["losses"]:
            avg_win_dollar  = gross_win  / s["wins"]
            avg_loss_dollar = gross_loss / s["losses"]
            wr_num = s["wins"] / decisive  # decisive > 0 si hay wins y losses
            expectancy = round(avg_win_dollar * wr_num - avg_loss_dollar * (1.0 - wr_num), 2)
        elif pnls:
            # Todo wins o todo losses — expectancy ≈ avg realizado
            expectancy = round(sum(pnls) / len(pnls), 2)
        else:
            expectancy = ""

        # Sharpe simple: mean(pnl) / std(pnl). Sin anualizar — por trade.
        if len(pnls) >= 2:
            import statistics
            try:
                mu = statistics.mean(pnls)
                sd = statistics.stdev(pnls)
                sharpe = round(mu / sd, 2) if sd > 0 else ""
            except statistics.StatisticsError:
                sharpe = ""
        else:
            sharpe = ""

        # ── Verdict (Phase 3) ─────────────────────────────
        # Si el caller pasa `rules`, usamos motor configurable; si además pasa
        # `history`, aplicamos cooldown sobre transiciones strong. Fallback al
        # heurístico hardcoded para backwards compat cuando no hay rules.
        wr_num  = wr if isinstance(wr, (int, float)) else None
        pf_num  = profit_factor if isinstance(profit_factor, (int, float)) else None
        exp_num = expectancy if isinstance(expectancy, (int, float)) else None

        if rules is not None:
            raw_v = _rule_engine_verdict(
                trades=len(pnls),
                win_rate=wr_num, profit_factor=pf_num, expectancy=exp_num,
                rules_cfg=rules,
            )
        else:
            raw_v = _heuristic_verdict(
                trades=len(pnls),
                win_rate=wr_num, profit_factor=pf_num, expectancy=exp_num,
            )

        prev_v: str | None = None
        pending_v: str | None = None
        streak_v: int = 0
        if rules is not None and history is not None:
            key_h = f"{bot}::{strategy}"
            prev_v = (history.get(key_h) or {}).get("verdict")
            final_v, updated_entry, pending_v, streak_v = _apply_cooldown(
                bot=bot, strategy=strategy, raw_verdict=raw_v,
                history=history,
                cooldown_syncs=int(rules.get("cooldown_syncs", 1) or 1),
                strong_transitions=list(rules.get("strong_transitions") or []),
            )
            history[key_h] = updated_entry
        else:
            final_v = raw_v

        verdict = final_v

        verdict_info.append({
            "strategy": strategy,
            "raw": raw_v,
            "final": final_v,
            "previous": prev_v,
            "pending": pending_v,
            "pending_streak": streak_v,
            "trades": len(pnls),
            "total_pnl": total if isinstance(total, (int, float)) else 0.0,
            "profit_factor": pf_num,
            "win_rate": wr_num,
            "expectancy": exp_num,
        })

        # enabled: True/False si la estrategia está en la config; en blanco si
        # aparece en trades pero no figura en la config (legacy / renombrada).
        if strategy in known_enabled:
            enabled_cell: Any = bool(known_enabled[strategy])
        else:
            enabled_cell = ""

        out.append([
            strategy, enabled_cell,
            s["executions"], s["buys"], s["sells"],
            s["wins"], s["losses"], s["breakeven"], wr,
            total, avg, best, worst,
            avg_hold, profit_factor, expectancy, sharpe,
            verdict,
            s["first_seen"], s["last_seen"],
            ",".join(sorted(s["timeframes"])),
            ",".join(sorted(s["tickers"])),
        ])
    return out, verdict_info


# ── Phase 3 — Verdict engine configurable ────────────────
#
# El engine reemplaza _heuristic_verdict por reglas evaluadas en orden, cargadas
# desde Firestore. Se mantiene _heuristic_verdict como fallback de backwards
# compat si _compute_strategy_stats se llama sin `rules`.

def _load_verdict_rules() -> dict:
    """Lee verdict_rules de Firestore y lo mergea sobre DEFAULT_VERDICT_RULES.
    Shallow merge — keys presentes en el doc reemplazan las defaults."""
    try:
        doc = db().collection(SYNC_STATE_COLLECTION).document(VERDICT_RULES_DOC).get()
        cfg = doc.to_dict() if doc.exists else {}
    except Exception as e:
        logger.warning(f"[VERDICT] _load_verdict_rules falló: {e}")
        cfg = {}
    merged = {k: v for k, v in DEFAULT_VERDICT_RULES.items()}
    for k, v in (cfg or {}).items():
        merged[k] = v
    return merged


def _load_verdict_history() -> dict:
    """Retorna dict global {bot::strategy: {verdict, pending_verdict,
    pending_streak, last_sync_ts}} desde Firestore."""
    try:
        doc = db().collection(SYNC_STATE_COLLECTION).document(VERDICT_HISTORY_DOC).get()
        data = doc.to_dict() if doc.exists else {}
        return dict(data or {})
    except Exception as e:
        logger.warning(f"[VERDICT] _load_verdict_history falló: {e}")
        return {}


def _save_verdict_history(history: dict) -> None:
    """Persiste el map completo. Se llama UNA sola vez por sync."""
    try:
        db().collection(SYNC_STATE_COLLECTION).document(VERDICT_HISTORY_DOC).set(
            history, merge=False,
        )
    except Exception as e:
        logger.warning(f"[VERDICT] _save_verdict_history falló: {e}")


def _rule_engine_verdict(
    trades: int,
    win_rate: float | None,
    profit_factor: float | None,
    expectancy: float | None,
    rules_cfg: dict,
) -> str:
    """Evalúa las reglas del engine en orden y retorna el primer match.

    Convención de thresholds:
      - max_X: la métrica existe AND métrica < X (estricto)
      - min_X: la métrica existe AND métrica >= X (no estricto)
    Una regla con múltiples condiciones las requiere TODAS (AND).
    Si una métrica es None, la condición que la refiera NO matchea (falla).
    """
    n = trades or 0
    if n < int(rules_cfg.get("insufficient_data_threshold", 10) or 0):
        return "insufficient_data"

    pf  = profit_factor if isinstance(profit_factor, (int, float)) else None
    wr  = win_rate     if isinstance(win_rate,     (int, float)) else None
    exp = expectancy   if isinstance(expectancy,   (int, float)) else None

    for rule in rules_cfg.get("rules") or []:
        when = rule.get("when") or {}
        ok = True
        if "max_pf"  in when and (pf  is None or pf  >= float(when["max_pf"])):   ok = False
        if ok and "min_pf"  in when and (pf  is None or pf  <  float(when["min_pf"])):   ok = False
        if ok and "max_wr"  in when and (wr  is None or wr  >= float(when["max_wr"])):   ok = False
        if ok and "min_wr"  in when and (wr  is None or wr  <  float(when["min_wr"])):   ok = False
        if ok and "max_exp" in when and (exp is None or exp >= float(when["max_exp"])):  ok = False
        if ok and "min_exp" in when and (exp is None or exp <  float(when["min_exp"])):  ok = False
        if ok:
            return str(rule.get("verdict", "watch"))
    return str(rules_cfg.get("default_verdict", "watch"))


def _apply_cooldown(
    bot: str, strategy: str,
    raw_verdict: str,
    history: dict,
    cooldown_syncs: int,
    strong_transitions: list[str],
) -> tuple[str, dict, str | None, int]:
    """Aplica cooldown a transiciones 'strong'. Retorna:
        (final_verdict, updated_entry_dict, pending_verdict, pending_streak)

    Semántica:
      - Primer sync: adoptar raw directo.
      - raw == prev: sin cambio, reset pending.
      - raw != prev & transición débil (ni raw ni prev en strong_transitions):
        flip inmediato.
      - raw != prev & transición strong: acumular streak. Si raw se mantiene
        por `cooldown_syncs` ciclos consecutivos → promover; si raw cambia
        en el medio, reset al nuevo candidato. Mientras tanto el veredicto
        reportado sigue siendo `prev`.
    """
    key_h = f"{bot}::{strategy}"
    entry = dict(history.get(key_h) or {})
    prev_verdict = entry.get("verdict")  # None si no hay historia
    now_iso = datetime.now(timezone.utc).isoformat()

    # Primer sync: adoptar raw sin cooldown.
    if not prev_verdict:
        return raw_verdict, {
            "verdict": raw_verdict,
            "pending_verdict": None,
            "pending_streak": 0,
            "last_sync_ts": now_iso,
        }, None, 0

    # Sin cambio: reset pending.
    if raw_verdict == prev_verdict:
        return prev_verdict, {
            "verdict": prev_verdict,
            "pending_verdict": None,
            "pending_streak": 0,
            "last_sync_ts": now_iso,
        }, None, 0

    strong_set = set(strong_transitions or [])
    is_strong = (raw_verdict in strong_set) or (prev_verdict in strong_set)
    if not is_strong or int(cooldown_syncs) <= 1:
        # Transición débil o cooldown desactivado → flip inmediato.
        return raw_verdict, {
            "verdict": raw_verdict,
            "pending_verdict": None,
            "pending_streak": 0,
            "last_sync_ts": now_iso,
        }, None, 0

    # Transición strong — acumular streak.
    pending = entry.get("pending_verdict")
    streak  = int(entry.get("pending_streak") or 0)
    if pending == raw_verdict:
        streak += 1
    else:
        pending = raw_verdict
        streak  = 1

    if streak >= int(cooldown_syncs):
        # Promoción: el nuevo veredicto aguantó N ciclos.
        return raw_verdict, {
            "verdict": raw_verdict,
            "pending_verdict": None,
            "pending_streak": 0,
            "last_sync_ts": now_iso,
        }, None, 0

    # Aún pending: reportamos prev y guardamos streak.
    return prev_verdict, {
        "verdict": prev_verdict,
        "pending_verdict": pending,
        "pending_streak": streak,
        "last_sync_ts": now_iso,
    }, pending, streak


def _heuristic_verdict(trades: int,
                       win_rate: float | None,
                       profit_factor: float | None,
                       expectancy: float | None) -> str:
    """
    Fallback heurístico — se mantiene por backwards compat. El motor
    configurable (`_rule_engine_verdict`) es ahora la ruta principal.

    Reglas (en orden de precedencia):
        - trades < 10      → "insufficient_data"
        - pf < 0.8 y exp<0 → "kill"
        - pf < 1.0         → "tune"
        - pf >= 1.5 y wr>=0.55 → "keep"
        - default          → "watch"
    """
    if trades is None or trades < 10:
        return "insufficient_data"
    if profit_factor is not None and profit_factor < 0.8 and (expectancy or 0) < 0:
        return "kill"
    if profit_factor is not None and profit_factor < 1.0:
        return "tune"
    if (profit_factor is not None and profit_factor >= 1.5
        and win_rate is not None and win_rate >= 0.55):
        return "keep"
    return "watch"


def _write_stats_tab(
    sheets, spreadsheet_id: str, stats_rows: list[list[Any]],
) -> None:
    """Borra Strategy_Stats y re-escribe headers + rows. Idempotente."""
    if not _ensure_tab(sheets, spreadsheet_id, STATS_TAB):
        return
    try:
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{STATS_TAB}'!A:Z",
        ).execute()
    except HttpError as e:
        logger.warning(f"[STATS] Clear de {STATS_TAB} falló: {e}")
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{STATS_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS_STATS] + stats_rows},
        ).execute()
        logger.info(
            f"[STATS] {STATS_TAB} actualizado en {spreadsheet_id}: "
            f"{len(stats_rows)} estrategias"
        )
    except HttpError as e:
        logger.error(f"[STATS] Write de {STATS_TAB} falló: {e}")


def _sync_stats_for(
    sheets, spreadsheet_id: str, bot: str,
    rules: dict | None = None,
    history: dict | None = None,
) -> dict:
    """Lee la pestaña principal + la config de strategies en Firestore + escribe
    stats. Retorna {count, verdicts}. `history` se muta in-place con cualquier
    cambio; el caller persiste una sola vez al final del sync.
    """
    headers, rows = _read_trades_rows(sheets, spreadsheet_id)
    known_enabled = _load_enabled_strategies(bot)
    if not headers and not known_enabled:
        logger.info(f"[STATS] {spreadsheet_id} sin data ni config — skip stats")
        return {"count": 0, "verdicts": []}
    stats, verdict_info = _compute_strategy_stats(
        headers, rows, bot, known_enabled,
        rules=rules, history=history,
    )
    _write_stats_tab(sheets, spreadsheet_id, stats)
    return {"count": len(stats), "verdicts": verdict_info}


# ============================================================
# Phase 2 (2026-04-21) — Pestañas analíticas avanzadas
#
# Las pestañas Completed_Trades / Strategy_x_Ticker / Daily_Summary /
# Regime_Performance se re-computan from scratch en cada sync leyendo
# la pestaña principal del sheet. No persisten IDs sincronizados.
#
# Fuente: primera pestaña del sheet (trades raw, incluye ambas BUYs y SELLs).
# ============================================================

def _row_to_trade_dict(headers: list[str], row: list[str], bot: str) -> dict:
    """Convierte un row de sheets (lista plana) a dict con claves normalizadas.

    Claves canónicas producidas:
        ts (ISO), side ('BUY'|'SELL'), symbol, qty (float), price (float),
        pnl_usd (float|None), strategy, timeframe, entry_price, hold_seconds,
        vix_snapshot, vix_bucket, spy_ret_5d, session_bucket, slippage_bps,
        trade_number_today, entry_reason, exit_reason, mode
    """
    idx = {h: i for i, h in enumerate(headers)}

    def _get(*keys, default=""):
        for k in keys:
            i = idx.get(k)
            if i is not None and i < len(row):
                v = row[i]
                if v != "" and v is not None:
                    return v
        return default

    side = str(_get("side", "action")).upper()
    return {
        "ts":                 str(_get("Time Stamp", "timestamp")),
        "side":               side,
        "symbol":             str(_get("symbol", "ticker")),
        "qty":                _float_or_none(_get("qty", "shares", "contracts")) or 0.0,
        "price":              _float_or_none(_get("price", "limit_price")) or 0.0,
        "pnl_usd":            _float_or_none(_get("pnl_usd", "pnl_usdt")),
        "pnl_pct":            _float_or_none(_get("pnl_pct")),
        "strategy":           str(_get("strategy")),
        "timeframe":          str(_get("timeframe")),
        "mode":               str(_get("mode")),
        # Enrichment
        "entry_price":        _float_or_none(_get("entry_price")),
        "hold_seconds":       _float_or_none(_get("hold_seconds")),
        "vix_snapshot":       _float_or_none(_get("vix_snapshot")),
        "vix_bucket":         str(_get("vix_bucket")),
        "spy_ret_5d":         _float_or_none(_get("spy_ret_5d")),
        "session_bucket":     str(_get("session_bucket")),
        "slippage_bps":       _float_or_none(_get("slippage_bps")),
        "trade_number_today": _float_or_none(_get("trade_number_today")),
        "entry_reason":       str(_get("entry_reason")),
        "exit_reason":        str(_get("exit_reason")),
        "bot":                bot,
    }


def _pair_completed_trades(trades: list[dict]) -> list[dict]:
    """Empareja BUYs con SELLs por (symbol, strategy) en orden cronológico (FIFO).

    Cada SELL consume la cola de BUYs pendientes del mismo (symbol, strategy).
    Si un SELL cubre más qty que la disponible, se para en lo que hay. Cada par
    match produce un dict con entry/exit y metadata combinada.

    Edge cases:
    - BUY sin SELL posterior → queda abierta, no aparece en Completed_Trades.
    - SELL sin BUY previo → se skipea (dust / posición pre-existente).
    - pnl_usd: si el SELL trae pnl_usd explícito (v1 ≥ task #38, crypto, v2)
      se respeta prorrateado por qty matched/total; si no, se computa como
      (exit_price - entry_price) * qty_match.
    """
    # Ordenar por ts ISO — lexicográficamente funciona para YYYY-MM-DD HH:MM:SS
    # y ISO-8601, que es como Firestore los devuelve.
    rows = sorted(trades, key=lambda r: r.get("ts", ""))

    queues: dict[tuple[str, str], list[dict]] = defaultdict(list)
    completed: list[dict] = []

    for r in rows:
        side = r.get("side", "")
        sym  = r.get("symbol", "")
        strat = r.get("strategy", "")
        if not sym or not strat:
            continue
        key = (sym, strat)

        if side == "BUY":
            if r.get("qty", 0) > 0 and r.get("price", 0) > 0:
                queues[key].append({
                    "entry_ts":          r.get("ts", ""),
                    "entry_price":       r.get("price", 0.0),
                    "qty_remaining":     r.get("qty", 0.0),
                    "qty_original":      r.get("qty", 0.0),
                    "session_bucket":    r.get("session_bucket", ""),
                    "vix_bucket":        r.get("vix_bucket", ""),
                    "slippage_bps":      r.get("slippage_bps"),
                    "entry_reason":      r.get("entry_reason") or r.get("exit_reason", ""),
                    "timeframe":         r.get("timeframe", ""),
                    "bot":               r.get("bot", ""),
                })
        elif side == "SELL":
            q = queues[key]
            remaining = r.get("qty", 0.0)
            exit_price = r.get("price", 0.0)
            total_pnl_sell = r.get("pnl_usd")  # puede ser None

            while remaining > 0 and q:
                lot = q[0]
                take = min(lot["qty_remaining"], remaining)
                if take <= 0:
                    q.pop(0)
                    continue

                entry_price = lot["entry_price"]
                # hold_seconds: preferir el valor que trajo el SELL (más preciso
                # porque el bot lo calcula como now() - opened_at_ts).
                # Si no, calcular del ISO ts.
                hold_sec = r.get("hold_seconds")
                if hold_sec is None:
                    hold_sec = _hold_seconds_from_iso(lot["entry_ts"], r.get("ts", ""))

                if total_pnl_sell is not None and lot["qty_original"] > 0:
                    # Prorratear el pnl del SELL por fracción de qty que
                    # cubrimos desde este lot.
                    pnl_usd = total_pnl_sell * (take / r.get("qty", 1.0))
                else:
                    pnl_usd = (exit_price - entry_price) * take

                pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) \
                          if entry_price else None

                completed.append({
                    "bot":                 r.get("bot", lot["bot"]),
                    "strategy":            strat,
                    "symbol":              sym,
                    "entry_ts":            lot["entry_ts"],
                    "exit_ts":             r.get("ts", ""),
                    "hold_seconds":        round(hold_sec, 1) if hold_sec is not None else "",
                    "qty":                 round(take, 8),
                    "entry_price":         round(entry_price, 6),
                    "exit_price":          round(exit_price, 6),
                    "pnl_usd":             round(pnl_usd, 2),
                    "pnl_pct":             round(pnl_pct, 2) if pnl_pct is not None else "",
                    "session_bucket_entry": lot["session_bucket"],
                    "session_bucket_exit":  r.get("session_bucket", ""),
                    "vix_bucket_entry":     lot["vix_bucket"],
                    "vix_bucket_exit":      r.get("vix_bucket", ""),
                    "slippage_bps_entry":   lot["slippage_bps"] if lot["slippage_bps"] is not None else "",
                    "slippage_bps_exit":    r.get("slippage_bps") if r.get("slippage_bps") is not None else "",
                    "entry_reason":        (lot["entry_reason"] or "")[:300],
                    "exit_reason":         (r.get("exit_reason") or r.get("entry_reason") or "")[:300],
                    "timeframe":           lot["timeframe"] or r.get("timeframe", ""),
                })

                lot["qty_remaining"] -= take
                remaining -= take
                if lot["qty_remaining"] <= 1e-9:
                    q.pop(0)

    return completed


def _hold_seconds_from_iso(entry_ts: str, exit_ts: str) -> float | None:
    """Diferencia en segundos entre dos timestamps ISO o 'YYYY-MM-DD HH:MM:SS'.
    Retorna None si no puede parsear."""
    if not entry_ts or not exit_ts:
        return None
    fmts = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",   "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S")
    def _parse(s: str):
        for f in fmts:
            try:
                dt = datetime.strptime(s, f)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    a = _parse(entry_ts)
    b = _parse(exit_ts)
    if not a or not b:
        return None
    return max(0.0, (b - a).total_seconds())


def _completed_rows(completed: list[dict]) -> list[list[Any]]:
    """Convierte la lista de dicts de Completed_Trades al layout de HEADERS_COMPLETED."""
    out = []
    for c in sorted(completed, key=lambda x: x.get("exit_ts", "")):
        out.append([
            c["bot"], c["strategy"], c["symbol"],
            c["entry_ts"], c["exit_ts"], c["hold_seconds"],
            c["qty"], c["entry_price"], c["exit_price"],
            c["pnl_usd"], c["pnl_pct"],
            c["session_bucket_entry"], c["session_bucket_exit"],
            c["vix_bucket_entry"],    c["vix_bucket_exit"],
            c["slippage_bps_entry"],  c["slippage_bps_exit"],
            c["entry_reason"],        c["exit_reason"],
            c["timeframe"],
        ])
    return out


def _strategy_x_ticker_rows(trades: list[dict],
                             completed: list[dict]) -> list[list[Any]]:
    """Agrega por (strategy, symbol). Counts desde `trades`, pnl desde `completed`."""
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "trades": 0, "buys": 0, "sells": 0,
        "wins": 0, "losses": 0,
        "pnls": [],
        "first_seen": "", "last_seen": "",
    })

    for r in trades:
        strat, sym = r.get("strategy", ""), r.get("symbol", "")
        if not strat or not sym:
            continue
        a = agg[(strat, sym)]
        a["trades"] += 1
        if r.get("side") == "BUY":
            a["buys"] += 1
        elif r.get("side") == "SELL":
            a["sells"] += 1
        ts = r.get("ts", "")
        if ts:
            if not a["first_seen"] or ts < a["first_seen"]:
                a["first_seen"] = ts
            if not a["last_seen"] or ts > a["last_seen"]:
                a["last_seen"] = ts

    for c in completed:
        strat, sym = c.get("strategy", ""), c.get("symbol", "")
        a = agg[(strat, sym)]
        pnl = c.get("pnl_usd")
        if isinstance(pnl, (int, float)):
            a["pnls"].append(float(pnl))
            if pnl > 0:
                a["wins"] += 1
            elif pnl < 0:
                a["losses"] += 1

    out = []
    for (strat, sym), a in sorted(agg.items()):
        pnls = a["pnls"]
        total = round(sum(pnls), 2) if pnls else ""
        avg   = round(sum(pnls) / len(pnls), 2) if pnls else ""
        best  = round(max(pnls), 2) if pnls else ""
        worst = round(min(pnls), 2) if pnls else ""
        decisive = a["wins"] + a["losses"]
        wr    = round(a["wins"] / decisive, 4) if decisive else ""
        out.append([
            strat, sym, a["trades"], a["buys"], a["sells"],
            a["wins"], a["losses"], wr,
            total, avg, best, worst,
            a["first_seen"], a["last_seen"],
        ])
    return out


def _daily_rows(trades: list[dict], completed: list[dict]) -> list[list[Any]]:
    """Agrega por fecha (YYYY-MM-DD extraído del ts). completed para pnl real."""
    def _date_of(ts: str) -> str:
        if not ts:
            return ""
        # Soporta 'YYYY-MM-DD HH:MM:SS' y 'YYYY-MM-DDTHH:MM:SS...'
        return ts.replace("T", " ")[:10]

    agg: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "buys": 0, "sells": 0,
        "wins": 0, "losses": 0,
        "pnls": [],
        "tickers": set(), "strategies": set(),
    })

    for r in trades:
        d = _date_of(r.get("ts", ""))
        if not d:
            continue
        a = agg[d]
        a["trades"] += 1
        if r.get("side") == "BUY":
            a["buys"] += 1
        elif r.get("side") == "SELL":
            a["sells"] += 1
        if r.get("symbol"):
            a["tickers"].add(r["symbol"])
        if r.get("strategy"):
            a["strategies"].add(r["strategy"])

    for c in completed:
        d = _date_of(c.get("exit_ts", ""))
        if not d:
            continue
        a = agg[d]
        pnl = c.get("pnl_usd")
        if isinstance(pnl, (int, float)):
            a["pnls"].append(float(pnl))
            if pnl > 0:
                a["wins"] += 1
            elif pnl < 0:
                a["losses"] += 1

    out = []
    for d in sorted(agg.keys(), reverse=True):  # más recientes arriba
        a = agg[d]
        pnls = a["pnls"]
        total = round(sum(pnls), 2) if pnls else ""
        avg   = round(sum(pnls) / len(pnls), 2) if pnls else ""
        best  = round(max(pnls), 2) if pnls else ""
        worst = round(min(pnls), 2) if pnls else ""
        decisive = a["wins"] + a["losses"]
        wr    = round(a["wins"] / decisive, 4) if decisive else ""
        out.append([
            d, a["trades"], a["buys"], a["sells"],
            a["wins"], a["losses"], wr,
            total, avg, best, worst,
            ",".join(sorted(a["tickers"])),
            ",".join(sorted(a["strategies"])),
        ])
    return out


def _regime_rows(completed: list[dict]) -> list[list[Any]]:
    """Agrega performance por (vix_bucket, session_bucket). Solo usa completed."""
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "pnls": [], "holds": [],
    })
    for c in completed:
        vix = c.get("vix_bucket_exit") or c.get("vix_bucket_entry") or ""
        ses = c.get("session_bucket_exit") or c.get("session_bucket_entry") or ""
        key = (vix, ses)
        a = agg[key]
        a["trades"] += 1
        pnl = c.get("pnl_usd")
        if isinstance(pnl, (int, float)):
            a["pnls"].append(float(pnl))
            if pnl > 0:
                a["wins"] += 1
            elif pnl < 0:
                a["losses"] += 1
        h = c.get("hold_seconds")
        if isinstance(h, (int, float)):
            a["holds"].append(float(h))

    out = []
    for (vix, ses), a in sorted(agg.items()):
        pnls = a["pnls"]
        holds = a["holds"]
        total = round(sum(pnls), 2) if pnls else ""
        avg   = round(sum(pnls) / len(pnls), 2) if pnls else ""
        best  = round(max(pnls), 2) if pnls else ""
        worst = round(min(pnls), 2) if pnls else ""
        decisive = a["wins"] + a["losses"]
        wr    = round(a["wins"] / decisive, 4) if decisive else ""
        avg_hold = round(sum(holds) / len(holds), 1) if holds else ""
        out.append([
            vix or "(n/a)", ses or "(n/a)",
            a["trades"], a["wins"], a["losses"], wr,
            total, avg, best, worst,
            avg_hold,
        ])
    return out


def _write_tab(sheets, spreadsheet_id: str, tab: str,
               headers: list[str], rows: list[list[Any]]) -> None:
    """Crea la pestaña si falta, clear, y escribe headers + rows."""
    if not _ensure_tab(sheets, spreadsheet_id, tab):
        return
    try:
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:ZZ",
        ).execute()
    except HttpError as e:
        logger.warning(f"[SYNC] Clear de {tab} falló: {e}")
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [headers] + rows},
        ).execute()
        logger.info(
            f"[SYNC] Tab '{tab}' actualizada en {spreadsheet_id}: {len(rows)} filas"
        )
    except HttpError as e:
        logger.error(f"[SYNC] Write de {tab} falló: {e}")


def _sync_analytics_tabs(sheets, spreadsheet_id: str, bot: str) -> dict:
    """Lee la pestaña principal y re-computa Completed_Trades, Strategy_x_Ticker,
    Daily_Summary, Regime_Performance. Retorna {counts, trades, completed}
    para que el Daily_Recap downstream pueda reutilizar la misma data sin
    re-leer el sheet."""
    headers, rows = _read_trades_rows(sheets, spreadsheet_id)
    if not headers:
        return {"counts": {}, "trades": [], "completed": []}

    trades = [_row_to_trade_dict(headers, r, bot) for r in rows]
    # Filtrar rows sin ts (probablemente headers mal pegados o rows vacíos)
    trades = [t for t in trades if t["ts"]]

    completed = _pair_completed_trades(trades)

    counts: dict = {}
    _write_tab(sheets, spreadsheet_id, COMPLETED_TRADES_TAB,
               HEADERS_COMPLETED, _completed_rows(completed))
    counts[COMPLETED_TRADES_TAB] = len(completed)

    sxt = _strategy_x_ticker_rows(trades, completed)
    _write_tab(sheets, spreadsheet_id, STRATEGY_X_TICKER_TAB,
               HEADERS_STRATEGY_X_TICKER, sxt)
    counts[STRATEGY_X_TICKER_TAB] = len(sxt)

    daily = _daily_rows(trades, completed)
    _write_tab(sheets, spreadsheet_id, DAILY_SUMMARY_TAB,
               HEADERS_DAILY, daily)
    counts[DAILY_SUMMARY_TAB] = len(daily)

    regime = _regime_rows(completed)
    _write_tab(sheets, spreadsheet_id, REGIME_PERFORMANCE_TAB,
               HEADERS_REGIME, regime)
    counts[REGIME_PERFORMANCE_TAB] = len(regime)

    return {"counts": counts, "trades": trades, "completed": completed}


# ============================================================
# Phase 3 (2026-04-21) — Daily_Recap generator
#
# Genera un resumen markdown del día más reciente presente en la tab principal,
# por bot. Lo persiste en Firestore (colección `eolo-daily-recaps`, doc_id
# `{bot}_{YYYY-MM-DD}`) y lo escribe a la pestaña `Daily_Recap` del spreadsheet
# (últimos MAX_DAILY_RECAPS_IN_TAB recaps, uno por día, separados por `---`).
#
# El día "más reciente" se deriva de max(ts) en trades, no de hoy según el
# reloj — esto hace el recap robusto a huecos (fines de semana, feriados).
# ============================================================

def _recap_day_key(ts_iso: str) -> str:
    """YYYY-MM-DD en UTC a partir de un timestamp ISO-ish. Fallback a primeros
    10 chars si el parsing falla (los bots escriben `YYYY-MM-DD...` arriba
    de todo igual)."""
    if not ts_iso:
        return ""
    s = str(ts_iso).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except Exception:
        return s[:10]


def _generate_daily_recap(
    bot: str,
    trades: list[dict],
    completed: list[dict],
    verdict_info: list[dict],
) -> dict | None:
    """Retorna dict con el recap del día más reciente, o None si no hay data.
    `completed` ya viene con pnl_usd calculado (numérico) por _pair_completed_trades.
    """
    if not trades:
        return None
    days = [_recap_day_key(t.get("ts", "")) for t in trades]
    days = [d for d in days if d]
    if not days:
        return None
    day = max(days)

    today_trades = [t for t in trades if _recap_day_key(t.get("ts", "")) == day]
    today_completed = [
        c for c in completed if _recap_day_key(c.get("exit_ts", "")) == day
    ]

    buys   = sum(1 for t in today_trades if t.get("side") == "BUY")
    sells  = sum(1 for t in today_trades if t.get("side") == "SELL")

    valid_completed = [
        c for c in today_completed if isinstance(c.get("pnl_usd"), (int, float))
    ]
    wins      = sum(1 for c in valid_completed if c["pnl_usd"] > 0)
    losses    = sum(1 for c in valid_completed if c["pnl_usd"] < 0)
    breakeven = sum(1 for c in valid_completed if c["pnl_usd"] == 0)
    pnl_net   = round(sum(float(c["pnl_usd"]) for c in valid_completed), 2)
    decisive  = wins + losses
    win_rate  = round(wins / decisive, 4) if decisive else None

    # Mejores / peores trades cerrados del día
    best = max(valid_completed, key=lambda c: c["pnl_usd"], default=None)
    worst = min(valid_completed, key=lambda c: c["pnl_usd"], default=None)
    best_trade = (
        {"symbol": best["symbol"], "strategy": best["strategy"], "pnl": round(best["pnl_usd"], 2)}
        if best and best["pnl_usd"] > 0 else None
    )
    worst_trade = (
        {"symbol": worst["symbol"], "strategy": worst["strategy"], "pnl": round(worst["pnl_usd"], 2)}
        if worst and worst["pnl_usd"] < 0 else None
    )

    # Top / bottom estrategias por pnl del día
    strat_pnl: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    for c in valid_completed:
        s = strat_pnl[c["strategy"]]
        s["pnl"] += float(c["pnl_usd"])
        s["trades"] += 1
    strat_ranked = sorted(
        strat_pnl.items(), key=lambda kv: kv[1]["pnl"], reverse=True,
    )
    top3 = [
        {"strategy": k, "pnl": round(v["pnl"], 2), "trades": v["trades"]}
        for k, v in strat_ranked[:3] if v["pnl"] > 0
    ]
    bottom3 = [
        {"strategy": k, "pnl": round(v["pnl"], 2), "trades": v["trades"]}
        for k, v in list(reversed(strat_ranked))[:3] if v["pnl"] < 0
    ]

    # Mejor / peor régimen (vix_bucket, session_bucket)
    regime_agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"pnl": 0.0, "trades": 0}
    )
    for c in valid_completed:
        vb = c.get("vix_bucket_exit") or c.get("vix_bucket_entry") or ""
        sb = c.get("session_bucket_exit") or c.get("session_bucket_entry") or ""
        r = regime_agg[(vb, sb)]
        r["pnl"] += float(c["pnl_usd"])
        r["trades"] += 1
    regime_ranked = sorted(
        regime_agg.items(), key=lambda kv: kv[1]["pnl"], reverse=True,
    )

    def _reg(kv):
        (vb, sb), v = kv
        return {
            "vix_bucket": vb, "session_bucket": sb,
            "pnl": round(v["pnl"], 2), "trades": v["trades"],
        }

    best_regime = _reg(regime_ranked[0]) if regime_ranked and regime_ranked[0][1]["pnl"] > 0 else None
    worst_regime = (
        _reg(regime_ranked[-1])
        if regime_ranked and regime_ranked[-1][1]["pnl"] < 0 else None
    )

    # Cambios de veredicto promovidos este sync + pendientes en cooldown
    verdict_changes = []
    verdict_pending = []
    for v in verdict_info:
        prev = v.get("previous")
        final = v.get("final")
        if prev and final and prev != final:
            verdict_changes.append(
                {"strategy": v["strategy"], "from": prev, "to": final}
            )
        if v.get("pending"):
            verdict_pending.append({
                "strategy": v["strategy"],
                "current":  final,
                "pending":  v["pending"],
                "streak":   int(v.get("pending_streak") or 0),
            })

    tickers = sorted({t.get("symbol", "") for t in today_trades if t.get("symbol")})

    return {
        "bot": bot,
        "date": day,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trades": len(today_trades),
        "buys": buys, "sells": sells,
        "wins": wins, "losses": losses, "breakeven": breakeven,
        "win_rate": win_rate,
        "pnl_net": pnl_net,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "top_strategies": top3,
        "bottom_strategies": bottom3,
        "best_regime": best_regime,
        "worst_regime": worst_regime,
        "verdict_changes": verdict_changes,
        "verdict_pending": verdict_pending,
        "tickers": tickers,
    }


def _render_daily_recap_md(recap: dict) -> str:
    """Render a markdown. Diseñado para ser legible tanto en celda de Sheets
    como desde Drive MCP en Phase 4."""
    lines: list[str] = []
    lines.append(f"# Daily Recap — {recap['bot']} — {recap['date']}")
    lines.append("")
    lines.append(
        f"- Trades: {recap['trades']} (buys {recap['buys']}, sells {recap['sells']})"
    )
    lines.append(
        f"- Completed: wins {recap['wins']} / losses {recap['losses']} / "
        f"breakeven {recap['breakeven']}"
    )
    wr = recap.get("win_rate")
    if wr is not None:
        lines.append(f"- Win rate: {wr * 100:.1f}%")
    lines.append(f"- Net PnL: ${recap['pnl_net']:+.2f}")

    if recap.get("best_trade"):
        b = recap["best_trade"]
        lines.append("")
        lines.append("## Best trade")
        lines.append(f"- {b['symbol']} ({b['strategy']}): ${b['pnl']:+.2f}")
    if recap.get("worst_trade"):
        w = recap["worst_trade"]
        lines.append("")
        lines.append("## Worst trade")
        lines.append(f"- {w['symbol']} ({w['strategy']}): ${w['pnl']:+.2f}")
    if recap.get("top_strategies"):
        lines.append("")
        lines.append("## Top strategies (by pnl)")
        for s in recap["top_strategies"]:
            lines.append(f"- {s['strategy']}: ${s['pnl']:+.2f} ({s['trades']} trades)")
    if recap.get("bottom_strategies"):
        lines.append("")
        lines.append("## Bottom strategies")
        for s in recap["bottom_strategies"]:
            lines.append(f"- {s['strategy']}: ${s['pnl']:+.2f} ({s['trades']} trades)")
    if recap.get("best_regime"):
        r = recap["best_regime"]
        lines.append("")
        lines.append(
            f"## Best regime: vix={r['vix_bucket'] or 'n/a'} / "
            f"session={r['session_bucket'] or 'n/a'}"
        )
        lines.append(f"- ${r['pnl']:+.2f} on {r['trades']} trades")
    if recap.get("worst_regime"):
        r = recap["worst_regime"]
        lines.append("")
        lines.append(
            f"## Worst regime: vix={r['vix_bucket'] or 'n/a'} / "
            f"session={r['session_bucket'] or 'n/a'}"
        )
        lines.append(f"- ${r['pnl']:+.2f} on {r['trades']} trades")
    if recap.get("verdict_changes"):
        lines.append("")
        lines.append("## Verdict changes (promoted this sync)")
        for v in recap["verdict_changes"]:
            lines.append(f"- {v['strategy']}: {v['from']} → {v['to']}")
    if recap.get("verdict_pending"):
        lines.append("")
        lines.append("## Verdict pending (cooldown in progress)")
        for v in recap["verdict_pending"]:
            lines.append(
                f"- {v['strategy']}: currently {v['current']}, "
                f"pending {v['pending']} (streak {v['streak']})"
            )
    if recap.get("tickers"):
        lines.append("")
        lines.append(f"## Tickers ({len(recap['tickers'])})")
        lines.append(", ".join(recap["tickers"]))

    # Strategy Diagnostics (Phase 5 — 2026-04-21, direccional Phase A/B).
    # Solo se attacha al recap del bot v1; en otros bots queda None.
    diag = recap.get("diagnostics")
    if diag and diag.get("summary"):
        lines.append("")
        lines.append("## Strategy Diagnostics")
        lines.append(
            f"_Last bar: {diag.get('interval')} candle from yfinance — "
            f"universe {len(diag.get('tickers', []))} tickers "
            f"({', '.join(diag.get('tickers', []))})_"
        )

        def _fmt(count, tickers):
            if not count:
                return "0"
            return f"{count} ({','.join(tickers)})"

        def _render_diag_block(title: str, entries: list[dict]) -> None:
            if not entries:
                return
            lines.append("")
            lines.append(f"### {title}")
            lines.append("| Strategy | Raw fired | Final fired | Blocked |")
            lines.append("|---|---|---|---|")
            for s in entries:
                lines.append(
                    f"| {s['strategy']} | "
                    f"{_fmt(s['raw_count'], s['raw_tickers'])} | "
                    f"{_fmt(s['final_count'], s['final_tickers'])} | "
                    f"{_fmt(s['blocked_count'], s['blocked_tickers'])} |"
                )

        summary = diag["summary"]
        longs  = [s for s in summary if s.get("direction") == "long"]
        shorts = [s for s in summary if s.get("direction") == "short"]
        others = [s for s in summary if s.get("direction") not in ("long", "short")]

        _render_diag_block("LONG side — setups alcistas (expected BUY)", longs)
        _render_diag_block("SHORT side — setups bajistas (expected SELL)", shorts)
        _render_diag_block("Otros (legacy, no direccional)", others)

        lines.append("")
        lines.append(
            "_Raw = indicador puro emitiendo la señal esperada. Final = con filtros. "
            "Blocked = raw esperado pero final=HOLD (oportunidad bloqueada por filtros)._"
        )

    lines.append("")
    lines.append(f"_Generated at {recap['generated_at']}_")
    return "\n".join(lines)


def _persist_daily_recap(bot: str, recap: dict) -> None:
    """Persiste en Firestore — un doc por (bot, date). Idempotente: si el día
    se re-computa porque llegaron trades nuevos, overwrite completo."""
    try:
        doc_id = f"{bot}_{recap['date']}"
        db().collection(DAILY_RECAP_COLLECTION).document(doc_id).set(
            recap, merge=False,
        )
    except Exception as e:
        logger.warning(f"[RECAP] persist {bot} {recap.get('date')} falló: {e}")


def _load_recent_recaps(bot: str, limit: int) -> list[dict]:
    """Retorna los últimos `limit` recaps de Firestore para este bot, ordenados
    desc por date. Falla graceful a [] si no hay índice compuesto todavía."""
    try:
        q = (
            db().collection(DAILY_RECAP_COLLECTION)
            .where("bot", "==", bot)
            .order_by("date", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [d.to_dict() or {} for d in q.stream()]
    except Exception as e:
        logger.warning(f"[RECAP] load_recent {bot} falló: {e}")
        return []


def _write_daily_recap_tab(
    sheets, spreadsheet_id: str, bot: str, latest_recap: dict | None,
) -> int:
    """Reescribe la pestaña Daily_Recap con los últimos MAX_DAILY_RECAPS_IN_TAB
    recaps en markdown, separados por `---`. Retorna # de recaps escritos.
    Reemplaza el bloque del día actual si Firestore ya tenía uno para esa fecha.
    """
    if not _ensure_tab(sheets, spreadsheet_id, DAILY_RECAP_TAB):
        return 0

    past = _load_recent_recaps(bot, MAX_DAILY_RECAPS_IN_TAB + 1)
    by_date: dict[str, dict] = {}
    if latest_recap:
        by_date[latest_recap["date"]] = latest_recap
    for r in past:
        d = r.get("date") if isinstance(r, dict) else None
        if d and d not in by_date:
            by_date[d] = r
    ordered = sorted(
        by_date.values(), key=lambda r: r.get("date", ""), reverse=True,
    )[:MAX_DAILY_RECAPS_IN_TAB]

    rows: list[list[Any]] = []
    for rc in ordered:
        md = _render_daily_recap_md(rc)
        for ln in md.splitlines():
            rows.append([ln])
        rows.append(["---"])

    try:
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{DAILY_RECAP_TAB}'!A:Z",
        ).execute()
    except HttpError as e:
        logger.warning(f"[RECAP] clear {DAILY_RECAP_TAB} falló: {e}")
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{DAILY_RECAP_TAB}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS_DAILY_RECAP] + rows},
        ).execute()
        logger.info(
            f"[RECAP] {DAILY_RECAP_TAB} actualizado en {spreadsheet_id}: "
            f"{len(ordered)} recaps"
        )
    except HttpError as e:
        logger.error(f"[RECAP] write {DAILY_RECAP_TAB} falló: {e}")
    return len(ordered)


def _sync_daily_recap(
    sheets, spreadsheet_id: str, bot: str,
    trades: list[dict], completed: list[dict], verdict_info: list[dict],
    diagnostics: dict | None = None,
) -> dict | None:
    """Orquesta: generar + persistir + escribir tab. Retorna el recap del día
    (o None si no había trades). Seguro contra fallos parciales."""
    recap = _generate_daily_recap(bot, trades, completed, verdict_info)
    if recap is None:
        return None
    if diagnostics:
        recap["diagnostics"] = diagnostics
    _persist_daily_recap(bot, recap)
    _write_daily_recap_tab(sheets, spreadsheet_id, bot, recap)
    return recap


# ── Strategy Diagnostics (Phase 5 — 2026-04-21, Plan B 2026-04-21) ─
#
# Cómputo original (Phase 5): sheets-sync bajaba data de yfinance y corría
# las estrategias localmente. Funcionó 5 minutos hasta que Yahoo empezó a
# ratelimitar a las IPs de GCP devolviendo DataFrame vacío.
#
# Plan B: el cómputo se movió al Bot v1 (que tiene Schwab MarketData). El
# bot persiste `eolo-strategy-diagnostics/{YYYY-MM-DD}` cada 15 min durante
# trading. Este servicio solo LEE el doc de Firestore, lo attachea al recap
# y lo escribe al tab Strategy_Diagnostics.
#
# Modos computados por el bot (cada wrapper × cada ticker):
#   • RAW   — cfg con todos los filtros OFF (señal pura del indicador)
#   • FINAL — cfg default (regime + EMA50 trend + ToD)
#
# Output:
#   - Firestore eolo-strategy-diagnostics/{YYYY-MM-DD}  — escrito por Bot v1
#   - Pestaña Strategy_Diagnostics en Eolo_v1_Trades   — matriz última corrida
#   - Sección "## Strategy Diagnostics" en Daily_Recap  — Phase 4 lo levanta

def _compute_strategy_diagnostics() -> dict | None:
    """Lee el diagnostic del día desde Firestore `eolo-strategy-diagnostics`.

    Plan B (2026-04-21): yfinance quedó bloqueado desde IPs de GCP (Yahoo
    ratelimit). El cómputo se movió al Bot v1, que usa Schwab MarketData y
    persiste el doc cada 15 min durante trading. Este servicio solo lee:

        eolo-strategy-diagnostics/{YYYY-MM-DD}

    Si no existe el doc (ej. weekend, bot v1 apagado, o sync corriendo
    antes del primer tick del bot), devuelve None y el recap sale sin
    la sección. El log muestra el motivo.
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc = db().collection(DIAGNOSTICS_COLLECTION).document(today).get()
        if not doc.exists:
            logger.info(
                f"[DIAG] Firestore sin doc para {today} — el Bot v1 aún no "
                "lo computó (¿fin de semana o bot v1 apagado?)"
            )
            return None
        diag = doc.to_dict() or {}
        if not diag.get("summary"):
            logger.warning(f"[DIAG] doc {today} existe pero summary vacío — skip")
            return None
        logger.info(
            f"[DIAG] leído de Firestore: {today} — "
            f"{len(diag.get('tickers') or [])} tickers × "
            f"{len(diag.get('strategies') or [])} wrappers, "
            f"generado={diag.get('generated_at', '?')}"
        )
        return diag
    except Exception as e:
        logger.warning(f"[DIAG] lectura Firestore falló: {e}")
        return None


def _persist_strategy_diagnostics(diag: dict) -> None:
    if not diag:
        return
    try:
        db().collection(DIAGNOSTICS_COLLECTION).document(diag["date"]).set(
            diag, merge=False,
        )
    except Exception as e:
        logger.warning(f"[DIAG] persist falló: {e}")


def _write_diagnostics_tab(sheets, spreadsheet_id: str, diag: dict) -> None:
    """Escribe la matriz última al tab Strategy_Diagnostics (overwrite cada sync)."""
    if not diag:
        return
    try:
        _ensure_tab(sheets, spreadsheet_id, DIAGNOSTICS_TAB)
    except Exception as e:
        logger.warning(f"[DIAG] ensure_tab falló: {e}")
        return

    rows: list[list[Any]] = []
    rows.append([
        f"Strategy Diagnostics — {diag['date']} — interval={diag['interval']} "
        f"period={diag['period']} — generated_at={diag['generated_at']}",
    ])
    rows.append([])

    # Phase A (Opción C): split summary y matrices en bloques LONG vs SHORT.
    longs  = [s for s in diag["summary"] if s.get("direction") == "long"]
    shorts = [s for s in diag["summary"] if s.get("direction") == "short"]
    others = [s for s in diag["summary"] if s.get("direction") not in ("long", "short")]

    def _write_summary_block(title: str, entries: list[dict]):
        if not entries:
            return
        rows.append([title])
        rows.append([
            "Strategy", "Direction", "Raw fired", "Final fired",
            "Blocked by filters",
            "Raw tickers", "Final tickers", "Blocked tickers",
        ])
        for s in entries:
            rows.append([
                s.get("base_strategy", s["strategy"]),
                s.get("direction", "-"),
                s["raw_count"],
                s["final_count"],
                s["blocked_count"],
                ",".join(s["raw_tickers"])     or "-",
                ",".join(s["final_tickers"])   or "-",
                ",".join(s["blocked_tickers"]) or "-",
            ])
        rows.append([])

    _write_summary_block("LONG side — setups alcistas",  longs)
    _write_summary_block("SHORT side — setups bajistas", shorts)
    _write_summary_block("Otros",                         others)

    def _write_matrix_block(title: str, matrix: dict, names: list[str]):
        if not names:
            return
        rows.append([title])
        # Mostrar nombre completo (NAME_LONG/NAME_SHORT) como header para que
        # el lector vea claramente qué wrapper corrió en esa columna.
        rows.append(["Ticker"] + names)
        for ticker in diag["tickers"]:
            rows.append([ticker] + [matrix[ticker][n] for n in names])
        rows.append([])

    long_names  = [s["strategy"] for s in longs]
    short_names = [s["strategy"] for s in shorts]

    rows.append(["RAW (no filters) — indicador puro"])
    rows.append([])
    _write_matrix_block("RAW · LONG side",  diag["matrix_raw"],   long_names)
    _write_matrix_block("RAW · SHORT side", diag["matrix_raw"],   short_names)

    rows.append(["FINAL (regime + trend + ToD) — señal que el bot ejecutaría"])
    rows.append([])
    _write_matrix_block("FINAL · LONG side",  diag["matrix_final"], long_names)
    _write_matrix_block("FINAL · SHORT side", diag["matrix_final"], short_names)

    # _write_tab firma: (sheets, spreadsheet_id, tab, headers, rows).
    # Este tab no tiene una header row fija (es un layout multi-bloque), así
    # que pasamos rows[0] como "header" (el título) y rows[1:] como body.
    _write_tab(sheets, spreadsheet_id, DIAGNOSTICS_TAB,
               rows[0] if rows else [], rows[1:] if len(rows) > 1 else [])
    logger.info(
        f"[DIAG] tab '{DIAGNOSTICS_TAB}' actualizado en {spreadsheet_id}: "
        f"{len(diag['strategies'])} estrategias × {len(diag['tickers'])} tickers"
    )


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

    result = {"appended": {}, "skipped": {}, "stats": {}, "errors": []}

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

    # ── Strategy_Stats por sheet ───────────────────────────
    # Corre DESPUÉS de ingestar los rows nuevos para que los stats reflejen
    # el estado más fresco. Lee la pestaña completa de cada sheet y re-computa
    # from scratch (las 4 pestañas son pequeñas — decenas a pocos miles de rows).
    stats_targets = [
        (cfg.get("v1_sheet_id"),     "v1"),
        (cfg.get("v2_sheet_id"),     "v2"),
        (cfg.get("crypto_sheet_id"), "crypto"),
        (cfg.get("all_sheet_id"),    "all"),
    ]

    # Phase 3: cargar rules + history UNA vez por sync. `history` se muta dentro
    # de _compute_strategy_stats y se persiste al final antes de recaps.
    verdict_rules   = _load_verdict_rules()
    verdict_history = _load_verdict_history()
    verdict_info_by_bot: dict[str, list[dict]] = {}

    for sid, bot in stats_targets:
        if not sid:
            continue
        try:
            stats_out = _sync_stats_for(
                sheets, sid, bot,
                rules=verdict_rules, history=verdict_history,
            )
            result["stats"][bot] = stats_out.get("count", 0)
            verdict_info_by_bot[bot] = stats_out.get("verdicts") or []
        except HttpError as e:
            result["errors"].append(f"stats {bot}: {e}")
            logger.error(f"[STATS] {bot} falló: {e}")
        except Exception as e:
            result["errors"].append(f"stats {bot}: {e}")
            logger.error(f"[STATS] {bot} error: {e}")

    # Persistir el history UNA vez — consolidado entre todos los bots.
    try:
        _save_verdict_history(verdict_history)
    except Exception as e:
        logger.warning(f"[VERDICT] save_verdict_history falló: {e}")

    # ── Analytics tabs (Phase 2 — 2026-04-21) ─────────────
    # Completed_Trades / Strategy_x_Ticker / Daily_Summary / Regime_Performance.
    # Se pueblan en TODOS los sheets (v1/v2/crypto/all) para permitir análisis
    # cruzado en el agregado y per-bot en los individuales.
    result["analytics"] = {}
    trades_by_bot:    dict[str, list[dict]] = {}
    completed_by_bot: dict[str, list[dict]] = {}
    for sid, bot in stats_targets:
        if not sid:
            continue
        try:
            out = _sync_analytics_tabs(sheets, sid, bot)
            result["analytics"][bot] = out.get("counts") or {}
            trades_by_bot[bot]    = out.get("trades") or []
            completed_by_bot[bot] = out.get("completed") or []
        except HttpError as e:
            result["errors"].append(f"analytics {bot}: {e}")
            logger.error(f"[SYNC] analytics {bot} falló: {e}")
        except Exception as e:
            result["errors"].append(f"analytics {bot}: {e}")
            logger.error(f"[SYNC] analytics {bot} error: {e}")

    # ── Strategy Diagnostics (Phase 5 — 2026-04-21) ────────
    # Corre UNA vez por sync (universo de tickers común). Persiste a Firestore +
    # escribe la matriz al tab Strategy_Diagnostics del sheet v1. Después se
    # attacha al recap de v1 para que aparezca en el markdown que Phase 4 lee.
    diagnostics: dict | None = None
    try:
        diagnostics = _compute_strategy_diagnostics()
        if diagnostics:
            _persist_strategy_diagnostics(diagnostics)
            v1_sid = cfg.get("v1_sheet_id")
            if v1_sid:
                _write_diagnostics_tab(sheets, v1_sid, diagnostics)
            result["diagnostics"] = {
                "tickers":    len(diagnostics.get("tickers") or []),
                "strategies": len(diagnostics.get("strategies") or []),
                "raw_total":   sum(s["raw_count"]   for s in diagnostics["summary"]),
                "final_total": sum(s["final_count"] for s in diagnostics["summary"]),
            }
        else:
            result["diagnostics"] = {"skipped": True}
    except Exception as e:
        result["errors"].append(f"diagnostics: {e}")
        logger.error(f"[DIAG] error: {e}")
        diagnostics = None

    # ── Daily_Recap (Phase 3 — 2026-04-21) ─────────────────
    # Genera markdown del día más reciente, persiste en Firestore y escribe
    # pestaña Daily_Recap en cada sheet (últimos MAX_DAILY_RECAPS_IN_TAB días).
    # Phase 5: pasa `diagnostics` solo a v1 (es el único bot equity con todas
    # las 11 estrategias mapeadas al universo de tickers del diag).
    result["recaps"] = {}
    for sid, bot in stats_targets:
        if not sid:
            continue
        try:
            diag_for_bot = diagnostics if bot == "v1" else None
            recap = _sync_daily_recap(
                sheets, sid, bot,
                trades=trades_by_bot.get(bot) or [],
                completed=completed_by_bot.get(bot) or [],
                verdict_info=verdict_info_by_bot.get(bot) or [],
                diagnostics=diag_for_bot,
            )
            if recap:
                result["recaps"][bot] = {
                    "date":    recap["date"],
                    "trades":  recap["trades"],
                    "pnl_net": recap["pnl_net"],
                    "verdict_changes": len(recap.get("verdict_changes") or []),
                    "verdict_pending": len(recap.get("verdict_pending") or []),
                }
        except HttpError as e:
            result["errors"].append(f"recap {bot}: {e}")
            logger.error(f"[RECAP] {bot} falló: {e}")
        except Exception as e:
            result["errors"].append(f"recap {bot}: {e}")
            logger.error(f"[RECAP] {bot} error: {e}")

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
        "endpoints": ["/sync (POST/GET)", "/status (GET)", "/config (GET)",
                      "/daily-reset (POST/GET)", "/daily-health (GET ?days=N)"],
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


# ═══════════════════════════════════════════════════════════
#  DAILY RESET — health-check 8am ET
# ═══════════════════════════════════════════════════════════
@app.route("/daily-reset", methods=["GET", "POST"])
def daily_reset():
    """
    Ejecuta los 7 checks del health-check y despacha a Telegram + Gmail + Firestore.
    Disparado por Cloud Scheduler `eolo-daily-reset` a las 8am ET.

    Query params:
      ?skip_notify=1  → solo corre checks, no despacha (útil para debug)
      ?only=telegram  → solo despacha a ese canal
    """
    try:
        from health_check import run_all_checks
        from notifiers import dispatch_all, send_telegram, send_gmail, persist_to_firestore

        logger.info(f"[DAILY_RESET] Trigger desde {request.remote_addr}")
        report = run_all_checks()
        logger.info(f"[DAILY_RESET] Overall={report['overall']} "
                    f"ok={report['n_ok']} warn={report['n_warn']} "
                    f"crit={report['n_crit']} err={report['n_err']} "
                    f"elapsed={report['elapsed_sec']}s")

        skip = request.args.get("skip_notify") in ("1", "true", "yes")
        only = request.args.get("only", "").lower()

        dispatch = {}
        if not skip:
            if only == "telegram":
                dispatch = {"telegram": send_telegram(report)}
            elif only == "gmail":
                dispatch = {"gmail": send_gmail(report)}
            elif only == "firestore":
                dispatch = {"firestore": persist_to_firestore(report)}
            else:
                dispatch = dispatch_all(report)

        return jsonify({
            "ok": True,
            "overall": report["overall"],
            "summary": {"n_ok": report["n_ok"], "n_warn": report["n_warn"],
                        "n_crit": report["n_crit"], "n_err": report["n_err"]},
            "report": report,
            "dispatch": dispatch,
        })
    except Exception as e:
        logger.exception("[DAILY_RESET] fatal")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/daily-health", methods=["GET"])
def daily_health_history():
    """
    Devuelve los últimos N días de health reports desde Firestore.
    Usado por el artifact Cowork para renderizar histórico.
      ?days=30 (default)
    """
    try:
        days = int(request.args.get("days", "30"))
        from notifiers import HEALTH_HISTORY_COLLECTION
        docs = (db().collection(HEALTH_HISTORY_COLLECTION)
                    .order_by("__name__", direction=firestore.Query.DESCENDING)
                    .limit(days).stream())
        history = [{"date": d.id, **(d.to_dict() or {})} for d in docs]
        return jsonify({"ok": True, "count": len(history), "history": history})
    except Exception as e:
        logger.exception("[daily-health] fatal")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    # Modo local para debugging
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
