# ============================================================
#  eolo_common/strategies_v3/strategies.py
#
#  Las 10 estrategias del upload v3 portadas al formato Eolo.
#
#  Contrato de cada estrategia:
#
#      def analyze_df(df, cfg=None, *, tod_filter=None, **params)
#          → dict { signal: "BUY"|"SELL"|"HOLD", reason: str, ...diag }
#
#  Diferencias clave vs el upload original:
#    • Columnas en minúsculas (high/low/close/open/volume) — porque los
#      DataFrames de MarketData/BufferMarketData vienen así.
#    • No devuelve DataFrame con long_signal/short_signal — devuelve un dict
#      con la acción del último bar, que es lo que consume el trader vivo.
#    • tod_filter es opcional: para crypto (24/7) se pasa `False` para skip.
#    • EMA_Crossover queda como dos variantes (3/8 y 8/21) para ser registradas
#      separadamente en el dashboard (Juan las quiere toggleables ambas).
#    • La dirección SELL aquí significa "señal bajista" (no mirror de BUY).
#      Para equity paper-trade de Eolo que no hace shorts, el adapter puede
#      interpretarla como "cerrar long si lo hay". En crypto (spot long-only)
#      idem. En v2 opciones, SELL se mapea a BUY_PUT.
# ============================================================
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

from .indicators import (
    ema, atr, macd, adx, choppiness_index, ema_slope,
    buy_sell_pressure_index, session_vwap, opening_range, donchian, time_of_day_flag,
    # Indicadores Combos Ganadores
    ttm_squeeze, tema, cci, heikin_ashi, supertrend,
    ema_stacked_bull, ema_stacked_bear, slim_ribbon, tdi,
    btd_navigator, campbell_trend, keltner_channels,
)


@dataclass
class StrategyConfig:
    # Régimen
    use_regime_filter: bool = False   # deshabilitado — sin filtro ADX/Choppiness
    adx_min: float = 22.0
    chop_max: float = 55.0
    # Trend filter sobre EMA50
    use_ema_trend_filter: bool = False  # deshabilitado — sin filtro de tendencia EMA50
    trend_ema_length: int = 50
    # Time-of-day (equity). Crypto debe pasar False.
    use_tod_filter: bool = True
    # ATR
    atr_length: int = 14
    # Bias externo: +1 long-only, -1 short-only, 0 ambos.
    higher_tf_bias: int = 0


# ── Helpers ──────────────────────────────────────────────────

def _last_bool(s: pd.Series, default: bool = False) -> bool:
    try:
        v = bool(s.iloc[-1])
        return v
    except Exception:
        return default


def _last_float(s: pd.Series, default: Optional[float] = None) -> Optional[float]:
    try:
        v = s.iloc[-1]
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _apply_common_filters(df: pd.DataFrame, raw_long: pd.Series, raw_short: pd.Series,
                          cfg: StrategyConfig,
                          tod_filter: Optional[bool] = None) -> tuple[pd.Series, pd.Series]:
    long_sig = raw_long.copy()
    short_sig = raw_short.copy()

    if cfg.use_regime_filter:
        adx_df = adx(df)
        ci = choppiness_index(df)
        regime_ok = (adx_df["adx"] >= cfg.adx_min) & (ci <= cfg.chop_max)
        long_sig &= regime_ok
        short_sig &= regime_ok
        long_sig &= (adx_df["di_plus"] > adx_df["di_minus"])
        short_sig &= (adx_df["di_minus"] > adx_df["di_plus"])

    if cfg.use_ema_trend_filter:
        trend = ema(df["close"], cfg.trend_ema_length)
        long_sig &= df["close"] > trend
        short_sig &= df["close"] < trend

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        long_sig &= tod["is_tradeable"].values
        short_sig &= tod["is_tradeable"].values

    if cfg.higher_tf_bias == 1:
        short_sig[:] = False
    elif cfg.higher_tf_bias == -1:
        long_sig[:] = False

    return long_sig, short_sig


def _verdict(long_last: bool, short_last: bool, reason: str) -> dict:
    if long_last and not short_last:
        return {"signal": "BUY", "reason": reason}
    if short_last and not long_last:
        return {"signal": "SELL", "reason": reason}
    return {"signal": "HOLD", "reason": reason}


# ============================================================
# 1 — EMA Crossover (usa `fast/slow` param; dos variantes registradas)
# ============================================================

