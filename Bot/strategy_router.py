# ============================================================
#  EOLO Strategy Router — Asset/Timeframe Targeting
#
#  Maps strategies to optimal assets/timeframes from FASE 6
#  backtesting. Routes each strategy call to correct combo.
# ============================================================

# ── Universo real de Eolo V1 (Bot/bot_trader.py) ────────────
# Las calibraciones FASE 6 originales apuntaban a JPM/MSFT/UNH/AMZN/XOM, que
# NO están en el universo de V1 → varias Tier 2 nunca disparaban. Para el
# re-test (RETEST_V1_2026H1) ampliamos el routing a los tickers que V1 opera.
# active_timeframes en prod = [5,15,30,60]: usamos 30 y 60 (ambos activos).
_V1_INDEX = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]       # tickers_ema_gap
_V1_LEV   = ["SOXL", "TSLL", "NVDL", "TQQQ"]             # tickers_leveraged
_V1_ALL   = _V1_INDEX + _V1_LEV


def _tf(tickers, tfs):
    """Helper: {ticker: tfs} para cada ticker de la lista."""
    return {t: list(tfs) for t in tickers}


# ── TIER 2 OPTIMIZED MAPPING (FASE 6 + ampliación re-test 2026-06-06) ──
# TF estándar 30m (modal de la calibración FASE 6). stop_run además 60m
# (tenía 60/240 original; 240 no está en active_timeframes, se omite).
# Las entradas originales fuera del universo V1 se conservan (inocuas: nunca
# se iteran) por si esos tickers se reincorporan al universo.
# H3 (audit 06-jun): stop_run y volume_reversal_bar están en TIER1_STRATEGIES
# (return True antes de mirar el mapa) → sus entradas Tier 2 eran inalcanzables
# y confundían. Se quitaron del mapa; corren en todos los TF vía Tier 1.
# H4 (audit 06-jun): bollinger corre por su bloque clásico SIN gate (sobre
# leveraged), así que su entrada Tier 2 nunca se consultaba — eliminada.
#
# Solo quedan en el mapa las que realmente se gatean en run_cycle y NO están
# en Tier 1: vwap_zscore, supertrend, macd_bb. Ampliadas al universo V1 @30m.
TIER2_STRATEGY_MAP = {
    # Strategy → {Asset → [TF1, TF2, ...]}
    # vwap_zscore itera tickers_all (9 V1):
    "vwap_zscore": _tf(_V1_ALL, [30]),
    # H7 (audit 06-jun): supertrend y macd_bb declaran en su header
    # "Tickers recomendados: SOXL, TSLL, NVDL, TQQQ" (leveraged) y su bloque
    # itera tickers_leveraged. Las entradas índice (QQQ/UNH/SPY/JPM/XOM) eran
    # restos de un backtest FASE 6 sobre otro universo → eliminadas.
    "supertrend": _tf(_V1_LEV, [30]),
    "macd_bb":    _tf(_V1_LEV, [30]),
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
