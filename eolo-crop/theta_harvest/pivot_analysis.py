# ============================================================
#  Pivot Analysis — Theta Harvest v2 (Eolo v2)
#
#  Tres componentes calculados diariamente con datos reales:
#
#  1. PIVOTS — 3 sistemas (Standard, Camarilla, Fibonacci)
#     Woodie's removido: prácticamente idéntico a Standard (±1-2 pts).
#     Average = promedio simple de los 3 sistemas por nivel.
#     Risk zones (thresholds del análisis de Juan):
#       NO_TRADE  < 0.25% del PP   → S1/R1, demasiado cerca ATM
#       MID       0.25%–0.51%      → S2/R2, crédito razonable
#       LOW       0.51%–0.80%      → S3/R3, más conservador
#       VERY_LOW  > 0.80%          → S4/R4, muy OTM
#
#  2. ATR — Solo daily (hourly removido: demasiado chico para entradas)
#     ATR_day = prev High − prev Low
#     Gate: price movió > 2× ATR_day desde prev close → no entrar
#     Niveles: ±1 y ±2 ATR (±3 removido: no se usa en ninguna decisión)
#
#  3. SECTOR DIRECTION — 11 ETFs ponderados (SPY) / 10 (TQQQ/QQQ)
#     Suma ponderada de cambios % → bullish / bearish / neutral
#     Determina spread direction: PUT vs CALL credit spread
#
#  Delta targets por risk level:
#    VERY_LOW  → Δ 0.10–0.16
#    LOW       → Δ 0.16–0.22
#    MID       → Δ 0.22–0.30
#    NO_TRADE  → no entrar
# ============================================================
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests
from loguru import logger

# ── Auth helper ───────────────────────────────────────────
_BASE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.join(_BASE, "..")
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
try:
    from helpers import get_access_token
except ImportError:
    def get_access_token(): return None

# ── Schwab endpoints ──────────────────────────────────────
SCHWAB_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
SCHWAB_QUOTES_URL  = "https://api.schwabapi.com/marketdata/v1/quotes"

# ──────────────────────────────────────────────────────────
#  THRESHOLDS DE RIESGO
#  Distancia del precio actual al PP promedio como % del PP.
# ──────────────────────────────────────────────────────────
DIST_VERY_LOW_PCT = 0.80   # > 0.80%  → VERY LOW risk
DIST_LOW_PCT      = 0.51   # 0.51%–0.80% → LOW risk
DIST_MID_PCT      = 0.25   # 0.25%–0.51% → MID risk
# < 0.25% → NO_TRADE (S1/R1 zone)

# ATR gate: no entrar si price ya movió > N × ATR_day desde prev close
ATR_GATE_MULTIPLIER = 2.0

# Delta targets por risk level
DELTA_BY_RISK: dict[str, tuple[float, float]] = {
    "VERY_LOW": (0.10, 0.16),
    "LOW":      (0.16, 0.22),
    "MID":      (0.22, 0.30),
    "NO_TRADE": (0.0,  0.0),
}

# ──────────────────────────────────────────────────────────
#  SECTOR ETFs — SPY (pesos S&P 500) y TQQQ (pesos QQQ)
# ──────────────────────────────────────────────────────────
SECTOR_WEIGHTS_SPY: dict[str, float] = {
    "XLK":  0.30,   # Technology
    "XLF":  0.13,   # Financials
    "XLV":  0.13,   # Health Care
    "XLY":  0.10,   # Consumer Discretionary
    "XLC":  0.08,   # Communication Services
    "XLI":  0.08,   # Industrials
    "XLP":  0.06,   # Consumer Staples
    "XLE":  0.04,   # Energy
    "XLU":  0.03,   # Utilities
    "XLB":  0.025,  # Materials
    "XLRE": 0.025,  # Real Estate
}

SECTOR_WEIGHTS_TQQQ: dict[str, float] = {
    "XLK":  0.48,   # Technology       ← ~48% del QQQ
    "XLC":  0.16,   # Comm. Services   ← GOOGL, META
    "XLY":  0.14,   # Consumer Discr.  ← AMZN
    "XLV":  0.06,   # Health Care
    "XLI":  0.05,   # Industrials
    "XLF":  0.04,   # Financials
    "XLP":  0.03,   # Consumer Staples
    "XLE":  0.02,   # Energy
    "XLB":  0.01,   # Materials
    "XLU":  0.01,   # Utilities
}