def strategy_ema_crossover(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                           tod_filter: Optional[bool] = None,
                           fast: int = 8, slow: int = 21) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < max(slow, cfg.trend_ema_length) + 5:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    ef = ema(df["close"], fast)
    es = ema(df["close"], slow)
    cross_up = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_dn = (ef < es) & (ef.shift(1) >= es.shift(1))

    vol_sma = df["volume"].rolling(20).mean()
    vol_confirm = df["volume"] > 1.2 * vol_sma

    raw_long = cross_up & vol_confirm
    raw_short = cross_dn & vol_confirm

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"EMA{fast}/{slow} cross + vol>1.2x SMA20 + filtros",
    )


# ============================================================
# 2 — MACD Accel
# ============================================================

def strategy_macd_accel(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                        tod_filter: Optional[bool] = None) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 40:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    m, s, h = macd(df["close"])
    hist_up = (h > 0) & (h > h.shift(1)) & (h.shift(1) > h.shift(2))
    hist_dn = (h < 0) & (h < h.shift(1)) & (h.shift(1) < h.shift(2))
    cross_zero_up = (h > 0) & (h.shift(1) <= 0)
    cross_zero_dn = (h < 0) & (h.shift(1) >= 0)

    raw_long = (cross_zero_up | hist_up) & (m > 0)
    raw_short = (cross_zero_dn | hist_dn) & (m < 0)
    raw_long &= ~raw_long.shift(1).fillna(False).astype(bool)
    raw_short &= ~raw_short.shift(1).fillna(False).astype(bool)

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        "MACD hist accel + zero cross",
    )


# ============================================================
# 3 — Volume Breakout
# ============================================================

def strategy_volume_breakout(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                             tod_filter: Optional[bool] = None,
                             lookback: int = 20, vol_mult: float = 2.0) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < lookback + 5:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    dc = donchian(df, lookback)
    dc_upper = dc["dc_upper"].shift(1)
    dc_lower = dc["dc_lower"].shift(1)

    vol_avg = df["volume"].rolling(lookback).mean()
    vol_spike = df["volume"] > vol_mult * vol_avg

    raw_long = (df["high"] > dc_upper) & vol_spike
    raw_short = (df["low"] < dc_lower) & vol_spike

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"Donchian({lookback}) break + vol>{vol_mult}x",
    )


# ============================================================
# 4 — Buy Pressure Trend
# ============================================================

def strategy_buy_pressure(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                          tod_filter: Optional[bool] = None,
                          bsp_threshold: float = 0.60, persistence_bars: int = 3) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 30:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    bsp = buy_sell_pressure_index(df)
    persistent_buy = (bsp["bsp_ratio"] > bsp_threshold).rolling(persistence_bars).sum() >= persistence_bars
    persistent_sell = (bsp["bsp_ratio"] < (1 - bsp_threshold)).rolling(persistence_bars).sum() >= persistence_bars

    raw_long = persistent_buy & ~persistent_buy.shift(1).fillna(False)
    raw_short = persistent_sell & ~persistent_sell.shift(1).fillna(False)

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"BSP ratio persistente ({persistence_bars} bars)",
    )


# ============================================================
# 5 — Sell Pressure / Net Pressure
# ============================================================

def strategy_sell_pressure(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                           tod_filter: Optional[bool] = None,
                           net_pressure_threshold: float = 0.5, lookback: int = 5) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 30:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    bsp = buy_sell_pressure_index(df)
    net_ma = bsp["net_pressure"].rolling(lookback).mean()

    raw_long = (net_ma > net_pressure_threshold) & (net_ma.shift(1) <= net_pressure_threshold)
    raw_short = (net_ma < -net_pressure_threshold) & (net_ma.shift(1) >= -net_pressure_threshold)

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"Net pressure cross ±{net_pressure_threshold}",
    )


# ============================================================
# 6 — VWAP Momentum
# ============================================================

def strategy_vwap_momentum(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                           tod_filter: Optional[bool] = None,
                           slope_bars: int = 10) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 30:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    vwap = session_vwap(df)
    vwap_slope = ema_slope(vwap, slope_bars)

    above = df["close"] > vwap
    below = df["close"] < vwap
    touched_above = ((df["low"] <= vwap) & above).rolling(3).max().astype(bool)
    touched_below = ((df["high"] >= vwap) & below).rolling(3).max().astype(bool)
    price_up = df["close"] > df["close"].shift(1)
    price_dn = df["close"] < df["close"].shift(1)

    raw_long = above & touched_above & price_up & (vwap_slope > 0)
    raw_short = below & touched_below & price_dn & (vwap_slope < 0)
    raw_long &= ~raw_long.shift(1).fillna(False).astype(bool)
    raw_short &= ~raw_short.shift(1).fillna(False).astype(bool)

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        "VWAP pullback + slope directional",
    )


