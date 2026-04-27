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


# ── Indicadores Combos Ganadores (portados de ThinkScript) ───

def keltner_channels(df: pd.DataFrame, length: int = 20, mult: float = 1.5) -> pd.DataFrame:
    """
    Keltner Channels: mid = EMA(close, length), band = mult * ATR(length).
    Base para TTM Squeeze (BB inside KC).
    """
    mid = ema(df["close"], length)
    atr_val = atr(df, length)
    return pd.DataFrame({
        "kc_upper": mid + mult * atr_val,
        "kc_lower": mid - mult * atr_val,
        "kc_mid":   mid,
    }, index=df.index)


def ttm_squeeze(
    df: pd.DataFrame,
    bb_length: int = 20, bb_mult: float = 2.0,
    kc_length: int = 20, kc_mult: float = 1.5,
    macd_fast: int = 12, macd_slow: int = 26, macd_sig: int = 9,
) -> pd.DataFrame:
    """
    TTM Squeeze: squeeze_on = BB inside KC.
    Retorna squeeze_on (bool), macd_diff (momentum), squeeze_count (barras seguidas en squeeze).

    Usado en Combo 3 (Nino Squeeze) y Combo 6 (FractalCCIx).
    """
    # Bollinger Bands
    bb_mid = sma(df["close"], bb_length)
    bb_std = df["close"].rolling(bb_length).std()
    bb_upper = bb_mid + bb_mult * bb_std
    bb_lower = bb_mid - bb_mult * bb_std

    # Keltner Channels
    kc = keltner_channels(df, kc_length, kc_mult)

    # Squeeze ON = BB inside KC
    squeeze_on = (bb_upper < kc["kc_upper"]) & (bb_lower > kc["kc_lower"])

    # Momentum (MACD Diff proxy, como en ThinkScript Robot_Squeeze)
    m, sig, diff = macd(df["close"], macd_fast, macd_slow, macd_sig)

    # Conteo de barras consecutivas en squeeze
    sq_int = squeeze_on.astype(int)
    # Identificar grupos de squeeze consecutivo
    group = (sq_int != sq_int.shift()).cumsum()
    sq_count = sq_int.groupby(group).cumsum().where(squeeze_on, 0)

    return pd.DataFrame({
        "squeeze_on":    squeeze_on,
        "macd_diff":     diff,
        "macd_val":      m,
        "macd_sig":      sig,
        "squeeze_count": sq_count,
        "bb_upper":      bb_upper,
        "bb_lower":      bb_lower,
    }, index=df.index)


def tema(s: pd.Series, length: int) -> pd.Series:
    """
    Triple EMA: TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA)).
    Usado en FractalCCIx (Combo 6) aplicado sobre Heikin Ashi close.
    """
    e1 = ema(s, length)
    e2 = ema(e1, length)
    e3 = ema(e2, length)
    return 3 * e1 - 3 * e2 + e3


def cci(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """
    Commodity Channel Index.
    CCI = (TP - SMA(TP, n)) / (0.015 * mean_dev)
    Usado en Combo 6 (FractalCCIx: señal cuando CCI < -100 o > +100).
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_sma = tp.rolling(length).mean()
    mean_dev = tp.rolling(length).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - tp_sma) / (0.015 * mean_dev.replace(0, np.nan))


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Heikin Ashi OHLC.
    ha_close = (O+H+L+C)/4
    ha_open  = (prev_ha_open + prev_ha_close) / 2  (se inicializa con open real)
    Usado en FractalCCIx (Combo 6) — TEMA se aplica sobre ha_close.
    """
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = ha_close.copy()
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low  = pd.concat([df["low"],  ha_open, ha_close], axis=1).min(axis=1)
    return pd.DataFrame({
        "ha_open":  ha_open,
        "ha_high":  ha_high,
        "ha_low":   ha_low,
        "ha_close": ha_close,
    }, index=df.index)


def supertrend(df: pd.DataFrame, length: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """
    SuperTrend: dirección basada en ATR.
    direction: +1 = bullish, -1 = bearish.
    line = la banda activa.
    Usado en Combo 7 (Campbell Swing) como trail stop / confirmación MTF.
    """
    atr_val = atr(df, length)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + mult * atr_val
    lower = hl2 - mult * atr_val

    n = len(df)
    direction = pd.Series(1, index=df.index, dtype=int)
    final_upper = upper.copy()
    final_lower = lower.copy()
    close = df["close"]

    for i in range(1, n):
        # Ajustar bandas: no se puede expandir hacia precio
        final_upper.iloc[i] = upper.iloc[i] if (
            upper.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]
        ) else final_upper.iloc[i - 1]
        final_lower.iloc[i] = lower.iloc[i] if (
            lower.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]
        ) else final_lower.iloc[i - 1]
        # Dirección
        if direction.iloc[i - 1] == -1 and close.iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif direction.iloc[i - 1] == 1 and close.iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

    line = pd.Series(np.where(direction == 1, final_lower, final_upper), index=df.index)
    return pd.DataFrame({"direction": direction, "line": line}, index=df.index)


