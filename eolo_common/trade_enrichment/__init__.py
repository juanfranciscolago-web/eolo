# ============================================================
#  eolo_common.trade_enrichment
#
#  Helpers compartidos por v1 / v2 / crypto para enriquecer el
#  payload que se persiste por cada trade en Firestore. El objetivo
#  es que todos los bots generen los mismos campos analíticos sin
#  duplicar lógica:
#
#     entry_price, hold_seconds, vix_snapshot, vix_bucket,
#     spy_ret_5d, session_bucket, slippage_bps,
#     trade_number_today, entry_reason, exit_reason
#
#  Todos los campos son opcionales por diseño: si falta input para
#  computar uno, se devuelve None y sheets-sync lo renderiza vacío.
#
#  Ejemplo de uso dentro de _log_trade:
#
#      from eolo_common.trade_enrichment import build_enrichment
#
#      enrich = build_enrichment(
#          ts_utc           = datetime.now(timezone.utc),
#          asset_class      = "stock",          # o "crypto"
#          side             = "BUY",             # o "SELL"
#          mode             = "PAPER",
#          expected_price   = price,
#          fill_price       = price,
#          macro_feeds      = feeds,             # puede ser None
#          spy_ret_5d_fn    = None,              # lazy fetcher opcional
#          entry_price      = stored_entry_price,
#          opened_at_ts     = opened_ts,         # epoch seconds
#          reason           = result.get("reason", ""),
#          counter_key      = "eolo_v1",
#      )
#      trade_payload.update(enrich)
# ============================================================
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Literal, Optional


# ── Slippage simulation (paper only) ──────────────────────────
# ~2 bps típico, con ruido gaussiano, acotado a [-6, 6] bps.
_SLIPPAGE_MEAN_BPS = 0.0      # media 0 → no sesgo sistemático
_SLIPPAGE_STD_BPS  = 2.0      # σ = 2bps
_SLIPPAGE_MIN_BPS  = -6.0
_SLIPPAGE_MAX_BPS  = 6.0


def simulate_slippage_bps(rng: Optional[random.Random] = None) -> float:
    """Slippage sintético para paper/testnet (~2bps σ). En LIVE
    preferir `real_slippage_bps(expected, fill)`."""
    r = rng or random
    s = r.gauss(_SLIPPAGE_MEAN_BPS, _SLIPPAGE_STD_BPS)
    return round(max(_SLIPPAGE_MIN_BPS, min(_SLIPPAGE_MAX_BPS, s)), 2)


def real_slippage_bps(expected_price: Optional[float],
                      fill_price:     Optional[float]) -> Optional[float]:
    """Slippage real en basis points: (fill - expected) / expected × 10000.
    Devuelve None si falta cualquier precio. Signo: positivo = peor fill."""
    if not expected_price or not fill_price:
        return None
    if expected_price <= 0:
        return None
    return round((fill_price - expected_price) / expected_price * 10_000.0, 2)


# ── VIX bucket ────────────────────────────────────────────────
def vix_bucket(vix: Optional[float]) -> Optional[str]:
    if vix is None:
        return None
    if vix < 15.0:   return "low"
    if vix < 20.0:   return "medium"
    if vix < 30.0:   return "high"
    return "panic"


# ── Session bucket ────────────────────────────────────────────
# Stocks: ventana NYSE en hora Nueva York (ET). La TZ conversion
# se hace con offset fijo (-5 EST / -4 EDT). Para evitar tener que
# lidiar con pytz / zoneinfo acá, usamos un cálculo directo desde
# UTC: NY = UTC - 4h durante DST (mar-nov), -5h fuera. Sheets-sync
# no usa este valor para lógica crítica — es descriptivo.
def _is_us_dst(dt_utc: datetime) -> bool:
    """Heurística simple: DST en US va del 2do domingo de marzo al
    1er domingo de noviembre. Para nuestros buckets alcanza."""
    y = dt_utc.year
    # 2do domingo de marzo
    mar = datetime(y, 3, 1, tzinfo=timezone.utc)
    dst_start = mar.replace(day=(14 - mar.weekday()) % 7 + 1)
    # 1er domingo de noviembre
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    dst_end = nov.replace(day=((6 - nov.weekday()) % 7) + 1)
    return dst_start <= dt_utc < dst_end