# ============================================================
# 7 — Opening Range Breakout (equity only — NO crypto)
# ============================================================

def strategy_opening_range_breakout(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                                    tod_filter: Optional[bool] = None,  # ignorado
                                    or_minutes: int = 30, vol_mult: float = 1.5) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 10:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    or_df = opening_range(df, or_minutes)
    or_high = or_df["or_high"]
    or_low = or_df["or_low"]

    vol_avg = df["volume"].rolling(20).mean()
    vol_ok = df["volume"] > vol_mult * vol_avg

    break_up = (df["high"] > or_high) & vol_ok
    break_dn = (df["low"] < or_low) & vol_ok

    # Primera ocurrencia del día
    try:
        day_key = pd.Series(df.index.date, index=df.index)
    except Exception:
        day_key = pd.Series(0, index=df.index)
    first_up = break_up & ~break_up.groupby(day_key).cumsum().gt(1).shift(1).fillna(False)
    first_dn = break_dn & ~break_dn.groupby(day_key).cumsum().gt(1).shift(1).fillna(False)

    # ORB NO usa tod_filter (queremos específicamente la mañana)
    cfg_orb = StrategyConfig(
        use_regime_filter=cfg.use_regime_filter,
        adx_min=cfg.adx_min, chop_max=cfg.chop_max,
        use_tod_filter=False,
        use_ema_trend_filter=cfg.use_ema_trend_filter,
        trend_ema_length=cfg.trend_ema_length,
        higher_tf_bias=cfg.higher_tf_bias,
    )
    long_s, short_s = _apply_common_filters(df, first_up, first_dn, cfg_orb, tod_filter=False)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"ORB {or_minutes}m primer break + vol>{vol_mult}x",
    )


# ============================================================
# 8 — Donchian Turtle
# ============================================================

def strategy_donchian_turtle(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                             tod_filter: Optional[bool] = None,
                             entry_len: int = 20, exit_len: int = 10) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < entry_len + 5:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    dc = donchian(df, entry_len)
    dc_entry_upper = dc["dc_upper"].shift(1)
    dc_entry_lower = dc["dc_lower"].shift(1)

    raw_long = df["high"] > dc_entry_upper
    raw_short = df["low"] < dc_entry_lower

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"Turtle {entry_len}-bar break",
    )


# ============================================================
# 9 — Bulls-gated BSP momentum
# ============================================================

def strategy_bulls_bsp(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                       tod_filter: Optional[bool] = None,
                       bvp_threshold: float = 65.0, bvp_lookback: int = 3) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 30:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    bsp = buy_sell_pressure_index(df)
    work = pd.concat([df, bsp[["bvp_pct", "svp_pct"]]], axis=1)

    # Breadth: si no viene attacheado a df, se asume True (degradar gracefully).
    the_bulls = df["the_bulls"] if "the_bulls" in df.columns else pd.Series(True, index=df.index)
    the_bears = df["the_bears"] if "the_bears" in df.columns else pd.Series(True, index=df.index)

    bvp_persistent = (work["bvp_pct"] > bvp_threshold).rolling(bvp_lookback).sum() >= bvp_lookback
    svp_persistent = (work["svp_pct"] > bvp_threshold).rolling(bvp_lookback).sum() >= bvp_lookback

    new_high_5 = df["close"] >= df["high"].rolling(5).max().shift(1)
    new_low_5 = df["close"] <= df["low"].rolling(5).min().shift(1)

    bvp_trigger = bvp_persistent & ~bvp_persistent.shift(1).fillna(False)
    svp_trigger = svp_persistent & ~svp_persistent.shift(1).fillna(False)

    raw_long = bvp_trigger & new_high_5 & the_bulls
    raw_short = svp_trigger & new_low_5 & the_bears

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"BVP>{bvp_threshold} persistente + breadth + nuevo H/L 5 bars",
    )


# ============================================================
# 10 — Net BSV Trend
# ============================================================