# Umbral mínimo de cambio neto para confirmar dirección
SECTOR_DIRECTION_THRESHOLD = 0.15   # 0.15% ponderado

# NYSE Tick / Advance-Decline — confirmación intraday de dirección
TICK_EXTREME_THRESHOLD  = 1000   # |TICK| > 1000 → bloqueador (mercado en pánico/euforia)
TICK_CONFIRM_THRESHOLD  = 300    # |TICK| > 300  → señal direccional significativa
AD_CONFIRM_THRESHOLD    = 0      # AD > 0 = más avances que retrocesos


# ── Data classes ──────────────────────────────────────────

@dataclass
class PivotLevels:
    """Niveles de un único sistema de pivots."""
    system: str
    pp:  float
    r1:  float = 0.0
    r2:  float = 0.0
    r3:  float = 0.0
    r4:  float = 0.0
    s1:  float = 0.0
    s2:  float = 0.0
    s3:  float = 0.0
    s4:  float = 0.0


@dataclass
class AveragedPivotLevels:
    """
    Promedio de los 3 sistemas (Standard + Camarilla + Fibonacci)
    por nivel. None = nivel no disponible en ningún sistema.
    """
    pp:  float
    r1:  Optional[float] = None
    r2:  Optional[float] = None
    r3:  Optional[float] = None
    r4:  Optional[float] = None
    s1:  Optional[float] = None
    s2:  Optional[float] = None
    s3:  Optional[float] = None
    s4:  Optional[float] = None

    def zone_for_price(self, price: float) -> str:
        """Risk level según distancia del precio al PP promedio."""
        if self.pp <= 0:
            return "MID"
        dist_pct = abs(price - self.pp) / self.pp * 100
        if dist_pct >= DIST_VERY_LOW_PCT:
            return "VERY_LOW"
        elif dist_pct >= DIST_LOW_PCT:
            return "LOW"
        elif dist_pct >= DIST_MID_PCT:
            return "MID"
        else:
            return "NO_TRADE"

    def support_target(self, risk_level: str) -> Optional[float]:
        """Nivel de soporte promedio para el risk level dado (para PUT spreads)."""
        return {"MID": self.s2, "LOW": self.s3, "VERY_LOW": self.s4}.get(risk_level)

    def resistance_target(self, risk_level: str) -> Optional[float]:
        """Nivel de resistencia promedio para el risk level dado (para CALL spreads)."""
        return {"MID": self.r2, "LOW": self.r3, "VERY_LOW": self.r4}.get(risk_level)


@dataclass
class ATRContext:
    """ATR diario del día anterior. Hourly y ±3ATR removidos — no aportan a entradas."""
    atr_day:    float
    prev_close: float
    prev_high:  float
    prev_low:   float
    up_1atr:    float = 0.0
    up_2atr:    float = 0.0
    down_1atr:  float = 0.0
    down_2atr:  float = 0.0

    def __post_init__(self):
        self.up_1atr   = round(self.prev_close + self.atr_day,     2)
        self.up_2atr   = round(self.prev_close + 2 * self.atr_day, 2)
        self.down_1atr = round(self.prev_close - self.atr_day,     2)
        self.down_2atr = round(self.prev_close - 2 * self.atr_day, 2)

    def is_extended(self, current_price: float) -> bool:
        """True si price movió > 2× ATR_day desde el close previo."""
        return abs(current_price - self.prev_close) > ATR_GATE_MULTIPLIER * self.atr_day

    def atr_zone(self, current_price: float) -> str:
        if current_price >= self.up_2atr:   return "+2atr"
        if current_price >= self.up_1atr:   return "+1atr"
        if current_price <= self.down_2atr: return "-2atr"
        if current_price <= self.down_1atr: return "-1atr"
        return "base"