def _us_minutes_from_midnight(dt_utc: datetime) -> int:
    """Minutos desde 00:00 hora NY (ET) del día actual en ET."""
    offset_hours = 4 if _is_us_dst(dt_utc) else 5
    total_min_utc = dt_utc.hour * 60 + dt_utc.minute
    total_min_et  = total_min_utc - offset_hours * 60
    return total_min_et % (24 * 60)


def session_bucket(ts_utc: datetime,
                   asset_class: Literal["stock", "crypto"] = "stock") -> str:
    """
    Bucket de sesión.
    Stocks (regular NYSE 9:30–16:00 ET):
        open_30m   9:30–10:00
        morning   10:00–12:00
        midday    12:00–14:00
        afternoon 14:00–15:30
        close_30m 15:30–16:00
        pre        pre-open 4:00–9:30 ET
        post       after-hours 16:00–20:00 ET
        overnight  resto

    Crypto (24h, referencia UTC):
        asia       23:00–07:00 UTC
        europe     07:00–14:00 UTC
        us_overlap 14:00–20:00 UTC
        late_us    20:00–23:00 UTC
    """
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)

    if asset_class == "crypto":
        h = ts_utc.hour
        if 7 <= h < 14:   return "europe"
        if 14 <= h < 20:  return "us_overlap"
        if 20 <= h < 23:  return "late_us"
        return "asia"  # 23-7 UTC

    # Stocks — cortes en hora ET
    m = _us_minutes_from_midnight(ts_utc)
    if   m < 4  * 60:                        return "overnight"
    elif m < 9  * 60 + 30:                   return "pre"
    elif m < 10 * 60:                        return "open_30m"
    elif m < 12 * 60:                        return "morning"
    elif m < 14 * 60:                        return "midday"
    elif m < 15 * 60 + 30:                   return "afternoon"
    elif m < 16 * 60:                        return "close_30m"
    elif m < 20 * 60:                        return "post"
    return "overnight"


# ── Trade-number-today counter (process-local, per bot) ───────
# Un counter por "key" (ej. "eolo_v1", "eolo_v2", "eolo_crypto").
# Se resetea cuando cambia el día UTC. Proceso-local; tras un
# restart arranca de 0 — aceptamos el costo porque Cloud Run no
# suele reiniciar mid-day y el contador es descriptivo.
_counter_lock = threading.Lock()
_counters: dict[str, tuple[str, int]] = {}   # key → (YYYY-MM-DD, count)


def bump_trade_counter(counter_key: str, ts_utc: Optional[datetime] = None) -> int:
    """Incrementa el contador para `counter_key` y devuelve el nuevo valor.
    Reset a 0 cuando cambia el día UTC."""
    if ts_utc is None:
        ts_utc = datetime.now(timezone.utc)
    elif ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)
    day = ts_utc.strftime("%Y-%m-%d")
    with _counter_lock:
        saved = _counters.get(counter_key)
        if saved is None or saved[0] != day:
            _counters[counter_key] = (day, 1)
            return 1
        new_count = saved[1] + 1
        _counters[counter_key] = (day, new_count)
        return new_count


# ── VIX / SPY de MacroFeeds (best-effort) ─────────────────────
def _safe_call(fn):
    try:
        return fn()
    except Exception:
        return None


def get_vix_snapshot(macro_feeds) -> Optional[float]:
    """Extrae el latest VIX si macro_feeds está disponible."""
    if macro_feeds is None:
        return None
    return _safe_call(lambda: macro_feeds.latest("VIX"))