def strategy_net_bsv(df: pd.DataFrame, cfg: Optional[StrategyConfig] = None,
                     tod_filter: Optional[bool] = None,
                     net_smooth: int = 5, net_threshold: float = 0.3) -> dict:
    cfg = cfg or StrategyConfig()
    if len(df) < 30:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    bsp = buy_sell_pressure_index(df)
    the_bulls = df["the_bulls"] if "the_bulls" in df.columns else pd.Series(True, index=df.index)
    the_bears = df["the_bears"] if "the_bears" in df.columns else pd.Series(True, index=df.index)

    net_ma = bsp["net_pressure"].rolling(net_smooth).mean()
    cross_up = (net_ma > net_threshold) & (net_ma.shift(1) <= net_threshold)
    cross_dn = (net_ma < -net_threshold) & (net_ma.shift(1) >= -net_threshold)

    raw_long = cross_up & the_bulls
    raw_short = cross_dn & the_bears

    long_s, short_s = _apply_common_filters(df, raw_long, raw_short, cfg, tod_filter)
    return _verdict(
        _last_bool(long_s), _last_bool(short_s),
        f"Net BSV cross ±{net_threshold} + breadth",
    )


# ============================================================
# ██████████████ COMBOS GANADORES (7 estrategias) ████████████
# ============================================================
# Portadas de las estrategias ThinkScript analizadas (2026-04).
# Cada combo combina 2-3 indicadores del archivo original para
# conseguir el win rate y frecuencia descriptos en el análisis.
# ============================================================


# ── Combo 1: EMA Scalper 3/8 (ALTA frecuencia) ──────────────

def strategy_combo1_ema_scalper(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
) -> dict:
    """
    Combo 1 — EMA Scalper 3/8.
    Signal: EMA3 cruza EMA8 (crossEMAbasic_V3) + stacked bullish (EMA8>21>34>55>89).
    Targets: entrada en cruce, stop = EMA8 ± ATR, 3 targets en 1/2/3×ATR.
    ~15 señales/día equity, ~10/día crypto. Win rate ~52-55%.
    No aplica _apply_common_filters estricto (el stacked ya es suficiente filtro).
    """
    cfg = cfg or StrategyConfig()
    if len(df) < 90:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    close = df["close"]
    e3  = ema(close, 3)
    e8  = ema(close, 8)
    e21 = ema(close, 21)
    e34 = ema(close, 34)
    e55 = ema(close, 55)
    e89 = ema(close, 89)
    atr_val = atr(df)

    cross_up  = (e3 > e8) & (e3.shift(1) <= e8.shift(1))
    cross_dn  = (e3 < e8) & (e3.shift(1) >= e8.shift(1))

    stacked_bull = (e8 > e21) & (e21 > e34) & (e34 > e55) & (e55 > e89)
    stacked_bear = (e8 < e21) & (e21 < e34) & (e34 < e55) & (e55 < e89)

    raw_long  = cross_up & stacked_bull
    raw_short = cross_dn & stacked_bear

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        raw_long  &= tod["is_tradeable"].values
        raw_short &= tod["is_tradeable"].values

    last_atr = _last_float(atr_val)
    last_e8  = _last_float(e8)
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        f"EMA3/8 cross + EMA stack | stop={round(last_e8 - last_atr, 4) if last_e8 and last_atr else 'n/a'}",
    )


# ── Combo 2: Rubber Band VWAP (ALTA, equity-only) ───────────

def strategy_combo2_rubber_band(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
) -> dict:
    """
    Combo 2 — Rubber Band VWAP.
    Mean reversion al VWAP entre 09:45 y 14:30 ET (Rubber_Band_V3).
    Long: precio < VWAP + EMA3 cruza sobre EMA8 en ventana horaria.
    Short: precio > VWAP + EMA3 cruza bajo EMA8 en ventana horaria.
    ~7 señales/día, win rate ~57%. EQUITY-ONLY (ventana RTH).
    """
    cfg = cfg or StrategyConfig()
    if len(df) < 20:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    # tod_filter=False indica crypto → skip (no tiene ventana Rubber Band)
    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if not use_tod:
        return {"signal": "HOLD", "reason": "Rubber Band requiere RTH (equity-only)"}

    close = df["close"]
    e3 = ema(close, 3)
    e8 = ema(close, 8)
    vwap = session_vwap(df)

    cross_up = (e3 > e8) & (e3.shift(1) <= e8.shift(1))
    cross_dn = (e3 < e8) & (e3.shift(1) >= e8.shift(1))

    # Ventana Rubber Band: 09:45–14:30 ET
    try:
        idx = df.index
        idx_et = idx.tz_localize("UTC").tz_convert("US/Eastern") if idx.tz is None else idx.tz_convert("US/Eastern")
        hour = idx_et.hour + idx_et.minute / 60.0
        in_window = (hour >= 9.75) & (hour <= 14.5)
    except Exception:
        in_window = pd.Series(True, index=df.index)

    raw_long  = cross_up & (close < vwap) & in_window
    raw_short = cross_dn & (close > vwap) & in_window

    last_vwap = _last_float(vwap)
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        f"Rubber Band VWAP | vwap={round(last_vwap, 4) if last_vwap else 'n/a'} | 09:45-14:30 ET",
    )