def ema_stacked_bull(df: pd.DataFrame,
                     lengths: tuple = (8, 21, 34, 55, 89)) -> pd.Series:
    """
    True cuando EMA8 > EMA21 > EMA34 > EMA55 > EMA89 (stacked bullish).
    Usado en Combo 1, 3, 5 como filtro de tendencia fuerte.
    """
    emas = [ema(df["close"], l) for l in lengths]
    stacked = emas[0] > emas[1]
    for i in range(1, len(emas) - 1):
        stacked = stacked & (emas[i] > emas[i + 1])
    return stacked


def ema_stacked_bear(df: pd.DataFrame,
                     lengths: tuple = (8, 21, 34, 55, 89)) -> pd.Series:
    """EMA8 < EMA21 < EMA34 < EMA55 < EMA89 (stacked bearish)."""
    emas = [ema(df["close"], l) for l in lengths]
    stacked = emas[0] < emas[1]
    for i in range(1, len(emas) - 1):
        stacked = stacked & (emas[i] < emas[i + 1])
    return stacked


def slim_ribbon(df: pd.DataFrame) -> pd.DataFrame:
    """
    SlimRibbon: EMA8 > EMA13 > EMA21 (buy) / EMA8 < EMA13 < EMA21 (sell).
    Usado en Combo 4 (SlimRibbon + MACD).
    """
    e8  = ema(df["close"], 8)
    e13 = ema(df["close"], 13)
    e21 = ema(df["close"], 21)
    bull = (e8 > e13) & (e13 > e21)
    bear = (e8 < e13) & (e13 < e21)
    return pd.DataFrame({"ribbon_bull": bull, "ribbon_bear": bear,
                         "ema8": e8, "ema13": e13, "ema21": e21}, index=df.index)


def tdi(df: pd.DataFrame, rsi_length: int = 13,
        smooth_fast: int = 2, smooth_slow: int = 7,
        bb_length: int = 34, bb_mult: float = 1.62) -> pd.DataFrame:
    """
    Traders Dynamic Index: RSI(13) + smooth + Bollinger Bands(34, 1.62σ).
    Usado en Combo 4 (contexto) y Combo 7 (confirmación Campbell).
    tdi_signal > tdi_mid → bullish context.
    """
    rsi_val = rsi(df["close"], rsi_length)
    fast_line = ema(rsi_val, smooth_fast)
    slow_line = ema(rsi_val, smooth_slow)
    bb_mid   = sma(rsi_val, bb_length)
    bb_std   = rsi_val.rolling(bb_length).std()
    bb_upper = bb_mid + bb_mult * bb_std
    bb_lower = bb_mid - bb_mult * bb_std
    return pd.DataFrame({
        "tdi_fast":  fast_line,   # señal principal
        "tdi_slow":  slow_line,   # confirmación
        "tdi_mid":   bb_mid,      # nivel 50 proxy
        "tdi_upper": bb_upper,
        "tdi_lower": bb_lower,
    }, index=df.index)


def btd_navigator(df: pd.DataFrame, length: int = 22) -> pd.Series:
    """
    Robot BTD Upper Navigator: ((close - Lowest(close[1], length)) / Lowest(close[1], length)) * 100.
    Señal alcista cuando cruza 0 hacia arriba (rebote desde mínimo).
    Usado en Combo 5 (BTD + Stacked).
    """
    lowest_prev = df["close"].shift(1).rolling(length).min()
    value = (df["close"] - lowest_prev) / lowest_prev.replace(0, np.nan) * 100
    return value


def campbell_trend(df: pd.DataFrame, trend_length: int = 20) -> pd.Series:
    """
    CampbellScan: True cuando EMA34 está en tendencia alcista los últimos N barras.
    Se mide como: EMA34[i] > EMA34[i-1] por los últimos trend_length barras.
    Usado en Combo 7 (Campbell Swing).
    """
    e34 = ema(df["close"], 34)
    rising = e34 > e34.shift(1)
    # True si rising es True en todos los últimos trend_length barras
    return rising.rolling(trend_length).min().fillna(0).astype(bool)


def time_of_day_flag(df: pd.DataFrame, tz: str = "US/Eastern") -> pd.DataFrame:
    """
    Marca opening (9:30-10:00), power-hour (15:00-16:00)
    e is_tradeable (RTH completo sin solo el opening). Midday desbloqueado.
    Cae al path naive si no hay tz info.
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

    opening = hour < 0   # opening desbloqueado
    midday   = hour < 0  # midday desbloqueado
    power = (hour >= 15.0) & (hour < 16.0)
    tradeable = (hour >= 9.5) & (hour < 16.0)  # todo RTH
    return pd.DataFrame({
        "hour_et": hour,
        "is_opening": opening,
        "is_midday": midday,
        "is_power_hour": power,
        "is_tradeable": tradeable,
    }, index=df.index)
