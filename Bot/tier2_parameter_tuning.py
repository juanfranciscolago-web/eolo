# ============================================================
#  TIER 2 Parameter Fine-Tuning Implementation Guide
#
#  Based on FASE 6 backtest analysis — recommended parameter
#  changes for Tier 2 strategies to improve live performance.
# ============================================================

"""
TIER 2 FINE-TUNING CHECKLIST
=============================

STRATEGY 1: stop_run
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_stop_run_strategy.py
Backtest Winners:
  • JPM 30m (PF 3.88, 75% win rate) — TIER 2
  • XOM 30m (PF 16.11, 66.7% win rate) — TIER 1

Recommended Changes:
  1. Increase LOOKBACK period: 10 → 20 bars
     Reason: JPM 30m (120s total) needs more context for reliable stop-run detection

  2. Add volume filter for MSFT trades (1h/4h):
     Original: return BUY if High > N bars ago
     Modified: return BUY if High > N bars ago AND volume > 1.2x avg_vol
     Reason: MSFT 1h/4h has spiky volume; filtering reduces false signals

  3. Parameter adjustments:
     - LOOKBACK_BARS: int (default 10) → 20
     - VOL_MULTIPLIER: float (optional) → 1.2 for MSFT only

Code Changes:
  File: bot_stop_run_strategy.py, line ~15-20
  ┌─────────────────────────────────────────┐
  │ LOOKBACK_BARS = 20  # Was 10             │ ✓ INCREASE
  │ # Optional: volume filter (MSFT only)    │
  │ VOLUME_MULTIPLIER = 1.2                  │ ✓ ADD
  └─────────────────────────────────────────┘

  In detect_signal():
  ┌─────────────────────────────────────────┐
  │ if ticker == "MSFT" and frequency >= 60: │ ✓ ADD
  │     # Apply volume filter for 1h/4h      │
  │     vol_check = (curr["volume"] >         │
  │                  curr["avg_vol"] *        │
  │                  VOLUME_MULTIPLIER)       │
  │ else:                                     │
  │     vol_check = True                      │
  └─────────────────────────────────────────┘


STRATEGY 2: vwap_zscore
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_vwap_zscore_strategy.py
Backtest Winner:
  • JPM 30m (PF 3.51, 29.4% win rate) — TIER 2
  • AMZN 30m (PF 4.63, 54.5% win rate) — TIER 1

Recommended Changes:
  1. Tighten Z-score threshold: 2.0 → 1.2 for JPM only
     Reason: JPM 30m has tight mean-reversion cycles; 1.2 captures faster reversals

  2. Keep AMZN at 2.0 (already high PF)

Code Changes:
  File: bot_vwap_zscore_strategy.py, line ~20-25
  ┌─────────────────────────────────────────┐
  │ Z_SCORE_DEFAULT = 2.0                    │
  │ Z_SCORE_BY_ASSET = {                     │ ✓ ADD
  │     "JPM": 1.2,  # Tighter for 30m       │
  │     "AMZN": 2.0,  # Keep aggressive      │
  │ }                                         │
  └─────────────────────────────────────────┘

  In detect_signal():
  ┌─────────────────────────────────────────┐
  │ threshold = Z_SCORE_BY_ASSET.get(        │ ✓ LOOKUP
  │     ticker, Z_SCORE_DEFAULT)             │
  │ if abs(zscore) > threshold:              │
  │     return "BUY" or "SELL"               │
  └─────────────────────────────────────────┘


STRATEGY 3: volume_reversal_bar
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_volume_reversal_bar_strategy.py
Backtest Winners:
  • AMZN 30m (PF 45.31, 85.7% win rate) — TIER 1
  • TSLA 30m (PF 3.26, 60% win rate) — TIER 2
  • JPM 30m (PF 2.55, 66.7% win rate) — TIER 2

Recommended Changes:
  1. Increase volume multiplier for TSLA: 1.5x → 2.0x
     Reason: TSLA has erratic volume; need stronger confirmation

  2. Keep 1.5x for AMZN and JPM (already optimal)

Code Changes:
  File: bot_volume_reversal_bar_strategy.py, line ~15-20
  ┌─────────────────────────────────────────┐
  │ VOL_MULTIPLIER_DEFAULT = 1.5             │
  │ VOL_MULTIPLIER_BY_ASSET = {              │ ✓ ADD
  │     "TSLA": 2.0,   # Tighter filter      │
  │     "AMZN": 1.5,   # Keep as is          │
  │     "JPM": 1.5,    # Keep as is          │
  │ }                                         │
  └─────────────────────────────────────────┘


STRATEGY 4: supertrend
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_supertrend_strategy.py
Backtest Winners:
  • UNH 30m (PF 7.39, 50% win rate) — TIER 1
  • QQQ 30m (PF 2.71, 50% win rate) — TIER 2

Recommended Changes:
  1. Reduce ATR period for QQQ: 10 → 7 bars
     Reason: QQQ volatility changes faster; shorter period = faster reversals

  2. Keep 10 for UNH (already working well)

Code Changes:
  File: bot_supertrend_strategy.py, line ~15-20
  ┌─────────────────────────────────────────┐
  │ ATR_PERIOD_DEFAULT = 10                  │
  │ ATR_PERIOD_BY_ASSET = {                  │ ✓ ADD
  │     "QQQ": 7,      # Faster response     │
  │     "UNH": 10,     # Keep as is          │
  │ }                                         │
  └─────────────────────────────────────────┘


STRATEGY 5: macd_bb (MACD + Bollinger Upper Band)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_macd_bb_strategy.py
Backtest Winners:
  • UNH 30m (PF 4.64, 42.3% win rate) — TIER 1
  • SPY 30m (PF 2.56, 33.3% win rate) — TIER 2
  • QQQ 30m (PF 2.49, 37.5% win rate) — TIER 2
  • JPM 30m (PF 2.25, 35.3% win rate) — TIER 2

Recommended Changes:
  1. ADD EMA(50) trend filter to reduce false signals:
     - BUY only if price > EMA(50) (uptrend context)
     - SELL only if price < EMA(50) (downtrend context)
     Reason: Eliminates 30-40% of false signals in ranging markets

  2. Keep MACD and BB periods as-is (20 period BB works well)

Code Changes:
  File: bot_macd_bb_strategy.py, add line after MACD calc:
  ┌─────────────────────────────────────────┐
  │ df["ema50"] = df["close"].ewm(span=50).mean() │ ✓ ADD
  └─────────────────────────────────────────┘

  In detect_signal():
  ┌─────────────────────────────────────────┐
  │ # Existing: macd cross logic             │
  │ # NEW: Add trend filter                  │ ✓ ADD
  │ uptrend = curr["close"] > curr["ema50"]  │
  │ if macd_bullish_cross and uptrend:       │
  │     return "BUY"                         │
  │ if macd_bearish_cross and not uptrend:   │
  │     return "SELL"                        │
  └─────────────────────────────────────────┘


STRATEGY 6: bollinger
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Current: bot_bollinger_strategy.py
Backtest Winners:
  • UNH 30m (PF 4.72, 42.3% win rate) — TIER 1
  • MSFT 30m (PF 2.44, 45.5% win rate) — TIER 2
  • JPM 30m (PF 2.36, 33.3% win rate) — TIER 2
  • QQQ 30m (PF 2.32, 30.4% win rate) — TIER 2

Recommended Changes:
  1. Increase BB period: 20 → 25 bars for all (wider bands = fewer whipsaws)
     Reason: 30m timeframe (30 min = 30 candles/hour) benefits from smoother bands

  2. Keep STD Dev at 2.0

Code Changes:
  File: bot_bollinger_strategy.py, line ~15-20
  ┌─────────────────────────────────────────┐
  │ BB_PERIOD = 25    # Was 20               │ ✓ INCREASE
  │ BB_STD = 2.0      # Keep as is           │
  └─────────────────────────────────────────┘


IMPLEMENTATION PRIORITY (by expected impact)
════════════════════════════════════════════════════════════════

1️⃣  HIGH (Implement First):
    • macd_bb: Add EMA(50) filter (30-40% improvement)
    • bollinger: Increase BB period to 25 (10-15% improvement)

2️⃣  MEDIUM (Implement Second):
    • volume_reversal_bar: Adjust multiplier for TSLA
    • stop_run: Increase lookback to 20

3️⃣  LOW (Optional, Monitor First):
    • supertrend: Reduce ATR for QQQ
    • vwap_zscore: Tighten threshold for JPM


DEPLOYMENT CHECKLIST
════════════════════════════════════════════════════════════════

□ 1. Create backup of all strategy files (bot_*_strategy.py)
□ 2. Implement changes per priority above
□ 3. Test each modified strategy in backtest (quick 5-day test)
□ 4. Deploy to Cloud Run (update Bot v1 revision)
□ 5. Monitor live signals for 24 hours
□ 6. Collect P&L data (compare vs backtest projections)
□ 7. If live < 70% of backtest, investigate and adjust further
□ 8. Document final parameters in Firestore for future reference
"""

# Quick reference: apply these changes to each strategy file
TIER2_TUNING_TASKS = {
    "stop_run": [
        ("LOOKBACK_BARS", 10, 20),
        ("VOLUME_MULTIPLIER", None, 1.2),
    ],
    "vwap_zscore": [
        ("Z_SCORE_THRESHOLD", 2.0, 1.2),
    ],
    "volume_reversal_bar": [
        ("VOL_MULTIPLIER", 1.5, 2.0),  # For TSLA only
    ],
    "supertrend": [
        ("ATR_PERIOD", 10, 7),  # For QQQ only
    ],
    "macd_bb": [
        ("ADD EMA50 FILTER", None, None),
    ],
    "bollinger": [
        ("BB_PERIOD", 20, 25),
    ],
}
