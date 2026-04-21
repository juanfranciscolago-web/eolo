# ============================================================
#  BufferMarketData — adaptador drop-in para las estrategias
#
#  Implementa la misma interfaz que Bot/marketdata.py (REST) pero
#  sirve los datos desde un CandleBuffer (buffer en memoria
#  alimentado por WebSocket). Las 14 estrategias no se enteran
#  de la diferencia.
#
#  Multi-TF: la instancia tiene una `frequency` por defecto pero
#  cada call a get_price_history puede pasar `frequency=` para
#  pedir otro TF sobre el mismo buffer 1min.
# ============================================================
from typing import Optional, Callable
import pandas as pd
from loguru import logger

from .buffer   import CandleBuffer
from .resample import resample_to_tf


class BufferMarketData:
    """
    Adaptador que expone la interface de MarketData (REST) pero sirve
    datos desde un buffer 1-min en memoria y resamplea on-demand.

    buffer:         CandleBuffer con velas 1-min.
    frequency:      TF por defecto (minutos) cuando el caller no pasa `frequency=`.
    quote_source:   callable(symbol)->dict|None  — fuente de quote L1 live.
                    Si None, get_quote() fallback al último close del buffer.
    rest_fallback:  objeto con .get_price_history(symbol,candles,days,frequency)
                    usado SOLO para TF=1440 (daily) o cuando el buffer está corto.
    """

    def __init__(self, buffer: CandleBuffer,
                 frequency: int = 1,
                 quote_source: Optional[Callable[[str], Optional[dict]]] = None,
                 rest_fallback=None):
        self._buffer        = buffer
        self.frequency      = int(frequency)
        self._quote_source  = quote_source
        self._rest_fallback = rest_fallback

    # ── Price history ────────────────────────────────────

    def get_price_history(self, symbol: str, candles: int = 50,
                          days: int = 1, frequency: Optional[int] = None
                          ) -> Optional[pd.DataFrame]:
        """
        Devuelve DataFrame de velas para el símbolo y TF pedidos.

        - frequency=None → usa self.frequency.
        - frequency=1440 → delega a rest_fallback si existe (el buffer
          no acumula varios días). Si no hay fallback, retorna None.
        - candles > 0   → recorta a las últimas N velas.
        - candles = 0   → retorna el DF completo (usado por ORB).
        """
        tf = int(frequency) if frequency is not None else self.frequency

        if tf == 1440:
            if self._rest_fallback is None:
                logger.warning(f"[BUFFER_MD] {symbol} TF=1440 requiere rest_fallback — skip")
                return None
            return self._rest_fallback.get_price_history(
                symbol, candles=candles, days=max(days, 30), frequency=1440
            )

        df = self._buffer.as_df_1min(symbol)
        if df is None or df.empty:
            logger.debug(f"[BUFFER_MD] {symbol} — buffer vacío @ {tf}min")
            return None

        if tf > 1:
            df = resample_to_tf(df, tf)
            if df is None or df.empty:
                logger.debug(f"[BUFFER_MD] {symbol} — resample {tf}min vacío (buffer corto)")
                return None

        if candles and candles > 0:
            df = df.tail(candles).reset_index(drop=True)

        logger.debug(f"[BUFFER_MD] {symbol} — {len(df)} candles @ {tf}min")
        return df

    # ── Quote realtime ──────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[float]:
        """Último precio live. Primero quote_source, luego último close 1m."""
        if self._quote_source is not None:
            try:
                q = self._quote_source(symbol.upper())
                if q:
                    price = q.get("last") or q.get("mark") or q.get("close") or q.get("bid")
                    if price is not None:
                        return float(price)
            except Exception as e:
                logger.debug(f"[BUFFER_MD] quote_source({symbol}) error: {e}")

        return self._buffer.latest_close(symbol)

    def get_quotes(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in symbols:
            p = self.get_quote(s)
            if p is not None:
                out[s] = p
        return out

    # ── Stubs de compatibilidad ─────────────────────────

    def refresh_access_token(self) -> None:
        """No-op: sin llamadas HTTP en el adaptador de buffer."""
        pass

    def get_candles(self, symbol: str, period_type: str = "day",
                    period: int = 1, frequency: int = 5
                    ) -> Optional[pd.DataFrame]:
        """Alias para compatibilidad con bot_trader.close_all_open_positions."""
        return self.get_price_history(symbol, candles=0, frequency=frequency)
