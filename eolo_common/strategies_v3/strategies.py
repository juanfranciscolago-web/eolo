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
)


@dataclass
class StrategyConfig:
    # Régimen
    use_regime_filter: bool = True
    adx_min: float = 22.0
    chop_max: float = 55.0
    # Trend filter sobre EMA50
    use_ema_trend_filter: bool = True
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
}

# Estrategias que NO deben ofrecerse en crypto (requieren RTH equity).
EQUITY_ONLY = {"ORB_V3"}

# Las breadth-gated degradan bien a True si el dashboard no tiene la feed breadth.
BREADTH_GATED = {"BULLS_BSP", "NET_BSV"}


def list_strategies_for_bot(bot_kind: str) -> list[str]:
    """bot_kind in {'v1','v2','crypto'}. v1/v2 ven las 11; crypto excluye ORB."""
    names = list(STRATEGY_REGISTRY_V3.keys())
    if bot_kind == "crypto":
        names = [n for n in names if n not in EQUITY_ONLY]
    return names