# ── Combo 3: Nino Squeeze Setup ⭐ (MEDIA frecuencia) ────────

def strategy_combo3_nino_squeeze(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
    vol_lookback: int = 20,
) -> dict:
    """
    Combo 3 — Nino Squeeze Setup ⭐ (la estrella del análisis).
    4 condiciones (portadas de Bull_Squeeze_VolumeSTUDY.ts + New_Stacked_EMA_V7.ts):
      1. EMA stack: EMA8>21>34>55>89 (bullish) o inverso (bearish)
      2. TTM Squeeze activo (BB inside KC)
      3. Volume > avg_volume(20)
      4. MACD Diff positivo y creciente (Diff > Diff[1])
    Targets: 3 × ATR. ~3 señales/día. Win rate ~67%.
    """
    cfg = cfg or StrategyConfig()
    if len(df) < 90:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    close = df["close"]
    sq = ttm_squeeze(df)

    # EMA stack
    stacked_bull = ema_stacked_bull(df)
    stacked_bear = ema_stacked_bear(df)

    # Volume > media
    avg_vol = df["volume"].rolling(vol_lookback).mean()
    vol_ok  = df["volume"] > avg_vol

    # MACD Diff positivo y creciendo
    diff = sq["macd_diff"]
    macd_up   = (diff > 0) & (diff > diff.shift(1))
    macd_down = (diff < 0) & (diff < diff.shift(1))

    # Señal en el bar SIGUIENTE al que se cumple (como ThinkScript conditions[1])
    conditions_long  = (sq["squeeze_on"] & stacked_bull & vol_ok & macd_up).shift(1).astype("boolean").fillna(False).astype(bool)
    conditions_short = (sq["squeeze_on"] & stacked_bear & vol_ok & macd_down).shift(1).astype("boolean").fillna(False).astype(bool)

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        conditions_long  &= tod["is_tradeable"].values
        conditions_short &= tod["is_tradeable"].values

    atr_val = atr(df)
    last_atr = _last_float(atr_val)
    last_close = _last_float(close)
    sq_cnt = int(_last_float(sq["squeeze_count"], 0) or 0)
    return _verdict(
        _last_bool(conditions_long), _last_bool(conditions_short),
        (f"Nino Squeeze: stack+squeeze({sq_cnt}bars)+vol+macd_diff | "
         f"t1={round(last_close + last_atr, 4) if last_close and last_atr else 'n/a'}"),
    )


# ── Combo 4: SlimRibbon + MACD (MEDIA frecuencia) ────────────

def strategy_combo4_slimribbon(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
) -> dict:
    """
    Combo 4 — SlimRibbon + MACD + TDI context.
    Long: EMA8>13>21 (ribbon bull) + MACD value cruza signal hacia arriba + TDI fast > TDI slow.
    Short: inverso.
    Portado de SlimRibbon_Marcelo_V2.ts + Robot_MACD_cross_Up.ts + TDI.ts.
    ~3 señales/día. Win rate ~62%.
    """
    cfg = cfg or StrategyConfig()
    if len(df) < 35:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    close = df["close"]
    ribbon = slim_ribbon(df)
    macd_val, macd_sig_line, macd_diff = macd(close)
    tdi_df = tdi(df)

    # MACD cross
    macd_cross_up = (macd_val > macd_sig_line) & (macd_val.shift(1) <= macd_sig_line.shift(1))
    macd_cross_dn = (macd_val < macd_sig_line) & (macd_val.shift(1) >= macd_sig_line.shift(1))

    # TDI contexto (fast > slow = bullish momentum)
    tdi_bull = tdi_df["tdi_fast"] > tdi_df["tdi_slow"]
    tdi_bear = tdi_df["tdi_fast"] < tdi_df["tdi_slow"]

    raw_long  = ribbon["ribbon_bull"] & macd_cross_up & tdi_bull
    raw_short = ribbon["ribbon_bear"] & macd_cross_dn & tdi_bear

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        raw_long  &= tod["is_tradeable"].values
        raw_short &= tod["is_tradeable"].values

    last_atr = _last_float(atr(df))
    last_c   = _last_float(close)
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        (f"SlimRibbon+MACD cross+TDI | "
         f"t1={round(last_c + last_atr, 4) if last_c and last_atr else 'n/a'}"),
    )


# ── Combo 5: BTD + Stacked + PullBack34 (MEDIA) ──────────────

