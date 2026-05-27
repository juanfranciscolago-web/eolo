"""
Market Data Collector - Builds MarketSnapshot from Schwab data.

NOTE: Esta es la FUNCIÓN que necesita ESTAR EN EOLO CROP, no aquí.
Eolo Crop ya tiene schwab-py autenticado, así que esta función
debería integrarse en el código de Eolo Crop y llamar al LLM service.

Este archivo es REFERENCIA / TEMPLATE para que Juan lo adapte.
"""
import logging
from datetime import datetime, timedelta
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
    # Pivot point clásico
    pivot = (pdh + pdl + open_price) / 3
    range_yesterday = pdh - pdl

    # Resistencias (extension upward)
    r1 = pivot + 0.382 * range_yesterday
    r2 = pivot + 0.618 * range_yesterday
    r3 = pivot + 1.000 * range_yesterday

    # Soportes (extension downward)
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

    # Standard deviation rolling
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
    bar_range = (df['high'] - df['low']).replace(0, 1)  # avoid div by 0
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


# ═══════════════════════════════════════════════════════
# TEMPLATE para integrar en Eolo Crop
# ═══════════════════════════════════════════════════════

def build_market_snapshot_from_schwab(schwab_client, ticker: str = "SPY") -> dict:
    """
    EJEMPLO de integración con schwab-py.
    Adaptá esto a tu código existente de Eolo Crop.

    Returns dict que se pasa al endpoint /decide del LLM service.
    """

    # 1. Get current quote
    # quote = schwab_client.get_quote(ticker).json()
    # current_price = quote[ticker]['quote']['lastPrice']
    # ...

    # 2. Get price history - 2min bars del dia
    # bars_2m = schwab_client.get_price_history(
    #     ticker, period_type='day', period=1, frequency_type='minute', frequency=2
    # ).json()
    # df_2m = pd.DataFrame(bars_2m['candles'])

    # 3. Get price history - 15min bars del dia
    # df_15m = ...

    # 4. Get price history - daily bars (ultimos 300 dias para EMAs)
    # df_daily = ...

    # 5. Get VIX quote
    # vix_quote = schwab_client.get_quote("$VIX").json()
    # vix_level = vix_quote['$VIX']['quote']['lastPrice']

    # 6. Calculate all indicators
    # rsi_2m = calculate_rsi(df_2m['close'], 14)
    # rsi_15m = calculate_rsi(df_15m['close'], 14)
    # atr_2m = calculate_atr(df_2m['high'], df_2m['low'], df_2m['close'], 14)
    # fib_levels = calculate_fibonacci_levels(open_price, pdh, pdl)
    # vwap_bands = calculate_vwap_bands(df_2m)
    # volume_pressure = calculate_buy_sell_volume_pressure(df_2m)
    # macd_line, macd_signal, macd_hist = calculate_macd(df_15m['close'])

    # 7. Build MarketSnapshot dict
    # snapshot = {
    #     "timestamp": datetime.now().isoformat(),
    #     "ticker": ticker,
    #     "session_phase": determine_session_phase(),
    #     "price": current_price,
    #     "open_price": today_open,
    #     "high": today_high,
    #     "low": today_low,
    #     "prev_close": pdc,
    #     "vix_level": vix_level,
    #     "vix_velocity_30m_pct": calc_vix_velocity_30m(),
    #     ...
    # }

    raise NotImplementedError(
        "Este es un TEMPLATE. Implementá esta función en Eolo Crop "
        "adaptando a tu código existente de schwab-py."
    )
