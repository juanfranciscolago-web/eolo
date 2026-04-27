# ============================================================
#  EOLO — Daily Health Check
#
#  Módulo standalone que reúne 7 chequeos para el reset diario
#  de las 8am ET. Cada check es independiente, devuelve dict con
#  shape común:
#     {
#       "name":   <str>,         # id del check
#       "status": "ok|warn|crit|err",
#       "value":  <primary metric>,
#       "details": {...},        # payload libre
#       "message": <str>,        # 1-line summary
#     }
#
#  Los thresholds están hardcodeados pero exponibles por env-var.
# ============================================================
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from google.cloud import firestore, secretmanager
from loguru import logger

# ── Config defaults (env-var override) ──────────────────────
GCP_PROJECT          = os.environ.get("GOOGLE_CLOUD_PROJECT", "eolo-schwab-agent")

# Thresholds
ANTHROPIC_BALANCE_WARN_USD = float(os.environ.get("ANTHROPIC_BALANCE_WARN", "20"))
ANTHROPIC_BALANCE_CRIT_USD = float(os.environ.get("ANTHROPIC_BALANCE_CRIT", "5"))
GCP_SPEND_WARN_PCT         = float(os.environ.get("GCP_SPEND_WARN_PCT", "0.80"))  # 80% budget
GCP_SPEND_CRIT_PCT         = float(os.environ.get("GCP_SPEND_CRIT_PCT", "0.95"))  # 95%
GCP_MONTHLY_BUDGET_USD     = float(os.environ.get("GCP_MONTHLY_BUDGET_USD", "100"))
SCHWAB_REFRESH_WARN_DAYS   = float(os.environ.get("SCHWAB_REFRESH_WARN_DAYS", "2"))
SCHWAB_REFRESH_CRIT_DAYS   = float(os.environ.get("SCHWAB_REFRESH_CRIT_DAYS", "1"))
TRADES_24H_WARN_MIN        = int(os.environ.get("TRADES_24H_WARN_MIN", "0"))      # 0 trades = warn
MARKET_DATA_STALE_MIN      = int(os.environ.get("MARKET_DATA_STALE_MIN", "10"))
ERRORS_24H_CRIT            = int(os.environ.get("ERRORS_24H_CRIT", "5"))

# Services a monitorear
CLOUD_RUN_SERVICES = [
    {"name": "eolo-bot",              "region": "us-central1"},
    {"name": "eolo-bot-v2",           "region": "us-east1"},
    {"name": "eolo-bot-crypto",       "region": "southamerica-east1"},
    {"name": "eolo-sheets-sync",      "region": "us-east1"},
    {"name": "eolo-dashboard",        "region": "us-central1"},
    {"name": "eolo-options-dashboard","region": "us-east1"},
    {"name": "eolo-crypto-dashboard", "region": "southamerica-east1"},
]

# Firestore refs
SCHWAB_TOKENS_COLLECTION = "schwab-tokens"
SCHWAB_TOKENS_DOC        = "schwab-tokens-auth"
V1_TRADES_COLLECTION     = "eolo-trades"
V2_TRADES_COLLECTION     = "eolo-trades-v2"
CRYPTO_TRADES_COLLECTION = "eolo-crypto-trades"
V1_STATE_COLLECTION      = "eolo-bot-state"
V2_STATE_COLLECTION      = "eolo-options-state"
CRYPTO_STATE_COLLECTION  = "eolo-crypto-state"
# v1: doc top-level con booleans directos.
# v2/crypto: doc `settings` con field anidado `strategies_enabled` (map).
STRATEGIES_PATHS = {
    "v1":     ("eolo-config",         "strategies", None),
    "v2":     ("eolo-options-config", "settings",   "strategies_enabled"),
    "crypto": ("eolo-crypto-config",  "settings",   "strategies_enabled"),
}

_db = None
def _fs() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=GCP_PROJECT)
    return _db


