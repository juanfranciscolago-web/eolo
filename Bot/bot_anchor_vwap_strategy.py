# ============================================================
#  EOLO — Estrategia: Anchor VWAP Bounce
#
#  Ref: trading_strategies_v2.md #16
#
#  Lógica (momentum en nivel clave):
#    - Anchors intradía soportados:
#        (1) Session-open VWAP (= VWAP acumulado del día)
#        (2) Prior-day-close VWAP (anclado al cierre de ayer)
#        (3) Anchor custom por timestamp (si se pasa via kwargs)
#    - La vela actual "toca" el anchor (low − anchor < 0.1 ATR)
#      y muestra:
#        * wick lower ≥ 60% (cierre cerca del high)
#        * volumen > 1.3 × media de las últimas 5 velas
#    - BUY si rebota hacia arriba. SHORT se reporta como HOLD.
#
#  Categoría: momentum_breakout.
#  Universo: todos (configurables via anchors).
#
#  NOTA: #16 original incluye anchors de earnings/FOMC/CPI/NFP.
#  Aquí implementamos la variante intraday-only. Los anchors por
#  evento se agregan como mapping externo en `anchors_registry.py`
#  en una fase posterior.
# ============================================================
import pandas as pd
import pytz
from datetime import datetime, timedelta
from loguru import logger

STRATEGY_NAME = "ANCHOR_VWAP"

ATR_PERIOD        = 14
TOUCH_ATR_FRAC    = 0.10
WICK_RATIO_MIN    = 0.60
VOL_LOOKBACK      = 5
VOL_MULT          = 1.30

EASTERN = pytz.timezone("America/New_York")


# ── Indicadores ───────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close  = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _anchor_vwap(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    """VWAP cumulative desde anchor_idx (inclusive) hasta el final."""
    sub = df.iloc[anchor_idx:].copy()
    tp  = (sub["high"] + sub["low"] + sub["close"]) / 3
    return (tp * sub["volume"]).cumsum() / sub["volume"].cumsum()


def _session_open_idx(df: pd.DataFrame) -> int:
    """Primera vela del día (hoy ET)."""
    today = datetime.now(EASTERN).date()
    if df["datetime"].dt.tz is None:
        dt_et = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
    else:
        dt_et = df["datetime"].dt.tz_convert(EASTERN)
    mask = dt_et.dt.date == today
    if not mask.any():
        return 0
    return int(mask.idxmax())


def _prior_close_idx(df: pd.DataFrame) -> int:
    """Última vela del día previo."""
    today = datetime.now(EASTERN).date()
    if df["datetime"].dt.tz is None:
        dt_et = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
    else:
        dt_et = df["datetime"].dt.tz_convert(EASTERN)
    mask = dt_et.dt.date < today
    if not mask.any():
        return 0
    return int(mask[mask].index.max())


# ── Señal ─────────────────────────────────────────────────

def _evaluate_anchor(df: pd.DataFrame, anchor_name: str, anchor_idx: int) -> tuple:
    """
    Evalúa el anchor. Devuelve (fire: bool, anchor_price: float|None, details).
    """
    if anchor_idx < 0 or anchor_idx >= len(df):
        return False, None, {}

    atr = df.iloc[-1].get("atr")
    if pd.isna(atr) or atr is None:
        return False, None, {}

    vwap_series = _anchor_vwap(df, anchor_idx)
    if vwap_series.empty or pd.isna(vwap_series.iloc[-1]):
        return False, None, {}

    anchor_vwap_now = float(vwap_series.iloc[-1])

    last = df.iloc[-1]
    bar_range = float(last["high"]) - float(last["low"])
    if bar_range <= 0:
        return False, anchor_vwap_now, {}

    touched = abs(float(last["low"]) - anchor_vwap_now) < TOUCH_ATR_FRAC * float(atr)
    wick_ratio = (float(last["close"]) - float(last["low"])) / bar_range
    strong_rejection = wick_ratio > WICK_RATIO_MIN

    if len(df) < VOL_LOOKBACK + 2:
        return False, anchor_vwap_now, {}
    vol_prev = float(df["volume"].iloc[-(VOL_LOOKBACK + 1):-1].mean())
    volume_rising = float(last["volume"]) > vol_prev * VOL_MULT

    fire = touched and strong_rejection and volume_rising
    details = {
        "anchor":            anchor_name,
        "anchor_vwap":       round(anchor_vwap_now, 4),
        "touched":           touched,
        "wick_ratio":        round(wick_ratio, 2),
        "volume_rising":     volume_rising,
    }
    return fire, anchor_vwap_now, details


def detect_signal(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
    extra_anchors: list = None,
) -> str:
    if len(df) < max(ATR_PERIOD, VOL_LOOKBACK) + 2:
        return "HOLD"

    last  = df.iloc[-1]
    price = float(last["close"])

    # ── Exit si hay posición ──────────────────────────────
    if entry_price is not None and entry_price > 0:
        profit_pct = (price - entry_price) / entry_price
        tp = profit_target if profit_target is not None else 0.02
        sl = stop_loss     if stop_loss     is not None else 0.01
        if profit_pct >= tp:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — TP {profit_pct:+.2%}")
            return "SELL"
        if profit_pct <= -sl:
            logger.info(f"[{STRATEGY_NAME}] {ticker} SELL — SL {profit_pct:+.2%}")
            return "SELL"
        return "HOLD"

    # ── Evaluar anchors ───────────────────────────────────
    anchors = [
        ("session_open", _session_open_idx(df)),
        ("prior_close",  _prior_close_idx(df)),
    ]
    if extra_anchors:
        for name, idx in extra_anchors:
            anchors.append((str(name), int(idx)))

    for name, idx in anchors:
        fire, _, det = _evaluate_anchor(df, name, idx)
        if fire:
            logger.info(
                f"[{STRATEGY_NAME}] {ticker} BUY — rebote en anchor {name} | "
                f"anchor_vwap={det['anchor_vwap']:.2f} wick={det['wick_ratio']:.2f}"
            )
            return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(
    market_data,
    ticker: str,
    entry_price: float = None,
    profit_target: float = None,
    stop_loss: float = None,
) -> dict:
    df = market_data.get_price_history(ticker, candles=0, days=2)

    if df is None or df.empty:
        logger.error(f"[{STRATEGY_NAME}] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None}

    df = df.copy()
    df["atr"] = calculate_atr(df, ATR_PERIOD)

    signal = detect_signal(df, ticker, entry_price, profit_target, stop_loss)
    last   = df.iloc[-1]

    def safe_round(val, nd=4):
        return round(float(val), nd) if pd.notna(val) else None

    # anchor_vwap diagnosticos
    sess_idx = _session_open_idx(df)
    pc_idx   = _prior_close_idx(df)
    sess_vwap = _anchor_vwap(df, sess_idx).iloc[-1] if sess_idx >= 0 else None
    pc_vwap   = _anchor_vwap(df, pc_idx).iloc[-1]   if pc_idx   >= 0 else None

    return {
        "ticker":          ticker,
        "signal":          signal,
        "strategy":        STRATEGY_NAME,
        "price":           round(float(last["close"]), 4),
        "atr":             safe_round(last.get("atr")),
        "session_vwap":    safe_round(sess_vwap),
        "prior_close_vwap": safe_round(pc_vwap),
        "candle_time":     str(last["datetime"]),
    }
