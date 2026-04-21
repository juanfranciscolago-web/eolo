# ============================================================
#  normalize.py — adaptadores de formato de vela a shape estándar
#
#  Cada stream (Schwab CHART_EQUITY, Binance klines, IEX, etc.)
#  tiene su propio formato. El CandleBuffer espera siempre:
#
#    { "symbol": str, "ts_ms": int,
#      "open": float, "high": float, "low": float,
#      "close": float, "volume": float }
#
#  Este módulo centraliza los converters para que cada Eolo
#  no reinvente la rueda.
# ============================================================
from typing import Optional


def from_schwab_chart_equity(raw: dict) -> Optional[dict]:
    """
    Normaliza un mensaje parseado de Schwab CHART_EQUITY.

    Spec Schwab (vigente, post 2025):
      0=key(symbol), 1=sequence, 2=open, 3=high, 4=low,
      5=close, 6=volume, 7=time_ms, 8=chart_day

    El stream.py de v1.2 ya parsea a:
      {symbol, sequence, open, high, low, close, volume, time, chart_day}
    donde `time` es el ms desde epoch. Lo llevamos a `ts_ms`.
    """
    symbol = (raw.get("symbol") or "").upper()
    ts = raw.get("time") or raw.get("ts_ms") or raw.get("chart_time_ms")
    if not symbol or ts is None:
        return None
    try:
        return {
            "symbol": symbol,
            "ts_ms":  int(ts),
            "open":   float(raw["open"]),
            "high":   float(raw["high"]),
            "low":    float(raw["low"]),
            "close":  float(raw["close"]),
            "volume": float(raw.get("volume") or 0),
        }
    except (TypeError, ValueError, KeyError):
        return None


def from_binance_kline(symbol: str, kline_event: dict) -> Optional[dict]:
    """
    Normaliza un evento kline de Binance Futures/Spot (combined stream).

    Estructura Binance kline: el event trae "k": {
      "t": open_ms, "T": close_ms, "s": symbol, "i": interval,
      "o": open, "c": close, "h": high, "l": low, "v": volume, "x": closed_flag
    }

    Usamos close_ms (T) como ts_ms — es el timestamp de cierre de la vela,
    que es el "anchor" estándar para calcular TF superiores (así el resample
    con closed="right" es coherente).
    """
    k = kline_event.get("k") if kline_event else None
    if not k:
        return None
    try:
        return {
            "symbol": symbol.upper(),
            "ts_ms":  int(k["T"]),
            "open":   float(k["o"]),
            "high":   float(k["h"]),
            "low":    float(k["l"]),
            "close":  float(k["c"]),
            "volume": float(k["v"]),
        }
    except (TypeError, ValueError, KeyError):
        return None


def from_binance_rest_kline(symbol: str, arr: list) -> Optional[dict]:
    """
    Normaliza un elemento del array retornado por GET /api/v3/klines
    (usado en backfill al arrancar).

    Formato del array:
      [open_ms, open, high, low, close, volume, close_ms,
       quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
    """
    if not arr or len(arr) < 7:
        return None
    try:
        return {
            "symbol": symbol.upper(),
            "ts_ms":  int(arr[6]),       # close_ms
            "open":   float(arr[1]),
            "high":   float(arr[2]),
            "low":    float(arr[3]),
            "close":  float(arr[4]),
            "volume": float(arr[5]),
        }
    except (TypeError, ValueError, IndexError):
        return None