def _secret(name: str) -> str | None:
    """Lee secret de Secret Manager; None si no existe."""
    try:
        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{GCP_PROJECT}/secrets/{name}/versions/latest"
        return client.access_secret_version(request={"name": path}).payload.data.decode("utf-8")
    except Exception as e:
        logger.warning(f"[secret:{name}] {e}")
        return None


def _result(name: str, status: str, value: Any, message: str, **details) -> dict:
    return {
        "name": name,
        "status": status,
        "value": value,
        "message": message,
        "details": details,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════
#  Check 1 — Schwab tokens (access + refresh vivos)
# ═══════════════════════════════════════════════════════════
def check_schwab_tokens() -> dict:
    """
    Lee Firestore schwab-tokens/schwab-tokens-auth, calcula edad del token
    según el `saved_at` que escribe schwab_oauth_refresh(). Si pasaron más de
    SCHWAB_REFRESH_WARN_DAYS (default 5 por debajo de los 7d de expiración),
    alerta.
    """
    try:
        doc = _fs().collection(SCHWAB_TOKENS_COLLECTION).document(SCHWAB_TOKENS_DOC).get()
        if not doc.exists:
            return _result("schwab_tokens", "crit", None,
                           "❌ Firestore schwab-tokens/schwab-tokens-auth NO existe")
        data = doc.to_dict() or {}

        # El doc guarda la respuesta OAuth cruda de Schwab (sin timestamp).
        # Fallback: Firestore `update_time` (metadata del doc, la pone GCS al escribir).
        saved_at_iso = data.get("saved_at") or data.get("updated_at") or data.get("refreshed_at")
        saved_at = None
        if saved_at_iso:
            try:
                saved_at = datetime.fromisoformat(saved_at_iso.replace("Z", "+00:00"))
            except Exception:
                saved_at = None

        if saved_at is None:
            ut = doc.update_time  # google.api_core DatetimeWithNanoseconds → tz-aware UTC
            if ut is None:
                return _result("schwab_tokens", "warn", None,
                               "⚠️ Tokens presentes pero sin timestamp (doc.update_time missing)")
            # Convertir a datetime "plain" con tzinfo=UTC (puede venir como pb Timestamp-like)
            saved_at = ut if isinstance(ut, datetime) else datetime.fromtimestamp(
                ut.timestamp(), tz=timezone.utc)
            saved_at_iso = saved_at.isoformat()

        age = datetime.now(timezone.utc) - saved_at
        age_hrs = age.total_seconds() / 3600

        # Schwab expira refresh_token en 7 días. Si no se usa el refresh en ese window,
        # muere. age_hrs midiendo "hace cuánto no corrió oauth_refresh" es proxy.
        remaining_days = 7 - (age_hrs / 24)

        if remaining_days <= SCHWAB_REFRESH_CRIT_DAYS:
            return _result("schwab_tokens", "crit", remaining_days,
                           f"🚨 Refresh_token expira en {remaining_days:.1f} días — correr init_auth YA",
                           age_hrs=age_hrs, saved_at=saved_at_iso)
        if remaining_days <= SCHWAB_REFRESH_WARN_DAYS:
            return _result("schwab_tokens", "warn", remaining_days,
                           f"⚠️ Refresh_token expira en {remaining_days:.1f} días",
                           age_hrs=age_hrs, saved_at=saved_at_iso)
        return _result("schwab_tokens", "ok", remaining_days,
                       f"🟢 Schwab tokens OK (refresh live {remaining_days:.1f}d)",
                       age_hrs=age_hrs, saved_at=saved_at_iso)
    except Exception as e:
        logger.exception("schwab_tokens check failed")
        return _result("schwab_tokens", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Check 2 — Anthropic credits (balance + burn)
# ═══════════════════════════════════════════════════════════
def check_anthropic_credits() -> dict:
    """
    La API de Anthropic no expone billing balance directamente.
    Usamos el burn estimado: contamos decisiones de Claude en Firestore (v2 + crypto)
    y estimamos costo con modelo Sonnet 4.6 ≈ $0.015/llamada.

    Si el balance en Secret Manager (`anthropic-balance-usd`) existe, lo usamos;
    caso contrario solo reportamos burn y status=ok si < warn threshold.
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        v2_calls = 0
        crypto_calls = 0
        try:
            v2_doc = _fs().collection("eolo-claude-decisions-v2").document(today).get()
            if v2_doc.exists:
                sub = list(_fs().collection(f"eolo-claude-decisions-v2/{today}/decisions").stream())
                v2_calls = len(sub)
        except Exception:
            pass
        try:
            # crypto usa {YYYY-MM-DD}-{ts_ms} como doc name directo
            ago = datetime.now(timezone.utc) - timedelta(hours=24)
            crypto_coll = _fs().collection("eolo-crypto-claude-decisions")
            crypto_calls = sum(1 for d in crypto_coll.stream()
                               if d.id.startswith(today))
        except Exception:
            pass

        total_calls = v2_calls + crypto_calls
        cost_per_call = 0.015  # Sonnet 4.6 avg
        burn_24h = total_calls * cost_per_call

        # Balance opcional
        balance_raw = _secret("anthropic-balance-usd")
        balance = None
        if balance_raw:
            try:
                balance = float(balance_raw)
            except ValueError:
                balance = None

        if balance is not None:
            if balance < ANTHROPIC_BALANCE_CRIT_USD:
                return _result("anthropic_credits", "crit", balance,
                               f"🚨 Anthropic balance ${balance:.2f} (burn {burn_24h:.2f}/d, {total_calls} calls)",
                               burn_24h=burn_24h, calls_24h=total_calls)
            if balance < ANTHROPIC_BALANCE_WARN_USD:
                return _result("anthropic_credits", "warn", balance,
                               f"⚠️ Anthropic balance ${balance:.2f} (burn {burn_24h:.2f}/d)",
                               burn_24h=burn_24h, calls_24h=total_calls)
            return _result("anthropic_credits", "ok", balance,
                           f"🟢 Anthropic ${balance:.2f} | burn {burn_24h:.2f}/d | {total_calls} calls",
                           burn_24h=burn_24h, calls_24h=total_calls)

        # Sin balance → reportar solo burn
        return _result("anthropic_credits", "ok", burn_24h,
                       f"🟢 Anthropic burn {burn_24h:.2f}/d | {total_calls} calls (balance desconocido)",
                       burn_24h=burn_24h, calls_24h=total_calls,
                       hint="setear secret `anthropic-balance-usd` con el balance actual para alertas")
    except Exception as e:
        logger.exception("anthropic_credits check failed")
        return _result("anthropic_credits", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Check 3 — GCP billing (spent MTD vs budget)
# ═══════════════════════════════════════════════════════════
def check_gcp_billing() -> dict:
    """
    Cloud Billing API requiere permisos extra que la SA actual no tiene
    por default (`roles/billing.viewer`). En vez de API, leemos del
    secret `gcp-spend-mtd-usd` (opcional, Juan lo actualiza manual o
    un cron lo refresca). Si no existe, status=ok con message genérico.
    """
    try:
        spend_raw = _secret("gcp-spend-mtd-usd")
        if not spend_raw:
            return _result("gcp_billing", "ok", None,
                           "🟢 GCP billing (sin tracking — setear secret `gcp-spend-mtd-usd`)",
                           hint="opcional: cron que llame `gcloud billing` y actualice secret")
        try:
            spend = float(spend_raw)
        except ValueError:
            return _result("gcp_billing", "err", None,
                           f"💥 Secret gcp-spend-mtd-usd no es número: {spend_raw!r}")

        pct = spend / GCP_MONTHLY_BUDGET_USD if GCP_MONTHLY_BUDGET_USD > 0 else 0
        if pct >= GCP_SPEND_CRIT_PCT:
            return _result("gcp_billing", "crit", spend,
                           f"🚨 GCP spent ${spend:.2f}/${GCP_MONTHLY_BUDGET_USD:.0f} ({pct*100:.0f}%)",
                           pct=pct, budget=GCP_MONTHLY_BUDGET_USD)
        if pct >= GCP_SPEND_WARN_PCT:
            return _result("gcp_billing", "warn", spend,
                           f"⚠️ GCP spent ${spend:.2f}/${GCP_MONTHLY_BUDGET_USD:.0f} ({pct*100:.0f}%)",
                           pct=pct, budget=GCP_MONTHLY_BUDGET_USD)
        return _result("gcp_billing", "ok", spend,
                       f"🟢 GCP ${spend:.2f}/${GCP_MONTHLY_BUDGET_USD:.0f} ({pct*100:.0f}%)",
                       pct=pct, budget=GCP_MONTHLY_BUDGET_USD)
    except Exception as e:
        logger.exception("gcp_billing check failed")
        return _result("gcp_billing", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Check 4 — Cloud Run services (up/down + latency)
# ═══════════════════════════════════════════════════════════
def check_cloud_run_services() -> dict:
    """
    Hace un GET a <service>/health (o `/`) y mide latencia.
    Usa Application Default Credentials para autenticar si el service
    requiere auth (la mayoría aceptan unauthenticated por defecto).
    """
    up = []
    down = []
    latencies = {}

    # Un solo secret JSON con todas las URLs: `eolo-service-urls`
    # Formato:
    #   {"eolo-bot": "https://...", "eolo-bot-v2": "https://...", ...}
    # Si el secret no existe, el check devuelve warn con mensaje para setearlo.
    import json as _json
    urls_raw = _secret("eolo-service-urls")
    if not urls_raw:
        return _result("cloud_run", "warn", None,
                       "⚠️ Secret `eolo-service-urls` no configurado — crearlo con JSON de URLs",
                       hint="crear secret: gcloud secrets create eolo-service-urls --data-file=...")
    try:
        url_map = _json.loads(urls_raw)
    except Exception as e:
        return _result("cloud_run", "err", None,
                       f"💥 Secret `eolo-service-urls` no es JSON válido: {e}")

    for svc in CLOUD_RUN_SERVICES:
        url = url_map.get(svc["name"])
        if not url:
            down.append({"service": svc["name"], "error": "URL falta en secret"})
            continue

        try:
            t0 = time.time()
            resp = requests.get(f"{url.rstrip('/')}/", timeout=8)
            dt = (time.time() - t0) * 1000  # ms
            latencies[svc["name"]] = round(dt, 0)
            if resp.status_code < 500:
                up.append(svc["name"])
            else:
                down.append({"service": svc["name"], "http": resp.status_code})
        except Exception as e:
            down.append({"service": svc["name"], "error": str(e)[:100]})

    total = len(CLOUD_RUN_SERVICES)
    up_n = len(up)

    if len(down) == 0:
        return _result("cloud_run", "ok", f"{up_n}/{total}",
                       f"🟢 Cloud Run {up_n}/{total} UP",
                       up=up, down=down, latencies_ms=latencies)
    if len(down) <= 1:
        return _result("cloud_run", "warn", f"{up_n}/{total}",
                       f"⚠️ Cloud Run {up_n}/{total} UP — {len(down)} DOWN",
                       up=up, down=down, latencies_ms=latencies)
    return _result("cloud_run", "crit", f"{up_n}/{total}",
                   f"🚨 Cloud Run {up_n}/{total} UP — {len(down)} DOWN",
                   up=up, down=down, latencies_ms=latencies)


# ═══════════════════════════════════════════════════════════
#  Check 5 — Strategies toggles (ON/OFF por bot)
# ═══════════════════════════════════════════════════════════
def check_strategies_toggles() -> dict:
    """
    Lee los 3 docs de strategies y cuenta ON/OFF por bot.
    - v1: toggles como fields top-level del doc (coll=eolo-config, doc=strategies).
    - v2 y crypto: toggles adentro de un field-map anidado:
        coll=eolo-*-config, doc=settings, field=strategies_enabled.
    """
    out = {}
    try:
        for bot, (coll, doc_id, nested_field) in STRATEGIES_PATHS.items():
            try:
                ref = _fs().collection(coll).document(doc_id).get()
                if not ref.exists:
                    out[bot] = {"on": 0, "off": 0, "total": 0, "error": "no_doc"}
                    continue
                data = ref.to_dict() or {}
                if nested_field:
                    data = data.get(nested_field, {}) or {}
                on  = sum(1 for v in data.values() if v is True)
                off = sum(1 for v in data.values() if v is False)
                out[bot] = {"on": on, "off": off, "total": on + off}
            except Exception as e:
                out[bot] = {"error": str(e)[:120]}

        summary = " • ".join(
            f"{b}:{d.get('on', '?')}on/{d.get('off', '?')}off" for b, d in out.items()
        )
        return _result("strategies", "ok", out, f"🟢 Strategies | {summary}", by_bot=out)
    except Exception as e:
        logger.exception("strategies check failed")
        return _result("strategies", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Check 6 — Trades últimas 24h (por bot)
# ═══════════════════════════════════════════════════════════
def check_trades_24h() -> dict:
    """Cuenta trades de hoy + ayer en los 3 namespaces."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ago   = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")

        counts = {}
        # v1 y v2: coleccion/{date}/ como doc con map inside
        for bot, coll in [("v1", V1_TRADES_COLLECTION), ("v2", V2_TRADES_COLLECTION)]:
            n = 0
            for d in (today, ago):
                doc = _fs().collection(coll).document(d).get()
                if doc.exists:
                    n += len((doc.to_dict() or {}))
            counts[bot] = n

        # crypto: docs directos {symbol}-{ts_ms}
        from_ts = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
        crypto_n = 0
        try:
            for d in _fs().collection(CRYPTO_TRADES_COLLECTION).stream():
                parts = d.id.split("-")
                if parts and parts[-1].isdigit() and int(parts[-1]) >= from_ts:
                    crypto_n += 1
        except Exception:
            pass
        counts["crypto"] = crypto_n

        total = sum(counts.values())
        msg = f"Trades 24h | v1:{counts['v1']} v2:{counts['v2']} crypto:{counts['crypto']} (total:{total})"

        if total <= TRADES_24H_WARN_MIN:
            return _result("trades_24h", "warn", total, f"⚠️ 0 trades 24h | {msg}", **counts)
        return _result("trades_24h", "ok", total, f"🟢 {msg}", **counts)
    except Exception as e:
        logger.exception("trades_24h check failed")
        return _result("trades_24h", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Check 7 — Market data freshness (último candle recibido)
# ═══════════════════════════════════════════════════════════
def _parse_ts_any(ts: Any) -> datetime | None:
    """
    Parser tolerante para múltiples formatos de timestamp que usan los bots:
      - epoch int/float (s o ms)
      - ISO string con/ sin Z
      - "YYYY-MM-DD HH:MM:SS ET" (v2 custom, America/New_York)
      - datetime nativo
    Devuelve datetime tz-aware en UTC, o None si no puede parsear.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc)
    if isinstance(ts, str):
        s = ts.strip()
        # Caso v2: "2026-04-24 10:06:01 ET"
        if s.endswith(" ET"):
            try:
                from zoneinfo import ZoneInfo
                naive = datetime.strptime(s[:-3].strip(), "%Y-%m-%d %H:%M:%S")
                return naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
            except Exception:
                return None
        # ISO estándar
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    # Protobuf Timestamp-like
    try:
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)  # type: ignore
    except Exception:
        return None


def check_market_data_freshness() -> dict:
    """
    Lee el state doc de cada bot (escrito por StateWriter / equivalente)
    y mira el timestamp más reciente.

    v1 no persiste state doc por diseño (loguru silent) → lo reportamos como
    N/A con info=True para que NO incluya al overall como warn.
    v2 usa formato "YYYY-MM-DD HH:MM:SS ET".
    crypto usa field `ts_updated`.
    """
    out = {}
    TS_FIELDS = ("last_candle_ts", "last_update", "updated_at",
                 "last_cycle_ts", "ts_updated", "ts", "last_ts")
    try:
        for bot, coll in [
            ("v1",     V1_STATE_COLLECTION),
            ("v2",     V2_STATE_COLLECTION),
            ("crypto", CRYPTO_STATE_COLLECTION),
        ]:
            try:
                doc = _fs().collection(coll).document("current").get()
                if not doc.exists:
                    # v1 no persiste state doc — no es falla, solo info
                    out[bot] = {"status": "na" if bot == "v1" else "no_state_doc",
                                "age_min": None,
                                "note": "v1 no persiste state" if bot == "v1" else "doc ausente"}
                    continue
                data = doc.to_dict() or {}
                ts = None
                ts_field = None
                for f in TS_FIELDS:
                    if f in data and data[f]:
                        ts = data[f]
                        ts_field = f
                        break

                if ts is None:
                    out[bot] = {"status": "no_ts_field", "age_min": None,
                                "keys": list(data.keys())[:10]}
                    continue

                ts_dt = _parse_ts_any(ts)
                if ts_dt is None:
                    out[bot] = {"status": "parse_err", "age_min": None,
                                "ts_field": ts_field, "raw": str(ts)[:80]}
                    continue

                age_min = (datetime.now(timezone.utc) - ts_dt).total_seconds() / 60
                status = "ok" if age_min <= MARKET_DATA_STALE_MIN else "warn"
                out[bot] = {"status": status, "age_min": round(age_min, 1),
                            "last_ts": ts_dt.isoformat(), "ts_field": ts_field}
            except Exception as e:
                out[bot] = {"status": "err", "error": str(e)[:100]}

        # v2/crypto pesan para overall; v1 "na" NO penaliza.
        worst = "ok"
        for bot, d in out.items():
            s = d.get("status", "err")
            if s == "na":
                continue
            if s in ("err", "no_state_doc", "no_ts_field", "parse_err"):
                if worst != "crit":
                    worst = "warn"
            elif s == "warn" and worst != "crit":
                worst = "warn"

        msg_parts = []
        for bot, d in out.items():
            if d.get("status") == "na":
                msg_parts.append(f"{bot}:N/A")
            elif d.get("age_min") is not None:
                msg_parts.append(f"{bot}:{d['age_min']:.0f}m")
            else:
                msg_parts.append(f"{bot}:{d.get('status', '?')}")
        msg = "Market data age | " + " • ".join(msg_parts)

        emoji = "🟢" if worst == "ok" else "⚠️"
        return _result("market_data", worst, out, f"{emoji} {msg}", by_bot=out)
    except Exception as e:
        logger.exception("market_data check failed")
        return _result("market_data", "err", None, f"💥 Error: {e}")


# ═══════════════════════════════════════════════════════════
#  Orquestador: corre los 7 checks y devuelve reporte agregado
# ═══════════════════════════════════════════════════════════
CHECKS = [
    check_schwab_tokens,
    check_anthropic_credits,
    check_gcp_billing,
    check_cloud_run_services,
    check_strategies_toggles,
    check_trades_24h,
    check_market_data_freshness,
]


def run_all_checks() -> dict:
    """Ejecuta los 7 checks en serie y devuelve dict agregado."""
    started = datetime.now(timezone.utc)
    results = []
    for check_fn in CHECKS:
        try:
            r = check_fn()
        except Exception as e:
            logger.exception(f"check {check_fn.__name__} crashed")
            r = _result(check_fn.__name__, "err", None, f"💥 crashed: {e}")
        results.append(r)

    # Agregado overall
    statuses = [r["status"] for r in results]
    if "crit" in statuses:
        overall = "crit"
    elif "err" in statuses:
        overall = "err"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "overall": overall,
        "started_at": started.isoformat(),
        "elapsed_sec": round(elapsed, 2),
        "checks": results,
        "n_ok":   sum(1 for s in statuses if s == "ok"),
        "n_warn": sum(1 for s in statuses if s == "warn"),
        "n_crit": sum(1 for s in statuses if s == "crit"),
        "n_err":  sum(1 for s in statuses if s == "err"),
    }