def strategy_combo5_btd(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
    btd_length: int = 22,
) -> dict:
    """
    Combo 5 — BTD (Buy The Dip) + Stacked EMA + PullBack34.
    Long: BTD % desde mínimo 22 barras cruza 0 hacia arriba
          + EMA stack bullish + precio <= EMA34 (pullback válido).
    Short: BTD % desde máximo cruza 0 hacia abajo + stack bearish.
    Portado de Robot_BTD_Upper_Navigator.ts + PullBack_34.ts.
    ~3 señales/día. Win rate ~62%.
    """
    cfg = cfg or StrategyConfig()
    if len(df) < btd_length + 5:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    close = df["close"]
    btd_val = btd_navigator(df, btd_length)

    # Cruce de 0
    btd_cross_up = (btd_val > 0) & (btd_val.shift(1) <= 0)
    btd_cross_dn = (btd_val < 0) & (btd_val.shift(1) >= 0)

    # EMA stack
    stacked_bull = ema_stacked_bull(df)
    stacked_bear = ema_stacked_bear(df)

    # PullBack34: precio toca o cae bajo EMA34 mientras stack bullish
    e34 = ema(close, 34)
    pb34_bull = close <= e34
    pb34_bear = close >= e34

    raw_long  = btd_cross_up & stacked_bull & pb34_bull
    raw_short = btd_cross_dn & stacked_bear & pb34_bear

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        raw_long  &= tod["is_tradeable"].values
        raw_short &= tod["is_tradeable"].values

    atr_val = atr(df)
    last_atr = _last_float(atr_val)
    last_c   = _last_float(close)
    last_btd = _last_float(btd_val)
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        (f"BTD({round(last_btd, 2) if last_btd else 'n/a'}%)cross0 + stack + PB34 | "
         f"t1={round(last_c + last_atr, 4) if last_c and last_atr else 'n/a'}"),
    )


# ── Combo 6: FractalCCIx Premium (BAJA frecuencia) ───────────

def strategy_combo6_fractalccix(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
    tema_length: int = 8,
    cci_length: int = 14,
    min_squeeze_bars: int = 4,
) -> dict:
    """
    Combo 6 — FractalCCIx Premium.
    Señal: TEMA(8) aplicado sobre Heikin Ashi close cruza hacia arriba
           + CCI(14) < -100 (extremo oversold para buy) o >+100 (overbought sell)
           + squeeze_count > 4 barras.
    Portado de FractalCCIx_V2.ts + BreakPriceMomentum_V3.ts.
    0-2 señales/día. Win rate ~72%.
    """
    cfg = cfg or StrategyConfig()
    if len(df) < max(tema_length * 3, cci_length, 20):
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    # Heikin Ashi
    ha = heikin_ashi(df)
    tema_ha = tema(ha["ha_close"], tema_length)

    # TEMA cross (buy = tema_ha > tema_ha[1] after being below, sell = inverso)
    tema_cross_up = (tema_ha > tema_ha.shift(1)) & (tema_ha.shift(1) <= tema_ha.shift(2))
    tema_cross_dn = (tema_ha < tema_ha.shift(1)) & (tema_ha.shift(1) >= tema_ha.shift(2))

    # CCI extremo
    cci_val = cci(df, cci_length)
    cci_oversold  = cci_val < -100
    cci_overbought = cci_val > 100

    # Squeeze activo ≥ min_squeeze_bars
    sq = ttm_squeeze(df)
    sq_mature = sq["squeeze_count"] > min_squeeze_bars

    raw_long  = tema_cross_up & cci_oversold  & sq_mature
    raw_short = tema_cross_dn & cci_overbought & sq_mature

    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        raw_long  &= tod["is_tradeable"].values
        raw_short &= tod["is_tradeable"].values

    sq_cnt = int(_last_float(sq["squeeze_count"], 0) or 0)
    last_cci = _last_float(cci_val)
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        (f"FractalCCIx: TEMA/HA cross + CCI={round(last_cci, 1) if last_cci else 'n/a'} "
         f"+ squeeze({sq_cnt}bars)"),
    )


# ── Combo 7: Campbell Swing (BAJA frecuencia) ────────────────

