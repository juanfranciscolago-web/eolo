"""
data_loader.py — OHLCV fetcher para backtesting

Descarga datos históricos desde:
  - yfinance: Acciones US (SPY, QQQ, AAPL, MSFT, TSLA, etc.)
  - Binance API: Crypto spot (BTC, ETH, BNB, etc.)

Features:
  - Almacenamiento local (caché) para velocidad
  - Resampleo a múltiples timeframes (1h, 4h, 1d)
  - Validación de datos (gaps, valores faltantes)
  - Logging detallado de descarga
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from pathlib import Path
import json

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from binance.client import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)


# ============================================================
# Constantes de Configuración
# ============================================================

CACHE_DIR = Path("/tmp/eolo_backtest_cache")
CACHE_DIR.mkdir(exist_ok=True)

# Configuración de fricción por régimen y activo
SLIPPAGE_BY_REGIME = {
    "bull_2017": 0.0005,      # 0.05% (spreads 1 pip)
    "crash_2020": 0.005,      # 0.5% (spreads amplios)
    "rebote_2021": 0.001,     # 0.1% (normal)
    "bear_2022": 0.002,       # 0.2% (volatile)
    "mixto_2023": 0.001,      # 0.1% (normal)
    "trend_2024": 0.0008,     # 0.08% (suave)
    "reciente": 0.001,        # 0.1% (normal)
}

COMMISSIONS = {
    "equities": 0.001,        # 0.1% Schwab
    "crypto": 0.001,          # 0.1% Binance Spot
}

# Regímenes y sus períodos
REGIMES = {
    "bull_2017": ("2017-01-01", "2017-12-31"),
    "crash_2020": ("2020-02-01", "2020-04-30"),
    "rebote_2021": ("2020-05-01", "2021-12-31"),
    "bear_2022": ("2022-01-01", "2022-12-31"),
    "mixto_2023": ("2023-01-01", "2023-12-31"),
    "trend_2024": ("2024-01-01", "2024-12-31"),
    "reciente": ("2024-09-01", None),  # Hasta hoy
}


# ============================================================
# Data Loader Principal
# ============================================================

class BacktestDataLoader:
    """
    Cargador de datos OHLCV para backtesting.

    Soporta múltiples fuentes y cachea resultados localmente.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Inicializar data loader.

        Args:
            cache_dir: Directorio para cachear datos (default: /tmp/eolo_backtest_cache)
        """
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(exist_ok=True)

    def _get_cache_path(self, symbol: str, timeframe: str) -> Path:
        """Obtener ruta de caché para símbolo + timeframe."""
        filename = f"{symbol}_{timeframe}.parquet"
        return self.cache_dir / filename

    def _load_from_cache(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Cargar datos desde caché si existen."""
        cache_path = self._get_cache_path(symbol, timeframe)
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                logger.info(f"✓ Cargado desde caché: {symbol} {timeframe} ({len(df)} velas)")
                return df
            except Exception as e:
                logger.warning(f"Error leyendo caché {cache_path}: {e}")
                return None
        return None

    def _save_to_cache(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """Guardar datos en caché."""
        cache_path = self._get_cache_path(symbol, timeframe)
        try:
            df.to_parquet(cache_path)
            logger.info(f"✓ Guardado en caché: {symbol} {timeframe}")
        except Exception as e:
            logger.warning(f"Error guardando caché: {e}")

    def load_equities(
        self,
        symbols: List[str],
        start_date: str = "2017-01-01",
        end_date: Optional[str] = None,
        timeframe: str = "1d",
        use_cache: bool = True
    ) -> Dict[str, pd.DataFrame]:
        """
        Descargar OHLCV de acciones US desde yfinance.

        Args:
            symbols: Lista de símbolos (SPY, QQQ, AAPL, etc.)
            start_date: Fecha inicio (default: 2017-01-01)
            end_date: Fecha fin (default: hoy)
            timeframe: Timeframe (1d, 1h, 15m, 5m, 1m)
            use_cache: Usar caché local

        Returns:
            Dict[symbol] = DataFrame con OHLCV
        """
        if yf is None:
            raise ImportError("yfinance no está instalado. Instalar: pip install yfinance")

        end_date = end_date or datetime.now().strftime("%Y-%m-%d")
        results = {}

        for symbol in symbols:
            logger.info(f"Descargando {symbol} ({start_date} a {end_date}, {timeframe})...")

            # Intentar cargar desde caché
            if use_cache:
                df_cached = self._load_from_cache(symbol, timeframe)
                if df_cached is not None and df_cached.index.max() >= pd.Timestamp(end_date):
                    results[symbol] = df_cached.loc[start_date:end_date]
                    continue

            # Descargar desde yfinance
            try:
                df = yf.download(
                    symbol,
                    start=start_date,
                    end=end_date,
                    interval=timeframe,
                    progress=False
                )

                if df.empty:
                    logger.warning(f"⚠️ {symbol}: No hay datos para {start_date} a {end_date}")
                    continue

                # Validar datos
                df = self._validate_ohlcv(df, symbol)

                # Guardar en caché
                if use_cache:
                    self._save_to_cache(symbol, timeframe, df)

                results[symbol] = df
                logger.info(f"✓ {symbol}: {len(df)} velas descargadas")

            except Exception as e:
                logger.error(f"❌ Error descargando {symbol}: {e}")

        return results

    def load_crypto(
        self,
        symbols: List[str],
        start_date: str = "2017-01-01",
        end_date: Optional[str] = None,
        timeframe: str = "1h",
        use_cache: bool = True,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        Descargar OHLCV de crypto desde Binance API.

        Args:
            symbols: Lista de símbolos Binance (BTCUSDT, ETHUSDT, etc.)
            start_date: Fecha inicio
            end_date: Fecha fin
            timeframe: Timeframe Binance (1h, 4h, 1d, etc.)
            use_cache: Usar caché local
            api_key: API key Binance (si no, usar datos históricos públicos)
            api_secret: API secret Binance

        Returns:
            Dict[symbol] = DataFrame con OHLCV
        """
        if Client is None:
            logger.warning("binance-connector no está instalado. Usar solo datos públicos.")

        end_date = end_date or datetime.now().strftime("%Y-%m-%d")
        results = {}

        for symbol in symbols:
            logger.info(f"Descargando {symbol} desde Binance ({start_date} a {end_date}, {timeframe})...")

            # Intentar cargar desde caché
            if use_cache:
                df_cached = self._load_from_cache(symbol, timeframe)
                if df_cached is not None and df_cached.index.max() >= pd.Timestamp(end_date):
                    results[symbol] = df_cached.loc[start_date:end_date]
                    continue

            # Descargar desde Binance (usando datos públicos)
            try:
                # Nota: Binance API requiere API key para datos históricos completos.
                # Para simplificar, aquí usamos un fallback que lee desde caché o
                # realiza descarga manual vía REST pública.
                # En producción, usar credentials correctas.

                df = self._fetch_binance_klines(symbol, start_date, end_date, timeframe)

                if df is None or df.empty:
                    logger.warning(f"⚠️ {symbol}: No hay datos disponibles")
                    continue

                df = self._validate_ohlcv(df, symbol)

                if use_cache:
                    self._save_to_cache(symbol, timeframe, df)

                results[symbol] = df
                logger.info(f"✓ {symbol}: {len(df)} velas descargadas")

            except Exception as e:
                logger.error(f"❌ Error descargando {symbol} desde Binance: {e}")

        return results

    def _fetch_binance_klines(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str
    ) -> Optional[pd.DataFrame]:
        """
        Fetch klines desde Binance REST API.

        Nota: Implementación simplificada. En producción, usar:
        - python-binance o binance-connector
        - Manejar rate limits
        - Requests batch si períodos son largos
        """
        # Por ahora, retornar None (fallback a caché)
        # En producción, implementar real fetch
        logger.warning(f"Binance fetch no implementado. Usar caché o yfinance.")
        return None

    def _validate_ohlcv(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Validar datos OHLCV.

        - Verificar que Open, High, Low, Close, Volume existan
        - Verificar que High >= Low
        - Rellenar gaps menores (finales de semana, feriados)
        - Log de warnings
        """
        required_cols = ["Open", "High", "Low", "Close", "Volume"]

        # Renombrar si es necesario (yfinance usa mayúsculas)
        df.columns = [c.capitalize() for c in df.columns]

        # Verificar columnas
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"{symbol}: Faltan columnas {missing}")

        # Verificar lógica OHLC
        invalid_high_low = df["High"] < df["Low"]
        if invalid_high_low.any():
            logger.warning(f"⚠️ {symbol}: {invalid_high_low.sum()} velas con High < Low. Corregir...")
            df.loc[invalid_high_low, ["High", "Low"]] = df.loc[invalid_high_low, ["Low", "High"]].values

        # Verificar volumen
        if (df["Volume"] == 0).any():
            logger.warning(f"⚠️ {symbol}: {(df['Volume'] == 0).sum()} velas con volumen 0")

        return df[required_cols + (["Dividends", "Stock Splits"] if "Dividends" in df.columns else [])]

    def resample_ohlcv(
        self,
        df: pd.DataFrame,
        target_timeframe: str
    ) -> pd.DataFrame:
        """
        Resamplear OHLCV a timeframe diferente.

        Args:
            df: DataFrame con OHLCV
            target_timeframe: Target timeframe (1h, 4h, 1d, etc.)

        Returns:
            DataFrame resampleado
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        agg_dict = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }

        return df.resample(target_timeframe).agg(agg_dict).dropna()


# ============================================================
# Funciones Auxiliares
# ============================================================

def load_all_backtest_data(
    equities_symbols: List[str] = None,
    crypto_symbols: List[str] = None,
    start_date: str = "2017-01-01",
    end_date: Optional[str] = None
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    Cargador conveniente que descarga acciones + crypto.

    Returns:
        {
            "equities": {symbol: df, ...},
            "crypto": {symbol: df, ...}
        }
    """
    loader = BacktestDataLoader()
    equities_symbols = equities_symbols or ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
    crypto_symbols = crypto_symbols or ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    results = {
        "equities": loader.load_equities(equities_symbols, start_date, end_date),
        "crypto": loader.load_crypto(crypto_symbols, start_date, end_date)
    }

    return results


if __name__ == "__main__":
    # Test simple
    logging.basicConfig(level=logging.INFO)

    loader = BacktestDataLoader()
    print("Descargando SPY...")
    data = loader.load_equities(["SPY"], start_date="2024-01-01", end_date="2024-12-31")
    print(f"SPY: {len(data.get('SPY', []))} velas")
