# ============================================================
#  EOLO v2 — BufferMarketData
#
#  Adaptador que implementa la misma interfaz que MarketData
#  (Bot/marketdata.py) pero sirve datos desde el buffer de
#  candles del WebSocket en lugar de hacer llamadas HTTP.
#
#  Permite reutilizar las 13 estrategias técnicas de Eolo v1
#  sin modificarlas ni hacer ninguna llamada extra a Schwab.
#
#  Interface requerida por las estrategias:
#    get_price_history(symbol, candles=50, days=1) → DataFrame
#      columnas: datetime, open, high, low, close, volume
#
#  Uso:
#    md = BufferMarketData(candle_buffers)
#    df = md.get_price_history("SOXL", candles=50)
# ============================================================
import pandas as pd
from datetime import datetime
from loguru import logger


class BufferMarketData:
    """
    Adaptador de MarketData que sirve datos desde el buffer
    de candles acumuladas por SchwabStream (WebSocket).

    candle_buffers: dict[ticker, list[dict]]
    Cada dict de candle tiene las claves:
        symbol, time, open, high, low, close, volume, chart_day
    donde `time` es unix timestamp en milisegundos (Schwab CHART_EQUITY field 2).
    """

    def __init__(self, candle_buffers: dict):
        self._buffers = candle_buffers

    def get_price_history(
        self,
        symbol: str,
        candles: int = 50,
        days: int = 1,
        frequency: int = 1,
    ) -> pd.DataFrame | None:
        """
        Retorna un DataFrame de candles para el símbolo dado,
        construido desde el buffer del WebSocket.

        El parámetro `days` se ignora — el buffer ya contiene
        todo lo acumulado desde que arrancó el stream.

        Retorna None si no hay datos suficientes.
        """
        buf = self._buffers.get(symbol.upper(), [])

        # Filtrar solo entradas de candle con OHLCV completo
        valid = [
            c for c in buf
            if all(c.get(k) is not None for k in ("open", "high", "low", "close", "volume"))
        ]

        if not valid:
            logger.debug(f"[BUFFER_MD] {symbol} — buffer vacío o sin datos OHLCV")
            return None

        df = pd.DataFrame(valid)

        # Convertir timestamp a datetime
        # Schwab CHART_EQUITY field 2 = time en ms desde epoch
        if "time" in df.columns:
            df["datetime"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        else:
            df["datetime"] = pd.NaT

        # Asegurar columnas numéricas
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_values("datetime").reset_index(drop=True)

        # Recortar al N más reciente si se especifica
        if candles and candles > 0:
            df = df.tail(candles).reset_index(drop=True)

        if df.empty:
            logger.debug(f"[BUFFER_MD] {symbol} — DataFrame vacío después de limpiar")
            return None

        logger.debug(f"[BUFFER_MD] {symbol} — {len(df)} candles desde buffer")
        return df

    # ── Stubs de métodos que algunas estrategias pueden llamar ──

    def get_quote(self, symbol: str) -> float | None:
        """
        Retorna el último close del buffer como proxy del precio real.
        En v2 el quote real viene del WebSocket directamente.
        """
        buf = self._buffers.get(f"_quote_{symbol.upper()}", [])
        if buf:
            last = buf[-1]
            return last.get("last") or last.get("mark") or last.get("ask")

        # Fallback: último close del buffer de candles
        df = self.get_price_history(symbol, candles=1)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return None

    def refresh_access_token(self):
        """No-op: no hay token HTTP en el adaptador de buffer."""
        pass

    def get_candles(self, symbol: str, period_type: str = "day",
                    period: int = 1, frequency: int = 5) -> pd.DataFrame | None:
        """Alias para compatibilidad."""
        return self.get_price_history(symbol, candles=0)
