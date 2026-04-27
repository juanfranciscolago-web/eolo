"""Diagnóstico local: cuántas señales raw vs con filtros dieron EMA_3_8 y MACD_ACCEL
usando data histórica de yfinance (pública, sin credenciales Schwab).

Uso:
    pip3 install --user numpy pandas yfinance
    python3 diag_ema_macd_yf.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

from eolo_common.strategies_v3.strategies import (  # noqa: E402
    StrategyConfig,
    strategy_ema_crossover,
    strategy_macd_accel,
)

TICKERS = ("AAPL", "NVDA", "NVDL", "SOXL", "SPY", "TSLA", "TQQQ", "TSLL", "QQQ")
INTERVAL = "5m"   # 5 minutos — consistente con la frecuencia típica del bot v1
PERIOD = "2d"     # últimas 2 sesiones

NO_FILTERS = StrategyConfig(
    use_regime_filter=False,
    use_ema_trend_filter=False,
    use_tod_filter=False,
)

print(f"{'TICKER':8s} {'bars':>5s}  "
      f"{'EMA_3_8 w/filt':16s} {'EMA_3_8 no-filt':16s} "
      f"{'MACD w/filt':16s} {'MACD no-filt':16s}")
print("-" * 88)

for ticker in TICKERS:
    try:
        df = yf.download(
            ticker, period=PERIOD, interval=INTERVAL,
            progress=False, auto_adjust=False,
        )
    except Exception as e:
        print(f"{ticker:8s} ERROR: {e}")
        continue

    if df is None or df.empty:
        print(f"{ticker:8s} NO DATA")
        continue

    # yfinance devuelve columnas en Title Case + a veces multi-index en ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df.index.name = "datetime"

    # EMA_3_8
    ema_with = strategy_ema_crossover(df, fast=3, slow=8)
    ema_none = strategy_ema_crossover(df, cfg=NO_FILTERS, fast=3, slow=8)

    # MACD_ACCEL
    mac_with = strategy_macd_accel(df)
    mac_none = strategy_macd_accel(df, cfg=NO_FILTERS)

    print(
        f"{ticker:8s} {len(df):>5d}  "
        f"{ema_with['signal']:16s} {ema_none['signal']:16s} "
        f"{mac_with['signal']:16s} {mac_none['signal']:16s}"
    )

print()
print("Interpretación:")
print("  - w/filt  = señal final con todos los filtros de régimen / trend / ToD.")
print("  - no-filt = señal raw (solo indicador puro).")
print("  - w/filt=HOLD + no-filt=BUY/SELL  → filtros bloquean señal viable.")
print("  - Ambos HOLD                      → el indicador no disparó en la última vela.")
print()
print("NOTA: data de yfinance, no de Schwab. Para tickers con schema diferente")
print("(NVDL/SOXL son leveraged ETFs) los precios son ~idénticos, pero el volumen")
print("puede diferir respecto a lo que ve Schwab intraday.")