@dataclass
class TickADContext:
    """
    NYSE Tick e Advance-Decline — confirmación intraday al momento de entrar.
    Se fetcha en cada intento de entrada (no se cachea como los pivots).

    confirmation:
      "confirm"  → Tick y A/D alinean con el spread_type propuesto → entrar
      "neutral"  → señal mixta o débil → entrar igual (sin confirmación extra)
      "skip"     → Tick y A/D contradicen la dirección → saltar esta entrada
      "block"    → |TICK| > 1000 (pánico/euforia extrema) → no entrar ningún spread
    """
    tick:         float   # NYSE $TICK (net upticks − downticks)
    ad:           float   # NYSE $ADD (advancing − declining issues)
    tick_extreme: bool    # True si |TICK| > 1000
    confirmation: str = "neutral"

    def __post_init__(self):
        self.tick_extreme = abs(self.tick) > TICK_EXTREME_THRESHOLD

    def evaluate(self, spread_type: str) -> str:
        """
        Evalúa si la señal de Tick/AD confirma, contradice o es neutral
        respecto al spread_type propuesto.
          PUT spread  → necesitamos mercado bullish (TICK > 0, AD > 0)
          CALL spread → necesitamos mercado bearish (TICK < 0, AD < 0)
        """
        if self.tick_extreme:
            self.confirmation = "block"
            return "block"

        is_put = "put" in spread_type  # PUT spread = dirección bullish

        tick_bullish = self.tick >  TICK_CONFIRM_THRESHOLD
        tick_bearish = self.tick < -TICK_CONFIRM_THRESHOLD
        ad_bullish   = self.ad   >  AD_CONFIRM_THRESHOLD
        ad_bearish   = self.ad   <  AD_CONFIRM_THRESHOLD

        if is_put:
            if tick_bullish and ad_bullish:
                result = "confirm"   # mercado sube → PUT spread confirmado
            elif tick_bearish and ad_bearish:
                result = "skip"      # mercado baja → no entrar PUT spread ahora
            else:
                result = "neutral"   # señal débil o mixta → proceder igual
        else:
            if tick_bearish and ad_bearish:
                result = "confirm"   # mercado baja → CALL spread confirmado
            elif tick_bullish and ad_bullish:
                result = "skip"      # mercado sube → no entrar CALL spread ahora
            else:
                result = "neutral"

        self.confirmation = result
        return result


@dataclass
class SectorDirection:
    """Dirección de mercado desde ETFs sectoriales ponderados."""
    weighted_change_pct: float
    direction:           str = ""
    spread_type:         str = ""
    top_movers:          list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.weighted_change_pct > SECTOR_DIRECTION_THRESHOLD:
            self.direction   = "bullish"
            self.spread_type = "put_credit_spread"
        elif self.weighted_change_pct < -SECTOR_DIRECTION_THRESHOLD:
            self.direction   = "bearish"
            self.spread_type = "call_credit_spread"
        else:
            self.direction   = "neutral"
            self.spread_type = "put_credit_spread"   # bullish bias por defecto


@dataclass
class PivotAnalysisResult:
    """Resultado completo: pivots + ATR + sector. Calculado 1 vez por día."""
    ticker:         str
    price:          float
    prev_close:     float
    prev_high:      float
    prev_low:       float
    averaged:       AveragedPivotLevels
    atr:            ATRContext
    standard:       Optional[PivotLevels]     = None
    camarilla:      Optional[PivotLevels]     = None
    fibonacci:      Optional[PivotLevels]     = None
    sector:         Optional[SectorDirection] = None
    consensus_risk: str   = "MID"
    atr_gate_hit:   bool  = False
    delta_min:      float = 0.22
    delta_max:      float = 0.30
    details:        dict  = field(default_factory=dict)

    def __post_init__(self):
        self._compute()

    def _compute(self):
        self.consensus_risk = self.averaged.zone_for_price(self.price)
        self.atr_gate_hit   = self.atr.is_extended(self.price)
        if self.atr_gate_hit:
            # Mercado extendido → ir un nivel más conservador
            remap = {"MID": "LOW", "LOW": "VERY_LOW",
                     "VERY_LOW": "VERY_LOW", "NO_TRADE": "NO_TRADE"}
            self.consensus_risk = remap.get(self.consensus_risk, self.consensus_risk)
        dmin, dmax      = DELTA_BY_RISK.get(self.consensus_risk, (0.22, 0.30))
        self.delta_min  = dmin
        self.delta_max  = dmax
        pp = self.averaged.pp
        self.details = {
            "avg_pp":   round(pp, 2),
            "avg_s1":   round(self.averaged.s1, 2) if self.averaged.s1 else None,
            "avg_s2":   round(self.averaged.s2, 2) if self.averaged.s2 else None,
            "avg_s3":   round(self.averaged.s3, 2) if self.averaged.s3 else None,
            "avg_s4":   round(self.averaged.s4, 2) if self.averaged.s4 else None,
            "avg_r1":   round(self.averaged.r1, 2) if self.averaged.r1 else None,
            "avg_r2":   round(self.averaged.r2, 2) if self.averaged.r2 else None,
            "avg_r3":   round(self.averaged.r3, 2) if self.averaged.r3 else None,
            "avg_r4":   round(self.averaged.r4, 2) if self.averaged.r4 else None,
            "dist_pp_pct":        round(abs(self.price - pp) / pp * 100, 3) if pp else 0,
            "atr_day":            round(self.atr.atr_day, 3),
            "atr_zone":           self.atr.atr_zone(self.price),
            "atr_gate":           self.atr_gate_hit,
            "sector_direction":   self.sector.direction if self.sector else "unknown",
            "sector_weighted_pct": round(self.sector.weighted_change_pct, 4) if self.sector else 0,
        }


