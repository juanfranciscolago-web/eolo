# ============================================================
#  EOLO Crypto — Estrategia: Funding Rate Carry
#
#  Ref: nueva estrategia crypto-native (2026-04-27)
#
#  Lógica:
#    El funding rate de perpetuos Binance se resetea cada 8h.
#    Cuando el rate es muy negativo (shorts pagan a longs),
#    señala que el mercado está excesivamente short/bearish →
#    reversión contraria (short squeeze / dead-cat bounce).
#    Cuando el rate es muy positivo (longs pagan mucho),
#    el mercado está overleveraged long → potencial liquidación.
#
#    BUY  : fundingRate < THRESHOLD_BEAR (e.g. -0.05%)
#           = mercado en pánico, longs reciben pagos de shorts
#    SELL : fundingRate > THRESHOLD_BULL (e.g. +0.20%)
#           = overleveraged, potencial liquidation cascade bajista
#    HOLD : rango normal de funding
#
#    El funding rate se obtiene de la API pública de Binance
#    perpetuos (/fapi/v1/premiumIndex — sin autenticación).
#    Se cachea 8h (frecuencia de pago real). Spot bot → no cobramos
#    el funding; lo usamos solo como señal de sentimiento.
#
#  Universo : todos los pares (apply_to_all=True)
#             priorizados: BTCUSDT, ETHUSDT, SOLUSDT
#  Timeframe : 4h (evaluado al cierre de la vela de 240m)
#  Categoría : sentiment / funding carry
# ============================================================
import os
import time
import threading
from typing import Optional

import pandas as pd
from loguru import logger

STRATEGY_NAME = "FUNDING_RATE_CARRY"

# Umbral negativo → señal BUY (funding muy negativo = exceso de shorts)
THRESHOLD_BEAR = float(os.environ.get("FRC_BEAR",  "-0.0005"))  # -0.05%
# Umbral positivo → señal SELL (funding muy positivo = exceso de longs)
THRESHOLD_BULL = float(os.environ.get("FRC_BULL",   "0.0020"))  # +0.20%
# TTL del cache de funding rates (8h = ciclo de pago Binance)
FUNDING_CACHE_TTL = float(os.environ.get("FRC_CACHE_TTL", str(8 * 60 * 60)))

# URL base perpetuos Binance (endpoint público, sin auth)
FAPI_BASE = "https://fapi.binance.com"

# ── Cache module-level (shared entre instancias en el mismo proceso) ──
_funding_cache: dict[str, dict] = {}   # symbol → {"rate": float, "ts": float}
_cache_lock = threading.Lock()


def _strip_symbol(symbol: str) -> str:
    """BTCUSDT → BTCUSDT (spot symbol), ya es compatible con fapi."""
    return symbol.upper()


def fetch_funding_rate(symbol: str, force: bool = False) -> Optional[float]:
    """
    Retorna el funding rate actual del perpetuo correspondiente a `symbol`.
    Usa cache local con TTL de 8h. Fail-soft: retorna None si falla.

    Nota: el endpoint /fapi/v1/premiumIndex devuelve `lastFundingRate`
    para los perpetuos. Los pares spot (BTCUSDT) tienen su equivalente
    perpetuo con el mismo nombre en Binance futures.
    """
    sym = _strip_symbol(symbol)
    now = time.time()

    with _cache_lock:
        cached = _funding_cache.get(sym)
        if not force and cached and (now - cached["ts"]) < FUNDING_CACHE_TTL:
            return cached["rate"]

    try:
        import urllib.request
        import json as _json
        url = f"{FAPI_BASE}/fapi/v1/premiumIndex?symbol={sym}"
        req = urllib.request.Request(url, headers={"User-Agent": "eolo-crypto/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read().decode())
        rate = float(data.get("lastFundingRate", 0))

        with _cache_lock:
            _funding_cache[sym] = {"rate": rate, "ts": now}

        logger.debug(
            f"[{STRATEGY_NAME}] {sym} fundingRate={rate:+.6f} "
            f"({rate*100:+.4f}%)"
        )
        return rate
    except Exception as e:
        logger.debug(f"[{STRATEGY_NAME}] fetch failed for {sym}: {type(e).__name__}: {e}")
        return None


def get_cached_funding_rate(symbol: str) -> Optional[float]:
    """Lee del cache sin hacer fetch (para llamadas frecuentes)."""
    with _cache_lock:
        cached = _funding_cache.get(_strip_symbol(symbol))
        return cached["rate"] if cached else None


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if len(df) < 5:
        return "HOLD"

    rate = fetch_funding_rate(ticker)
    if rate is None:
        return "HOLD"

    price = float(df.iloc[-1]["close"])

    # ── SELL: gestión de posición existente con señal de salida ──
    if entry_price is not None and entry_price > 0:
        pnl_pct = (price - entry_price) / entry_price * 100
        # Salir si el mercado se dio vuelta (funding ya positivo extremo)
        if rate > THRESHOLD_BULL:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — funding={rate:+.6f} > {THRESHOLD_BULL} "
                f"(overleveraged, potencial liquidation)"
            )
            return "SELL"
        # Profit target: si funding volvió a rango normal, el carry terminó
        if rate > -0.0001 and pnl_pct > 0.5:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — funding normalizado "
                f"({rate:+.6f}), tomando ganancia {pnl_pct:+.2f}%"
            )
            return "SELL"
        return "HOLD"

    # ── BUY: funding muy negativo = short squeeze pendiente ──
    if rate < THRESHOLD_BEAR:
        logger.info(
            f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.4f} — "
            f"funding={rate:+.6f} ({rate*100:+.4f}%) < {THRESHOLD_BEAR} "
            f"(shorts pagando, señal contraria)"
        )
        return "BUY"

    return "HOLD"


def analyze(market_data, ticker: str, **kwargs) -> dict:
    """Wrapper para StrategyRunner de crypto_adapters."""
    try:
        df = (market_data.get_candles_resampled(ticker, 240)
              if hasattr(market_data, "get_candles_resampled") else market_data)
        if df is None or (hasattr(df, "empty") and df.empty):
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}
        entry_price = kwargs.get("entry_price")
        signal = detect_signal(df, ticker, entry_price=entry_price)
        price  = float(df.iloc[-1]["close"]) if not df.empty else 0
        rate   = get_cached_funding_rate(ticker)
        return {
            "signal":   signal,
            "ticker":   ticker,
            "price":    price,
            "reason":   f"{STRATEGY_NAME} fundingRate={rate:+.6f}" if rate else STRATEGY_NAME,
            "strategy": STRATEGY_NAME,
        }
    except Exception as e:
        logger.error(f"[{STRATEGY_NAME}] analyze error {ticker}: {e}")
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": f"error: {e}", "strategy": STRATEGY_NAME}
