# ============================================================
#  EOLO Crypto — OHLCV buffer rolling por símbolo
#
#  Thin wrapper sobre eolo_common.multi_tf.CandleBuffer para:
#    • Mantener la interfaz pública existente (MarketDataBuffer,
#      backfill, push, as_dataframe, size, symbols, drop, latest_close)
#    • Agregar soporte multi-TF (as_dataframe_tf, resample pandas)
#    • Unificar el storage con v1, v1.2 y v2 (una sola implementación)
#
#  Backfill inicial via GET /api/v3/klines al arrancar (para que
#  SMA200, Bollinger, etc. tengan data suficiente desde el primer tick).
# ============================================================
import os
import sys
from typing import Optional

import pandas as pd
from loguru import logger

# ── Asegurar que eolo_common sea importable ───────────────
# En Cloud Run el Dockerfile copia eolo_common/ al mismo nivel que
# el código de la app, así que basta con agregar el parent dir del
# working dir. En dev local el repo tiene eolo_common/ al root.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.multi_tf import (  # noqa: E402
    CandleBuffer,
    resample_to_tf,
)
from eolo_common.multi_tf.normalize import from_binance_rest_kline  # noqa: E402

import settings  # noqa: E402
from helpers import binance_get  # noqa: E402


class MarketDataBuffer:
    """
    Buffer rolling OHLCV por símbolo. Internamente usa el CandleBuffer
    común (eolo_common.multi_tf.CandleBuffer) — así toda la flota
    (v1, v1.2, v2, crypto) comparte el mismo storage y las mismas reglas
    de resample para multi-TF.

    Interfaz pública preservada para backward-compat con el resto del
    código crypto (claude_bot_crypto, eolo_crypto_main, screener, etc.).
    """

    def __init__(self, max_len: int = None):
        self.max_len = max_len or settings.BUFFER_SIZE
        self._cb = CandleBuffer(max_len=self.max_len)

    # ── Inicialización / backfill ─────────────────────────

    def backfill(self, symbol: str, limit: int = None):
        """
        Descarga las últimas `limit` velas de 1m vía REST y las
        carga al buffer. Se llama una vez por símbolo al arrancar.
        """
        limit = limit or settings.HISTORICAL_LOAD
        symbol = symbol.upper()
        try:
            raw = binance_get(
                "/api/v3/klines",
                params={
                    "symbol":   symbol,
                    "interval": settings.KLINE_INTERVAL,
                    "limit":    limit,
                },
                signed=False,
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[BUFFER] Backfill {symbol} falló: {e}")
            return

        # Reset previo: drop del símbolo si existía, para no mezclar
        # viejo con nuevo cuando hacemos re-backfill.
        self._cb.drop(symbol)

        count = 0
        for k in raw:
            c = from_binance_rest_kline(symbol, k)
            if c is None:
                continue
            # Preservar campos extra que estrategias pueden usar
            # (trades, open_ms, close_ms) — el CandleBuffer guarda
            # el dict completo, así que podemos enriquecerlo:
            c["interval"] = settings.KLINE_INTERVAL
            c["open_ms"]  = int(k[0])
            c["close_ms"] = int(k[6])
            try:
                c["trades"] = int(k[8])
            except (TypeError, ValueError, IndexError):
                c["trades"] = 0
            c["closed"] = True
            self._cb.push(c)
            count += 1

        logger.info(f"[BUFFER] {symbol} backfilled con {count} velas")

    # ── Write (desde stream) ──────────────────────────────

    def push(self, candle: dict):
        """
        Agrega una vela cerrada al buffer. Si el símbolo es nuevo,
        lo crea y dispara backfill automático.

        El stream Binance emite candles con:
            {symbol, interval, open_ms, close_ms, open, high, low,
             close, volume, trades, closed}

        Normalizamos a {..., ts_ms=close_ms} para el CandleBuffer
        común (preservando el resto de fields en el mismo dict).
        """
        symbol = (candle.get("symbol") or "").upper()
        if not symbol:
            return

        # Primera vez que vemos este símbolo — backfill on-the-fly
        # (el screener acaba de agregarlo dinámicamente)
        if self._cb.size(symbol) == 0:
            self.backfill(symbol)

        # Normalizar: ts_ms es el anchor estándar. Usamos close_ms
        # que es el timestamp de cierre — coherente con resample
        # closed="right", label="right".
        ts_ms = candle.get("close_ms") or candle.get("ts_ms")
        if ts_ms is None:
            return

        c = dict(candle)            # shallow copy — no mutar el original
        c["symbol"] = symbol
        c["ts_ms"]  = int(ts_ms)
        self._cb.push(c)

    # ── Read ──────────────────────────────────────────────

    def as_dataframe(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Devuelve las velas de 1m del símbolo como DataFrame con
        índice datetime UTC (tz-aware). None si no hay data.

        Mantenemos el datetime como INDEX (no columna) para
        backward-compat con las estrategias crypto que hacen
        df.iloc[-1]["close"] y similar.
        """
        rows = self._cb.raw_candles(symbol)
        if not rows:
            return None

        # Filtrar filas con OHLC válido
        clean = [c for c in rows
                 if all(c.get(k) is not None
                        for k in ("open", "high", "low", "close"))]
        if not clean:
            return None

        df = pd.DataFrame(clean)
        # ts_ms es el anchor del CandleBuffer común; para crypto
        # usamos close_ms como datetime (es igual en este path,
        # pero mantenemos close_ms como fallback por si acaso).
        ts_col = "close_ms" if "close_ms" in df.columns else "ts_ms"
        df["datetime"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)

        for col in ("open", "high", "low", "close", "volume", "trades"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"])
        df = (df.sort_values("datetime")
                .drop_duplicates(subset=["datetime"], keep="last")
                .set_index("datetime"))

        keep = [c for c in ("open", "high", "low", "close", "volume", "trades")
                if c in df.columns]
        return df[keep] if not df.empty else None

    def as_dataframe_tf(self, symbol: str, tf: int) -> Optional[pd.DataFrame]:
        """
        Devuelve el buffer resampleado al timeframe pedido (minutos).
        TF soportados: 1, 5, 15, 30, 60, 240, 1440.

        El DataFrame retornado tiene índice datetime UTC (tz-aware)
        — consistente con as_dataframe() para backward-compat.
        Si tf=1 retorna el 1-min directo (sin resample).
        """
        if tf == 1:
            return self.as_dataframe(symbol)

        df1 = self._cb.as_df_1min(symbol)
        if df1 is None:
            return None
        try:
            df_tf = resample_to_tf(df1, tf)
        except Exception as e:
            logger.warning(f"[BUFFER] resample {symbol}@{tf}m falló: {e}")
            return None
        if df_tf is None or df_tf.empty:
            return None

        # resample_to_tf devuelve datetime como columna → a index
        return df_tf.set_index("datetime")

    def latest_close(self, symbol: str) -> Optional[float]:
        return self._cb.latest_close(symbol)

    def size(self, symbol: str) -> int:
        return self._cb.size(symbol)

    def symbols(self) -> list[str]:
        return self._cb.symbols()

    def drop(self, symbol: str):
        """Remove a symbol (used cuando screener saca un par del universo)."""
        self._cb.drop(symbol)

    # ── Helpers para snapshots Claude / debug ─────────────

    def raw_candles(self, symbol: str) -> list[dict]:
        """Acceso crudo a la deque de velas — útil para Claude snapshot."""
        return self._cb.raw_candles(symbol)
