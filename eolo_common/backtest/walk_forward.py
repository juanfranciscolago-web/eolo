"""
walk_forward.py — Motor de Validación Walk-Forward

Implementa la validación robusta de estrategias:
  - Train: 12 meses
  - Test: 3 meses (out-of-sample)
  - Rolling: desplazar ventanas secuencialmente

Esto simula el aprendizaje continuo de una estrategia en el mundo real.

Estructuración:
  - 2017-2025: ~8 años → ~13-16 ventanas walk-forward
  - Detecta degradación (in-sample vs out-of-sample)
  - Identifica overfitting automáticamente
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


# ============================================================
# Data Classes
# ============================================================

@dataclass
class WFWindow:
    """Una ventana de walk-forward."""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    description: str = ""

    def __str__(self) -> str:
        return (f"WF{self.window_id}: "
                f"Train [{self.train_start} → {self.train_end}] | "
                f"Test [{self.test_start} → {self.test_end}]")


# ============================================================
# Walk-Forward Engine
# ============================================================

class WalkForwardValidator:
    """
    Motor de validación walk-forward para backtesting robusto.

    Características:
      - Ventanas rolling (train 12m, test 3m)
      - Sin lookahead bias (test siempre después de train)
      - Métricas de degradación (OOS vs IS)
      - Análisis de significancia estadística
    """

    def __init__(
        self,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 3
    ):
        """
        Inicializar walk-forward validator.

        Args:
            train_months: Meses de entrenamiento (default: 12)
            test_months: Meses de testing (default: 3)
            step_months: Desplazamiento entre ventanas (default: 3 = sin overlap)
        """
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months

    def generate_windows(
        self,
        start_date: str = "2017-01-01",
        end_date: Optional[str] = None
    ) -> List[WFWindow]:
        """
        Generar ventanas walk-forward.

        Args:
            start_date: Fecha inicio (default: 2017-01-01)
            end_date: Fecha fin (default: hoy)

        Returns:
            Lista de ventanas WFWindow
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date) if end_date else pd.Timestamp.now()

        windows = []
        window_id = 0
        train_start = start

        while train_start < end:
            # Período de entrenamiento
            train_end = train_start + pd.DateOffset(months=self.train_months)

            # Período de testing (inmediatamente después)
            test_start = train_end
            test_end = test_start + pd.DateOffset(months=self.test_months)

            # Si el test sale de rango, truncar
            if test_end > end:
                test_end = end

            # Solo agregar si hay test period válido
            if test_end > test_start:
                window = WFWindow(
                    window_id=window_id,
                    train_start=train_start.strftime("%Y-%m-%d"),
                    train_end=train_end.strftime("%Y-%m-%d"),
                    test_start=test_start.strftime("%Y-%m-%d"),
                    test_end=test_end.strftime("%Y-%m-%d"),
                    description=f"Window {window_id} ({train_start.year})"
                )
                windows.append(window)
                window_id += 1

            # Desplazar inicio de próxima ventana
            train_start += pd.DateOffset(months=self.step_months)

        logger.info(f"✓ Generadas {len(windows)} ventanas walk-forward:")
        for w in windows:
            logger.info(f"  {w}")

        return windows

    def split_window(
        self,
        df: pd.DataFrame,
        window: WFWindow
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Dividir DataFrame según ventana.

        Args:
            df: DataFrame con OHLCV
            window: WFWindow a aplicar

        Returns:
            (train_df, test_df)
        """
        train_df = df.loc[window.train_start:window.train_end]
        test_df = df.loc[window.test_start:window.test_end]

        if train_df.empty or test_df.empty:
            logger.warning(f"⚠️ Ventana {window.window_id}: "
                          f"Train={len(train_df)} velas, Test={len(test_df)} velas")

        return train_df, test_df

    def run_backtests(
        self,
        df: pd.DataFrame,
        strategy_func,
        windows: Optional[List[WFWindow]] = None,
        symbol: str = "SPY"
    ) -> Dict[int, Dict]:
        """
        Ejecutar backtests en todas las ventanas.

        Args:
            df: DataFrame con OHLCV
            strategy_func: Función que ejecuta la estrategia.
                          Signature: strategy_func(train_df, test_df) → Dict[metrics]
            windows: Ventanas WF (si None, generar automáticamente)
            symbol: Símbolo para logging

        Returns:
            {
                window_id: {
                    "window": WFWindow,
                    "train_metrics": Dict,
                    "test_metrics": Dict,
                    "degradation": float,  # (train_pf - test_pf) / train_pf
                    "is_overfitted": bool,  # degradation > 0.30
                }
            }
        """
        if windows is None:
            windows = self.generate_windows(df.index.min().strftime("%Y-%m-%d"),
                                           df.index.max().strftime("%Y-%m-%d"))

        results = {}

        for window in windows:
            logger.info(f"\nEjecutando: {window}")

            train_df, test_df = self.split_window(df, window)

            if len(train_df) < 100 or len(test_df) < 20:
                logger.warning(f"  ⚠️ Insuficientes datos. Saltear.")
                continue

            try:
                # Ejecutar estrategia
                train_metrics = strategy_func(train_df, is_training=True)
                test_metrics = strategy_func(test_df, is_training=False)

                # Calcular degradación
                train_pf = train_metrics.get("profit_factor", 0)
                test_pf = test_metrics.get("profit_factor", 0)

                if train_pf > 0:
                    degradation = (train_pf - test_pf) / train_pf
                else:
                    degradation = 0

                is_overfitted = degradation > 0.30

                results[window.window_id] = {
                    "window": window,
                    "train_metrics": train_metrics,
                    "test_metrics": test_metrics,
                    "degradation": degradation,
                    "is_overfitted": is_overfitted,
                }

                # Log
                logger.info(f"  Train PF: {train_pf:.2f} | Test PF: {test_pf:.2f} | "
                           f"Degradation: {degradation:.1%} "
                           f"{'❌ OVERFITTED' if is_overfitted else '✓ OK'}")

            except Exception as e:
                logger.error(f"  ❌ Error ejecutando estrategia: {e}")

        return results

    def aggregate_results(
        self,
        wf_results: Dict[int, Dict]
    ) -> Dict:
        """
        Agregar resultados de todas las ventanas.

        Args:
            wf_results: Salida de run_backtests()

        Returns:
            {
                "total_windows": int,
                "avg_degradation": float,
                "overfitted_windows": int,
                "test_pf_mean": float,
                "test_pf_std": float,
                "test_sharpe_mean": float,
                "min_test_trades": int,
                "metrics_by_window": [...],
                "verdict": "robust" | "overfitted" | "inconsistent"
            }
        """
        if not wf_results:
            return {}

        test_pfs = []
        test_sharpes = []
        degradations = []
        min_trades = float("inf")
        overfitted_count = 0

        for window_id, result in wf_results.items():
            test_metrics = result["test_metrics"]

            pf = test_metrics.get("profit_factor", 0)
            sharpe = test_metrics.get("sharpe", 0)
            num_trades = test_metrics.get("num_trades", 0)

            if pf > 0:
                test_pfs.append(pf)
            if sharpe is not None:
                test_sharpes.append(sharpe)

            degradations.append(result["degradation"])
            min_trades = min(min_trades, num_trades)

            if result["is_overfitted"]:
                overfitted_count += 1

        # Estadísticas
        test_pf_mean = np.mean(test_pfs) if test_pfs else 0
        test_pf_std = np.std(test_pfs) if len(test_pfs) > 1 else 0
        test_sharpe_mean = np.mean(test_sharpes) if test_sharpes else 0
        avg_degradation = np.mean(degradations)

        # Veredicto
        if overfitted_count >= len(wf_results) * 0.5:
            verdict = "overfitted"  # Más del 50% de ventanas overfitted
        elif test_pf_std > test_pf_mean * 0.5:
            verdict = "inconsistent"  # Std > 50% de la media
        else:
            verdict = "robust"

        results = {
            "total_windows": len(wf_results),
            "avg_degradation": avg_degradation,
            "overfitted_windows": overfitted_count,
            "test_pf_mean": test_pf_mean,
            "test_pf_std": test_pf_std,
            "test_sharpe_mean": test_sharpe_mean,
            "min_test_trades": min_trades if min_trades != float("inf") else 0,
            "verdict": verdict,
        }

        logger.info(f"\n" + "=" * 60)
        logger.info(f"RESUMEN WALK-FORWARD:")
        logger.info(f"  Ventanas totales: {results['total_windows']}")
        logger.info(f"  PF promedio (OOS): {results['test_pf_mean']:.2f} (±{results['test_pf_std']:.2f})")
        logger.info(f"  Sharpe promedio (OOS): {results['test_sharpe_mean']:.2f}")
        logger.info(f"  Degradación promedio: {results['avg_degradation']:.1%}")
        logger.info(f"  Ventanas overfitted: {results['overfitted_windows']}/{results['total_windows']}")
        logger.info(f"  Veredicto: {results['verdict'].upper()}")
        logger.info(f"=" * 60 + "\n")

        return results


# ============================================================
# Funciones Auxiliares
# ============================================================

def create_standard_windows(
    start_date: str = "2017-01-01",
    end_date: Optional[str] = None
) -> List[WFWindow]:
    """Convenience function para generar ventanas estándar."""
    wf = WalkForwardValidator()
    return wf.generate_windows(start_date, end_date)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test: generar ventanas
    wf = WalkForwardValidator(train_months=12, test_months=3)
    windows = wf.generate_windows()

    print(f"\n✓ Generadas {len(windows)} ventanas")
    print(f"Primera: {windows[0]}")
    print(f"Última: {windows[-1]}")
