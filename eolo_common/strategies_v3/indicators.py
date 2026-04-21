# ============================================================
#  eolo_common/strategies_v3/indicators.py
#
#  Port de la librería de indicadores del upload de Juan.
#  Diferencias vs el upload:
#    1) columnas en minúsculas (open/high/low/close/volume) para
#       matchear los DataFrames que produce MarketData (Schwab) y
#       BufferMarketData (v2/crypto).
#    2) tz-aware opcional — `time_of_day_flag` soporta índices
#       naive (cae al path "local ET").
# ============================================================
from __future__ import annotations
import numpy as np
import pandas as pd


# ── Básicos ──────────────────────────────────────────────────

def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False).mean()


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return true_range(df).rolling(length).mean()


def rsi(s: pd.Series, length: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.where(delta > 0, 0).rolling(length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(length).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    m = ema(s, fast) - ema(s, slow)
    sig = ema(m, signal)
    return m, sig, m - sig


def roc(s: pd.Series, length: int = 10) -> pd.Series:
    return (s / s.shift(length) - 1) * 100


# ── Régimen ──────────────────────────────────────────────────

def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    atr_ = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / length, adjust=False).mean()
    return pd.DataFrame({"adx": adx_val, "di_plus": plus_di, "di_minus": minus_di})


def choppiness_index(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = true_range(df)
    atr_sum = tr.rolling(length).sum()
    h_max = df["high"].rolling(length).max()
    l_min = df["low"].rolling(length).min()
    ci = 100 * np.log10(atr_sum / (h_max - l_min)) / np.log10(length)
    return ci


def ema_slope(ema_series: pd.Series, lookback: int = 10) -> pd.Series:
    return (ema_series - ema_series.shift(lookback)) / ema_series.shift(lookback) * 100


# ── Buy/Sell volume proxies (bar-decomposition, TOS-native) ──

def bar_decomposition_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    Descomposición exacta de la barra (formula TOS de Juan):
        buyvol  = ((high-open) + (close-low))  / 2 / (high-low) * volume
        sellvol = ((low-open)  + (close-high)) / 2 / (high-low) * volume (negative)

    Devuelve: buy_vol, sell_vol (positivos), bvp_pct, svp_pct, net_bsv.
    """
    out = df.copy()
    h, l, o, c, v = out["high"], out["low"], out["open"], out["close"], out["volume"]
    rng = (h - l).replace(0, np.nan)

    buy_raw = ((h - o) + (c - l)) / 2 / rng * v
    sell_raw = ((l - o) + (c - h)) / 2 / rng * v   # negativo por construcción

    buy_vol = buy_raw.fillna(v / 2).clip(lower=0)
    sell_vol = sell_raw.abs().fillna(v / 2).clip(lower=0)

    total = buy_vol + sell_vol
    bvp = (buy_vol / total.replace(0, np.nan) * 100).fillna(50)
    svp = (sell_vol / total.replace(0, np.nan) * 100).fillna(50)

    avg_v = v.rolling(20).mean()
    net_bsv = ((buy_vol - sell_vol) / avg_v.replace(0, np.nan)).fillna(0)

    return pd.DataFrame({
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "bvp_pct": bvp,
        "svp_pct": svp,
        "net_bsv": net_bsv,
    }, index=df.index)


def buy_sell_pressure_index(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    bd = bar_decomposition_volume(df)
    total = bd["buy_vol"] + bd["sell_vol"]
    ratio = (bd["buy_vol"] / total.replace(0, np.nan)).fillna(0.5)
    return pd.DataFrame({
        "buy_vol": bd["buy_vol"],
        "sell_vol": bd["sell_vol"],
        "bsp_ratio": ratio,
        "bsp_ma": ratio.rolling(length).mean(),
        "net_pressure": bd["net_bsv"],
        "bvp_pct": bd["bvp_pct"],
        "svp_pct": bd["svp_pct"],
    }, index=df.index)


# ── VWAP / Opening Range / Donchian ──────────────────────────

def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP que resetea cada día. Requiere índice datetime."""
    typ = (df["high"] + df["low"] + df["close"]) / 3
    pv = typ * df["volume"]
    try:
        date_key = df.index.date
    except AttributeError:
        # Si el DF trae datetime como columna en lugar de índice, se arregla en el caller.
        return pd.Series(index=df.index, dtype=float)
    groups = pd.Series(date_key, index=df.index)
    cum_pv = pv.groupby(groups).cumsum()
    cum_v = df["volume"].groupby(groups).cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def opening_range(df: pd.DataFrame, minutes: int = 30) -> pd.DataFrame:
    """Devuelve or_high / or_low forward-filled por día (desde que cierra el OR)."""
    out = df.copy()
    if len(out) >= 2:
        delta = (out.index[1] - out.index[0]).total_seconds() / 60
        bar_minutes = max(1, int(delta))
    else:
        bar_minutes = 5
    bars_in_or = max(1, minutes // bar_minutes)

    or_h_list, or_l_list = [], []
    try:
        group_key = out.index.date
    except AttributeError:
        return pd.DataFrame({"or_high": np.nan, "or_low": np.nan}, index=out.index)

    for _, group in out.groupby(group_key):
        if len(group) < bars_in_or:
            or_h = [np.nan] * len(group)
            or_l = [np.nan] * len(group)
        else:
            h = group["high"].iloc[:bars_in_or].max()
            l = group["low"].iloc[:bars_in_or].min()
            or_h = [np.nan] * bars_in_or + [h] * (len(group) - bars_in_or)
            or_l = [np.nan] * bars_in_or + [l] * (len(group) - bars_in_or)
        or_h_list.extend(or_h)
        or_l_list.extend(or_l)
    out["or_high"] = or_h_list
    out["or_low"] = or_l_list
    return out[["or_high", "or_low"]]


def donchian(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    return pd.DataFrame({
        "dc_upper": df["high"].rolling(length).max(),
        "dc_lower": df["low"].rolling(length).min(),
    }, index=df.index)


def time_of_day_flag(df: pd.DataFrame, tz: str = "US/Eastern") -> pd.DataFrame:
    """
    Marca opening (9:30-10:00), midday (11:30-14:00), power-hour (15:00-16:00)
    e is_tradeable (RTH sin opening ni midday). Cae al path naive si no hay tz info.
    """
    try:
        idx = df.index
        if idx.tz is None:
            idx_et = idx.tz_localize("UTC").tz_convert(tz)
        else:
            idx_et = idx.tz_convert(tz)
    except Exception:
        idx_et = df.index

    try:
        hour = idx_et.hour + idx_et.minute / 60.0
    except Exception:
        # Si el índice no tiene .hour (ej: RangeIndex), degradar y marcar todo tradeable
        return pd.DataFrame({
            "hour_et": np.nan, "is_opening": False, "is_midday": False,
            "is_power_hour": False, "is_tradeable": True,
        }, index=df.index)

    opening = (hour >= 9.5) & (hour < 10.0)
    midday = (hour >= 11.5) & (hour < 14.0)
    power = (hour >= 15.0) & (hour < 16.0)
    tradeable = ~(opening | midday) & (hour >= 9.5) & (hour < 16.0)
    return pd.DataFrame({
        "hour_et": hour,
        "is_opening": opening,
        "is_midday": midday,
        "is_power_hour": power,
        "is_tradeable": tradeable,
    }, index=df.index)