# ── Calculadores de pivots ────────────────────────────────

def calc_standard(high: float, low: float, close: float) -> PivotLevels:
    """Standard Pivot Points clásico. No tiene S4/R4."""
    pp  = (high + low + close) / 3
    rng = high - low
    return PivotLevels(
        system="standard", pp=pp,
        r1 = 2 * pp - low,
        r2 = pp + rng,
        r3 = high + 2 * (pp - low),
        s1 = 2 * pp - high,
        s2 = pp - rng,
        s3 = low - 2 * (high - pp),
    )


def calc_camarilla(high: float, low: float, close: float) -> PivotLevels:
    """
    Camarilla Pivots — anclado al close, niveles más estrechos.
    Cubre S1..S4 y R1..R4.
    """
    rng = high - low
    pp  = (high + low + close) / 3
    return PivotLevels(
        system="camarilla", pp=pp,
        r1 = close + rng * 1.1 / 12,
        r2 = close + rng * 1.1 / 6,
        r3 = close + rng * 1.1 / 4,
        r4 = close + rng * 1.1 / 2,
        s1 = close - rng * 1.1 / 12,
        s2 = close - rng * 1.1 / 6,
        s3 = close - rng * 1.1 / 4,
        s4 = close - rng * 1.1 / 2,
    )


def calc_fibonacci(high: float, low: float, close: float) -> PivotLevels:
    """
    Fibonacci Pivots — ratios 0.382, 0.618, 1.0, 1.618 sobre el rango.
    Cubre S1..S4 y R1..R4.
    """
    pp  = (high + low + close) / 3
    rng = high - low
    return PivotLevels(
        system="fibonacci", pp=pp,
        r1 = pp + 0.382 * rng,
        r2 = pp + 0.618 * rng,
        r3 = pp + 1.000 * rng,
        r4 = pp + 1.618 * rng,
        s1 = pp - 0.382 * rng,
        s2 = pp - 0.618 * rng,
        s3 = pp - 1.000 * rng,
        s4 = pp - 1.618 * rng,
    )


def _average_levels(systems: list[PivotLevels]) -> AveragedPivotLevels:
    """Promedio simple de cada nivel ignorando valores 0.0 (no disponible)."""
    def _avg(vals: list[float]) -> Optional[float]:
        valid = [v for v in vals if v != 0.0]
        return round(sum(valid) / len(valid), 2) if valid else None

    return AveragedPivotLevels(
        pp = _avg([s.pp for s in systems]) or 0.0,
        r1 = _avg([s.r1 for s in systems]),
        r2 = _avg([s.r2 for s in systems]),
        r3 = _avg([s.r3 for s in systems]),
        r4 = _avg([s.r4 for s in systems]),
        s1 = _avg([s.s1 for s in systems]),
        s2 = _avg([s.s2 for s in systems]),
        s3 = _avg([s.s3 for s in systems]),
        s4 = _avg([s.s4 for s in systems]),
    )


# ── ATR ───────────────────────────────────────────────────

def _calc_atr(high: float, low: float, close: float) -> ATRContext:
    """ATR diario = High − Low del día previo."""
    return ATRContext(
        atr_day    = round(high - low, 4),
        prev_close = close,
        prev_high  = high,
        prev_low   = low,
    )


# ── Fetch OHLC día anterior ───────────────────────────────

