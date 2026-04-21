# ============================================================
#  eolo_common.filters.cross_asset
#
#  Ref: trading_strategies_v2.md #26
#
#  Cross-Asset Volume Confirmation. Valida que una señal primaria
#  (p.ej. BUY en NVDA) esté acompañada por movimiento + volumen en
#  tickers correlacionados del mismo sector/subyacente.
#
#  Mapping original:
#     NVDA → [SMH, AMD]
#     MSTR → [BTC-USD, IBIT]
#     SOXL → [SMH, AMD]
#     NVDL → [SMH, AMD]
#
#  El filtro exige confirmación direccional (close vs close(-5))
#  y volumen por encima de 1.3× media(20) en al menos N-1 de los
#  correlacionados. Sin confirmación → veta la señal (None).
#
#  Uso:
#     from eolo_common.filters import cross_asset_confirmation
#
#     signal = {"signal": "LONG", "entry": 120.3, ...}
#     ok = cross_asset_confirmation(
#         ticker="NVDA",
#         primary_signal=signal,
#         correlated_bars={"SMH": df_smh, "AMD": df_amd},
#         min_required=None,   # default len(correlated)-1
#     )
#     if ok is None:
#         # veto
#
#  Un ticker sin mapping pasa sin modificar (regla "unknown-pass").
# ============================================================
from typing import Optional

import pandas as pd


CROSS_ASSET_MAP = {
    "NVDA": ["SMH", "AMD"],
    "MSTR": ["BTC-USD", "IBIT"],
    "SOXL": ["SMH", "AMD"],
    "NVDL": ["SMH", "AMD"],
    # Crypto: opcional, configurable por bot
    "BTCUSDT": ["ETHUSDT", "SOLUSDT"],
    "ETHUSDT": ["BTCUSDT", "SOLUSDT"],
}

VOL_MA_PERIOD = 20
VOL_MULT      = 1.30
LOOKBACK      = 5


def _normalize_direction(signal: dict) -> str:
    """Admite 'LONG'/'SHORT' o 'BUY'/'SELL'. Devuelve 'LONG'|'SHORT'|''. """
    d = (signal.get("signal") or signal.get("direction") or "").upper()
    if d in ("LONG", "BUY"):
        return "LONG"
    if d in ("SHORT", "SELL"):
        return "SHORT"
    return ""


def _confirm_one(
    df: pd.DataFrame, direction: str,
    vol_ma_period: int = VOL_MA_PERIOD,
    vol_mult: float = VOL_MULT,
    lookback: int = LOOKBACK,
) -> bool:
    """Confirma un solo correlacionado."""
    if df is None or df.empty or len(df) < max(vol_ma_period, lookback) + 1:
        return False

    last_close    = float(df["close"].iloc[-1])
    ref_close     = float(df["close"].iloc[-(lookback + 1)])
    direction_ok = (
        (direction == "LONG"  and last_close > ref_close) or
        (direction == "SHORT" and last_close < ref_close)
    )
    vol_ma = float(df["volume"].rolling(vol_ma_period).mean().iloc[-1])
    if vol_ma <= 0:
        return False
    rvol = float(df["volume"].iloc[-1]) / vol_ma
    volume_ok = rvol > vol_mult
    return direction_ok and volume_ok


def cross_asset_confirmation(
    ticker: str,
    primary_signal: dict,
    correlated_bars: dict,
    min_required: Optional[int] = None,
    mapping: Optional[dict] = None,
) -> Optional[dict]:
    """
    Aplica el filtro. Devuelve el mismo `primary_signal` si pasa, o None (veto).

    ticker:          símbolo primario (ej. "NVDA")
    primary_signal:  dict con key 'signal' o 'direction' en LONG/SHORT/BUY/SELL
    correlated_bars: dict ticker → DataFrame (columns close, volume)
    min_required:    nº de confirmaciones exigidas. None → len(correlated) - 1.
    mapping:         override del CROSS_ASSET_MAP para testing.
    """
    if primary_signal is None:
        return None

    m = mapping if mapping is not None else CROSS_ASSET_MAP
    correlated = m.get(ticker.upper(), [])
    if not correlated:
        return primary_signal  # unknown-pass

    direction = _normalize_direction(primary_signal)
    if direction == "":
        return None

    confirmations = 0
    for corr_ticker in correlated:
        df = correlated_bars.get(corr_ticker)
        if df is None:
            df = correlated_bars.get(corr_ticker.upper())
        if _confirm_one(df, direction):
            confirmations += 1

    needed = min_required if min_required is not None else max(1, len(correlated) - 1)
    return primary_signal if confirmations >= needed else None


class CrossAssetConfirmation:
    """Wrapper OO para uso desde el orquestador."""

    def __init__(
        self,
        mapping: Optional[dict] = None,
        vol_ma_period: int = VOL_MA_PERIOD,
        vol_mult: float = VOL_MULT,
        lookback: int = LOOKBACK,
    ):
        self.mapping = mapping if mapping is not None else dict(CROSS_ASSET_MAP)
        self.vol_ma_period = vol_ma_period
        self.vol_mult = vol_mult
        self.lookback = lookback

    def apply(
        self,
        ticker: str,
        primary_signal: dict,
        correlated_bars: dict,
        min_required: Optional[int] = None,
    ) -> Optional[dict]:
        return cross_asset_confirmation(
            ticker, primary_signal, correlated_bars, min_required, self.mapping,
        )
