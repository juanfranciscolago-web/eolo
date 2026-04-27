"""
strategy_wrapper.py — Adaptador de estrategias para backtesting

Convierte las 27 estrategias de Bot/ al formato compatible con el backtest engine.

Cada estrategia se adapta así:
  - Input: DataFrame con OHLCV
  - Output: Dict con {"profit_factor": x, "num_trades": y, ...}

Las estrategias originales usan detect_signal() que retorna "BUY", "SELL", "HOLD".
Este wrapper las convierte a señales numéricas (1, -1, 0).
"""

import pandas as pd
import numpy as np
from typing import Dict, Callable, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# ============================================================
# Adaptadores de Estrategias
# ============================================================

class StrategyWrapper:
    """
    Adaptador genérico para convertir estrategias de Bot/ al formato de backtesting.
    """

    def __init__(
        self,
        strategy_name: str,
        signal_func: Callable,
        indicator_func: Optional[Callable] = None,
        ticker: str = "SPY"
    ):
        """
        Inicializar wrapper de estrategia.

        Args:
            strategy_name: Nombre de la estrategia
            signal_func: Función que genera señales (BUY/SELL/HOLD)
            indicator_func: Función que calcula indicadores (opcional)
            ticker: Símbolo para estrategias que lo requieren
        """
        self.strategy_name = strategy_name
        self.signal_func = signal_func
        self.indicator_func = indicator_func
        self.ticker = ticker

    def backtest_func(
        self,
        df: pd.DataFrame,
        is_training: bool = True
    ) -> Dict:
        """
        Ejecutar backtest de la estrategia.

        Args:
            df: DataFrame con OHLCV
            is_training: Si es período de entrenamiento (para logging)

        Returns:
            Dict con métricas de backtesting
        """
        from .backtest_engine import BacktestEngine

        df = df.copy()

        # Normalizar nombres de columnas a minúsculas (estrategias usan minúsculas)
        df_lowercase = df.copy()
        df_lowercase.columns = df_lowercase.columns.str.lower()

        # Calcular indicadores si existen (con columnas minúsculas)
        try:
            if self.indicator_func:
                df_lowercase = self.indicator_func(df_lowercase)
        except Exception as e:
            logger.debug(f"Erro calculando indicadores: {e}")
            # Continuar sin indicadores

        # Generar señales (con columnas minúsculas)
        signals = []
        for i in range(len(df_lowercase)):
            period_df = df_lowercase.iloc[:i+1].copy()

            # Intentar con dos argumentos (df, ticker) primero, luego con uno solo
            signal_str = "HOLD"
            try:
                signal_str = self.signal_func(period_df, self.ticker)
            except TypeError:
                try:
                    signal_str = self.signal_func(period_df)
                except Exception as e:
                    logger.debug(f"Error en signal_func: {type(e).__name__}: {str(e)[:40]}")
                    signal_str = "HOLD"

            if signal_str == "BUY":
                signals.append(1)
            elif signal_str == "SELL":
                signals.append(-1)
            else:  # HOLD o cualquier otra cosa
                signals.append(0)

        # Usar DF original con columnas capitalizadas para el backtest engine
        # (engine espera Close, Open, etc)
        close_prices = df["Close"].values if "Close" in df.columns else df["close"].values

        # Ejecutar backtest
        engine = BacktestEngine(
            initial_capital=100000,
            position_size_pct=1.0,
            asset_type="equities"
        )

        backtest_signals = {
            "signal": np.array(signals),
            "entry_prices": close_prices,
        }

        metrics = engine.run(
            df,
            backtest_signals,
            regime=None,
            symbol=self.strategy_name
        )

        return metrics


# ============================================================
# Cargar Estrategias Dinámicamente
# ============================================================

