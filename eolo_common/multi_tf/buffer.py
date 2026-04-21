# ============================================================
#  CandleBuffer — almacén de velas 1-min por símbolo
#
#  Reemplaza los 3 buffers ad-hoc que tienen v1.2, v2 y crypto.
#  Acepta velas de cualquier fuente (Schwab CHART_EQUITY, Binance
#  klines, etc.) siempre que vengan normalizadas al shape:
#
#    { "symbol": str, "ts_ms": int, "open": float, "high": float,
#      "low": float, "close": float, "volume": float }
#
#  Cada Eolo debe proveer su propio "normalizer" al push —
#  helpers disponibles en normalize.py.
# ============================================================
from collections import deque
from typing import Optional
import pandas as pd
from loguru import logger


class CandleBuffer:
    """
    Buffer rolling de velas 1-min por símbolo. Thread-safe para
    reads simples (GIL protege operaciones atómicas sobre deque)
    y safe para writes single-producer (el callback del stream).

    Si se usa con múltiples productores, envolver push() en un lock.
    """

    def __init__(self, max_len: int = 500):
        self.max_len = int(max_len)
        self._buffers: dict[str, deque] = {}

    # ── Write ─────────────────────────────────────────────

    def push(self, candle: dict) -> None:
        """
        Agrega una vela al buffer. Requiere el shape normalizado:
        {symbol, ts_ms, open, high, low, close, volume}.

        Si llega una vela con el mismo ts_ms que la última del buffer
        (update in-place de la vela activa) reemplaza; si es nueva,
        append. Evita duplicados.
        """
        symbol = candle.get("symbol", "").upper()
        if not symbol:
            return
        ts_ms = candle.get("ts_ms")
        if ts_ms is None:
            return

        buf = self._buffers.setdefault(symbol, deque(maxlen=self.max_len))
        if buf and buf[-1].get("ts_ms") == ts_ms:
            # Update de la vela activa (misma ts) — reemplazar
            buf[-1] = candle
            return
        buf.append(candle)

    def push_many(self, candles: list[dict]) -> None:
        for c in candles:
            self.push(c)

    # ── Read ──────────────────────────────────────────────

    def as_df_1min(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Devuelve el buffer de 1min como DataFrame con columnas:
        datetime (UTC, tz-aware), open, high, low, close, volume.
        None si no hay data.
        """
        symbol = symbol.upper()
        buf = self._buffers.get(symbol)
        if not buf:
            return None

        rows = [c for c in buf
                if all(c.get(k) is not None
                       for k in ("open", "high", "low", "close"))]
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = (df.sort_values("datetime")
                .drop_duplicates(subset=["datetime"], keep="last")
                .reset_index(drop=True))
        return df if not df.empty else None

    def latest_close(self, symbol: str) -> Optional[float]:
        buf = self._buffers.get(symbol.upper())
        if not buf:
            return None
        c = buf[-1].get("close")
        try:
            return float(c) if c is not None else None
        except (TypeError, ValueError):
            return None

    def size(self, symbol: str) -> int:
        return len(self._buffers.get(symbol.upper(), []))

    def symbols(self) -> list[str]:
        return list(self._buffers.keys())

    def raw_candles(self, symbol: str) -> list[dict]:
        """
        Devuelve la lista de velas crudas (dicts con keys estándar) para
        acceso tipo list — útil para Claude snapshots o logs.
        Retorna [] si no hay buffer o está en modo legacy (usar as_df_1min en ese caso).
        """
        buf = self._buffers.get(symbol.upper()) if self._buffers is not None else None
        return list(buf) if buf else []

    def drop(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol in self._buffers:
            del self._buffers[symbol]
            logger.debug(f"[BUFFER] {symbol} removido")

    # ── Compatibilidad con código legacy v1.2 / v2 ───────
    #
    # v1.2 y v2 instancian BufferMarketData con un dict[str, list[dict]]
    # crudo (sin ts_ms normalizado). Para que el refactor sea incremental,
    # aceptamos también ese modo: si al arrancar el CandleBuffer recibe
    # un dict externo, lo envolvemos y los writes van al dict.

    @classmethod
    def from_legacy_dict(cls, raw: dict, ts_field: str = "time",
                         max_len: int = 500) -> "CandleBuffer":
        """
        Construye un buffer desde un dict legacy {symbol: [candles]}
        donde cada candle tiene el timestamp bajo `ts_field` (ms desde
        epoch). El stream legacy sigue escribiendo al dict externo —
        este buffer lee on-demand (no copia).
        """
        buf = cls(max_len=max_len)
        # Reemplazo el storage interno por un proxy que lee del dict externo
        buf._legacy_raw    = raw
        buf._legacy_ts_key = ts_field
        buf._buffers       = None   # señal: modo legacy
        return buf

    # Override silencioso de as_df_1min para modo legacy
    # (decidido via setattr porque no queremos 2 clases)

    def _as_df_1min_legacy(self, symbol: str) -> Optional[pd.DataFrame]:
        raw = getattr(self, "_legacy_raw", {})
        ts_key = getattr(self, "_legacy_ts_key", "time")
        candles = raw.get(symbol.upper(), []) or []
        rows = [c for c in candles
                if all(c.get(k) is not None
                       for k in ("open", "high", "low", "close"))]
        if not rows:
            return None
        df = pd.DataFrame(rows)
        if ts_key not in df.columns:
            return None
        df["datetime"] = pd.to_datetime(df[ts_key], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        keep = [c for c in ("datetime", "open", "high", "low", "close", "volume")
                if c in df.columns]
        df = df[keep].dropna(subset=["open", "high", "low", "close"])
        df = (df.sort_values("datetime")
                .drop_duplicates(subset=["datetime"], keep="last")
                .reset_index(drop=True))
        return df if not df.empty else None


# Monkey-patch limpio: si el buffer fue creado con from_legacy_dict,
# redirigir as_df_1min al helper legacy.
_orig_as_df_1min = CandleBuffer.as_df_1min


def _dispatch_as_df_1min(self, symbol: str):
    if getattr(self, "_buffers", None) is None and hasattr(self, "_legacy_raw"):
        return self._as_df_1min_legacy(symbol)
    return _orig_as_df_1min(self, symbol)


CandleBuffer.as_df_1min = _dispatch_as_df_1min
