# ============================================================
#  EOLO v1.2 — BufferMarketData (multi-timeframe)
#
#  Adaptador que implementa la misma interfaz que MarketData
#  (Bot/marketdata.py) pero sirve datos desde el buffer de
#  candles del WebSocket en lugar de hacer llamadas HTTP.
#
#  Extensión v1.2 vs v2:
#    - Soporta multi-timeframe via pandas.resample().
#    - self.frequency puede setearse desde fuera (1, 5, 15, 30, 60, 240, 1440).
#    - Las velas base SIEMPRE son 1min (CHART_EQUITY del WebSocket).
#      Para TFs > 1min se resamplea in-memory.
#    - Para 1440 (diario) se hace un fallback a REST si el buffer es corto
#      (normalmente lleva demasiado tiempo acumular un día completo).
#
#  Interface requerida por las 14 estrategias de v1:
#    get_price_history(symbol, candles=50, days=1, frequency=None) → DataFrame
#      columnas: datetime, open, high, low, close, volume
#
#  Uso:
#    md = BufferMarketData(candle_buffers, frequency=5)
#    df = md.get_price_history("SPY", candles=50)
# ============================================================
import pandas as pd
from loguru import logger


class BufferMarketData:
    """
    Adaptador de MarketData que sirve datos desde el buffer
    de candles acumuladas por SchwabStream (WebSocket).

    candle_buffers: dict[ticker, list[dict]]
    Cada dict de candle tiene las claves:
        symbol, time, open, high, low, close, volume, chart_day
    donde `time` es unix timestamp en milisegundos (Schwab CHART_EQUITY field 2).

    quote_buffers: dict[ticker, dict]  — último quote L1 por ticker
    rest_fallback: MarketData | None   — fallback REST para TF=1440 o buffer corto
    """

    def __init__(self, candle_buffers: dict, quote_buffers: dict | None = None,
                 frequency: int = 1, rest_fallback=None):
        self._buffers   = candle_buffers
        self._quotes    = quote_buffers if quote_buffers is not None else {}
        self.frequency  = int(frequency)
        self._rest      = rest_fallback  # MarketData instance para TF=1440

    # ── Helper: construir DF base 1min desde buffer ──────────

    def _df_1min(self, symbol: str) -> pd.DataFrame | None:
        buf = self._buffers.get(symbol.upper(), [])
        valid = [
            c for c in buf
            if all(c.get(k) is not None for k in ("open", "high", "low", "close", "volume"))
        ]
        if not valid:
            return None

        df = pd.DataFrame(valid)
        if "time" in df.columns:
            df["datetime"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        else:
            return None

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        return df.reset_index(drop=True)

    # ── API pública ──────────────────────────────────────────

    def get_price_history(
        self,
        symbol: str,
        candles: int = 50,
        days: int = 1,
        frequency: int = None,
    ) -> pd.DataFrame | None:
        """
        Devuelve un DataFrame de candles para el símbolo dado,
        construido desde el buffer del WebSocket y resampleado al TF pedido.

        frequency: None = usa self.frequency
                   1            → candles 1min tal cual llegan del stream
                   5, 15, 30    → resample de 1min
                   60, 240      → resample de 1min (1h, 4h)
                   1440         → REST fallback (diario), si hay rest_fallback
        """
        tf = int(frequency) if frequency is not None else self.frequency

        # ── Daily: siempre por REST (el buffer no acumula suficientes días) ──
        if tf == 1440:
            if self._rest is None:
                logger.warning(f"[BUFFER_MD] {symbol} — TF=1440 requiere rest_fallback, skip")
                return None
            return self._rest.get_price_history(symbol, candles=candles,
                                                days=max(days, 30), frequency=1440)

        # ── 1min base ──────────────────────────────────────
        df = self._df_1min(symbol)
        if df is None or df.empty:
            logger.debug(f"[BUFFER_MD] {symbol} — buffer vacío")
            return None

        # ── Resample si TF > 1 ─────────────────────────────
        if tf > 1:
            rule = f"{tf}min"
            df = (df.set_index("datetime")
                    .resample(rule, label="right", closed="right")
                    .agg({"open": "first", "high": "max",
                          "low":  "min",   "close": "last",
                          "volume": "sum"})
                    .dropna(subset=["close"])
                    .reset_index())
            if df.empty:
                logger.debug(f"[BUFFER_MD] {symbol} — resample {tf}min vacío (poco buffer aún)")
                return None

        # ── Recortar a últimas N ───────────────────────────
        if candles and candles > 0:
            df = df.tail(candles).reset_index(drop=True)

        logger.debug(f"[BUFFER_MD] {symbol} — {len(df)} candles @ {tf}min")
        return df

    # ── Quote en tiempo real ────────────────────────────────

    def get_quote(self, symbol: str) -> float | None:
        """
        Retorna el último precio live desde el buffer de quotes L1
        (actualizado tick a tick por el WebSocket).
        Si no hay quote live → fallback al último close de la vela 1min.
        """
        q = self._quotes.get(symbol.upper())
        if q:
            price = q.get("last") or q.get("mark") or q.get("close")
            if price:
                return float(price)

        # Fallback: último close de la vela 1min
        df = self._df_1min(symbol)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    def get_quotes(self, symbols: list) -> dict:
        """Bulk version — devuelve dict {ticker: price}."""
        return {s: self.get_quote(s) for s in symbols
                if self.get_quote(s) is not None}

    # ── Stubs de compatibilidad ─────────────────────────────

    def refresh_access_token(self):
        """No-op: no hay token HTTP en el adaptador de buffer."""
        pass

    def get_candles(self, symbol: str, period_type: str = "day",
                    period: int = 1, frequency: int = 5) -> pd.DataFrame | None:
        """Alias para compatibilidad con bot_trader.close_all_open_positions."""
        return self.get_price_history(symbol, candles=0, frequency=frequency)
