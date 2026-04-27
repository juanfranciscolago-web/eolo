# ============================================================
#  Theta Harvest Backtester — Data Loader
#
#  Descarga datos históricos via yfinance con cache local
#  en formato parquet. Evita re-descargas innecesarias.
#
#  Tickers:
#    - SPY, TQQQ       → underlying prices
#    - ^VIX, ^VVIX     → volatility indices
#    - 11 sector ETFs  → dirección sectorial
# ============================================================
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
import warnings

import pandas as pd
import yfinance as yf

from .config import (
    TICKERS,
    ALL_SECTOR_ETFS,
    BACKTEST_START,
    BACKTEST_END,
)

# ── Cache directory ───────────────────────────────────────
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ── All tickers needed ────────────────────────────────────
VOL_TICKERS  = ["^VIX", "^VVIX"]
ALL_TICKERS  = TICKERS + VOL_TICKERS + ALL_SECTOR_ETFS


# ─────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    safe = ticker.replace("^", "").replace("/", "_")
    return CACHE_DIR / f"{safe}_{BACKTEST_START}_{BACKTEST_END}.parquet"


def _download_ticker(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Descarga OHLCV de un ticker vía yfinance."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(
            ticker,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
        )
    if df.empty:
        print(f"  [WARNING] {ticker}: no data returned from yfinance")
        return pd.DataFrame()

    # yfinance puede devolver MultiIndex si se pasa lista → aplanar
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    return df


def _load_or_download(ticker: str, start: str, end: str) -> pd.DataFrame:
    path = _cache_path(ticker)
    if path.exists():
        df = pd.read_parquet(path)
        print(f"  [CACHE]  {ticker:10s} → {len(df)} días")
        return df

    print(f"  [DL]     {ticker:10s} ...", end=" ", flush=True)
    df = _download_ticker(ticker, start, end)
    if not df.empty:
        df.to_parquet(path)
        print(f"{len(df)} días guardados")
    else:
        print("VACÍO")
    return df


# ─────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────

class MarketData:
    """
    Contenedor de todos los DataFrames necesarios para el backtest.

    Attributes
    ----------
    spy      : OHLCV de SPY
    tqqq     : OHLCV de TQQQ
    vix      : Close diario de ^VIX  (columna 'Close')
    vvix     : Close diario de ^VVIX (columna 'Close')
    sectors  : dict[ticker -> OHLCV]
    calendar : DatetimeIndex con días de trading válidos (unión de spy + tqqq)
    """

    def __init__(
        self,
        spy:     pd.DataFrame,
        tqqq:    pd.DataFrame,
        vix:     pd.DataFrame,
        vvix:    pd.DataFrame,
        sectors: dict[str, pd.DataFrame],
    ):
        self.spy     = spy
        self.tqqq    = tqqq
        self.vix     = vix
        self.vvix    = vvix
        self.sectors = sectors

        # Calendario de trading: días donde SPY tiene datos
        self.calendar: pd.DatetimeIndex = spy.index

    # ── Helpers de lookup ─────────────────────────────────

    def get_ohlcv(self, ticker: str) -> pd.DataFrame:
        if ticker == "SPY":
            return self.spy
        if ticker == "TQQQ":
            return self.tqqq
        if ticker in self.sectors:
            return self.sectors[ticker]
        raise KeyError(f"Ticker {ticker!r} no disponible en MarketData")

    def get_vix(self, date: pd.Timestamp) -> Optional[float]:
        try:
            return float(self.vix.loc[date, "Close"])
        except KeyError:
            # Buscar el día más cercano anterior
            idx = self.vix.index.searchsorted(date)
            if idx > 0:
                return float(self.vix.iloc[idx - 1]["Close"])
            return None

    def get_vvix(self, date: pd.Timestamp) -> Optional[float]:
        try:
            return float(self.vvix.loc[date, "Close"])
        except KeyError:
            idx = self.vvix.index.searchsorted(date)
            if idx > 0:
                return float(self.vvix.iloc[idx - 1]["Close"])
            return None

    def get_sector_return_pct(
        self,
        ticker: str,
        date: pd.Timestamp,
    ) -> Optional[float]:
        """Retorno diario del ETF sectorial en `date` (Open/PrevClose - 1)."""
        df = self.sectors.get(ticker)
        if df is None or date not in df.index:
            return None
        loc = df.index.get_loc(date)
        if loc == 0:
            return None
        prev_close = df.iloc[loc - 1]["Close"]
        today_open = df.iloc[loc]["Open"]
        if prev_close == 0:
            return None
        return float(today_open / prev_close - 1)

    def get_atr(
        self,
        ticker: str,
        date: pd.Timestamp,
        period: int = 14,
    ) -> Optional[float]:
        """ATR de `period` días calculado hasta `date` (exclusive)."""
        df = self.get_ohlcv(ticker)
        if date not in df.index:
            return None
        loc = df.index.get_loc(date)
        if loc < period + 1:
            return None
        window = df.iloc[loc - period - 1 : loc].copy()
        high   = window["High"].values
        low    = window["Low"].values
        close  = window["Close"].values
        tr = []
        for i in range(1, len(window)):
            tr.append(max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i]  - close[i - 1]),
            ))
        return float(sum(tr[-period:]) / period)

    def summary(self) -> str:
        lines = ["MarketData summary:"]
        for name, df in [
            ("SPY",  self.spy),
            ("TQQQ", self.tqqq),
            ("VIX",  self.vix),
            ("VVIX", self.vvix),
        ]:
            lines.append(f"  {name:6s}: {len(df):4d} días  "
                         f"[{df.index[0].date()} → {df.index[-1].date()}]")
        lines.append(f"  Sectors: {len(self.sectors)} ETFs — "
                     f"{list(self.sectors.keys())}")
        return "\n".join(lines)