def strategy_combo7_campbell(
    df: pd.DataFrame,
    cfg: Optional[StrategyConfig] = None,
    tod_filter: Optional[bool] = None,
    trend_bars: int = 20,
) -> dict:
    """
    Combo 7 — Campbell Swing.
    Condiciones (CampbellScan.ts + Supertrend_MTF_Auto.ts + TDI):
      1. EMA34 en tendencia alcista los últimos 20 barras (slope positivo continuo)
      2. Close cruza por encima de EMA3 (señal de entrada)
      3. Supertrend dirección bullish (= close > supertrend line)
      4. TDI: fast > slow (momentum de fondo positivo)
    Frecuencia: 2-5/semana. Win rate ~70%.
    """
    cfg = cfg or StrategyConfig()
    if len(df) < 55:
        return {"signal": "HOLD", "reason": f"insufficient bars ({len(df)})"}

    close = df["close"]
    e3  = ema(close, 3)
    e34 = ema(close, 34)

    # 1. EMA34 trending up (todos los últimos trend_bars bars en subida)
    e34_up   = campbell_trend(df, trend_bars)
    e34_down = ~campbell_trend(df, trend_bars)

    # 2. Close cruza EMA3
    cross_above = (close > e3) & (close.shift(1) <= e3.shift(1))
    cross_below = (close < e3) & (close.shift(1) >= e3.shift(1))

    # 3. Supertrend dirección
    st = supertrend(df)
    st_bull = st["direction"] == 1
    st_bear = st["direction"] == -1

    # 4. TDI contexto
    tdi_df = tdi(df)
    tdi_bull = tdi_df["tdi_fast"] > tdi_df["tdi_slow"]
    tdi_bear = tdi_df["tdi_fast"] < tdi_df["tdi_slow"]

    raw_long  = e34_up   & cross_above & st_bull & tdi_bull
    raw_short = e34_down & cross_below & st_bear & tdi_bear

    # Combo 7 es swing → no excluir midday ni opening (señales menos frecuentes)
    use_tod = cfg.use_tod_filter if tod_filter is None else tod_filter
    if use_tod:
        tod = time_of_day_flag(df)
        # Solo excluir power hour (16:00) y fuera de RTH; no midday
        idx_et = None
        try:
            idx = df.index
            idx_et = idx.tz_localize("UTC").tz_convert("US/Eastern") if idx.tz is None else idx.tz_convert("US/Eastern")
            hour = idx_et.hour + idx_et.minute / 60.0
            rth = (hour >= 9.5) & (hour < 16.0)
        except Exception:
            rth = pd.Series(True, index=df.index)
        raw_long  &= rth
        raw_short &= rth

    last_st   = _last_float(st["line"])
    last_c    = _last_float(close)
    st_dir    = "▲" if (_last_bool(st_bull)) else "▼"
    return _verdict(
        _last_bool(raw_long), _last_bool(raw_short),
        f"Campbell: EMA34_trend({trend_bars}b)+EMA3cross+ST{st_dir}+TDI | st={round(last_st, 4) if last_st else 'n/a'}",
    )


# ============================================================
# REGISTRY
# ============================================================
# Claves = identificadores canónicos que usan los dashboards y DEFAULT_STRATEGIES.
# EMA_Crossover se registra como dos entries (3/8 y 8/21) con params pre-bindeados.

def _ema_3_8(df, cfg=None, tod_filter=None):
    return strategy_ema_crossover(df, cfg=cfg, tod_filter=tod_filter, fast=3, slow=8)

def _ema_8_21(df, cfg=None, tod_filter=None):
    return strategy_ema_crossover(df, cfg=cfg, tod_filter=tod_filter, fast=8, slow=21)


STRATEGY_REGISTRY_V3 = {
    "EMA_3_8":                 _ema_3_8,
    "EMA_8_21":                _ema_8_21,
    "MACD_ACCEL":              strategy_macd_accel,
    "VOLUME_BREAKOUT":         strategy_volume_breakout,
    "BUY_PRESSURE":            strategy_buy_pressure,
    "SELL_PRESSURE":           strategy_sell_pressure,
    "VWAP_MOMENTUM":           strategy_vwap_momentum,
    "ORB_V3":                  strategy_opening_range_breakout,
    "DONCHIAN_TURTLE":         strategy_donchian_turtle,
    "BULLS_BSP":               strategy_bulls_bsp,
    "NET_BSV":                 strategy_net_bsv,
    # ── Combos Ganadores (7 estrategias, 2026-04) ─────────────
    "COMBO1_EMA_SCALPER":      strategy_combo1_ema_scalper,   # ALTA  — EMA3/8 + stack
    "COMBO2_RUBBER_BAND":      strategy_combo2_rubber_band,   # ALTA  — Rubber Band VWAP (equity)
    "COMBO3_NINO_SQUEEZE":     strategy_combo3_nino_squeeze,  # MEDIA — Nino Squeeze ⭐
    "COMBO4_SLIMRIBBON":       strategy_combo4_slimribbon,    # MEDIA — SlimRibbon + MACD
    "COMBO5_BTD":              strategy_combo5_btd,           # MEDIA — BTD + Stacked + PB34
    "COMBO6_FRACTALCCIX":      strategy_combo6_fractalccix,   # BAJA  — FractalCCIx Premium
    "COMBO7_CAMPBELL":         strategy_combo7_campbell,      # BAJA  — Campbell Swing
}