def get_spy_ret_5d(spy_ret_5d_fn: Optional[Callable[[], Optional[float]]]) -> Optional[float]:
    """Callable opcional que devuelve el retorno SPY de 5 días.
    El bot lo inyecta solo si tiene cómo calcularlo (crypto bot
    típicamente pasa None y el campo queda vacío)."""
    if spy_ret_5d_fn is None:
        return None
    v = _safe_call(spy_ret_5d_fn)
    return float(v) if v is not None else None


# ── Builder principal ─────────────────────────────────────────
def build_enrichment(
    *,
    ts_utc:         Optional[datetime] = None,
    asset_class:    Literal["stock", "crypto"] = "stock",
    side:           Literal["BUY", "SELL"] = "BUY",
    mode:           str = "PAPER",
    expected_price: Optional[float] = None,
    fill_price:     Optional[float] = None,
    macro_feeds                        = None,
    spy_ret_5d_fn:  Optional[Callable[[], Optional[float]]] = None,
    entry_price:    Optional[float] = None,
    opened_at_ts:   Optional[float] = None,
    reason:         str = "",
    counter_key:    str = "eolo_unknown",
) -> dict:
    """
    Genera el dict de campos de enriquecimiento para mergear en el
    payload del trade. No persiste nada — el caller se encarga.
    """
    if ts_utc is None:
        ts_utc = datetime.now(timezone.utc)
    elif ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)

    # Slippage: en paper simulamos, en live calculamos de expected/fill
    is_paper = mode.upper() in ("PAPER", "TESTNET", "PAPER_TRADING")
    if is_paper:
        slip = simulate_slippage_bps()
    else:
        slip = real_slippage_bps(expected_price, fill_price)

    # VIX
    vix   = get_vix_snapshot(macro_feeds)
    vbuck = vix_bucket(vix)

    # SPY 5d
    spy5  = get_spy_ret_5d(spy_ret_5d_fn)

    # Session
    sbuck = session_bucket(ts_utc, asset_class=asset_class)

    # Counter
    trade_num = bump_trade_counter(counter_key, ts_utc=ts_utc)

    # hold_seconds + entry_price (solo en SELL)
    hold_sec: Optional[float] = None
    entry_p_out: Optional[float] = None
    if side == "SELL":
        entry_p_out = float(entry_price) if entry_price else None
        if opened_at_ts:
            hold_sec = round(max(0.0, time.time() - float(opened_at_ts)), 1)

    # entry_reason vs exit_reason (split desde `reason`)
    entry_reason = ""
    exit_reason  = ""
    reason_str   = (reason or "").strip()
    if side == "BUY":
        entry_reason = reason_str[:500]
    elif side == "SELL":
        exit_reason  = reason_str[:500]

    payload: dict = {}
    # Solo incluimos keys con valor — evita llenar Firestore con Nones.
    if entry_p_out is not None:       payload["entry_price"]       = entry_p_out
    if hold_sec is not None:          payload["hold_seconds"]      = hold_sec
    if vix is not None:               payload["vix_snapshot"]      = round(float(vix), 2)
    if vbuck is not None:             payload["vix_bucket"]        = vbuck
    if spy5 is not None:              payload["spy_ret_5d"]        = round(spy5, 4)
    payload["session_bucket"]     = sbuck
    if slip is not None:              payload["slippage_bps"]      = slip
    payload["trade_number_today"] = trade_num
    if entry_reason:                  payload["entry_reason"]      = entry_reason
    if exit_reason:                   payload["exit_reason"]       = exit_reason
    return payload


__all__ = [
    "build_enrichment",
    "simulate_slippage_bps",
    "real_slippage_bps",
    "vix_bucket",
    "session_bucket",
    "bump_trade_counter",
    "get_vix_snapshot",
    "get_spy_ret_5d",
]