def load_strategy_from_bot(strategy_name: str, ticker: str = "SPY") -> Optional[StrategyWrapper]:
    """
    Cargar una estrategia desde Bot/bot_*_strategy.py.

    Args:
        strategy_name: Nombre de la estrategia (ej: "bollinger", "macd_bb")
        ticker: Símbolo para estrategias que lo requieren

    Returns:
        StrategyWrapper o None si no se encuentra
    """
    import sys
    from pathlib import Path

    # Ruta a Bot/
    bot_path = Path(__file__).parent.parent.parent / "Bot"

    # Buscar archivo
    strategy_file = bot_path / f"bot_{strategy_name}_strategy.py"
    if not strategy_file.exists():
        logger.warning(f"⚠️ Archivo no encontrado: {strategy_file}")
        return None

    # Cargar módulo dinámicamente
    spec = __import__("importlib.util").util.spec_from_file_location(
        f"bot_{strategy_name}_strategy",
        strategy_file
    )
    module = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Obtener funciones
    detect_signal = getattr(module, "detect_signal", None)
    calculate_indicators = getattr(module, f"calculate_{strategy_name.replace('_', '')}", None)

    if not detect_signal:
        logger.error(f"❌ {strategy_name}: No tiene función detect_signal")
        return None

    logger.info(f"✓ Cargada estrategia: {strategy_name}")

    return StrategyWrapper(
        strategy_name=strategy_name.upper(),
        signal_func=detect_signal,
        indicator_func=calculate_indicators,
        ticker=ticker
    )


def load_all_strategies() -> Dict[str, StrategyWrapper]:
    """
    Cargar todas las 26 estrategias de Bot/.

    Returns:
        Dict[strategy_name] = StrategyWrapper
    """
    strategy_names = [
        "anchor_vwap",
        "bollinger",
        "ema_tsi",
        "gap",
        "ha_cloud",
        "hh_ll",
        "macd_bb",
        "obv",
        "opening_drive",
        "orb",
        "rsi_sma200",
        "rvol_breakout",
        "squeeze",
        "stop_run",
        "supertrend",
        "tick_trin_fade",
        "tsv",
        "vela_pivot",
        "vix_correlation",
        "vix_mean_reversion",
        "vix_squeeze",
        "volume_reversal_bar",
        "vrp",
        "vw_macd",
        "vwap_rsi",
        "vwap_zscore",
    ]

    strategies = {}
    for name in strategy_names:
        try:
            wrapper = load_strategy_from_bot(name)
            if wrapper:
                strategies[name] = wrapper
        except Exception as e:
            logger.error(f"❌ Error cargando {name}: {e}")

    logger.info(f"✓ {len(strategies)} estrategias cargadas de un total de {len(strategy_names)}")

    return strategies


# ============================================================
# Batch Backtesting
# ============================================================

def run_batch_backtests(
    df: pd.DataFrame,
    strategies: Dict[str, StrategyWrapper],
    windows: List,  # WFWindow objects
    symbol: str = "SPY"
) -> Dict:
    """
    Ejecutar backtests de múltiples estrategias en múltiples ventanas.

    Args:
        df: DataFrame con OHLCV
        strategies: Dict de estrategias
        windows: Ventanas walk-forward
        symbol: Símbolo para logging

    Returns:
        {
            strategy_name: {
                window_id: metrics,
                ...
            },
            ...
        }
    """
    from .walk_forward import WalkForwardValidator

    wf = WalkForwardValidator()
    results = {}

    for strategy_name, wrapper in strategies.items():
        logger.info(f"\nEjecutando: {strategy_name}")
        strategy_results = wf.run_backtests(
            df,
            wrapper.backtest_func,
            windows,
            symbol=f"{symbol}_{strategy_name}"
        )

        results[strategy_name] = strategy_results

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test: cargar todas las estrategias
    print("Cargando estrategias...")
    strategies = load_all_strategies()
    print(f"✓ {len(strategies)} estrategias cargadas")

    for name in strategies.keys():
        print(f"  - {name}")