def _fetch_prev_day_ohlc(ticker: str, token: str) -> Optional[dict]:
    """Fetcha OHLC del día anterior vía Schwab /pricehistory."""
    params = {
        "symbol":        ticker,
        "periodType":    "day",
        "period":        5,
        "frequencyType": "daily",
        "frequency":     1,
        "needExtendedHoursData": False,
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(SCHWAB_HISTORY_URL, headers=headers,
                            params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"[PivotAnalysis] {ticker} HTTP {resp.status_code}")
            return None
        candles   = (resp.json() or {}).get("candles", [])
        today_str = date.today().isoformat()
        prev = sorted(
            [c for c in candles if _ts_to_date(c.get("datetime", 0)) < today_str],
            key=lambda c: c.get("datetime", 0),
        )
        if not prev:
            return None
        c = prev[-1]
        return {
            "open":  float(c.get("open",  0)),
            "high":  float(c.get("high",  0)),
            "low":   float(c.get("low",   0)),
            "close": float(c.get("close", 0)),
            "date":  _ts_to_date(c.get("datetime", 0)),
        }
    except Exception as e:
        logger.warning(f"[PivotAnalysis] OHLC fetch error {ticker}: {e}")
        return None


def _ts_to_date(ts_ms: int) -> str:
    try:
        return datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return "1970-01-01"


# ── Sector direction ──────────────────────────────────────

def fetch_sector_direction(token: str, ticker: str = "SPY") -> Optional[SectorDirection]:
    """
    Fetcha los ETFs sectoriales y calcula la suma ponderada de cambios.
    SPY → 11 ETFs del S&P 500. TQQQ → 10 ETFs del QQQ (XLK-heavy).
    """
    weights = SECTOR_WEIGHTS_TQQQ if ticker.upper() == "TQQQ" else SECTOR_WEIGHTS_SPY
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params  = {"symbols": ",".join(weights.keys()), "fields": "quote", "indicative": False}
    try:
        resp = requests.get(SCHWAB_QUOTES_URL, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"[SectorDir] HTTP {resp.status_code}")
            return None
        data = resp.json() or {}
    except Exception as e:
        logger.warning(f"[SectorDir] fetch error: {e}")
        return None

    weighted_sum = 0.0
    top_movers   = []
    for sym, weight in weights.items():
        quote  = (data.get(sym) or {}).get("quote", {})
        change = quote.get("netPercentChangeInDouble") or quote.get("percentChange") or 0.0
        try:
            change = float(change)
        except (TypeError, ValueError):
            change = 0.0
        contribution = change * weight
        weighted_sum += contribution
        top_movers.append({
            "symbol":       sym,
            "change_pct":   round(change, 2),
            "weight":       weight,
            "contribution": round(contribution, 4),
        })

    top_movers.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    sd = SectorDirection(weighted_change_pct=round(weighted_sum, 4))
    sd.top_movers = top_movers[:5]
    logger.info(
        f"[SectorDir] {ticker} weighted={weighted_sum:+.3f}% → {sd.direction} "
        f"| top: {top_movers[0]['symbol']} {top_movers[0]['change_pct']:+.1f}%"
    )
    return sd


# ── Tick / A-D intraday ───────────────────────────────────

def fetch_tick_ad(token: str) -> Optional[TickADContext]:
    """
    Fetcha NYSE $TICK y $ADD desde Schwab /quotes en tiempo real.
    Se llama al momento de cada entrada — no se cachea.

    Returns TickADContext o None si Schwab no devuelve los datos.
    La lógica de evaluación está en TickADContext.evaluate(spread_type).
    """
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params  = {"symbols": "$TICK,$ADD", "fields": "quote", "indicative": False}
    try:
        resp = requests.get(SCHWAB_QUOTES_URL, headers=headers, params=params, timeout=8)
        if resp.status_code != 200:
            logger.warning(f"[TickAD] HTTP {resp.status_code} — sin datos, se omite confirmación")
            return None
        data = resp.json() or {}
    except Exception as e:
        logger.warning(f"[TickAD] fetch error: {e} — se omite confirmación")
        return None

    def _val(symbol: str) -> float:
        q = (data.get(symbol) or data.get(symbol.replace("$", "")) or {}).get("quote", {})
        raw = q.get("lastPrice") or q.get("mark") or q.get("close") or 0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    tick = _val("$TICK")
    ad   = _val("$ADD")

    ctx = TickADContext(tick=tick, ad=ad, tick_extreme=abs(tick) > TICK_EXTREME_THRESHOLD)
    logger.info(
        f"[TickAD] $TICK={tick:+.0f}  $ADD={ad:+.0f}  "
        f"{'⚠️ EXTREMO' if ctx.tick_extreme else 'normal'}"
    )
    return ctx


# ── Función principal ─────────────────────────────────────

def analyze_pivots(
    ticker:        str,
    current_price: float,
    token:         Optional[str] = None,
    ohlc_override: Optional[dict] = None,
    fetch_sectors: bool = True,
) -> Optional[PivotAnalysisResult]:
    """
    Corre una vez por día por ticker al inicio de la sesión.
    Calcula: Standard + Camarilla + Fibonacci pivots, ATR, sector direction.

    Args:
        ticker:         "SPY" | "TQQQ" (u otro subyacente)
        current_price:  Precio actual del subyacente
        token:          Schwab access token (None = auto-fetch)
        ohlc_override:  {"open", "high", "low", "close"} — para tests
        fetch_sectors:  False para skip sector analysis (tests/debug)
    """
    if token is None:
        token = get_access_token()
    if not token and not ohlc_override:
        logger.warning("[PivotAnalysis] Sin token")
        return None

    ohlc = ohlc_override or _fetch_prev_day_ohlc(ticker, token)
    if not ohlc:
        return None

    h = ohlc["high"]
    l = ohlc["low"]
    c = ohlc["close"]

    if h <= 0 or l <= 0 or c <= 0:
        logger.warning(f"[PivotAnalysis] {ticker} OHLC inválido: {ohlc}")
        return None

    std = calc_standard(h, l, c)
    cam = calc_camarilla(h, l, c)
    fib = calc_fibonacci(h, l, c)

    averaged = _average_levels([std, cam, fib])
    atr_ctx  = _calc_atr(h, l, c)

    sector = None
    if fetch_sectors and token:
        try:
            sector = fetch_sector_direction(token, ticker=ticker)
        except Exception as e:
            logger.warning(f"[PivotAnalysis] sector fetch failed: {e}")

    result = PivotAnalysisResult(
        ticker     = ticker,
        price      = current_price,
        prev_close = c,
        prev_high  = h,
        prev_low   = l,
        averaged   = averaged,
        atr        = atr_ctx,
        standard   = std,
        camarilla  = cam,
        fibonacci  = fib,
        sector     = sector,
    )

    logger.info(
        f"[PivotAnalysis] {ticker} @ ${current_price:.2f} | "
        f"PP={averaged.pp:.2f} risk={result.consensus_risk} "
        f"δ={result.delta_min:.2f}–{result.delta_max:.2f} | "
        f"ATR={atr_ctx.atr_day:.2f} zone={atr_ctx.atr_zone(current_price)} "
        f"gate={'⚠️ HIT' if result.atr_gate_hit else 'ok'} | "
        f"sector={result.details.get('sector_direction','?')} "
        f"{result.details.get('sector_weighted_pct', 0):+.2f}%"
    )
    return result


# ── Formato Telegram / log ────────────────────────────────

def format_pivot_summary(result: PivotAnalysisResult,
                          tick_ctx: Optional["TickADContext"] = None) -> str:
    """Una línea por nivel para Telegram al inicio del día.
    tick_ctx opcional — se incluye si se pasa (se fetcha aparte en cada entrada).
    """
    if not result:
        return "[PivotAnalysis] sin datos"
    d   = result.details
    atr = result.atr
    s   = result.sector

    gate_str   = " ⚠️ ATR GATE" if result.atr_gate_hit else ""
    sector_str = f"\n   📊 Sector: {s.direction} ({s.weighted_change_pct:+.2f}%) → {s.spread_type}" if s else ""
    tick_str   = (
        f"\n   📈 Tick/AD: TICK={tick_ctx.tick:+.0f}  AD={tick_ctx.ad:+.0f}"
        + (" ⚠️ EXTREMO" if tick_ctx.tick_extreme else "")
    ) if tick_ctx else ""

    return (
        f"📐 {result.ticker} Pivots{gate_str} | risk={result.consensus_risk} "
        f"δ={result.delta_min:.2f}–{result.delta_max:.2f}\n"
        f"   PP={d['avg_pp']} | "
        f"S4={d['avg_s4']} S3={d['avg_s3']} S2={d['avg_s2']} S1={d['avg_s1']}\n"
        f"   R1={d['avg_r1']} R2={d['avg_r2']} R3={d['avg_r3']} R4={d['avg_r4']}\n"
        f"   ATR={atr.atr_day:.2f} "
        f"(+1={atr.up_1atr} +2={atr.up_2atr} / -1={atr.down_1atr} -2={atr.down_2atr}) "
        f"zone={d['atr_zone']} dist_pp={d['dist_pp_pct']:.2f}%"
        + sector_str
        + tick_str
    )
