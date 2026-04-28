# ============================================================
#  EOLO Crypto — Estrategia: Post-Liquidation Cascade
#
#  Ref: nueva estrategia crypto-native (2026-04-27)
#
#  Lógica:
#    En mercados de crypto, los liquidation cascades son movimientos
#    bruscos provocados por cierres forzados de posiciones apalancadas.
#    Suelen sobrerreaccionar y rebotar rápido. La estrategia:
#
#    SEÑAL BUY  : la vela previa cayó > DROP_THRESHOLD_PCT con
#                 volumen > VOL_MULT_THRESHOLD × MA20(volume).
#                 Esto sugiere que el movimiento fue impulsado por
#                 liquidaciones, no por fundamentos.
#
#    SEÑAL SELL : precio subió > PROFIT_TARGET_PCT desde entrada
#                 O la vela previa bajó > SECONDARY_STOP_PCT
#                 desde la entrada (no se confirmó el rebote).
#
#    Filtros adicionales:
#      - Solo activo en modo 4h (TF largo = menos ruido).
#      - El mínimo de la vela de liquidación es el SL implícito.
#      - No abrir si ya hay una posición abierta en el símbolo.
#
#    Compatibilidad:
#      - detect_signal(df, ticker, ...) → "BUY"|"SELL"|"HOLD"
#      - Registrado en crypto_adapters.py como "liquidation_cascade"
#
#  Universo : todo el universo crypto (aplicable a cualquier par USDT)
#  Timeframe : 4h (evaluado cuando cierra la vela de 240m)
#  Categoría : liquidation / mean_reversion
# ============================================================
import os

import numpy as np
import pandas as pd
from loguru import logger

STRATEGY_NAME = "LIQUIDATION_CASCADE"

# Caída mínima de la vela en % para señal de liquidación
DROP_THRESHOLD_PCT     = float(os.environ.get("LC_DROP_PCT",     "3.0"))

# El volumen de la vela debe ser >= este múltiplo del MA20(vol)
VOL_MULT_THRESHOLD     = float(os.environ.get("LC_VOL_MULT",     "2.0"))

# Profit target desde la entrada para SELL
PROFIT_TARGET_PCT      = float(os.environ.get("LC_PROFIT_PCT",   "4.0"))

# Stop loss — si el precio cae otro X% desde la entry, salir
SECONDARY_STOP_PCT     = float(os.environ.get("LC_STOP_PCT",     "2.5"))

# Cuántas velas mirar para calcular MA del volumen
VOL_MA_PERIOD          = int(os.environ.get("LC_VOL_MA",        "20"))

# Máximo % de drop que aceptamos (evitar catástrofes no rebotables)
DROP_THRESHOLD_MAX_PCT = float(os.environ.get("LC_DROP_MAX_PCT", "15.0"))


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    macro=None,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> str:
    if len(df) < VOL_MA_PERIOD + 2:
        return "HOLD"

    # Necesitamos al menos la vela previa + la actual
    prev = df.iloc[-2]
    last = df.iloc[-1]

    price = float(last["close"])

    # ── SELL: gestión de posición abierta ────────────────────
    if entry_price is not None and entry_price > 0:
        pnl_pct = (price - entry_price) / entry_price * 100

        # Profit target
        if pnl_pct >= PROFIT_TARGET_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL — profit "
                f"{pnl_pct:+.2f}% ≥ +{PROFIT_TARGET_PCT}%"
            )
            return "SELL"

        # Stop loss secundario (rebote no confirmado)
        if pnl_pct <= -SECONDARY_STOP_PCT:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} SELL (stop) — "
                f"P&L {pnl_pct:+.2f}% ≤ -{SECONDARY_STOP_PCT}%"
            )
            return "SELL"

        return "HOLD"

    # ── BUY: detectar vela de liquidación ────────────────────
    prev_open  = float(prev["open"])
    prev_close = float(prev["close"])

    if prev_open <= 0:
        return "HOLD"

    # Caída porcentual de la vela previa
    candle_drop_pct = (prev_open - prev_close) / prev_open * 100

    # Debe ser una vela bajista con caída significativa
    if candle_drop_pct < DROP_THRESHOLD_PCT:
        return "HOLD"

    # No comprar si la caída fue demasiado profunda (catástrofe)
    if candle_drop_pct > DROP_THRESHOLD_MAX_PCT:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — drop too large "
            f"({candle_drop_pct:.1f}% > {DROP_THRESHOLD_MAX_PCT}%)"
        )
        return "HOLD"

    # Confirmar con volumen: liquidation cascades tienen vol alto
    vol_history = df["volume"].iloc[-(VOL_MA_PERIOD + 1):-1]
    if len(vol_history) < 5:
        return "HOLD"

    vol_ma     = float(vol_history.mean())
    prev_vol   = float(prev["volume"])

    if vol_ma <= 0:
        return "HOLD"

    vol_mult = prev_vol / vol_ma
    if vol_mult < VOL_MULT_THRESHOLD:
        logger.debug(
            f"[{STRATEGY_NAME}] {ticker} skip — vol ratio {vol_mult:.2f}x "
            f"< {VOL_MULT_THRESHOLD}x (not a liquidation)"
        )
        return "HOLD"

    # Señal confirmada: vela bajista + volumen alto = liquidation cascade
    logger.info(
        f"[{STRATEGY_NAME}] {ticker} BUY @ {price:.4f} — "
        f"prev candle drop={candle_drop_pct:.1f}%, "
        f"vol={vol_mult:.1f}x MA20 (liquidation cascade)"
    )
    return "BUY"


def analyze(market_data, ticker: str, **kwargs) -> dict:
    """Wrapper para compatibilidad con crypto_adapters.py StrategyRunner."""
    try:
        tf = kwargs.get("timeframe", 240)
        # Para crypto, el df llega del MarketDataBuffer ya resampleado
        df = market_data.get_candles_resampled(ticker, tf) if hasattr(market_data, "get_candles_resampled") else market_data
        if df is None or (hasattr(df, "empty") and df.empty):
            return {"signal": "HOLD", "ticker": ticker, "price": 0,
                    "reason": "no data", "strategy": STRATEGY_NAME}

        entry_price = kwargs.get("entry_price")
        signal = detect_signal(df, ticker, entry_price=entry_price)
        price  = float(df.iloc[-1]["close"]) if not df.empty else 0
        return {
            "signal":   signal,
            "ticker":   ticker,
            "price":    price,
            "reason":   f"{STRATEGY_NAME} signal={signal}",
            "strategy": STRATEGY_NAME,
        }
    except Exception as e:
        logger.error(f"[{STRATEGY_NAME}] analyze error {ticker}: {e}")
        return {"signal": "HOLD", "ticker": ticker, "price": 0,
                "reason": f"error: {e}", "strategy": STRATEGY_NAME}
