# ============================================================
#  EOLO Strategy Router — Asset/Timeframe Targeting
#
#  Maps strategies to optimal assets/timeframes from FASE 6
#  backtesting. Routes each strategy call to correct combo.
# ============================================================

# ── TIER 2 OPTIMIZED MAPPING (from FASE 6 backtest results) ──
TIER2_STRATEGY_MAP = {
    # Strategy → {Asset → [TF1, TF2, ...]}
    "stop_run": {
        "JPM": [30],
        "MSFT": [60, 240],
        "TSLA": [60, 240],
        "XOM": [30],
        "SPY": [30],
    },
    "vwap_zscore": {
        "JPM": [30],
        "AMZN": [30],
    },
    "volume_reversal_bar": {
        "TSLA": [30],
        "JPM": [30],
        "AMZN": [30],
        "NVDA": [30],
    },
    "supertrend": {
        "QQQ": [30],
        "UNH": [30],
    },
    "macd_bb": {
        "SPY": [30],
        "QQQ": [30],
        "JPM": [30],
        "XOM": [30],
        "UNH": [30],
    },
    "bollinger": {
        "MSFT": [30],
        "JPM": [30],
        "QQQ": [30],
        "UNH": [30],
    },
}

# ── TIER 1 & Baseline (run on all, or specific tickers) ──
# Tier 1: Keep running on all tickers — they're winners
TIER1_STRATEGIES = {
    "volume_reversal_bar",
    "stop_run",
    "vw_macd",
    "rvol_breakout",
    "tsv",
}

# TIER 1 with Timeframe Restrictions (FASE 6 optimization)
TIER1_TF_RESTRICTIONS = {
    "gap_fade": {
        # Gap only works on 1h+ timeframes; disable on 30m
        "min_tf": 60,  # Minimum 60 minutes (1h)
    },
}

# "Suite strategies" (v3 combos) → run on all
SUITE_STRATEGIES = {
    "ema_3_8",
    "ema_8_21",
    "macd_accel",
    "volume_breakout",
    "buy_pressure",
    "sell_pressure",
    "vwap_momentum",
    "orb_v3",
    "donchian_turtle",
    "bulls_bsp",
    "net_bsv",
    "combo1_ema_scalper",
    "combo2_rubber_band",
    "combo3_nino_squeeze",
    "combo4_slimribbon",
    "combo5_btd",
    "combo6_fractalccix",
    "combo7_campbell",
}


def should_run_strategy(strategy_name: str, ticker: str, candle_frequency_min: int) -> bool:
    """
    Decide if a strategy should run for (ticker, timeframe) combo.

    Args:
        strategy_name: e.g., "stop_run"
        ticker: e.g., "AMZN"
        candle_frequency_min: e.g., 30 (for 30m), 60 (for 1h), 240 (for 4h)

    Returns:
        True if strategy should execute; False if should skip (return HOLD)
    """

    # Check Tier 1 TF restrictions (e.g., gap only on 1h+)
    if strategy_name in TIER1_TF_RESTRICTIONS:
        restrictions = TIER1_TF_RESTRICTIONS[strategy_name]
        if "min_tf" in restrictions:
            if candle_frequency_min < restrictions["min_tf"]:
                return False  # Below minimum TF, skip

    # Tier 1 strategies run everywhere (unless restricted above)
    if strategy_name in TIER1_STRATEGIES:
        return True

    # Suite strategies run everywhere
    if strategy_name in SUITE_STRATEGIES:
        return True

    # Tier 2: Check specific asset/timeframe combo
    if strategy_name in TIER2_STRATEGY_MAP:
        asset_map = TIER2_STRATEGY_MAP[strategy_name]
        if ticker in asset_map:
            allowed_tfs = asset_map[ticker]
            return candle_frequency_min in allowed_tfs
        # If ticker not in map, don't run
        return False

    # Other strategies (baseline, etc.) — run everywhere
    return True


# ── Usage in bot_main.py ──
#
# 1. After getting a signal from a strategy, check filtering:
#
#    signal = stop_run_strategy.analyze(market_data, "JPM")
#    if not should_run_strategy("stop_run", "JPM", CANDLE_MINUTES):
#        signal = "HOLD"  # Skip this strategy for this combo
#
# 2. Or, before calling the strategy:
#
#    if should_run_strategy("macd_bb", ticker, CANDLE_MINUTES):
#        signal = macd_bb_strategy.analyze(market_data, ticker)
#    else:
#        signal = "HOLD"
#