def load_market_data(
    start: str = BACKTEST_START,
    end:   str = BACKTEST_END,
    force_refresh: bool = False,
) -> MarketData:
    """
    Carga (o descarga y cachea) todos los datos necesarios.

    Parameters
    ----------
    start         : fecha inicio YYYY-MM-DD
    end           : fecha fin   YYYY-MM-DD
    force_refresh : si True, borra cache y re-descarga todo
    """
    if force_refresh:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        print("[DATA] Cache borrado — re-descargando todo")

    print(f"[DATA] Cargando datos {start} → {end}")

    spy  = _load_or_download("SPY",   start, end)
    tqqq = _load_or_download("TQQQ",  start, end)
    vix  = _load_or_download("^VIX",  start, end)
    vvix = _load_or_download("^VVIX", start, end)

    sectors: dict[str, pd.DataFrame] = {}
    for etf in ALL_SECTOR_ETFS:
        df = _load_or_download(etf, start, end)
        if not df.empty:
            sectors[etf] = df

    # Verificaciones básicas
    assert not spy.empty,  "SPY data vacía"
    assert not tqqq.empty, "TQQQ data vacía"
    assert not vix.empty,  "VIX data vacía"

    md = MarketData(spy=spy, tqqq=tqqq, vix=vix, vvix=vvix, sectors=sectors)
    print(md.summary())
    return md


# ─────────────────────────────────────────────────────────
#  Sector direction helper (usado desde simulator)
# ─────────────────────────────────────────────────────────

def compute_sector_direction(
    md: MarketData,
    ticker: str,
    date: pd.Timestamp,
    weights: dict[str, float],
    threshold: float = 0.15,
) -> str:
    """
    Retorna 'bullish' | 'bearish' | 'neutral' para `ticker` en `date`.

    Lógica idéntica a SectorDirection en pivot_analysis.py:
      weighted_score = Σ(weight_i × sign(return_i)) / Σ(weight_i)
      > threshold  → bullish
      < -threshold → bearish
      else         → neutral
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for etf, weight in weights.items():
        ret = md.get_sector_return_pct(etf, date)
        if ret is None:
            continue
        sign = 1.0 if ret > 0 else (-1.0 if ret < 0 else 0.0)
        weighted_sum  += weight * sign
        total_weight  += weight

    if total_weight == 0:
        return "neutral"

    score = weighted_sum / total_weight
    if score > threshold:
        return "bullish"
    if score < -threshold:
        return "bearish"
    return "neutral"
