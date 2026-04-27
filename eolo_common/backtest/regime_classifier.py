"""
regime_classifier.py — Clasificador de regímenes de mercado

Identifica 7 regímenes principales (2017-2025) basados en:
  - Volatilidad (std dev de retornos)
  - Trend direction (media móvil, RSI)
  - Drawdown acumulado

También incluye detector LIVE del régimen actual para activar estrategias.

Los 7 regímenes:
  1. bull_2017: Bull fuerte, baja vol
  2. crash_2020: Crash violento, vol altísima
  3. rebote_2021: Rebote explosivo + bull impulsivo
  4. bear_2022: Bear market con rallies falsos
  5. mixto_2023: Rotación selectiva, no todo sube
  6. trend_2024: Bull concentrado en tech/mega-caps
  7. reciente: Últimos 6-12 meses (actual)
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# ============================================================
# Constantes de Regímenes
# ============================================================

REGIMES_DEFINITION = {
    "bull_2017": {
        "start": "2017-01-01",
        "end": "2017-12-31",
        "description": "Bull fuerte, baja volatilidad",
        "expected_vol": "low",
        "expected_trend": "up"
    },
    "crash_2020": {
        "start": "2020-02-01",
        "end": "2020-04-30",
        "description": "Crash extremo, volatilidad altísima",
        "expected_vol": "high",
        "expected_trend": "down"
    },
    "rebote_2021": {
        "start": "2020-05-01",
        "end": "2021-12-31",
        "description": "Rebote explosivo + bull impulsivo",
        "expected_vol": "medium",
        "expected_trend": "up"
    },
    "bear_2022": {
        "start": "2022-01-01",
        "end": "2022-12-31",
        "description": "Bear market con rallies falsos",
        "expected_vol": "high",
        "expected_trend": "down"
    },
    "mixto_2023": {
        "start": "2023-01-01",
        "end": "2023-12-31",
        "description": "Rotación selectiva, no todo sube",
        "expected_vol": "medium",
        "expected_trend": "mixed"
    },
    "trend_2024": {
        "start": "2024-01-01",
        "end": "2024-08-31",
        "description": "Bull concentrado en tech/mega-caps",
        "expected_vol": "low",
        "expected_trend": "up"
    },
    "reciente": {
        "start": "2024-09-01",
        "end": None,  # Hoy
        "description": "Período reciente/actual",
        "expected_vol": None,
        "expected_trend": None
    }
}

# Umbrales para clasificación automática LIVE
VOLATILITY_THRESHOLDS = {
    "low": 0.01,      # 1% retorno diario std
    "medium": 0.02,   # 2%
    "high": 0.03      # 3%+
}


# ============================================================
# Regime Classifier
# ============================================================

class RegimeClassifier:
    """
    Clasificador de regímenes de mercado.

    Características:
      - Etiquetado de períodos históricos (2017-2025)
      - Detección LIVE del régimen actual
      - Métricas de volatilidad y trend
    """

    def __init__(self):
        """Inicializar classifier."""
        self.regimes = REGIMES_DEFINITION

    def label_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str = "SPY"
    ) -> pd.DataFrame:
        """
        Etiquetar DataFrame con régimen para cada vela.

        Args:
            df: DataFrame con OHLCV (index = datetime)
            symbol: Símbolo para logging

        Returns:
            DataFrame con columna 'regime' agregada
        """
        df = df.copy()
        df["regime"] = None

        for regime_name, regime_info in self.regimes.items():
            start = pd.Timestamp(regime_info["start"])
            end = pd.Timestamp(regime_info["end"]) if regime_info["end"] else pd.Timestamp.now()

            mask = (df.index >= start) & (df.index <= end)
            df.loc[mask, "regime"] = regime_name

            count = mask.sum()
            if count > 0:
                logger.info(f"  {regime_name}: {count} velas ({start.date()} a {end.date()})")

        # Rellenar regímenes sin etiquetar (si hay gaps)
        if df["regime"].isna().any():
            logger.warning(f"⚠️ {symbol}: {df['regime'].isna().sum()} velas sin régimen. "
                          f"Revisar períodos de definición.")

        return df

    def detect_live(
        self,
        df: pd.DataFrame,
        lookback_days: int = 60,
        symbol: str = "SPY"
    ) -> Tuple[str, Dict[str, float]]:
        """
        Detectar el régimen ACTUAL (para HOY).

        Análisis:
          - Volatilidad reciente (últimas 60 velas)
          - Trend direction (SMA 20 vs SMA 50)
          - RSI reciente

        Args:
            df: DataFrame con OHLCV
            lookback_days: Días recientes a analizar (default: 60 = 3 meses aprox)
            symbol: Símbolo para logging

        Returns:
            (regime_name, metrics_dict)
        """
        if len(df) < lookback_days:
            logger.warning(f"⚠️ {symbol}: Menos de {lookback_days} velas. "
                          f"No hay suficientes datos para detectar régimen.")
            return "unknown", {}

        recent = df.tail(lookback_days).copy()

        # Calcular métricas
        close = recent["Close"]
        returns = close.pct_change().dropna()

        metrics = {
            "volatility": returns.std(),
            "mean_return": returns.mean(),
            "sharpe": returns.mean() / (returns.std() + 1e-8) if returns.std() > 0 else 0,
        }

        # Trend direction (SMA)
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        current_price = close.iloc[-1]

        if current_price > sma20 > sma50:
            trend = "uptrend"
            metrics["trend"] = "strong_up"
        elif current_price > sma20:
            trend = "up"
            metrics["trend"] = "up"
        elif current_price < sma20 < sma50:
            trend = "downtrend"
            metrics["trend"] = "strong_down"
        elif current_price < sma20:
            trend = "down"
            metrics["trend"] = "down"
        else:
            trend = "mixed"
            metrics["trend"] = "mixed"

        # Clasificar volatilidad
        vol = metrics["volatility"]
        if vol < VOLATILITY_THRESHOLDS["low"]:
            vol_classification = "low"
        elif vol < VOLATILITY_THRESHOLDS["medium"]:
            vol_classification = "medium"
        else:
            vol_classification = "high"

        metrics["volatility_classification"] = vol_classification

        # Determinar régimen basado en vol + trend
        # Nota: Este es un ejemplo simplificado.
        # En producción, usar clasificación más sofisticada (PCA, clustering).

        if vol_classification == "low" and trend in ["up", "strong_up"]:
            regime = "bull_clean"  # Similar a 2017, 2024
        elif vol_classification == "high" and trend in ["down", "strong_down"]:
            regime = "bear_crash"  # Similar a 2020 crash, 2022
        elif vol_classification == "medium" and trend in ["up", "strong_up"]:
            regime = "bull_noisy"  # Similar a 2021
        elif vol_classification == "medium" and trend == "mixed":
            regime = "chop_mixed"  # Similar a 2023
        else:
            regime = "mixed"

        logger.info(f"📊 {symbol} Régimen ACTUAL: {regime}")
        logger.info(f"   Volatilidad: {vol:.4f} ({vol_classification})")
        logger.info(f"   Trend: {metrics['trend']}")
        logger.info(f"   Sharpe (reciente): {metrics['sharpe']:.2f}")

        return regime, metrics

    def get_regime_period(self, regime_name: str) -> Tuple[str, str]:
        """
        Obtener período (start, end) de un régimen.

        Args:
            regime_name: Nombre del régimen

        Returns:
            (start_date, end_date)
        """
        if regime_name not in self.regimes:
            raise ValueError(f"Régimen desconocido: {regime_name}")

        r = self.regimes[regime_name]
        end = r["end"] or datetime.now().strftime("%Y-%m-%d")
        return r["start"], end

    def get_regime_metrics(
        self,
        df: pd.DataFrame,
        regime_name: str,
        symbol: str = "SPY"
    ) -> Dict[str, float]:
        """
        Calcular métricas de un régimen específico.

        Args:
            df: DataFrame con OHLCV
            regime_name: Nombre del régimen
            symbol: Símbolo para logging

        Returns:
            Dict con métricas (volatilidad, retorno, sharpe, DD, etc.)
        """
        start, end = self.get_regime_period(regime_name)
        period_df = df.loc[start:end]

        if period_df.empty:
            logger.warning(f"⚠️ {symbol}: No hay datos para {regime_name}")
            return {}

        close = period_df["Close"]
        returns = close.pct_change().dropna()

        # Calidad de datos
        data_quality = {
            "num_bars": len(period_df),
            "start_date": period_df.index[0].strftime("%Y-%m-%d"),
            "end_date": period_df.index[-1].strftime("%Y-%m-%d"),
        }

        # Volatilidad y retorno
        volatility_metrics = {
            "daily_volatility": returns.std(),
            "total_return": (close.iloc[-1] / close.iloc[0] - 1),
            "annualized_return": returns.mean() * 252,
            "annualized_volatility": returns.std() * np.sqrt(252),
        }

        # Sharpe (asumiendo risk-free = 0)
        sharpe = (volatility_metrics["annualized_return"] /
                 (volatility_metrics["annualized_volatility"] + 1e-8))

        # Drawdown
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        metrics = {
            **data_quality,
            **volatility_metrics,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
        }

        logger.info(f"Régimen {regime_name}:")
        logger.info(f"  Retorno total: {metrics['total_return']:.2%}")
        logger.info(f"  Volatilidad anual: {metrics['annualized_volatility']:.2%}")
        logger.info(f"  Sharpe: {metrics['sharpe']:.2f}")
        logger.info(f"  Max DD: {metrics['max_drawdown']:.2%}")

        return metrics

    def split_by_regimes(
        self,
        df: pd.DataFrame,
        symbol: str = "SPY"
    ) -> Dict[str, pd.DataFrame]:
        """
        Dividir DataFrame en bloques por régimen.

        Args:
            df: DataFrame con OHLCV (con columna 'regime')
            symbol: Símbolo para logging

        Returns:
            Dict[regime_name] = DataFrame del régimen
        """
        # Primero etiquetar si no está hecho
        if "regime" not in df.columns:
            df = self.label_dataframe(df, symbol)

        results = {}
        for regime_name in self.regimes.keys():
            regime_df = df[df["regime"] == regime_name]
            if not regime_df.empty:
                results[regime_name] = regime_df
                logger.info(f"  {regime_name}: {len(regime_df)} velas")

        return results


# ============================================================
# Funciones Auxiliares
# ============================================================

def create_regime_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience function para etiquetar DataFrame."""
    classifier = RegimeClassifier()
    return classifier.label_dataframe(df)


def detect_live_regime(df: pd.DataFrame) -> Tuple[str, Dict]:
    """Convenience function para detectar régimen actual."""
    classifier = RegimeClassifier()
    return classifier.detect_live(df)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test: crear DataFrame dummy
    dates = pd.date_range("2017-01-01", "2024-12-31", freq="D")
    dummy_data = {
        "Close": np.random.randn(len(dates)).cumsum() + 100,
        "High": np.random.randn(len(dates)).cumsum() + 102,
        "Low": np.random.randn(len(dates)).cumsum() + 98,
        "Open": np.random.randn(len(dates)).cumsum() + 100,
        "Volume": np.random.randint(1000000, 10000000, len(dates)),
    }
    df = pd.DataFrame(dummy_data, index=dates)

    classifier = RegimeClassifier()
    df_labeled = classifier.label_dataframe(df)
    print(f"\nDataFrame etiquetado con regímenes:")
    print(df_labeled.tail(10))

    # Test: detección LIVE
    regime, metrics = classifier.detect_live(df)
    print(f"\nRégimen ACTUAL detectado: {regime}")
    print(f"Métricas: {metrics}")
