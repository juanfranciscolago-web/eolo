"""Pure-Python technical indicators for backtest snapshot quality.

Sin numpy/pandas dependencies. Eficiente para series cortas (50-252 candles).
"""
from __future__ import annotations
from typing import Iterable


def ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average. Output mismo largo, primeros (period-1) = first value."""
    if not values or period <= 0:
        return []
    if period == 1:
        return list(values)
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    if not values or period <= 0:
        return []
    out = []
    for i in range(len(values)):
        start = max(0, i - period + 1)
        window = values[start: i + 1]
        out.append(sum(window) / len(window))
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    """RSI (Relative Strength Index). Wilder smoothing.

    Returns list aligned to closes. First `period` values default to 50 (neutral).
    """
    if len(closes) <= period:
        return [50.0] * len(closes)
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[1: period + 1]) / period
    avg_loss = sum(losses[1: period + 1]) / period

    out = [50.0] * period
    if avg_loss == 0:
        out.append(100.0)
    else:
        rs = avg_gain / avg_loss
        out.append(100 - (100 / (1 + rs)))

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100 - (100 / (1 + rs)))
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Average True Range. Wilder smoothing."""
    n = min(len(highs), len(lows), len(closes))
    if n == 0:
        return []
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if n <= period:
        avg = sum(trs) / n
        return [avg] * n
    out = [trs[0]] * period
    initial = sum(trs[: period]) / period
    out.append(initial)
    cur = initial
    for i in range(period + 1, n):
        cur = (cur * (period - 1) + trs[i]) / period
        out.append(cur)
    return out


def vwap_from_candles(candles: list[dict]) -> float:
    """VWAP sobre lista de candles {high, low, close, volume}. Single value."""
    if not candles:
        return 0.0
    num = 0.0
    den = 0.0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c["volume"]
        num += typical * vol
        den += vol
    if den == 0:
        return 0.0
    return num / den


def fibonacci_levels(high: float, low: float) -> dict[str, float]:
    """Standard Fibonacci retracements. Returns r1, r2, r3, s1, s2, s3 sobre el rango.

    Aproximación de pivot points: pp = (H+L+C)/3 needed for traditional. Aquí usamos
    Fibonacci retracements desde rango (H-L).
    """
    if high <= low:
        return {"fib_r1": high, "fib_r2": high, "fib_r3": high,
                "fib_s1": low, "fib_s2": low, "fib_s3": low}
    rng = high - low
    return {
        "fib_r1": low + rng * 0.382,
        "fib_r2": low + rng * 0.618,
        "fib_r3": low + rng * 1.000,
        "fib_s1": high - rng * 0.382,
        "fib_s2": high - rng * 0.618,
        "fib_s3": high - rng * 1.000,
    }
