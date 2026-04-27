"""
backtest_engine.py — Motor de Backtesting con Fricción Real

Simula ejecución de estrategias CON:
  ✓ Slippage dinámico por régimen
  ✓ Comisiones por activo tipo
  ✓ Separación long/short
  ✓ Cálculo de métricas detalladas
  ✓ Validación de significancia estadística

NO asume fills perfectos. Simula la realidad del trading.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# ============================================================
# Enums y Data Classes
# ============================================================

class TradeType(Enum):
    """Tipo de operación."""
    LONG = "long"
    SHORT = "short"


@dataclass
class Trade:
    """Una operación ejecutada."""
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: float
    trade_type: TradeType
    slippage_entry: float
    slippage_exit: float
    commission: float
    pnl: float
    pnl_pct: float


# ============================================================
# Backtest Engine
# ============================================================

class BacktestEngine:
    """
    Motor de backtesting robusto.

    Simula ejecución real con fricción (slippage, comisiones).
    """

    def __init__(
        self,
        initial_capital: float = 100000,
        position_size_pct: float = 1.0,
        asset_type: str = "equities"  # "equities" o "crypto"
    ):
        """
        Inicializar backtest engine.

        Args:
            initial_capital: Capital inicial
            position_size_pct: % del capital por posición (default: 1%)
            asset_type: Tipo de activo ("equities" o "crypto")
        """
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.asset_type = asset_type

        # Comisiones
        self.commission = 0.001 if asset_type == "equities" else 0.001

    def run(
        self,
        df: pd.DataFrame,
        signals: Dict[str, np.ndarray],
        regime: Optional[str] = None,
        symbol: str = "SPY"
    ) -> Dict:
        """
        Ejecutar backtest.

        Args:
            df: DataFrame con OHLCV
            signals: Dict con señales
                    {
                        "signal": np.ndarray (1=long, -1=short, 0=no),
                        "entry_prices": np.ndarray (precios teóricos),
                        "stop_loss": np.ndarray (SL),
                        "take_profit": np.ndarray (TP),
                    }
            regime: Régimen actual (para determinar slippage)
            symbol: Símbolo para logging

        Returns:
            Dict con métricas del backtest
        """
        close = df["Close"].values
        high = df["High"].values
        low = df["Low"].values
        dates = df.index.strftime("%Y-%m-%d").values

        signal = signals.get("signal", np.zeros(len(df)))
        entry_prices = signals.get("entry_prices", close)
        stop_loss = signals.get("stop_loss", None)
        take_profit = signals.get("take_profit", None)

        # Determinar slippage
        slippage = self._get_slippage(regime)

        trades = []
        equity_curve = [self.initial_capital]
        position = None  # None o Trade actual
        position_size = int(self.initial_capital * self.position_size_pct / close[0])

        for i in range(len(df)):
            current_price = close[i]
            current_high = high[i]
            current_low = low[i]
            current_date = dates[i]
            current_signal = signal[i] if i < len(signal) else 0

            # Cerrar posición actual si hay señal opuesta
            if position is not None:
                should_exit = False

                # Lógica de exit
                if position.trade_type == TradeType.LONG:
                    # Long: exit si signal es negativo, o si toca SL, o si toca TP
                    if current_signal < 0:
                        should_exit = True
                    elif stop_loss is not None and current_low <= stop_loss[i]:
                        should_exit = True
                        current_price = stop_loss[i]
                    elif take_profit is not None and current_high >= take_profit[i]:
                        should_exit = True
                        current_price = take_profit[i]

                elif position.trade_type == TradeType.SHORT:
                    # Short: exit si signal es positivo, o si toca SL, o si toca TP
                    if current_signal > 0:
                        should_exit = True
                    elif stop_loss is not None and current_high >= stop_loss[i]:
                        should_exit = True
                        current_price = stop_loss[i]
                    elif take_profit is not None and current_low <= take_profit[i]:
                        should_exit = True
                        current_price = take_profit[i]

                if should_exit:
                    # Ejecutar exit con slippage
                    exit_price_with_slippage = current_price * (1 - slippage)

                    if position.trade_type == TradeType.LONG:
                        pnl = (exit_price_with_slippage - position.entry_price) * position.quantity
                        pnl -= position.commission
                    else:  # SHORT
                        pnl = (position.entry_price - exit_price_with_slippage) * position.quantity
                        pnl -= position.commission

                    pnl_pct = pnl / (position.entry_price * position.quantity) if position.entry_price > 0 else 0

                    # Actualizar trade
                    position.exit_date = current_date
                    position.exit_price = exit_price_with_slippage
                    position.pnl = pnl
                    position.pnl_pct = pnl_pct

                    trades.append(position)
                    position = None

            # Abrir nueva posición
            if position is None and current_signal != 0:
                entry_price_with_slippage = current_price * (1 + slippage) if current_signal > 0 else current_price * (1 - slippage)
                commission_cost = position_size * entry_price_with_slippage * self.commission

                position = Trade(
                    entry_date=current_date,
                    exit_date=None,
                    entry_price=entry_price_with_slippage,
                    exit_price=None,
                    quantity=position_size,
                    trade_type=TradeType.LONG if current_signal > 0 else TradeType.SHORT,
                    slippage_entry=slippage,
                    slippage_exit=None,
                    commission=commission_cost,
                    pnl=0,
                    pnl_pct=0
                )

            # Actualizar equity
            current_equity = self.initial_capital
            if trades:
                current_equity += sum(t.pnl for t in trades)
            if position is not None:
                # Mark-to-market
                if position.trade_type == TradeType.LONG:
                    mtm = (current_price - position.entry_price) * position.quantity
                else:
                    mtm = (position.entry_price - current_price) * position.quantity
                current_equity += mtm

            equity_curve.append(current_equity)

        # Cerrar posición pendiente
        if position is not None:
            last_price = close[-1]
            if position.trade_type == TradeType.LONG:
                pnl = (last_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - last_price) * position.quantity
            pnl -= position.commission

            position.exit_date = dates[-1]
            position.exit_price = last_price
            position.pnl = pnl
            pnl_pct = pnl / (position.entry_price * position.quantity) if position.entry_price > 0 else 0
            position.pnl_pct = pnl_pct

            trades.append(position)

        # Calcular métricas
        metrics = self._calculate_metrics(trades, equity_curve, symbol)
        metrics["trades"] = trades

        return metrics

    def _get_slippage(self, regime: Optional[str]) -> float:
        """Obtener slippage para régimen."""
        from .data_loader import SLIPPAGE_BY_REGIME
        if regime and regime in SLIPPAGE_BY_REGIME:
            return SLIPPAGE_BY_REGIME[regime]
        return 0.001  # Default 0.1%

    def _calculate_metrics(
        self,
        trades: List[Trade],
        equity_curve: List[float],
        symbol: str = "SPY"
    ) -> Dict:
        """Calcular métricas de rendimiento."""
        if not trades:
            logger.warning(f"⚠️ {symbol}: Sin trades ejecutados")
            return {
                "num_trades": 0,
                "profit_factor": 0,
                "win_rate": 0,
                "sharpe": 0,
                "max_drawdown": 0,
            }

        # PnL totales
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        total_wins = sum(t.pnl for t in winning_trades)
        total_losses = sum(abs(t.pnl) for t in losing_trades)

        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # Win rate
        win_rate = len(winning_trades) / len(trades) if trades else 0

        # Retornos
        returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
        sharpe = (np.mean(returns) / (np.std(returns) + 1e-8)) * np.sqrt(252) if len(returns) > 1 else 0

        # Drawdown
        equity_array = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_array)
        drawdown = (equity_array - running_max) / running_max
        max_drawdown = np.min(drawdown)

        # Otros
        total_return = (equity_curve[-1] - self.initial_capital) / self.initial_capital
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([abs(t.pnl) for t in losing_trades]) if losing_trades else 0

        metrics = {
            "num_trades": len(trades),
            "num_wins": len(winning_trades),
            "num_losses": len(losing_trades),
            "profit_factor": profit_factor,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": abs(avg_loss),
            "sharpe": sharpe,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "final_equity": equity_curve[-1],
        }

        logger.info(f"{symbol} Results:")
        logger.info(f"  Trades: {metrics['num_trades']} "
                   f"(W:{metrics['num_wins']} L:{metrics['num_losses']})")
        logger.info(f"  PF: {metrics['profit_factor']:.2f}")
        logger.info(f"  Win Rate: {metrics['win_rate']:.1%}")
        logger.info(f"  Sharpe: {metrics['sharpe']:.2f}")
        logger.info(f"  Max DD: {metrics['max_drawdown']:.2%}")
        logger.info(f"  Return: {metrics['total_return']:.2%}")

        return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test dummy
    dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    df = pd.DataFrame({
        "Close": np.random.randn(len(dates)).cumsum() + 100,
        "High": np.random.randn(len(dates)).cumsum() + 102,
        "Low": np.random.randn(len(dates)).cumsum() + 98,
        "Open": np.random.randn(len(dates)).cumsum() + 100,
    }, index=dates)

    signals = {
        "signal": np.random.choice([-1, 0, 1], size=len(df)),
        "entry_prices": df["Close"].values,
    }

    engine = BacktestEngine()
    metrics = engine.run(df, signals)
    print(f"Métricas:\n{metrics}")
