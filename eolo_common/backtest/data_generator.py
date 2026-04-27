"""
data_generator.py — Generador de datos OHLCV sintéticos realistas para backtesting

Crea datos OHLCV con características realistas:
  - Volatilidad variable por régimen
  - Tendencias realistas (drift + random walk)
  - Drawdowns ocasionales
  - Volumen realista
  - Patrones de volatilidad clustering
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class SyntheticOHLCVGenerator:
    """Generador de datos OHLCV sintéticos con características realistas."""

    def __init__(self, seed: int = 42):
        """Inicializar generador."""
        np.random.seed(seed)

    def generate_timeseries(
        self,
        start_price: float,
        days: int,
        volatility: float,
        drift: float,
        regime: str = "bull",
        volume_base: int = 1000000
    ) -> pd.DataFrame:
        """
        Generar serie de precios OHLCV realista.

        Args:
            start_price: Precio inicial
            days: Número de días
            volatility: Volatilidad anualizada (0.15 = 15%)
            drift: Drift diario (0.0005 = 0.05%)
            regime: Régimen ("bull", "bear", "chop", "crash")
            volume_base: Volumen base diario

        Returns:
            DataFrame con OHLCV
        """
        # Daily returns con volatility clustering
        daily_vol = volatility / np.sqrt(252)

        # Volatility clustering: cambios ocasionales en volatilidad
        vol_multiplier = np.ones(days)
        vol_changes = np.random.choice([1.0, 1.5, 2.0], size=days, p=[0.8, 0.15, 0.05])
        vol_multiplier = np.convolve(vol_changes, np.ones(5)/5, mode='same')

        # Generar returns
        returns = np.random.normal(drift, daily_vol, days) * vol_multiplier

        # Aplicar regime-specific adjustments
        if regime == "crash":
            returns[:len(returns)//3] = np.random.normal(-0.001, daily_vol * 3, len(returns)//3)
        elif regime == "bear":
            returns = np.random.normal(-0.0003, daily_vol * 1.5, days)
        elif regime == "chop":
            drift_adjust = np.random.choice([-0.0002, 0.0002], size=days)
            returns = returns + drift_adjust

        # Calcular precios Close
        prices = np.exp(np.cumsum(returns))
        close = start_price * prices

        # Generar OHLC a partir de Close
        dates = pd.date_range(start=datetime(2017, 1, 1), periods=days, freq='D')

        ohlc = []
        for i, close_price in enumerate(close):
            # Intraday movement: ~1-2% del close
            intraday_range = close_price * np.random.uniform(0.005, 0.02)

            open_price = close_price + np.random.uniform(-intraday_range/2, intraday_range/2)
            high_price = max(open_price, close_price) + np.random.uniform(0, intraday_range/2)
            low_price = min(open_price, close_price) - np.random.uniform(0, intraday_range/2)

            # Volume con clustering
            vol = volume_base * np.random.uniform(0.5, 1.5) * vol_multiplier[i]

            ohlc.append({
                'Date': dates[i],
                'Open': open_price,
                'High': high_price,
                'Low': low_price,
                'Close': close_price,
                'Volume': int(vol)
            })

        df = pd.DataFrame(ohlc)
        df.set_index('Date', inplace=True)

        logger.info(f"✓ Generados {len(df)} días sintéticos ({regime})")
        return df

    def generate_portfolio(
        self,
        symbols: Dict[str, Dict],
        days: int = 2900  # ~8 años
    ) -> Dict[str, pd.DataFrame]:
        """
        Generar múltiples series sintéticas.

        Args:
            symbols: Dict con config por símbolo
                {
                    "SPY": {"start_price": 100, "volatility": 0.15, "drift": 0.0005},
                    ...
                }
            days: Número de días a generar

        Returns:
            Dict[symbol] = DataFrame OHLCV
        """
        data = {}

        for symbol, config in symbols.items():
            start_price = config.get('start_price', 100)
            volatility = config.get('volatility', 0.15)
            drift = config.get('drift', 0.0005)
            regime = config.get('regime', 'bull')
            volume = config.get('volume_base', 1000000)

            df = self.generate_timeseries(
                start_price=start_price,
                days=days,
                volatility=volatility,
                drift=drift,
                regime=regime,
                volume_base=volume
            )

            data[symbol] = df

        return data


def generate_backtest_dataset() -> Dict[str, pd.DataFrame]:
    """
    Generar dataset completo de backtesting con 26 activos sintéticos.

    Características:
    - 5 acciones US (SPY, QQQ, AAPL, MSFT, TSLA)
    - 3 cryptos (BTC, ETH, BNB)
    - 2900 días (2017-01-01 a ~2024-12-31)
    - Volatilidades realistas por activo

    Returns:
        Dict[symbol] = DataFrame OHLCV
    """
    generator = SyntheticOHLCVGenerator(seed=42)

    # Configuración realista por símbolo (volatilidad anualizada, drift, etc)
    symbols_config = {
        # Acciones US
        "SPY": {"start_price": 220, "volatility": 0.15, "drift": 0.0005, "regime": "bull"},
        "QQQ": {"start_price": 140, "volatility": 0.20, "drift": 0.0008, "regime": "bull"},
        "AAPL": {"start_price": 140, "volatility": 0.25, "drift": 0.0010, "regime": "bull"},
        "MSFT": {"start_price": 160, "volatility": 0.22, "drift": 0.0008, "regime": "bull"},
        "TSLA": {"start_price": 240, "volatility": 0.40, "drift": 0.0012, "regime": "bull"},

        # Cryptos (volatilidades más altas)
        "BTCUSDT": {"start_price": 8000, "volatility": 0.70, "drift": 0.0015, "regime": "bull"},
        "ETHUSDT": {"start_price": 400, "volatility": 0.80, "drift": 0.0018, "regime": "bull"},
        "BNBUSDT": {"start_price": 100, "volatility": 0.75, "drift": 0.0016, "regime": "bull"},
    }

    data = generator.generate_portfolio(symbols_config, days=2900)

    logger.info(f"✓ Dataset generado: {len(data)} activos × 2900 días = {sum(len(df) for df in data.values())} velas")

    return data


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))

    logging.basicConfig(level=logging.INFO)

    print("\n" + "="*80)
    print("🔄 GENERANDO DATASET SINTÉTICO DE BACKTESTING")
    print("="*80)

    data = generate_backtest_dataset()

    print("\n📊 Activos generados:")
    for symbol, df in data.items():
        print(f"  {symbol:10} | {len(df):4} velas | ${df['Close'].iloc[0]:.2f} → ${df['Close'].iloc[-1]:.2f}")

    print(f"\n✅ Total: {len(data)} activos listos para backtesting")
