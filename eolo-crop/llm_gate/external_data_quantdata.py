"""QuantData API integration — read-only fetch + cache para enrichment del snapshot.

Tier 1 endpoints (validados con SPY real 2026-06-01):
    /v1/options/tool/max-pain          (requires filter.expirationDate)
    /v1/options/tool/iv-rank           (requires lookBackPeriod + maturity)
    /v1/options/tool/exposure-by-strike (requires greekMode + representationMode)

Tier 1 endpoint pendiente de verificar shape:
    /v1/options/tool/net-drift         (wire LIVE post-hotfix #95, commit 0f77177)

Tier S endpoints (Sprint T1.A 2026-06-02, Master Plan v2.1 sec 5):
    /v1/options/tool/volatility-drift      (IV30/ARV20 → VRP + percentile 252d)
    /v1/options/tool/volatility-skew       (put/call 25Δ skew + ATM IV)
    /v1/options/tool/term-structure        (IV 7d/30d/60d + slope 60-7)
    /v1/options/tool/open-interest-by-strike (max OI call/put strikes)
    /v1/options/tool/max-pain-over-time    (max_pain trend 7d slope)

Diseño:
- API key vía Secret Manager con fallback a env var QUANTDATA_API_KEY.
- Cache in-memory por (endpoint, params) con TTL configurable.
- Defensive: fetch fail → log warning + retorna cache stale o None.
- Parsers tolerantes a variantes camelCase / snake_case / nested 'data'.
  Si el shape no matchea ningún variante conocido, loguea los keys reales y
  retorna None (NO inventa fields).

Wire al snapshot.py implementado en OPS-3 (PR #34, 1-jun-2026): snapshot.py:355-406
importa y llama get_max_pain / get_iv_rank / get_gex_regime / get_net_premium_drift.
Wire al prompt LLM cerrado en hotfix #95 (commit 0f77177, 2-jun-2026): MarketSnapshot
Pydantic schema declara los 11 fields QD.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from loguru import logger


def _build_ssl_context() -> ssl.SSLContext:
    """SSL context con CA bundle robusto.

    Prefiere `certifi` (presente vía google-auth en Cloud Run y en dev Mac);
    si no está, cae al default del sistema. Esto evita el clásico
    CERTIFICATE_VERIFY_FAILED en macOS Python.
    """
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CTX: ssl.SSLContext = _build_ssl_context()

# ── Constantes ────────────────────────────────────────────────────────
API_BASE: str = "https://api.quantdata.us/v1"
_SECRET_PATH: str = (
    "projects/eolo-schwab-agent/secrets/quantdata-api-key/versions/latest"
)
_HTTP_TIMEOUT: int = 5

# TTLs por endpoint (segundos).
_TTL_MAX_PAIN: int = 300   # 5 min — max pain shifts lento intraday
_TTL_IV_RANK: int = 1800   # 30 min — rolling window stable
_TTL_GEX: int = 300        # 5 min — GEX intraday moves
_TTL_DRIFT: int = 120      # 2 min — net drift cambia rápido
# Sprint T1.A — Tier S TTLs
_TTL_VOL_DRIFT: int = 300       # 5 min — VRP shifts moderado
_TTL_VOL_SKEW: int = 900        # 15 min — skew estable intraday
_TTL_TERM_STRUCT: int = 3600    # 60 min — term structure muy estable
_TTL_OI_BY_STRIKE: int = 3600   # 60 min — OI refresh diario
_TTL_MAX_PAIN_OVER_TIME: int = 86400  # diario — trend 7d slope

# ── State ─────────────────────────────────────────────────────────────
_API_KEY: Optional[str] = None
_CACHE: dict[str, dict[str, Any]] = {}  # {cache_key: {"ts": float, "value": dict}}


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════
def _get_api_key() -> str:
    """Lazy-load la API key de Secret Manager con fallback a env var.

    Returns:
        Key string, o "" si ni Secret Manager ni env var están disponibles.
    """
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    try:
        from google.cloud import secretmanager  # type: ignore
        client = secretmanager.SecretManagerServiceClient()
        resp = client.access_secret_version(request={"name": _SECRET_PATH})
        _API_KEY = resp.payload.data.decode("utf-8").strip()
    except Exception as e:
        logger.warning(f"[quantdata] secret manager failed: {e}; trying env")
        _API_KEY = os.environ.get("QUANTDATA_API_KEY", "")
    return _API_KEY or ""


def _post(
    endpoint: str,
    body: dict,
    cache_key: str = "",
    ttl: int = _TTL_MAX_PAIN,
) -> Optional[dict]:
    """POST genérico contra QuantData. Cache aggressive + stale fallback.

    Args:
        endpoint: Path relativo (ej `/options/tool/max-pain`).
        body: Payload JSON.
        cache_key: Key para cache lookup; "" → no cache.
        ttl: TTL del cache en segundos.

    Returns:
        Dict decodificado del JSON response, o None si fetch falla y no hay
        cache stale.
    """
    now = time.time()
    cached = _CACHE.get(cache_key) if cache_key else None
    if cached and (now - cached["ts"]) < ttl:
        return cached["value"]

    api_key = _get_api_key()
    if not api_key:
        logger.warning("[quantdata] no API key available; skip fetch")
        return cached["value"] if cached else None

    req = urllib.request.Request(
        f"{API_BASE}{endpoint}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body_snippet = e.read()[:200].decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        logger.warning(f"[quantdata] {endpoint} HTTP {e.code}: {body_snippet}")
        return cached["value"] if cached else None
    except Exception as e:
        logger.warning(f"[quantdata] {endpoint} failed: {e}")
        return cached["value"] if cached else None

    if cache_key:
        _CACHE[cache_key] = {"ts": now, "value": data}
    return data


def _pick(data: Any, *paths: str) -> Optional[Any]:
    """Busca el primer path que matchee. Cada path puede ser:

    - Key simple: "max_pain_strike"
    - Path con puntos: "data.maxPainStrike"

    Returns:
        El primer valor no-None encontrado, o None.
    """
    if not isinstance(data, dict):
        return None
    for p in paths:
        cur: Any = data
        ok = True
        for seg in p.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _log_shape_mismatch(endpoint: str, expected: list[str], actual_keys: list) -> None:
    """Loguea un warning con los keys reales del response cuando el parser
    no encuentra los fields esperados. Ayuda a ajustar el parser sin
    inventar nombres."""
    logger.warning(
        f"[quantdata] {endpoint} shape mismatch: expected {expected}, "
        f"got keys={list(actual_keys)[:20]}"
    )


# ══════════════════════════════════════════════════════════════════════
#  Endpoints públicos
# ══════════════════════════════════════════════════════════════════════
def get_max_pain(ticker: str, expiration_date: str) -> Optional[dict]:
    """Max pain strike para `ticker` en `expiration_date`.

    Use case: detectar pin bias del próximo Wed/Fri SPY expiry.

    Args:
        ticker: e.g. "SPY".
        expiration_date: ISO date "YYYY-MM-DD".

    Returns:
        {"max_pain_strike": float, "stock_price": float, "distance_pct": float}
        o None si fetch/parse falla.
    """
    body = {"filter": {"ticker": ticker, "expirationDate": expiration_date}}
    cache_key = f"maxpain:{ticker}:{expiration_date}"
    raw = _post("/options/tool/max-pain", body, cache_key=cache_key, ttl=_TTL_MAX_PAIN)
    if not raw:
        return None

    max_pain_strike = raw.get("maxPainStrikePrice")
    stock_price = raw.get("stockPrice")
    if max_pain_strike is None or stock_price is None:
        _log_shape_mismatch(
            "max-pain",
            ["maxPainStrikePrice", "stockPrice"],
            list(raw.keys()),
        )
        return None

    max_pain_strike = float(max_pain_strike)
    stock_price = float(stock_price)
    distance_pct = (
        ((stock_price - max_pain_strike) / max_pain_strike) * 100.0
        if max_pain_strike != 0
        else 0.0
    )
    return {
        "max_pain_strike": max_pain_strike,
        "stock_price":     stock_price,
        "distance_pct":    round(distance_pct, 3),
    }


def get_iv_rank(
    ticker: str,
    look_back_period: int = 30,
    maturity: int = 7,
) -> Optional[dict]:
    """IV rank (call + put) para `ticker` en una ventana rolling.

    Rank = (lastIv - windowMinIv) / (windowMaxIv - windowMinIv) * 100.

    Use case: IV rank < 20 → premium pobre, reduce confidence.

    Args:
        ticker: e.g. "SPY".
        look_back_period: Días de la rolling window (default 30).
        maturity: DTE target para la IV (default 7).

    Returns:
        {"call_rank_pct": float, "put_rank_pct": float,
         "call_last_iv": float, "put_last_iv": float}
        o None si fetch/parse falla.
    """
    body = {
        "filter": {"ticker": ticker},
        "lookBackPeriod": look_back_period,
        "maturity": maturity,
    }
    cache_key = f"ivrank:{ticker}:{look_back_period}:{maturity}"
    raw = _post("/options/tool/iv-rank", body, cache_key=cache_key, ttl=_TTL_IV_RANK)
    if not raw:
        return None

    data = raw.get("data") or {}
    if not data:
        _log_shape_mismatch("iv-rank", ["data"], list(raw.keys()))
        return None

    # Las keys son fechas (YYYY-MM-DD); sort ascending y agarrar la última.
    sorted_dates = sorted(data.keys())
    if not sorted_dates:
        return None
    last_date = sorted_dates[-1]
    last_entry = data.get(last_date) or {}
    contract_iv = last_entry.get("contractTypeToIVData") or {}
    call_data = contract_iv.get("CALL") or {}
    put_data = contract_iv.get("PUT") or {}

    call_last = call_data.get("lastIv")
    call_max  = call_data.get("windowMaxIv")
    call_min  = call_data.get("windowMinIv")
    put_last  = put_data.get("lastIv")
    put_max   = put_data.get("windowMaxIv")
    put_min   = put_data.get("windowMinIv")

    if None in (call_last, call_max, call_min, put_last, put_max, put_min):
        logger.warning(
            f"[quantdata] iv-rank missing fields in {last_date}: "
            f"CALL={call_data}, PUT={put_data}"
        )
        return None

    def _rank(last_val: float, max_val: float, min_val: float) -> float:
        if max_val == min_val:
            return 50.0  # neutral si no hay range
        return ((last_val - min_val) / (max_val - min_val)) * 100.0

    return {
        "call_rank_pct": round(_rank(call_last, call_max, call_min), 1),
        "put_rank_pct":  round(_rank(put_last,  put_max,  put_min),  1),
        "call_last_iv":  float(call_last),
        "put_last_iv":   float(put_last),
        "session_date":  last_date,
    }


def get_gex_regime(ticker: str) -> Optional[dict]:
    """GEX (gamma exposure) aggregate + regime label.

    Thresholds (USD gamma notional, brief):
        > 5e9        → "positive_high"
        1e9..5e9     → "positive_low"
        -1e9..1e9    → "flip_zone"
        < -1e9       → "negative"

    Use case: GEX positive → pin → SELL premium A+. Negative → vol expansion.

    Args:
        ticker: e.g. "SPY".

    Returns:
        {"total_gamma": float, "regime": str, "stock_price": float,
         "max_call_strike": float, "max_put_strike": float}
        o None si fetch/parse falla.
    """
    body = {
        "filter": {"ticker": ticker},
        "greekMode": "GAMMA",
        "representationMode": "RAW",
    }
    cache_key = f"gex:{ticker}"
    raw = _post(
        "/options/tool/exposure-by-strike",
        body,
        cache_key=cache_key,
        ttl=_TTL_GEX,
    )
    if not raw:
        return None

    data = raw.get("data") or {}
    ticker_upper = ticker.upper()
    ticker_data = data.get(ticker_upper) or {}
    if not ticker_data:
        _log_shape_mismatch(
            "exposure-by-strike",
            [f"data.{ticker_upper}"],
            list(data.keys()),
        )
        return None

    exposure_map = ticker_data.get("exposureMap") or {}
    stock_price = float(ticker_data.get("stockPrice") or 0.0)

    if not exposure_map:
        logger.warning(f"[quantdata] gex empty exposureMap for {ticker_upper}")
        return None

    total_call_gamma = 0.0
    total_put_gamma = 0.0
    max_call_strike: Optional[float] = None
    max_call_value = 0.0
    max_put_strike: Optional[float] = None
    max_put_value = 0.0

    for _expiry, strikes in exposure_map.items():
        if not isinstance(strikes, dict):
            continue
        for strike_str, exposures in strikes.items():
            try:
                strike = float(strike_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(exposures, dict):
                continue
            call_exp = float(exposures.get("callExposure") or 0)
            put_exp = float(exposures.get("putExposure") or 0)
            total_call_gamma += call_exp
            total_put_gamma += put_exp
            if call_exp > max_call_value:
                max_call_value = call_exp
                max_call_strike = strike
            if abs(put_exp) > abs(max_put_value):
                max_put_value = put_exp
                max_put_strike = strike

    total_gamma = total_call_gamma + total_put_gamma

    # TODO calibrate-2026-06: thresholds placeholder basados en 1 sample SPY
    # (total_gamma ≈ 6.97e+05 2026-06-01). El brief asumía e+09 pero el API
    # devuelve gamma en unidades pre-normalizadas (no multiplicado por 100
    # shares). Necesita 7 días de SPY data + comparación con SpotGamma /
    # Squeezemetrics para umbrales finales. Por ahora escalados 1000x menos
    # para que la clasificación no caiga siempre en flip_zone. Re-calibrar
    # antes de wire al snapshot productivo.
    if total_gamma > 5e6:
        regime = "positive_high"
    elif total_gamma > 1e6:
        regime = "positive_low"
    elif total_gamma > -1e6:
        regime = "flip_zone"
    else:
        regime = "negative"

    return {
        "total_gamma":     round(total_gamma, 2),
        "regime":          regime,
        "stock_price":     stock_price,
        "max_call_strike": max_call_strike,
        "max_put_strike":  max_put_strike,
    }


def get_net_premium_drift(ticker: str) -> Optional[dict]:
    """Net call/put premium drift intraday.

    Shape verificado SPY 2026-06-01:
        {"data": {
            "<unix_ms_str>": {
                "netCallPremium": float,
                "netPutPremium":  float,
                "stockPrice":     float,
            },
            "<unix_ms_str>": {...},
        }}

    Las keys del dict `data` son timestamps unix-ms (string). Tomamos el
    bucket más reciente y exponemos el ts como `timestamp_ms`.

    Use case: detectar institutional sentiment shifts intraday.

    Args:
        ticker: e.g. "SPY".

    Returns:
        {"net_call_premium": float, "net_put_premium": float,
         "stock_price": float, "timestamp_ms": int}
        o None si fetch/parse falla.
    """
    body = {"filter": {"ticker": ticker}}
    cache_key = f"drift:{ticker}"
    raw = _post(
        "/options/tool/net-drift", body, cache_key=cache_key, ttl=_TTL_DRIFT,
    )
    if not raw:
        return None

    data = raw.get("data") or {}
    if not data:
        _log_shape_mismatch(
            "net-drift",
            ["data"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    try:
        sorted_ts = sorted(data.keys(), key=lambda k: int(k))
    except (ValueError, TypeError):
        logger.warning(
            f"[quantdata] net-drift unexpected key format: {list(data.keys())[:3]}"
        )
        return None

    if not sorted_ts:
        return None

    last_ts_key = sorted_ts[-1]
    last_bucket = data.get(last_ts_key) or {}

    net_call = last_bucket.get("netCallPremium")
    net_put = last_bucket.get("netPutPremium")
    stock_price = last_bucket.get("stockPrice")

    if net_call is None and net_put is None:
        _log_shape_mismatch(
            "net-drift",
            ["netCallPremium", "netPutPremium"],
            list(last_bucket.keys()),
        )
        return None

    return {
        "net_call_premium": float(net_call) if net_call is not None else 0.0,
        "net_put_premium":  float(net_put)  if net_put  is not None else 0.0,
        "stock_price":      float(stock_price) if stock_price is not None else 0.0,
        "timestamp_ms":     int(last_ts_key),
    }


# ══════════════════════════════════════════════════════════════════════
#  Tier S endpoints (Sprint T1.A 2026-06-02)
#  Shapes asumidas del provider — parsers tolerantes (log + None si difiere).
# ══════════════════════════════════════════════════════════════════════
def get_volatility_drift(ticker: str) -> Optional[dict]:
    """Volatility drift: IV implícita vs realizada (VRP) + percentile 252d.

    Master Plan v2.1 sec 5 Tier S #2 + sec 6.2 (VRP scoring).

    VRP = IV(30d) - ARV(20d). Percentile 252d permite scoring rich/fair/cheap.

    Args:
        ticker: e.g. "SPY".

    Returns:
        {"iv_30d": float, "arv_20d": float, "vrp_value": float,
         "vrp_percentile_252d": float} o None si fetch/parse falla.
    """
    body = {"filter": {"ticker": ticker}}
    cache_key = f"voldrift:{ticker}"
    raw = _post(
        "/options/tool/volatility-drift",
        body,
        cache_key=cache_key,
        ttl=_TTL_VOL_DRIFT,
    )
    if not raw:
        return None

    iv_30d = _as_float(
        _pick(raw, "iv_30d", "iv30d", "implied_volatility_30d",
              "data.iv_30d", "data.iv30d")
    )
    arv_20d = _as_float(
        _pick(raw, "arv_20d", "arv20d", "realized_volatility_20d",
              "data.arv_20d", "data.arv20d")
    )
    vrp_value = _as_float(
        _pick(raw, "vrp_value", "vrp", "vrpValue",
              "data.vrp_value", "data.vrp")
    )
    if vrp_value is None and iv_30d is not None and arv_20d is not None:
        vrp_value = iv_30d - arv_20d
    vrp_pct = _as_float(
        _pick(raw, "vrp_percentile_252d", "vrpPercentile252d",
              "percentile_252d", "data.vrp_percentile_252d",
              "data.percentile_252d")
    )

    if iv_30d is None and arv_20d is None and vrp_value is None and vrp_pct is None:
        _log_shape_mismatch(
            "volatility-drift",
            ["iv_30d", "arv_20d", "vrp_value", "vrp_percentile_252d"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    return {
        "iv_30d":              iv_30d,
        "arv_20d":             arv_20d,
        "vrp_value":           vrp_value,
        "vrp_percentile_252d": vrp_pct,
    }


def get_volatility_skew(ticker: str, expiration_date: str) -> Optional[dict]:
    """Volatility skew: IV(25Δ put), IV(25Δ call), ATM IV.

    Master Plan v2.1 sec 5 Tier S #4. Skew = IV(wing) - IV(ATM).

    Args:
        ticker: e.g. "SPY".
        expiration_date: ISO "YYYY-MM-DD".

    Returns:
        {"put_skew_25d": float, "call_skew_25d": float, "atm_iv": float}
        o None si fetch/parse falla.
    """
    body = {"filter": {"ticker": ticker, "expirationDate": expiration_date}}
    cache_key = f"volskew:{ticker}:{expiration_date}"
    raw = _post(
        "/options/tool/volatility-skew",
        body,
        cache_key=cache_key,
        ttl=_TTL_VOL_SKEW,
    )
    if not raw:
        return None

    put_skew = _as_float(
        _pick(raw, "put_skew_25d", "putSkew25d", "skew_put_25d",
              "data.put_skew_25d", "data.putSkew25d")
    )
    call_skew = _as_float(
        _pick(raw, "call_skew_25d", "callSkew25d", "skew_call_25d",
              "data.call_skew_25d", "data.callSkew25d")
    )
    atm_iv = _as_float(
        _pick(raw, "atm_iv", "atmIv", "atm_implied_volatility",
              "data.atm_iv", "data.atmIv")
    )

    if put_skew is None and call_skew is None and atm_iv is None:
        _log_shape_mismatch(
            "volatility-skew",
            ["put_skew_25d", "call_skew_25d", "atm_iv"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    return {
        "put_skew_25d":  put_skew,
        "call_skew_25d": call_skew,
        "atm_iv":        atm_iv,
    }


def get_term_structure(ticker: str) -> Optional[dict]:
    """Term structure: IV across maturities (7d/30d/60d) + slope.

    Master Plan v2.1 sec 5 Tier S #5. slope = iv_60d - iv_7d (contango/backw.).

    Args:
        ticker: e.g. "SPY".

    Returns:
        {"ts_iv_7d": float, "ts_iv_30d": float, "ts_iv_60d": float,
         "term_slope_60d_7d": float} o None.
    """
    body = {"filter": {"ticker": ticker}}
    cache_key = f"termstruct:{ticker}"
    raw = _post(
        "/options/tool/term-structure",
        body,
        cache_key=cache_key,
        ttl=_TTL_TERM_STRUCT,
    )
    if not raw:
        return None

    iv_7d = _as_float(
        _pick(raw, "ts_iv_7d", "iv_7d", "tsIv7d",
              "data.iv_7d", "data.ts_iv_7d")
    )
    iv_30d = _as_float(
        _pick(raw, "ts_iv_30d", "iv_30d", "tsIv30d",
              "data.iv_30d", "data.ts_iv_30d")
    )
    iv_60d = _as_float(
        _pick(raw, "ts_iv_60d", "iv_60d", "tsIv60d",
              "data.iv_60d", "data.ts_iv_60d")
    )
    slope = _as_float(
        _pick(raw, "term_slope_60d_7d", "slope_60d_7d", "termSlope60d7d",
              "data.term_slope_60d_7d")
    )
    if slope is None and iv_60d is not None and iv_7d is not None:
        slope = iv_60d - iv_7d

    if iv_7d is None and iv_30d is None and iv_60d is None:
        _log_shape_mismatch(
            "term-structure",
            ["ts_iv_7d", "ts_iv_30d", "ts_iv_60d"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    return {
        "ts_iv_7d":          iv_7d,
        "ts_iv_30d":         iv_30d,
        "ts_iv_60d":         iv_60d,
        "term_slope_60d_7d": slope,
    }


def get_oi_by_strike(ticker: str, expiration_date: str) -> Optional[dict]:
    """Open Interest distribution por strike. Extrae strike de max OI call/put.

    Master Plan v2.1 sec 5 Tier S #6. Refresh diario.

    Args:
        ticker: e.g. "SPY".
        expiration_date: ISO "YYYY-MM-DD".

    Returns:
        {"oi_max_call_strike": float, "oi_max_put_strike": float,
         "oi_total_call": float, "oi_total_put": float} o None.
    """
    body = {"filter": {"ticker": ticker, "expirationDate": expiration_date}}
    cache_key = f"oibystrike:{ticker}:{expiration_date}"
    raw = _post(
        "/options/tool/open-interest-by-strike",
        body,
        cache_key=cache_key,
        ttl=_TTL_OI_BY_STRIKE,
    )
    if not raw:
        return None

    # Si vienen pre-agregados, úsalos directos.
    max_call_strike = _as_float(
        _pick(raw, "oi_max_call_strike", "maxCallOiStrike",
              "data.oi_max_call_strike", "data.maxCallOiStrike")
    )
    max_put_strike = _as_float(
        _pick(raw, "oi_max_put_strike", "maxPutOiStrike",
              "data.oi_max_put_strike", "data.maxPutOiStrike")
    )
    total_call = _as_float(
        _pick(raw, "oi_total_call", "totalCallOi",
              "data.oi_total_call", "data.totalCallOi")
    )
    total_put = _as_float(
        _pick(raw, "oi_total_put", "totalPutOi",
              "data.oi_total_put", "data.totalPutOi")
    )

    # Fallback: si vienen por strike, agregar.
    if max_call_strike is None or max_put_strike is None:
        ticker_upper = ticker.upper()
        data = raw.get("data") if isinstance(raw, dict) else None
        strikes_map: Any = None
        if isinstance(data, dict):
            ticker_block = data.get(ticker_upper) or data.get(ticker)
            if isinstance(ticker_block, dict):
                strikes_map = (
                    ticker_block.get("oiByStrike")
                    or ticker_block.get("strikes")
                    or ticker_block
                )
            else:
                strikes_map = data

        if isinstance(strikes_map, dict):
            sum_call = 0.0
            sum_put = 0.0
            best_call_strike: Optional[float] = None
            best_call_oi = -1.0
            best_put_strike: Optional[float] = None
            best_put_oi = -1.0
            for strike_key, vals in strikes_map.items():
                try:
                    strike = float(strike_key)
                except (TypeError, ValueError):
                    continue
                if not isinstance(vals, dict):
                    continue
                call_oi = _as_float(
                    vals.get("callOi") or vals.get("call_oi")
                    or vals.get("openInterestCall")
                ) or 0.0
                put_oi = _as_float(
                    vals.get("putOi") or vals.get("put_oi")
                    or vals.get("openInterestPut")
                ) or 0.0
                sum_call += call_oi
                sum_put += put_oi
                if call_oi > best_call_oi:
                    best_call_oi = call_oi
                    best_call_strike = strike
                if put_oi > best_put_oi:
                    best_put_oi = put_oi
                    best_put_strike = strike
            if max_call_strike is None:
                max_call_strike = best_call_strike
            if max_put_strike is None:
                max_put_strike = best_put_strike
            if total_call is None:
                total_call = sum_call
            if total_put is None:
                total_put = sum_put

    if max_call_strike is None and max_put_strike is None:
        _log_shape_mismatch(
            "open-interest-by-strike",
            ["oi_max_call_strike", "oi_max_put_strike"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    return {
        "oi_max_call_strike": max_call_strike,
        "oi_max_put_strike":  max_put_strike,
        "oi_total_call":      total_call,
        "oi_total_put":       total_put,
    }


def get_max_pain_over_time(ticker: str) -> Optional[dict]:
    """Max pain trend (slope) últimos 7 días.

    Master Plan v2.1 sec 5 Tier S #7. Refresh diario.

    Args:
        ticker: e.g. "SPY".

    Returns:
        {"max_pain_trend_7d": float} (slope o delta), o None.
    """
    body = {"filter": {"ticker": ticker}}
    cache_key = f"maxpain_ot:{ticker}"
    raw = _post(
        "/options/tool/max-pain-over-time",
        body,
        cache_key=cache_key,
        ttl=_TTL_MAX_PAIN_OVER_TIME,
    )
    if not raw:
        return None

    # Primero, valor pre-agregado si viene.
    trend = _as_float(
        _pick(raw, "max_pain_trend_7d", "trend_7d", "slope_7d",
              "data.max_pain_trend_7d", "data.trend_7d", "data.slope_7d")
    )

    if trend is None:
        # Fallback: derivar de serie temporal {date: max_pain_strike}.
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict) and data:
            try:
                sorted_keys = sorted(data.keys())
            except TypeError:
                sorted_keys = []
            # Tomar últimos hasta 7 puntos
            tail_keys = sorted_keys[-7:] if sorted_keys else []
            values: list[float] = []
            for k in tail_keys:
                v = data.get(k)
                if isinstance(v, dict):
                    mp = _as_float(
                        v.get("maxPainStrikePrice") or v.get("max_pain_strike")
                        or v.get("maxPain")
                    )
                else:
                    mp = _as_float(v)
                if mp is not None:
                    values.append(mp)
            if len(values) >= 2:
                trend = values[-1] - values[0]

    if trend is None:
        _log_shape_mismatch(
            "max-pain-over-time",
            ["max_pain_trend_7d"],
            list(raw.keys()) if isinstance(raw, dict) else [type(raw).__name__],
        )
        return None

    return {"max_pain_trend_7d": trend}


# Public re-exports (explicit para auditoría de wire).
__all__ = [
    "get_max_pain",
    "get_iv_rank",
    "get_gex_regime",
    "get_net_premium_drift",
    # Sprint T1.A — Tier S
    "get_volatility_drift",
    "get_volatility_skew",
    "get_term_structure",
    "get_oi_by_strike",
    "get_max_pain_over_time",
]
