"""
build_market_snapshot_from_crop — Construye el dict MarketSnapshot
para enviar al LLM Engine desde el estado del bot eolo-crop.

Approach 1 (full snapshot) con limitaciones de v0.1:
- Daily indicators (rsi_daily, ema_*_daily) defaulteados a neutral
  porque el CandleBuffer es intraday-only (100 min de 1-min bars).
  Tech debt #15: agregar REST call cacheada en v0.2.
- BVP/SVP rolling 100min (no intraday completo).
  Tech debt #16: bumpear CANDLE_BUFFER_SIZE a ~500 en v0.2.
- MACD 15m con pocos candles si buffer.size < 30. Warning logged.
  Tech debt #17.
- VIX velocity 30m/1d defaulteado a 0.0 — el bot no computa hoy.
  Tech debt #18: agregar VIX history buffer.

Inputs:
- ticker, chain (dict de Schwab stream), vix_level + velocities (4 floats),
  pivot_result (PivotAnalysisResult), candle_buffer (eolo_common.multi_tf.CandleBuffer),
  allowed_dtes (list[int]), open_positions_summary (str o None).

Output:
- dict con shape compatible con MarketSnapshot pydantic del LLM Engine.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List, TypedDict, Dict, Any

import pandas as pd

from llm_gate.indicators import (
    calculate_rsi, calculate_atr, calculate_ema, calculate_macd,
    calculate_fibonacci_levels, calculate_vwap_bands,
    calculate_buy_sell_volume_pressure,
)

logger = logging.getLogger(__name__)


# Defaults para campos sin data en v0.1 (ver tech debt #15)
_RSI_NEUTRAL = 50.0
_EMA_DEFAULT = 0.0
_MACD_MIN_CANDLES = 30  # min para MACD 15m razonable

# Module-level flag para no spamear el warning de VIX velocity (tech debt #18)
_VIX_WARNING_LOGGED = False

# Module-level dict para no spamear el warning de MACD 15m por ticker (tech debt #17).
# Reseteado solo en cold start del proceso. Acepta el trade-off de que un ticker
# que recuperó buffer suficiente y luego lo vuelve a perder, no re-logueará.
_MACD_15M_WARNED: dict[str, bool] = {}


class MarketSnapshotDict(TypedDict, total=False):
    """TypedDict de hints para el snapshot. total=False = todos opcionales."""
    timestamp: str
    ticker: str
    session_phase: str
    price: float
    open_price: float
    high: float
    low: float
    prev_close: float
    vix_level: float
    vix_velocity_30m_pct: float
    vix_velocity_1d_pct: float
    vix_vs_prev_close_pct: float
    pdh: float
    pdl: float
    pdc: float
    fib_r1: float
    fib_r2: float
    fib_r3: float
    fib_s1: float
    fib_s2: float
    fib_s3: float
    vwap: float
    vwap_upper_1sigma: float
    vwap_upper_2sigma: float
    vwap_lower_1sigma: float
    vwap_lower_2sigma: float
    rsi_2m: float
    rsi_15m: float
    rsi_daily: float
    atr_2m: float
    atr_15m: float
    atr_daily: float
    adr_daily: float
    ema_9_2m: float
    ema_21_2m: float
    ema_200_2m: float
    ema_9_15m: float
    ema_21_15m: float
    ema_9_daily: float
    ema_21_daily: float
    ema_50_daily: float
    ema_200_daily: float
    macd_histogram_15m: float
    macd_signal_15m: float
    macd_line_15m: float
    bvp_pct: float
    svp_pct: float
    volume_current_bar: float
    volume_avg_20bar: float
    iv_rank_spy: Optional[float]
    iv_30d: Optional[float]
    days_to_next_fomc: Optional[int]
    days_to_next_cpi: Optional[int]
    days_to_next_nfp: Optional[int]
    session_news: Optional[str]
    has_open_positions: bool
    open_positions_summary: Optional[str]


def _resample_to_df(candle_buffer, ticker: str, frequency_min: int) -> Optional[pd.DataFrame]:
    """
    Resample 1-min buffer a TF custom.
    Returns DataFrame con columns: open, high, low, close, volume (datetime index).
    None si buffer no tiene data.
    """
    try:
        # CandleBuffer.as_df_1min retorna df con 'datetime' como COLUMNA, no index.
        df_1m = candle_buffer.as_df_1min(ticker)
        if df_1m is None or len(df_1m) == 0:
            return None
        if frequency_min == 1:
            return df_1m
        rule = f"{frequency_min}min"  # 'T' deprecado en pandas >=2.2
        df = (
            df_1m.set_index("datetime")
                 .resample(rule)
                 .agg({
                     "open": "first",
                     "high": "max",
                     "low": "min",
                     "close": "last",
                     "volume": "sum",
                 })
                 .dropna()
        )
        return df
    except Exception as e:
        logger.warning(f"[snapshot] resample {ticker} freq={frequency_min} failed: {e}")
        return None


def build_market_snapshot_from_crop(
    ticker: str,
    chain: Dict[str, Any],
    vix_level: float,
    pivot_result,  # PivotAnalysisResult del bot
    candle_buffer,  # eolo_common.multi_tf.CandleBuffer
    vix_velocity_30m_pct: float = 0.0,
    vix_velocity_1d_pct: float = 0.0,
    vix_vs_prev_close_pct: float = 0.0,
    allowed_dtes: Optional[List[int]] = None,
    open_positions_summary: Optional[str] = None,
    iv_rank_spy: Optional[float] = None,
    iv_30d: Optional[float] = None,
    days_to_next_fomc: Optional[int] = None,
    days_to_next_cpi: Optional[int] = None,
    days_to_next_nfp: Optional[int] = None,
    daily_buffer=None,  # Sprint 6 (tech debt #15): CandleBuffer con daily candles
) -> MarketSnapshotDict:
    """
    Construye snapshot completo para enviar al LLM.

    NOTA v0.1 — VIX velocities defaultean a 0.0:
    El bot eolo-crop no computa VIX velocity 30m/1d hoy (no hay VIX history
    buffer). Hasta que se implemente (tech debt #18), el LLM no podra detectar
    spikes intradia desde estos campos. El Haiku prefilter caera al modo de
    setup-neutral y delegara a Sonnet incluso en spikes reales.

    Defaults documentados:
    - rsi_daily / ema_*_daily = neutrales (tech debt #15)
    - BVP/SVP rolling 100min (tech debt #16)
    - MACD 15m warning si pocos candles (tech debt #17)
    """
    global _VIX_WARNING_LOGGED
    if (not _VIX_WARNING_LOGGED
            and vix_velocity_30m_pct == 0.0
            and vix_velocity_1d_pct == 0.0):
        logger.warning(
            "[snapshot] VIX velocity not computed by bot (tech debt #18). "
            "VIX spike detection disabled. Haiku/Sonnet wont see spikes."
        )
        _VIX_WARNING_LOGGED = True

    snapshot: MarketSnapshotDict = {}

    # Identificacion
    # 4.D HZ-3 RESUELTA: timestamp en ET (no UTC) para que el LLM no confunda
    # 14:00 UTC con 14:00 ET. ISO format con offset (ej. "2026-05-28T10:00:00-04:00").
    snapshot["timestamp"] = datetime.now(ZoneInfo("America/New_York")).isoformat()
    snapshot["ticker"] = ticker
    snapshot["session_phase"] = "regular"  # TODO computar fase real en v0.2

    # Price action
    price = float(chain.get("underlying", {}).get("mark", 0.0))
    snapshot["price"] = price

    # OHLC del dia (del buffer 1-min, si esta)
    df_1m = _resample_to_df(candle_buffer, ticker, 1)
    if df_1m is not None and len(df_1m) > 0:
        snapshot["open_price"] = float(df_1m["open"].iloc[0])
        snapshot["high"] = float(df_1m["high"].max())
        snapshot["low"] = float(df_1m["low"].min())
    else:
        snapshot["open_price"] = price
        snapshot["high"] = price
        snapshot["low"] = price

    # Prev day del pivot_result
    try:
        snapshot["prev_close"] = float(pivot_result.atr.prev_close)
        snapshot["pdh"] = float(pivot_result.atr.prev_high)
        snapshot["pdl"] = float(pivot_result.atr.prev_low)
        snapshot["pdc"] = snapshot["prev_close"]
    except Exception as e:
        logger.warning(f"[snapshot] pivot_result prev OHLC failed: {e}")
        snapshot["prev_close"] = price
        snapshot["pdh"] = price
        snapshot["pdl"] = price
        snapshot["pdc"] = price

    # VIX (level + velocities pasadas como params)
    snapshot["vix_level"] = float(vix_level)
    snapshot["vix_velocity_30m_pct"] = float(vix_velocity_30m_pct)
    snapshot["vix_velocity_1d_pct"] = float(vix_velocity_1d_pct)
    snapshot["vix_vs_prev_close_pct"] = float(vix_vs_prev_close_pct)

    # Fibonacci levels
    try:
        fibs = calculate_fibonacci_levels(
            snapshot["open_price"], snapshot["pdh"], snapshot["pdl"]
        )
        snapshot["fib_r1"] = fibs["r1"]
        snapshot["fib_r2"] = fibs["r2"]
        snapshot["fib_r3"] = fibs["r3"]
        snapshot["fib_s1"] = fibs["s1"]
        snapshot["fib_s2"] = fibs["s2"]
        snapshot["fib_s3"] = fibs["s3"]
    except Exception as e:
        logger.warning(f"[snapshot] fibonacci failed: {e}")
        for k in ["fib_r1", "fib_r2", "fib_r3", "fib_s1", "fib_s2", "fib_s3"]:
            snapshot[k] = price

    # 2m indicators
    df_2m = _resample_to_df(candle_buffer, ticker, 2)
    if df_2m is not None and len(df_2m) >= 15:
        try:
            snapshot["rsi_2m"] = calculate_rsi(df_2m["close"], 14)
            snapshot["atr_2m"] = calculate_atr(df_2m["high"], df_2m["low"], df_2m["close"], 14)
            snapshot["ema_9_2m"] = calculate_ema(df_2m["close"], 9)
            snapshot["ema_21_2m"] = calculate_ema(df_2m["close"], 21)
            vwap = calculate_vwap_bands(df_2m)
            snapshot.update(vwap)
            bvp = calculate_buy_sell_volume_pressure(df_2m)
            snapshot["bvp_pct"] = bvp["bvp_pct"]
            snapshot["svp_pct"] = bvp["svp_pct"]
            snapshot["volume_current_bar"] = bvp["volume_current_bar"]
            snapshot["volume_avg_20bar"] = bvp["volume_avg_20bar"]
        except Exception as e:
            logger.warning(f"[snapshot] 2m indicators failed: {e}")
            _apply_2m_defaults(snapshot, price)
    else:
        logger.warning(f"[snapshot] {ticker} 2m buffer insuficiente, defaults")
        _apply_2m_defaults(snapshot, price)

    # 15m indicators
    df_15m = _resample_to_df(candle_buffer, ticker, 15)
    if df_15m is not None and len(df_15m) >= 15:
        try:
            snapshot["rsi_15m"] = calculate_rsi(df_15m["close"], 14)
            snapshot["atr_15m"] = calculate_atr(df_15m["high"], df_15m["low"], df_15m["close"], 14)
            snapshot["ema_9_15m"] = calculate_ema(df_15m["close"], 9)
            snapshot["ema_21_15m"] = calculate_ema(df_15m["close"], 21)
            if len(df_15m) >= _MACD_MIN_CANDLES:
                macd_line, macd_signal, macd_hist = calculate_macd(df_15m["close"])
                snapshot["macd_line_15m"] = macd_line
                snapshot["macd_signal_15m"] = macd_signal
                snapshot["macd_histogram_15m"] = macd_hist
            else:
                # Tech debt #17: one-shot por ticker — sin gate spammea cada ciclo
                # theta_harvest mientras el buffer 15m no llega a 30 candles (~7.5h
                # market time desde cold start si tickers no estan pre-warmed).
                if not _MACD_15M_WARNED.get(ticker):
                    logger.warning(
                        f"[snapshot] {ticker} MACD 15m: buffer={len(df_15m)} "
                        f"< {_MACD_MIN_CANDLES} (tech debt #17 — defaults=0.0 "
                        f"hasta warm-up completo; no se re-logueará por ticker)"
                    )
                    _MACD_15M_WARNED[ticker] = True
                snapshot["macd_line_15m"] = 0.0
                snapshot["macd_signal_15m"] = 0.0
                snapshot["macd_histogram_15m"] = 0.0
        except Exception as e:
            logger.warning(f"[snapshot] 15m indicators failed: {e}")
            _apply_15m_defaults(snapshot)
    else:
        _apply_15m_defaults(snapshot)

    # Daily — Sprint 6 (tech debt #15): real indicators desde daily_buffer.
    # Fallback al pivot_result.atr.atr_day + defaults si daily_buffer no
    # disponible (boot warm-up o caller sin Sprint 6).
    _apply_daily_indicators(snapshot, ticker, daily_buffer, pivot_result)

    # Options context
    snapshot["iv_rank_spy"] = iv_rank_spy
    snapshot["iv_30d"] = iv_30d

    # Macro context
    snapshot["days_to_next_fomc"] = days_to_next_fomc
    snapshot["days_to_next_cpi"] = days_to_next_cpi
    snapshot["days_to_next_nfp"] = days_to_next_nfp
    snapshot["session_news"] = None

    # Open positions
    snapshot["has_open_positions"] = open_positions_summary is not None
    snapshot["open_positions_summary"] = open_positions_summary

    return snapshot


def _apply_2m_defaults(snapshot: dict, price: float) -> None:
    """Defaults intencionales 2m — comportamiento de "ventana parcial" (tech debt #21).

    CUÁNDO se llama: el CandleBuffer no tiene suficientes 2-min candles para
    indicators con look-back >= 14 bars (RSI/ATR). En cold start del bot esto
    puede tardar ~28 min de market time en saturar. NO es un error, es la
    fase warm-up legítima del stream feed.

    QUÉ devuelve: neutrales que NO sesgan la decisión del LLM ni del
    rule-based downstream:
      - RSI=50 (zona neutra, ni overbought ni oversold)
      - ATR=0 (señala "no volatility info" → consumers que dividen por ATR
        deben guardarse contra 0)
      - EMA_9 / EMA_21 / VWAP / VWAP_bands = price actual (la "mejor
        aproximación" sin histórico es el precio spot)
      - bvp_pct / svp_pct = 50 (volume profile balanced)
      - volume_current_bar / volume_avg_20bar = 0

    CUÁNDO INDICA BUG: si el buffer tiene >100 candles y aún caemos aquí,
    es signo de que `_resample_to_df` retornó None — investigar el path
    de normalize/push antes de aceptar el default.
    """
    snapshot["rsi_2m"] = _RSI_NEUTRAL
    snapshot["atr_2m"] = 0.0
    snapshot["ema_9_2m"] = price
    snapshot["ema_21_2m"] = price
    snapshot["vwap"] = price
    snapshot["vwap_upper_1sigma"] = price
    snapshot["vwap_upper_2sigma"] = price
    snapshot["vwap_lower_1sigma"] = price
    snapshot["vwap_lower_2sigma"] = price
    snapshot["bvp_pct"] = 50.0
    snapshot["svp_pct"] = 50.0
    snapshot["volume_current_bar"] = 0.0
    snapshot["volume_avg_20bar"] = 0.0


def _apply_15m_defaults(snapshot: dict) -> None:
    """Defaults intencionales 15m — comportamiento de "ventana parcial" (tech debt #21).

    CUÁNDO se llama: el resample 15m del CandleBuffer (1-min → 15-min) no
    cubre los 15 bars mínimos para RSI/ATR ni los 30 para MACD. Como cada
    bar de 15-min requiere 15 candles 1-min consecutivas, el warm-up es más
    largo que el 2m (~7.5h de market time en cold start). NO es un error.

    QUÉ devuelve: defaults neutrales = 0.0 para EMA/MACD y 50 para RSI.
    A diferencia de `_apply_2m_defaults`, NO usamos `price` como aproximación
    para EMA_9 / EMA_21 — el LLM/rule-based downstream debe detectar la
    diferencia (ema=0 → flag "indicators no disponibles") y proceder con
    cautela en lugar de tratar "ema = price" como señal alcista.

    CUÁNDO INDICA BUG: ver `_apply_2m_defaults` — buffer poblado pero
    resample falla = path de normalize/push roto.
    """
    snapshot["rsi_15m"] = _RSI_NEUTRAL
    snapshot["atr_15m"] = 0.0
    snapshot["ema_9_15m"] = 0.0
    snapshot["ema_21_15m"] = 0.0
    snapshot["macd_line_15m"] = 0.0
    snapshot["macd_signal_15m"] = 0.0
    snapshot["macd_histogram_15m"] = 0.0


def _apply_daily_defaults(snapshot: dict, pivot_result) -> None:
    """Defaults daily (boot warm-up o daily_buffer no provisto — tech debt #15).

    Fallback al pivot_result.atr.atr_day cuando está, sino 0.0. Resto = neutrales.
    """
    try:
        snapshot["atr_daily"] = float(pivot_result.atr.atr_day)
    except Exception:
        snapshot["atr_daily"] = 0.0
    snapshot["rsi_daily"] = _RSI_NEUTRAL
    snapshot["ema_9_daily"] = _EMA_DEFAULT
    snapshot["ema_21_daily"] = _EMA_DEFAULT
    snapshot["ema_50_daily"] = _EMA_DEFAULT
    snapshot["ema_200_daily"] = _EMA_DEFAULT
    snapshot["adr_daily"] = 0.0


def _apply_daily_indicators(snapshot: dict, ticker: str, daily_buffer, pivot_result) -> None:
    """Sprint 6 (tech debt #15) — daily indicators reales desde daily_buffer.

    CUÁNDO se llama: cada `build_market_snapshot_from_crop`. Si `daily_buffer`
    es None o no tiene suficientes candles, fallback a `_apply_daily_defaults`.

    THRESHOLDS:
      - RSI(14)         requiere >=14
      - ATR(14)         requiere >=14
      - EMA(9/21/50)    requieren ese N de candles (graceful: si <N → 0.0)
      - EMA(200)        idem (común que no haya 200 en cold start hasta backfill)
      - ADR(20)         mean(high-low) sobre 20 días — si <20 → 0.0

    Si daily_buffer tiene <14 candles → defaults completos (treat as warm-up).
    """
    if daily_buffer is None:
        _apply_daily_defaults(snapshot, pivot_result)
        return
    try:
        df = daily_buffer.as_df_1min(ticker)  # accessor genérico devuelve OHLCV ordenado
    except Exception as e:
        logger.warning(f"[snapshot] {ticker} daily_buffer.as_df_1min falló: {e}")
        df = None
    if df is None or len(df) < 14:
        _apply_daily_defaults(snapshot, pivot_result)
        return
    try:
        close = df["close"]
        snapshot["rsi_daily"] = calculate_rsi(close, 14)
        snapshot["atr_daily"] = calculate_atr(df["high"], df["low"], close, 14)
        snapshot["ema_9_daily"]  = calculate_ema(close, 9)  if len(df) >= 9   else _EMA_DEFAULT
        snapshot["ema_21_daily"] = calculate_ema(close, 21) if len(df) >= 21  else _EMA_DEFAULT
        snapshot["ema_50_daily"] = calculate_ema(close, 50) if len(df) >= 50  else _EMA_DEFAULT
        snapshot["ema_200_daily"]= calculate_ema(close, 200)if len(df) >= 200 else _EMA_DEFAULT
        if len(df) >= 20:
            snapshot["adr_daily"] = float((df["high"] - df["low"]).tail(20).mean())
        else:
            snapshot["adr_daily"] = 0.0
    except Exception as e:
        logger.warning(f"[snapshot] {ticker} daily indicators failed: {e}")
        _apply_daily_defaults(snapshot, pivot_result)
