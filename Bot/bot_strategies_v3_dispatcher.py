# ============================================================
#  EOLO V1 — Adapter para el suite "EMA 3/8 y MACD" (v3)
#
#  Wrappers delgados que convierten el contrato de V1
#      analyze(market_data, ticker) → dict con campos Eolo
#  sobre la lógica pura de eolo_common.strategies_v3.
#
#  Las 11 estrategias expuestas:
#      EMA_3_8, EMA_8_21, MACD_ACCEL, VOLUME_BREAKOUT,
#      BUY_PRESSURE, SELL_PRESSURE, VWAP_MOMENTUM, ORB_V3,
#      DONCHIAN_TURTLE, BULLS_BSP, NET_BSV
#
#  Notas sobre V1 (long-only, equity paper-trade):
#    • SELL → se pasa igual al trader; Eolo V1 lo interpreta como
#      "cerrar long si existe" (el trader ignora SELL si no hay
#      posición abierta en ese ticker).
#    • Todas corren sobre el DF que devuelve MarketData a la
#      frecuencia activa de bot_main (1/5/15/30/60/240).
#    • BULLS_BSP y NET_BSV degradan a breadth=True si el DF no
#      trae columnas the_bulls/the_bears — wiring del breadth
#      feed queda pendiente (market_breadth.py).
# ============================================================
from __future__ import annotations

import os
import sys
import pandas as pd
from loguru import logger

# eolo_common (compartido entre V1/V2/crypto)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.strategies_v3 import STRATEGY_REGISTRY_V3  # noqa: E402


# ── Helpers compartidos ──────────────────────────────────────

def _prep_df(market_data, ticker: str) -> pd.DataFrame | None:
    """
    Pide a Schwab vía MarketData. days=1 es suficiente con el auto-scale
    de marketdata.py (15m→3d, 30m→5d, 5m→2d, 60/240→10d nativo).
    Para 1m en sesión normal queda ~1 día (~390 velas) — ok para casi
    todas salvo Donchian largo, pero las estrategias ya devuelven HOLD
    con "insufficient bars" cuando no alcanza.
    """
    df = market_data.get_price_history(ticker, candles=0, days=1)
    if df is None or df.empty:
        return None
    if "datetime" in df.columns:
        df = df.set_index("datetime").sort_index()
    return df


def _result(ticker: str, strat_name: str, res: dict, df: pd.DataFrame) -> dict:
    try:
        last = df.iloc[-1]
        price = float(last["close"])
        ct = str(df.index[-1])
    except Exception:
        price, ct = None, None
    return {
        "ticker":      ticker,
        "signal":      res.get("signal", "HOLD"),
        "strategy":    strat_name,
        "price":       round(price, 4) if price else None,
        "reason":      res.get("reason", ""),
        "candle_time": ct,
    }


def _dispatch(strategy_key: str, market_data, ticker: str) -> dict:
    fn = STRATEGY_REGISTRY_V3.get(strategy_key)
    if fn is None:
        return {"ticker": ticker, "signal": "ERROR", "strategy": strategy_key,
                "price": None, "reason": f"unknown strategy {strategy_key}"}

    df = _prep_df(market_data, ticker)
    if df is None:
        logger.error(f"[V3/{strategy_key}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": strategy_key,
                "price": None, "reason": "no data"}

    try:
        res = fn(df)
    except Exception as e:
        logger.warning(f"[V3/{strategy_key}] {ticker} — exception: {e}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": strategy_key,
                "price": None, "reason": f"exception: {e}"}

    out = _result(ticker, strategy_key, res, df)
    sig = out["signal"]
    if sig in ("BUY", "SELL"):
        logger.info(
            f"[V3/{strategy_key}] {ticker} {sig} ✅ — "
            f"close={out['price']} | {out['reason']}"
        )
    return out


# ── Entry points por estrategia (uno por key del registry) ──

def analyze_ema_3_8(market_data, ticker):
    return _dispatch("EMA_3_8", market_data, ticker)


def analyze_ema_8_21(market_data, ticker):
    return _dispatch("EMA_8_21", market_data, ticker)


def analyze_macd_accel(market_data, ticker):
    return _dispatch("MACD_ACCEL", market_data, ticker)


def analyze_volume_breakout(market_data, ticker):
    return _dispatch("VOLUME_BREAKOUT", market_data, ticker)


def analyze_buy_pressure(market_data, ticker):
    return _dispatch("BUY_PRESSURE", market_data, ticker)


def analyze_sell_pressure(market_data, ticker):
    return _dispatch("SELL_PRESSURE", market_data, ticker)


def analyze_vwap_momentum(market_data, ticker):
    return _dispatch("VWAP_MOMENTUM", market_data, ticker)


def analyze_orb_v3(market_data, ticker):
    return _dispatch("ORB_V3", market_data, ticker)


def analyze_donchian_turtle(market_data, ticker):
    return _dispatch("DONCHIAN_TURTLE", market_data, ticker)


def analyze_bulls_bsp(market_data, ticker):
    return _dispatch("BULLS_BSP", market_data, ticker)


def analyze_net_bsv(market_data, ticker):
    return _dispatch("NET_BSV", market_data, ticker)
