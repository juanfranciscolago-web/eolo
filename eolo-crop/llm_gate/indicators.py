"""
Indicadores tecnicos para eolo-crop/llm_gate.

Copia literal de llm_engine_eolo/llm_engine/market_data_collector.py (solo
funciones puras de calculo, sin el template build_market_snapshot_from_schwab).

TODO: extraer a paquete compartido cuando ambos proyectos lo necesiten.
"""
import logging
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    """Calcula RSI sobre serie de precios (Wilder smoothing)."""
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                  period: int = 14) -> float:
    """Calcula ATR (Wilder)."""
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return float(atr.iloc[-1])


def calculate_ema(prices: pd.Series, period: int) -> float:
    """Calcula EMA."""
    return float(prices.ewm(span=period, adjust=False).mean().iloc[-1])


def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> tuple:
    """Calcula MACD line, signal, histogram."""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return (float(macd_line.iloc[-1]),
            float(signal_line.iloc[-1]),
            float(histogram.iloc[-1]))


def calculate_fibonacci_levels(open_price: float, pdh: float, pdl: float) -> dict:
    """
    Calcula niveles Fibonacci sobre rango del dia anterior + open actual.
    Usa el sistema clásico de pivots + Fibonacci extensions.
    """
    pivot = (pdh + pdl + open_price) / 3
    range_yesterday = pdh - pdl

    r1 = pivot + 0.382 * range_yesterday
    r2 = pivot + 0.618 * range_yesterday
    r3 = pivot + 1.000 * range_yesterday

    s1 = pivot - 0.382 * range_yesterday
    s2 = pivot - 0.618 * range_yesterday
    s3 = pivot - 1.000 * range_yesterday

    return {
        "pivot": round(pivot, 2),
        "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
        "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
    }


def calculate_vwap_bands(df: pd.DataFrame) -> dict:
    """
    Calcula VWAP + bandas ±1σ ±2σ del dia.

    df debe tener columnas: high, low, close, volume
    """
    typical = (df['high'] + df['low'] + df['close']) / 3
    cum_vol = df['volume'].cumsum()
    cum_pv = (typical * df['volume']).cumsum()
    vwap = cum_pv / cum_vol

    deviation = ((typical - vwap) ** 2 * df['volume']).cumsum() / cum_vol
    std = np.sqrt(deviation)

    return {
        "vwap": float(vwap.iloc[-1]),
        "vwap_upper_1sigma": float((vwap + std).iloc[-1]),
        "vwap_upper_2sigma": float((vwap + 2 * std).iloc[-1]),
        "vwap_lower_1sigma": float((vwap - std).iloc[-1]),
        "vwap_lower_2sigma": float((vwap - 2 * std).iloc[-1]),
    }


def calculate_buy_sell_volume_pressure(df: pd.DataFrame) -> dict:
    """
    Implementa la fórmula custom de Juan para BVP/SVP.

    Bar decomposition: cada barra se descompone en buying volume y selling volume
    según donde cerró respecto al rango.

    Buy vol = volume * (close - low) / (high - low)
    Sell vol = volume * (high - close) / (high - low)
    """
    df = df.copy()
    bar_range = (df['high'] - df['low']).replace(0, 1)
    df['buy_vol'] = df['volume'] * (df['close'] - df['low']) / bar_range
    df['sell_vol'] = df['volume'] * (df['high'] - df['close']) / bar_range

    total_buy = df['buy_vol'].sum()
    total_sell = df['sell_vol'].sum()
    total = total_buy + total_sell

    if total == 0:
        return {"bvp_pct": 50.0, "svp_pct": 50.0,
                "volume_current_bar": 0, "volume_avg_20bar": 0}

    return {
        "bvp_pct": float(total_buy / total * 100),
        "svp_pct": float(total_sell / total * 100),
        "volume_current_bar": float(df['volume'].iloc[-1]),
        "volume_avg_20bar": float(df['volume'].tail(20).mean()),
    }
