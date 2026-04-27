"""
multi_tf_features.py — Extractor de 28 Features Multi-Timeframe para NN

Genera features en 4 timeframes (1m, 5m, 1h, 4h):
  - RSI, MACD, Bollinger Bands, SMA en cada TF
  - Trend alignment entre TFs
  - Volatility consolidation
  - Momentum divergence

Output: (N, 28) array normalizado para LSTM
"""

import numpy as np
import pandas as pd
from typing import Tuple, Dict, List
from dataclasses import dataclass


@dataclass
class MultiTFFeatures:
    """Contenedor para features multi-timeframe."""
    features: np.ndarray  # (N, 28)
    labels: np.ndarray    # (N,) - 1 si ganó trade, 0 si perdió
    timeframes: List[str]  # ["1m", "5m", "1h", "4h"]
    feature_names: List[str]
    scaler_params: Dict  # Para desnormalizar


class MultiTFFeatureExtractor:
    """Extrae 28 features multi-TF para entrenar NN."""

    FEATURE_NAMES = [
        # 1-minute (7 features)
        "rsi_1m", "macd_1m", "bb_width_1m", "sma_trend_1m", "atr_1m", "momentum_1m", "volatility_1m",
        # 5-minute (7 features)
        "rsi_5m", "macd_5m", "bb_width_5m", "sma_trend_5m", "atr_5m", "momentum_5m", "volatility_5m",
        # 1-hour (7 features)
        "rsi_1h", "macd_1h", "bb_width_1h", "sma_trend_1h", "atr_1h", "momentum_1h", "volatility_1h",
        # 4-hour (7 features)
        "rsi_4h", "macd_4h", "bb_width_4h", "sma_trend_4h", "atr_4h", "momentum_4h", "volatility_4h",
    ]

    @staticmethod
    def _resample_to_tf(df: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
        """Resample OHLCV data a timeframe específico."""
        if tf_minutes == 1:
            return df.copy()

        tf_str = f"{tf_minutes}min"
        resampled = pd.DataFrame()
        resampled['open'] = df['open'].resample(tf_str).first()
        resampled['high'] = df['high'].resample(tf_str).max()
        resampled['low'] = df['low'].resample(tf_str).min()
        resampled['close'] = df['close'].resample(tf_str).last()
        resampled['volume'] = df['volume'].resample(tf_str).sum()

        return resampled.dropna()

    @staticmethod
    def _calculate_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Calcula RSI (0-100)."""
        deltas = np.diff(closes)
        seed = deltas[:period + 1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 1
        rsi = np.zeros_like(closes)
        rsi[:period] = 100 - 100 / (1 + rs)

        for i in range(period, len(closes)):
            delta = deltas[i - 1]
            if delta > 0:
                up = (up * (period - 1) + delta) / period
                down = (down * (period - 1)) / period
            else:
                up = (up * (period - 1)) / period
                down = (down * (period - 1) - delta) / period

            rs = up / down if down != 0 else 1
            rsi[i] = 100 - 100 / (1 + rs)

        return rsi / 100  # Normalizar a 0-1

    @staticmethod
    def _calculate_macd(closes: np.ndarray) -> np.ndarray:
        """Calcula MACD (normalizado -1 a 1)."""
        exp1 = pd.Series(closes).ewm(span=12).mean().values
        exp2 = pd.Series(closes).ewm(span=26).mean().values
        macd_line = exp1 - exp2

        # Normalizar a [-1, 1]
        max_val = np.abs(macd_line).max()
        if max_val > 0:
            macd_line = macd_line / max_val

        return np.clip(macd_line, -1, 1)

    @staticmethod
    def _calculate_bollinger_bands(closes: np.ndarray, period: int = 20) -> np.ndarray:
        """Calcula BB width (normalizado 0-1)."""
        sma = pd.Series(closes).rolling(period).mean().values
        std = pd.Series(closes).rolling(period).std().values
        bb_width = (2 * std) / sma  # Proporción

        return np.clip(bb_width / 0.5, 0, 1)  # Normalizar a 0-1

    @staticmethod
    def _calculate_sma_trend(closes: np.ndarray) -> np.ndarray:
        """Calcula trend SMA (normalizado -1 a 1)."""
        sma_fast = pd.Series(closes).rolling(10).mean().values
        sma_slow = pd.Series(closes).rolling(20).mean().values
        trend = (sma_fast - sma_slow) / sma_slow

        return np.clip(trend, -0.1, 0.1) / 0.1  # Normalizar a aprox [-1, 1]

    @staticmethod
    def _calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        """Calcula ATR (Average True Range, normalizado)."""
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        atr = pd.Series(tr).rolling(period).mean().values

        # Normalizar por el cierre
        atr_pct = atr / close
        return np.clip(atr_pct, 0, 0.1) / 0.1  # 0-1

    @staticmethod
    def _calculate_momentum(closes: np.ndarray, period: int = 10) -> np.ndarray:
        """Calcula momentum (ROC, normalizado)."""
        roc = (closes - np.roll(closes, period)) / np.roll(closes, period)
        return np.clip(roc, -0.5, 0.5) / 0.5  # Normalizar a [-1, 1]

    @staticmethod
    def _calculate_volatility(returns: np.ndarray, period: int = 20) -> np.ndarray:
        """Calcula volatilidad realizada."""
        rolling_vol = pd.Series(returns).rolling(period).std().values
        return np.clip(rolling_vol, 0, 0.05) / 0.05  # Normalizar a 0-1

    @classmethod
    def extract_features(
        cls,
        df: pd.DataFrame,
        timeframes_min: List[int] = None
    ) -> Tuple[np.ndarray, Dict]:
        """
        Extrae 28 features multi-TF.

        Args:
            df: DataFrame con OHLCV (index=datetime, columns=['open','high','low','close','volume'])
            timeframes_min: Timeframes a procesar [1, 5, 60, 240] minutos

        Returns:
            (features_array: (N, 28), scaler_params: dict con min/max para desnormalizar)
        """
        if timeframes_min is None:
            timeframes_min = [1, 5, 60, 240]

        df = df.copy()
        df.index = pd.to_datetime(df.index)

        all_features = []
        scaler_params = {"mins": {}, "means": {}, "stds": {}}

        for tf_min in timeframes_min:
            tf_df = cls._resample_to_tf(df, tf_min)

            close = tf_df['close'].values
            high = tf_df['high'].values
            low = tf_df['low'].values

            # Calcular features
            rsi = cls._calculate_rsi(close)
            macd = cls._calculate_macd(close)
            bb_width = cls._calculate_bollinger_bands(close)
            sma_trend = cls._calculate_sma_trend(close)
            atr = cls._calculate_atr(high, low, close)

            returns = np.diff(np.log(close))
            returns = np.insert(returns, 0, 0)  # Padding
            momentum = cls._calculate_momentum(close)
            volatility = cls._calculate_volatility(returns)

            # Stack features para este TF (7 features)
            tf_features = np.column_stack([
                rsi, macd, bb_width, sma_trend, atr, momentum, volatility
            ])

            all_features.append(tf_features)

            # Guardar scaler params (para desnormalizar después)
            scaler_params["mins"][f"tf_{tf_min}"] = tf_features.min(axis=0)
            scaler_params["stds"][f"tf_{tf_min}"] = tf_features.std(axis=0) + 1e-8

        # Concatenar todos los features (28 columnas)
        features = np.column_stack(all_features)

        # Rellenar NaNs
        features = np.nan_to_num(features, nan=0.5, posinf=0.99, neginf=0.01)

        return features, scaler_params

    @classmethod
    def create_training_dataset(
        cls,
        backtest_results: Dict,
        df_by_symbol: Dict[str, pd.DataFrame]
    ) -> MultiTFFeatures:
        """
        Crea dataset para entrenar NN desde resultados de backtest.

        Args:
            backtest_results: {symbol: {strategy: {trades: [...], pf_oos: ...}}}
            df_by_symbol: {symbol: OHLCV DataFrame}

        Returns:
            MultiTFFeatures con features y labels
        """
        all_features_list = []
        all_labels = []

        for symbol, strategies in backtest_results.items():
            if symbol not in df_by_symbol:
                continue

            df = df_by_symbol[symbol]

            # Extraer features para este símbolo
            features, scaler_params = cls.extract_features(df)

            # Label: 1 si PF > 1.2 (ganador), 0 si PF <= 1.2 (perdedor)
            # Promediamos sobre estrategias
            pf_values = []
            for strategy, metrics in strategies.items():
                pf = metrics.get("pf_test_mean", 0.5)
                pf_values.append(pf)

            avg_pf = np.mean(pf_values) if pf_values else 0.5
            label = 1 if avg_pf > 1.2 else 0

            # Usar mismo label para todos los samples de este símbolo
            labels = np.ones(len(features)) * label

            all_features_list.append(features)
            all_labels.extend(labels)

        if not all_features_list:
            raise ValueError("No features extraídas de backtest results")

        features_array = np.vstack(all_features_list)
        labels_array = np.array(all_labels, dtype=int)

        return MultiTFFeatures(
            features=features_array,
            labels=labels_array,
            timeframes=["1m", "5m", "1h", "4h"],
            feature_names=cls.FEATURE_NAMES,
            scaler_params=scaler_params
        )


if __name__ == "__main__":
    # Test
    print("\n" + "="*80)
    print("🔍 TEST: Multi-TF Feature Extractor")
    print("="*80)

    # Crear dummy OHLCV data
    dates = pd.date_range("2024-01-01", periods=1000, freq="1min")
    np.random.seed(42)
    closes = 100 + np.cumsum(np.random.randn(1000) * 0.5)

    df_test = pd.DataFrame({
        'open': closes + np.random.randn(1000) * 0.1,
        'high': closes + np.abs(np.random.randn(1000) * 0.3),
        'low': closes - np.abs(np.random.randn(1000) * 0.3),
        'close': closes,
        'volume': np.random.randint(1000, 10000, 1000)
    }, index=dates)

    extractor = MultiTFFeatureExtractor()
    features, scaler = extractor.extract_features(df_test)

    print(f"✅ Features extraídos: shape {features.shape}")
    print(f"✅ Timeframes: 1m, 5m, 1h, 4h (28 features totales)")
    print(f"✅ Feature names: {len(extractor.FEATURE_NAMES)} features")
    print(f"✅ Scaler params: {list(scaler.keys())}")
    print(f"\n📊 Primeras 5 filas de features:")
    print(features[:5])
