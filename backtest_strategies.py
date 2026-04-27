"""
FASE 6: Backtest Wrappers para 18 Estrategias de Bot v1

Cada función `backtest_strategy_name(df)`:
- Recibe DataFrame histórico
- Retorna array de señales (BUY=1, SELL=-1, HOLD=0)
- Compatible con BacktestEngine

Estrategias incluidas:
1. bollinger - Bollinger Bands bounce
2. ema_tsi - EMA Cloud + TSI + MACD
3. gap - Gap Detection
4. ha_cloud - Heikin Ashi + EMA Cloud
5. hh_ll - High/Low Breakout
6. macd_bb - MACD + Bollinger Upper
7. obv_mtf - OBV Multi-TF
8. rsi_sma200 - RSI + SMA200
9. rvol_breakout - RVOL Breakout
10. squeeze - Bollinger/Keltner Squeeze
11. stop_run - Stop Run Reversal
12. supertrend - Supertrend
13. tsv - Time-Segmented Volume
14. vela_pivot - Vela Pivot
15. volume_reversal_bar - Volume Reversal Bar
16. vw_macd - Volume-Weighted MACD
17. vwap_rsi - VWAP + RSI
18. vwap_zscore - VWAP Z-Score
"""

import pandas as pd
import numpy as np
from loguru import logger


# ============================================================================
# 1. BOLLINGER BANDS BOUNCE
# ============================================================================

