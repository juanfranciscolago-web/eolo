"""Diagnóstico local: matriz de las 11 estrategias × tickers, raw vs con filtros.

Sirve para responder en cada vela:
  - ¿Qué estrategias dispararon señal raw (indicador puro)?
  - ¿Cuáles sobrevivieron a los filtros (regime / trend / ToD)?
  - ¿Dónde están bloqueando los filtros (raw=BUY pero final=HOLD)?

Uso:
    pip3 install --user --break-system-packages numpy pandas yfinance
    python3 diag_all_strategies_yf.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

from eolo_common.strategies_v3 import (  # noqa: E402
    STRATEGY_REGISTRY_V3,
    StrategyConfig,
    EQUITY_ONLY,
)

# ── Config ───────────────────────────────────────────────────
TICKERS = ("AAPL", "NVDA", "NVDL", "SOXL", "SPY", "TSLA", "TQQQ", "TSLL", "QQQ")
INTERVAL = "5m"
PERIOD   = "2d"

NO_FILTERS = StrategyConfig(
    use_regime_filter=False,
    use_ema_trend_filter=False,
    use_tod_filter=False,
)

STRATEGY_NAMES = list(STRATEGY_REGISTRY_V3.keys())  # 11

# ── Helpers ──────────────────────────────────────────────────
def _normalize_yf(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df.index.name = "datetime"
    return df


def _safe_call(fn, df, cfg):
    try:
        res = fn(df, cfg=cfg) if cfg is not None else fn(df)
        return str(res.get("signal", "ERR"))
    except Exception:
        return "ERR"


# ── Pull data ────────────────────────────────────────────────
print(f"Descargando {len(TICKERS)} tickers @ {INTERVAL} / {PERIOD} desde yfinance...")
data = {}
for t in TICKERS:
    try:
        df = yf.download(t, period=PERIOD, interval=INTERVAL,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            print(f"  {t}: NO DATA")
            continue
        data[t] = _normalize_yf(df)
    except Exception as e:
        print(f"  {t}: ERROR {e}")

print(f"OK: {len(data)} tickers con data.\n")

# ── Build matrices: raw + final ──────────────────────────────
def _build_matrix(cfg) -> pd.DataFrame:
    rows = {}
    for ticker, df in data.items():
        rows[ticker] = {
            name: _safe_call(STRATEGY_REGISTRY_V3[name], df, cfg)
            for name in STRATEGY_NAMES
        }
    return pd.DataFrame(rows).T[STRATEGY_NAMES]   # tickers × strategies


m_final = _build_matrix(cfg=None)        # default cfg = todos los filtros on
m_raw   = _build_matrix(cfg=NO_FILTERS)  # solo indicador puro

# ── Print: matrix raw ────────────────────────────────────────
print("=" * 100)
print("RAW (indicador puro, sin filtros)")
print("=" * 100)
print(m_raw.to_string())
print()

# ── Print: matrix final ──────────────────────────────────────
print("=" * 100)
print("FINAL (con regime + trend + ToD)")
print("=" * 100)
print(m_final.to_string())
print()

# ── Resumen por estrategia ───────────────────────────────────
print("=" * 100)
print("RESUMEN POR ESTRATEGIA")
print("=" * 100)
print(f"{'Strategy':18s} {'Raw fired':25s} {'Final fired':25s} {'Filter blocked':25s}")
print("-" * 100)

for name in STRATEGY_NAMES:
    raw_hits   = [t for t in m_raw.index   if m_raw.loc[t, name]   in ("BUY", "SELL")]
    final_hits = [t for t in m_final.index if m_final.loc[t, name] in ("BUY", "SELL")]
    blocked    = [t for t in raw_hits if t not in final_hits]

    def _fmt(lst):
        if not lst:
            return "-"
        if len(lst) > 3:
            return f"{len(lst)} ({','.join(lst[:3])}…)"
        return f"{len(lst)} ({','.join(lst)})"

    print(f"{name:18s} {_fmt(raw_hits):25s} {_fmt(final_hits):25s} {_fmt(blocked):25s}")

print()
print("Leyenda:")
print("  - Raw fired       = indicador puro disparó BUY/SELL (sin filtros)")
print("  - Final fired     = señal final que el bot ejecutaría")
print("  - Filter blocked  = raw=BUY/SELL pero filtros la bloquearon (oportunidad perdida)")
print()
print("Interpretación rápida:")
print("  • Final fired alto + Filter blocked bajo → estrategia saludable hoy")
print("  • Raw fired alto + Final fired bajo     → filtros muy restrictivos para esta")
print("  • Raw fired = 0 + Final fired = 0       → indicador no disparó (no hay setup)")
print()
print(f"NOTA: data de yfinance @ {INTERVAL}. ORB_V3 puede degradar fuera de la primera")
print("media hora. BULLS_BSP/NET_BSV degradan a breadth=True si no hay feed breadth.")