# Estrategias que NO deben ofrecerse en crypto (requieren RTH equity).
# COMBO2_RUBBER_BAND usa ventana 09:45-14:30 ET — equity-only.
EQUITY_ONLY = {"ORB_V3", "COMBO2_RUBBER_BAND"}

# Las breadth-gated degradan bien a True si el dashboard no tiene la feed breadth.
BREADTH_GATED = {"BULLS_BSP", "NET_BSV"}


def list_strategies_for_bot(bot_kind: str) -> list[str]:
    """bot_kind in {'v1','v2','crypto'}. v1/v2 ven las 11; crypto excluye ORB."""
    names = list(STRATEGY_REGISTRY_V3.keys())
    if bot_kind == "crypto":
        names = [n for n in names if n not in EQUITY_ONLY]
    return names


# ============================================================
# DIRECTIONAL REGISTRY (Phase A — 2026-04-21, Opción C)
# ============================================================
# Cada estrategia se expone en dos variantes independientes:
#   <NAME>_LONG    → solo emite BUY (ignora SELL → HOLD)
#   <NAME>_SHORT   → solo emite SELL (ignora BUY → HOLD)
#
# Esto permite que el bot toggle on/off cada dirección por separado
# (rsi_sma200_long ON, rsi_sma200_short OFF, etc.) y que Strategy_Stats
# trackee performance independiente por dirección. Crypto sigue usando
# solo _LONG porque el spot no permite short.
#
# Las funciones originales (registradas en STRATEGY_REGISTRY_V3) quedan
# intactas para back-compat y para el caso "ambas direcciones unificadas".

def _directional_wrapper(fn, direction: str):
    """Filtra la salida del strategy para emitir solo señales de la dirección dada.
    Si la estrategia retorna BUY y direction='short', devuelve HOLD (y viceversa).
    El campo 'reason' se anota con [LONG-only] / [SHORT-only] para trazabilidad.
    """
    if direction not in ("long", "short"):
        raise ValueError(f"direction debe ser 'long' o 'short', got {direction!r}")
    expected_signal = "BUY" if direction == "long" else "SELL"
    tag = f"[{direction.upper()}-only]"

    def wrapped(df, *args, **kwargs):
        try:
            res = fn(df, *args, **kwargs)
        except Exception as e:
            return {"signal": "ERROR", "reason": f"{tag} {e}"}
        if not isinstance(res, dict):
            return {"signal": "HOLD", "reason": f"{tag} non-dict result"}
        sig = res.get("signal", "HOLD")
        if sig == expected_signal:
            out = dict(res)
            out["reason"] = (out.get("reason") or "") + f" {tag}"
            out["direction"] = direction
            return out
        # Diferente dirección → HOLD silencioso (no leak de señal opuesta).
        out = dict(res)
        out["signal"] = "HOLD"
        out["direction"] = direction
        return out

    wrapped.__name__ = f"{getattr(fn, '__name__', 'strategy')}_{direction}"
    return wrapped


# Registry directional: 22 entries (11 estrategias × 2 direcciones).
STRATEGY_REGISTRY_V3_DIRECTIONAL: dict[str, callable] = {}
for _name, _fn in STRATEGY_REGISTRY_V3.items():
    STRATEGY_REGISTRY_V3_DIRECTIONAL[f"{_name}_LONG"]  = _directional_wrapper(_fn, "long")
    STRATEGY_REGISTRY_V3_DIRECTIONAL[f"{_name}_SHORT"] = _directional_wrapper(_fn, "short")


def list_directional_strategies_for_bot(bot_kind: str) -> list[str]:
    """Variante directional de list_strategies_for_bot.

    bot_kind:
      - 'v1' / 'v2': retorna las 22 (11 LONG + 11 SHORT). v2 puede mapear
        SHORT → BUY_PUT en su OptionsBrain.
      - 'crypto': solo las 10 LONG (Binance spot no permite short, ORB excluido).
    """
    if bot_kind == "crypto":
        return [
            f"{n}_LONG" for n in STRATEGY_REGISTRY_V3.keys()
            if n not in EQUITY_ONLY
        ]
    return list(STRATEGY_REGISTRY_V3_DIRECTIONAL.keys())