def backtest_bollinger(df: pd.DataFrame) -> np.ndarray:
    """Bollinger Bands bounce detection."""
    df = df.copy()
    bb_period = 20
    bb_std = 2.0

    df["bb_mid"] = df["close"].rolling(bb_period).mean()
    df["bb_std"] = df["close"].rolling(bb_period).std()
    df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_std"]

    signal = np.zeros(len(df))
    for i in range(bb_period + 1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        if pd.isna(curr["bb_upper"]) or pd.isna(curr["bb_lower"]):
            continue

        prev_touched = prev["low"] <= prev["bb_lower"] or prev["close"] <= prev["bb_lower"]
        curr_inside = curr["close"] > curr["bb_lower"]

        if prev_touched and curr_inside:
            signal[i] = 1  # BUY
        elif curr["high"] >= curr["bb_upper"] or curr["close"] < curr["bb_lower"]:
            signal[i] = -1  # SELL

    return signal


# ============================================================================
# 2. EMA TSI (EMA Cloud + TSI + MACD)
# ============================================================================

def backtest_ema_tsi(df: pd.DataFrame) -> np.ndarray:
    """EMA Cloud + TSI + MACD strategy."""
    df = df.copy()

    # EMA Cloud
    df["ema3"] = df["close"].ewm(span=3, adjust=False).mean()
    df["ema8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    # MACD
    df["macd"] = df["close"].ewm(span=12, adjust=False).mean() - df["close"].ewm(span=26, adjust=False).mean()
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # TSI (simple version)
    momentum = df["close"].diff()
    df["tsi"] = momentum.ewm(span=25, adjust=False).mean().ewm(span=13, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(25, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # BUY: EMA bullish + MACD bullish + TSI > 0
        if (curr["ema3"] > curr["ema8"] > curr["ema21"] and
            curr["macd"] > curr["macd_signal"] and
            curr["tsi"] > 0):
            signal[i] = 1
        # SELL: EMA bearish or MACD bearish
        elif (curr["ema3"] < curr["ema8"] or curr["macd"] < curr["macd_signal"]):
            signal[i] = -1

    return signal


# ============================================================================
# 3. GAP DETECTION
# ============================================================================

def backtest_gap(df: pd.DataFrame) -> np.ndarray:
    """Gap detection and fade/follow."""
    df = df.copy()
    signal = np.zeros(len(df))

    for i in range(1, len(df)):
        prev_close = df.iloc[i - 1]["close"]
        curr_open = df.iloc[i]["open"]
        curr_close = df.iloc[i]["close"]

        gap = (curr_open - prev_close) / prev_close

        # BUY if gap down and close recovers
        if gap < -0.005 and curr_close > curr_open:
            signal[i] = 1
        # SELL if gap up but close retreats
        elif gap > 0.005 and curr_close < curr_open:
            signal[i] = -1

    return signal


# ============================================================================
# 4. HEIKIN ASHI + EMA CLOUD
# ============================================================================

def backtest_ha_cloud(df: pd.DataFrame) -> np.ndarray:
    """Heikin Ashi + EMA Cloud."""
    df = df.copy()

    # Heikin Ashi
    df["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    df["ha_open"] = df["open"].shift(1).fillna(df["open"])
    for i in range(1, len(df)):
        df.at[i, "ha_open"] = (df.at[i-1, "ha_open"] + df.at[i-1, "ha_close"]) / 2

    df["ha_high"] = df[["high", "ha_open", "ha_close"]].max(axis=1)
    df["ha_low"] = df[["low", "ha_open", "ha_close"]].min(axis=1)

    # EMA Cloud
    df["ema3"] = df["ha_close"].ewm(span=3, adjust=False).mean()
    df["ema8"] = df["ha_close"].ewm(span=8, adjust=False).mean()
    df["ema21"] = df["ha_close"].ewm(span=21, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(21, len(df)):
        curr = df.iloc[i]

        if curr["ema3"] > curr["ema8"] > curr["ema21"] and curr["ha_close"] > curr["ema3"]:
            signal[i] = 1
        elif curr["ema3"] < curr["ema8"]:
            signal[i] = -1

    return signal


# ============================================================================
# 5. HIGH/LOW BREAKOUT
# ============================================================================

def backtest_hh_ll(df: pd.DataFrame) -> np.ndarray:
    """High/Low Breakout with EMA filter."""
    df = df.copy()
    lookback = 10

    df["hh"] = df["high"].rolling(lookback).max()
    df["ll"] = df["low"].rolling(lookback).min()
    df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(lookback, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # BUY: breakout above HH
        if curr["close"] > curr["hh"] and prev["close"] <= prev["hh"] and curr["close"] > curr["ema10"]:
            signal[i] = 1
        # SELL: breakdown below LL
        elif curr["close"] < curr["ll"] and prev["close"] >= prev["ll"]:
            signal[i] = -1

    return signal


# ============================================================================
# 6. MACD + BOLLINGER UPPER BREAKOUT
# ============================================================================

def backtest_macd_bb(df: pd.DataFrame) -> np.ndarray:
    """MACD + Bollinger Upper Breakout."""
    df = df.copy()

    # MACD
    df["macd"] = df["close"].ewm(span=12, adjust=False).mean() - df["close"].ewm(span=26, adjust=False).mean()
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Bollinger
    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # BUY: MACD bullish
        if curr["macd"] > curr["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
            signal[i] = 1
        # SELL: touch BB upper
        elif curr["high"] >= curr["bb_upper"]:
            signal[i] = -1

    return signal


# ============================================================================
# 7. OBV MULTI-TIMEFRAME
# ============================================================================

def backtest_obv_mtf(df: pd.DataFrame) -> np.ndarray:
    """OBV Multi-TF Breakout."""
    df = df.copy()

    # OBV
    df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # BUY: OBV breaks above EMA
        if curr["obv"] > curr["obv_ema"] and prev["obv"] <= prev["obv_ema"]:
            signal[i] = 1
        # SELL: OBV breaks below EMA
        elif curr["obv"] < curr["obv_ema"] and prev["obv"] >= prev["obv_ema"]:
            signal[i] = -1

    return signal


# ============================================================================
# 8. RSI + SMA200
# ============================================================================

def backtest_rsi_sma200(df: pd.DataFrame) -> np.ndarray:
    """RSI + SMA200 Mean Reversion."""
    df = df.copy()

    # RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # SMA200
    df["sma200"] = df["close"].rolling(200).mean()

    signal = np.zeros(len(df))
    for i in range(200, len(df)):
        curr = df.iloc[i]

        # BUY: RSI oversold + above SMA200
        if curr["rsi"] < 30 and curr["close"] > curr["sma200"]:
            signal[i] = 1
        # SELL: RSI overbought
        elif curr["rsi"] > 70:
            signal[i] = -1

    return signal


# ============================================================================
# 9. RVOL BREAKOUT
# ============================================================================

def backtest_rvol_breakout(df: pd.DataFrame) -> np.ndarray:
    """Relative Volume Breakout."""
    df = df.copy()

    df["atr"] = (df["high"] - df["low"]).rolling(20).mean()
    df["avg_vol"] = df["volume"].rolling(20).mean()
    df["rvol"] = df["volume"] / df["avg_vol"]

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # BUY: volume spike + price breakout
        if curr["rvol"] > 2.0 and curr["close"] > prev["close"]:
            signal[i] = 1
        elif curr["rvol"] > 2.0 and curr["close"] < prev["close"]:
            signal[i] = -1

    return signal


# ============================================================================
# 10. SQUEEZE (Bollinger/Keltner)
# ============================================================================

def backtest_squeeze(df: pd.DataFrame) -> np.ndarray:
    """Bollinger/Keltner Squeeze Release."""
    df = df.copy()

    # Bollinger
    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_width"] = (df["close"].rolling(20).std() * 2).fillna(0)

    # Keltner (simple ATR version)
    df["atr"] = (df["high"] - df["low"]).rolling(10).mean()
    df["kc_width"] = df["atr"] * 2

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]

        # Squeeze: BB width < KC width
        is_squeeze = curr["bb_width"] < curr["kc_width"]

        # BUY: squeeze release upward
        if is_squeeze and curr["close"] > curr["bb_mid"]:
            signal[i] = 1
        elif is_squeeze and curr["close"] < curr["bb_mid"]:
            signal[i] = -1

    return signal


# ============================================================================
# 11. STOP RUN REVERSAL
# ============================================================================

def backtest_stop_run(df: pd.DataFrame) -> np.ndarray:
    """Stop Run Reversal (Volume Spike)."""
    df = df.copy()

    df["avg_vol"] = df["volume"].rolling(20).mean()
    df["vol_spike"] = df["volume"] / df["avg_vol"]

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # Stop run: big volume spike + reversal
        if curr["vol_spike"] > 3.0:
            if curr["close"] < prev["close"]:
                signal[i] = 1  # bouncer buy
            else:
                signal[i] = -1  # reversal sell

    return signal


# ============================================================================
# 12. SUPERTREND
# ============================================================================

def backtest_supertrend(df: pd.DataFrame) -> np.ndarray:
    """Supertrend (ATR trailing bands)."""
    df = df.copy()

    # ATR
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=6, min_periods=1).mean()

    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = hl2 + 0.60 * atr
    lower_basic = hl2 - 0.60 * atr

    signal = np.zeros(len(df))
    st_direction = 0

    for i in range(1, len(df)):
        if i == 1:
            if df["close"].iloc[i] > hl2.iloc[i]:
                st_direction = 1
            else:
                st_direction = -1
        else:
            if st_direction == 1 and df["close"].iloc[i] < lower_basic.iloc[i]:
                signal[i] = -1
                st_direction = -1
            elif st_direction == -1 and df["close"].iloc[i] > upper_basic.iloc[i]:
                signal[i] = 1
                st_direction = 1

    return signal


# ============================================================================
# 13. TSV (Time-Segmented Volume)
# ============================================================================

def backtest_tsv(df: pd.DataFrame) -> np.ndarray:
    """Time-Segmented Volume Cross."""
    df = df.copy()

    # TSV = OBV smoothed
    df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["tsv"] = df["obv"].ewm(span=13, adjust=False).mean()
    df["tsv_signal"] = df["tsv"].ewm(span=7, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(13, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        if curr["tsv"] > curr["tsv_signal"] and prev["tsv"] <= prev["tsv_signal"]:
            signal[i] = 1
        elif curr["tsv"] < curr["tsv_signal"] and prev["tsv"] >= prev["tsv_signal"]:
            signal[i] = -1

    return signal


# ============================================================================
# 14. VELA PIVOT
# ============================================================================

def backtest_vela_pivot(df: pd.DataFrame) -> np.ndarray:
    """Vela Pivot (Volume × Range Energy)."""
    df = df.copy()

    df["range"] = df["high"] - df["low"]
    df["vela"] = df["range"] * df["volume"]
    df["vela_avg"] = df["vela"].rolling(20).mean()

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]

        # BUY: High volume + wide range
        if curr["vela"] > curr["vela_avg"] * 2 and curr["close"] > curr["open"]:
            signal[i] = 1
        elif curr["vela"] > curr["vela_avg"] * 2 and curr["close"] < curr["open"]:
            signal[i] = -1

    return signal


# ============================================================================
# 15. VOLUME REVERSAL BAR
# ============================================================================

def backtest_volume_reversal_bar(df: pd.DataFrame) -> np.ndarray:
    """Volume Reversal Bar."""
    df = df.copy()

    df["avg_vol"] = df["volume"].rolling(20).mean()

    signal = np.zeros(len(df))
    for i in range(2, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        prev2 = df.iloc[i - 2]

        # Reversal: closes opposite to prior candle, high volume
        if (curr["close"] > curr["open"] and
            prev["close"] < prev["open"] and
            curr["volume"] > df["avg_vol"].iloc[i] * 1.5):
            signal[i] = 1
        elif (curr["close"] < curr["open"] and
              prev["close"] > prev["open"] and
              curr["volume"] > df["avg_vol"].iloc[i] * 1.5):
            signal[i] = -1

    return signal


# ============================================================================
# 16. VOLUME-WEIGHTED MACD
# ============================================================================

def backtest_vw_macd(df: pd.DataFrame) -> np.ndarray:
    """Volume-Weighted MACD."""
    df = df.copy()

    # Volume-weighted price
    vwp = (df["close"] * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()

    # MACD on volume-weighted price
    macd = vwp.ewm(span=12, adjust=False).mean() - vwp.ewm(span=26, adjust=False).mean()
    signal_line = macd.ewm(span=9, adjust=False).mean()

    signal = np.zeros(len(df))
    for i in range(26, len(df)):
        curr_macd = macd.iloc[i]
        prev_macd = macd.iloc[i - 1]
        curr_sig = signal_line.iloc[i]
        prev_sig = signal_line.iloc[i - 1]

        if curr_macd > curr_sig and prev_macd <= prev_sig:
            signal[i] = 1
        elif curr_macd < curr_sig and prev_macd >= prev_sig:
            signal[i] = -1

    return signal


# ============================================================================
# 17. VWAP + RSI
# ============================================================================

def backtest_vwap_rsi(df: pd.DataFrame) -> np.ndarray:
    """VWAP + RSI."""
    df = df.copy()

    # VWAP (cumulative volume-weighted average price)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumsum_vol = df["volume"].cumsum()
    df["vwap"] = (typical_price * df["volume"]).cumsum() / cumsum_vol

    # RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    signal = np.zeros(len(df))
    for i in range(14, len(df)):
        curr = df.iloc[i]

        # BUY: price above VWAP + RSI < 50
        if curr["close"] > curr["vwap"] and curr["rsi"] < 50:
            signal[i] = 1
        # SELL: price below VWAP + RSI > 50
        elif curr["close"] < curr["vwap"] and curr["rsi"] > 50:
            signal[i] = -1

    return signal


# ============================================================================
# 18. VWAP Z-SCORE
# ============================================================================

def backtest_vwap_zscore(df: pd.DataFrame) -> np.ndarray:
    """VWAP Z-Score."""
    df = df.copy()

    # VWAP
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumsum_vol = df["volume"].cumsum()
    df["vwap"] = (typical_price * df["volume"]).cumsum() / cumsum_vol

    # Z-Score
    df["vwap_std"] = df["vwap"].rolling(20).std()
    df["vwap_mean"] = df["vwap"].rolling(20).mean()
    df["zscore"] = (df["vwap"] - df["vwap_mean"]) / (df["vwap_std"] + 1e-10)

    signal = np.zeros(len(df))
    for i in range(20, len(df)):
        curr = df.iloc[i]

        # BUY: Z-score < -2 (oversold)
        if curr["zscore"] < -2.0:
            signal[i] = 1
        # SELL: Z-score > 2 (overbought)
        elif curr["zscore"] > 2.0:
            signal[i] = -1

    return signal


# ============================================================================
# STRATEGY REGISTRY
# ============================================================================

BACKTEST_STRATEGIES = {
    "bollinger": backtest_bollinger,
    "ema_tsi": backtest_ema_tsi,
    "gap": backtest_gap,
    "ha_cloud": backtest_ha_cloud,
    "hh_ll": backtest_hh_ll,
    "macd_bb": backtest_macd_bb,
    "obv_mtf": backtest_obv_mtf,
    "rsi_sma200": backtest_rsi_sma200,
    "rvol_breakout": backtest_rvol_breakout,
    "squeeze": backtest_squeeze,
    "stop_run": backtest_stop_run,
    "supertrend": backtest_supertrend,
    "tsv": backtest_tsv,
    "vela_pivot": backtest_vela_pivot,
    "volume_reversal_bar": backtest_volume_reversal_bar,
    "vw_macd": backtest_vw_macd,
    "vwap_rsi": backtest_vwap_rsi,
    "vwap_zscore": backtest_vwap_zscore,
}


def get_strategy_signal(strategy_name: str, df: pd.DataFrame) -> np.ndarray:
    """Get signal array for a strategy."""
    if strategy_name not in BACKTEST_STRATEGIES:
        logger.error(f"Strategy {strategy_name} not found")
        return np.zeros(len(df))

    try:
        return BACKTEST_STRATEGIES[strategy_name](df)
    except Exception as e:
        logger.error(f"Error in strategy {strategy_name}: {e}")
        return np.zeros(len(df))
