"""Diagnóstico: cuántas señales raw vs con filtros dieron EMA_3_8 y MACD_ACCEL hoy.

Uso:
    cd ~/PycharmProjects/eolo
    source Bot/venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
    python3 diag_ema_macd.py
"""
import sys
import os

# Asegurar que importa eolo_common + Bot desde el repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eolo_common.strategies_v3.strategies import (
    StrategyConfig,
    strategy_ema_crossover,
    strategy_macd_accel,
)

try:
    from Bot.bot_marketdata import MarketData
except ImportError:
    # algunos repos exponen MarketData en otro path
    from bot_marketdata import MarketData  # type: ignore

TICKERS = ("AAPL", "NVDA", "NVDL", "SOXL", "SPY", "TSLA", "TQQQ", "TSLL", "QQQ")

# Config completamente relajada para ver la señal raw
NO_FILTERS = StrategyConfig(
    use_regime_filter=False,
    use_ema_trend_filter=False,
    use_tod_filter=False,
)

md = MarketData()

print(f"{'TICKER':8s} {'EMA_3_8 w/filt':16s} {'EMA_3_8 no-filt':16s} {'MACD w/filt':16s} {'MACD no-filt':16s}")
print("-" * 80)

for ticker in TICKERS:
    df = md.get_price_history(ticker, candles=0, days=1)
    if df is None or df.empty:
        print(f"{ticker:8s} NO DATA")
        continue
    if "datetime" in df.columns:
        df = df.set_index("datetime").sort_index()

    # EMA_3_8
    ema_with = strategy_ema_crossover(df, fast=3, slow=8)
    ema_none = strategy_ema_crossover(df, cfg=NO_FILTERS, fast=3, slow=8)

    # MACD_ACCEL
    mac_with = strategy_macd_accel(df)
    mac_none = strategy_macd_accel(df, cfg=NO_FILTERS)

    print(
        f"{ticker:8s} "
        f"{ema_with['signal']:16s} "
        f"{ema_none['signal']:16s} "
        f"{mac_with['signal']:16s} "
        f"{mac_none['signal']:16s}"
    )

print()
print("Interpretación:")
print("  - Columnas 'w/filt'  = señal final que produce el bot (con filtros de régimen).")
print("  - Columnas 'no-filt' = señal raw (solo indicador puro, sin régimen/trend/ToD).")
print("  - Si 'no-filt' = BUY/SELL y 'w/filt' = HOLD → los filtros están bloqueando.")
print("  - Si ambos son HOLD → el indicador no disparó en la última vela.")
