# ============================================================
#  EOLO — Walk-Forward Calibrator
#
#  Ref: nueva infraestructura v2 (2026-04-27)
#
#  Qué hace:
#    Divide la historia del precio en ventanas solapadas:
#      [train_window días] → optimiza parámetros
#      [test_window días]  → valida out-of-sample
#
#    Para cada parámetro candidato, evalúa el Profit Factor
#    sobre la ventana de entrenamiento y retiene el mejor.
#    Luego corre el test para verificar que no hay overfit.
#
#    Los resultados (parámetros óptimos + métricas) se guardan
#    en Firestore bajo:
#      eolo_walk_forward / {strategy_name}_{ticker} / {run_id}
#
#  Uso:
#    from eolo_common.walk_forward import run_walk_forward
#
#    best = run_walk_forward(
#        strategy_fn=my_strategy,
#        param_grid={"rsi_period": [10, 14, 20]},
#        daily_df=df,
#        ticker="SPY",
#        strategy_name="MY_STRAT",
#    )
#    # best = {"rsi_period": 14, "oos_profit_factor": 1.82, ...}
#
#  Dependencias: pandas, numpy, loguru, google-cloud-firestore
# ============================================================
from __future__ import annotations

import itertools
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger

# ── Config ────────────────────────────────────────────────
WF_TRAIN_DAYS = int(os.environ.get("WF_TRAIN_DAYS",  "180"))   # días de entrenamiento
WF_TEST_DAYS  = int(os.environ.get("WF_TEST_DAYS",   "60"))    # días de test OOS
WF_STEP_DAYS  = int(os.environ.get("WF_STEP_DAYS",   "30"))    # paso de ventana
WF_MIN_TRADES = int(os.environ.get("WF_MIN_TRADES",  "10"))    # trades mínimos para validar
GCP_PROJECT   = os.environ.get("GCP_PROJECT_ID", "eolo-schwab-agent")
FIRESTORE_COL = "eolo_walk_forward"


@dataclass
class WFResult:
    strategy_name:   str
    ticker:          str
    best_params:     dict
    train_pf:        float   # Profit Factor en entrenamiento
    oos_pf:          float   # Profit Factor out-of-sample
    oos_win_rate:    float
    oos_trades:      int
    run_id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:       str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    windows_tested:  int = 0
    all_oos_pfs:     list[float] = field(default_factory=list)


# ── Backtester simple ─────────────────────────────────────

def _backtest_simple(
    strategy_fn: Callable[[pd.DataFrame, dict], list[float]],
    df: pd.DataFrame,
    params: dict,
) -> tuple[float, float, int]:
    """
    Corre la estrategia sobre df con params.
    strategy_fn debe retornar una lista de PnL por trade (en %).
    Retorna (profit_factor, win_rate, n_trades).
    """
    try:
        pnls = strategy_fn(df, params)
    except Exception as e:
        logger.debug(f"[WF] backtest error params={params}: {e}")
        return 0.0, 0.0, 0

    if not pnls or len(pnls) < 1:
        return 0.0, 0.0, 0

    arr = np.array(pnls, dtype=float)
    wins  = arr[arr > 0]
    losses = arr[arr < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss   = float(abs(losses.sum())) if len(losses) else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0.0 else (999.0 if gross_profit > 0 else 0.0)
    wr = len(wins) / len(arr) if len(arr) > 0 else 0.0
    return round(pf, 4), round(wr, 4), len(arr)


# ── Walk-Forward Engine ───────────────────────────────────

class WalkForwardCalibrator:
    """
    Optimizador walk-forward para estrategias de trading de EOLO.

    Parámetros:
        strategy_fn   : función (df: DataFrame, params: dict) → list[float pnl]
        param_grid    : dict {nombre_param: [val1, val2, ...]}
        train_days    : días de entrenamiento por ventana
        test_days     : días de test OOS por ventana
        step_days     : paso de ventana en días
        min_trades    : trades mínimos para considerar resultado válido
    """

    def __init__(
        self,
        strategy_fn: Callable,
        param_grid: dict[str, list],
        train_days:  int = WF_TRAIN_DAYS,
        test_days:   int = WF_TEST_DAYS,
        step_days:   int = WF_STEP_DAYS,
        min_trades:  int = WF_MIN_TRADES,
    ):
        self.strategy_fn = strategy_fn
        self.param_grid  = param_grid
        self.train_days  = train_days
        self.test_days   = test_days
        self.step_days   = step_days
        self.min_trades  = min_trades

        # Expandir grid cartesiano
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        self._param_combos: list[dict] = [
            dict(zip(keys, combo))
            for combo in itertools.product(*values)
        ]
        logger.debug(f"[WF] {len(self._param_combos)} combinaciones de parámetros")

    def run(
        self,
        daily_df: pd.DataFrame,
        ticker: str,
        strategy_name: str,
        save_firestore: bool = True,
    ) -> WFResult:
        """
        Ejecuta walk-forward sobre daily_df.
        Retorna WFResult con los mejores parámetros promedio de las ventanas.
        """
        df = daily_df.reset_index(drop=True)
        total_rows = len(df)
        window_size = self.train_days + self.test_days

        if total_rows < window_size:
            logger.warning(
                f"[WF] {ticker}/{strategy_name}: datos insuficientes "
                f"({total_rows} < {window_size} días)"
            )
            return WFResult(
                strategy_name=strategy_name, ticker=ticker,
                best_params=self._param_combos[0] if self._param_combos else {},
                train_pf=0.0, oos_pf=0.0, oos_win_rate=0.0, oos_trades=0,
            )

        # Acumular resultados OOS por parámetro
        param_oos_pfs: dict[str, list[float]] = {
            str(p): [] for p in self._param_combos
        }
        windows_tested = 0
        all_oos_pfs_best: list[float] = []

        start = 0
        while start + window_size <= total_rows:
            train_df = df.iloc[start:start + self.train_days]
            test_df  = df.iloc[start + self.train_days:start + window_size]

            # 1. Encontrar mejor params en train
            best_pf_train = -1.0
            best_params_train = self._param_combos[0]

            for params in self._param_combos:
                pf, _, n = _backtest_simple(self.strategy_fn, train_df, params)
                if n >= self.min_trades and pf > best_pf_train:
                    best_pf_train = pf
                    best_params_train = params

            # 2. Evaluar mejores params en test OOS
            oos_pf, oos_wr, oos_n = _backtest_simple(
                self.strategy_fn, test_df, best_params_train
            )
            key = str(best_params_train)
            if key in param_oos_pfs:
                param_oos_pfs[key].append(oos_pf)
            all_oos_pfs_best.append(oos_pf)

            logger.debug(
                f"[WF] ventana {windows_tested+1}: "
                f"train rows {start}-{start+self.train_days} "
                f"params={best_params_train} train_PF={best_pf_train:.2f} "
                f"OOS_PF={oos_pf:.2f} n={oos_n}"
            )
            windows_tested += 1
            start += self.step_days

        if not all_oos_pfs_best:
            logger.warning(f"[WF] {ticker}/{strategy_name}: sin ventanas válidas")
            return WFResult(
                strategy_name=strategy_name, ticker=ticker,
                best_params=self._param_combos[0] if self._param_combos else {},
                train_pf=0.0, oos_pf=0.0, oos_win_rate=0.0, oos_trades=0,
                windows_tested=windows_tested,
            )

        # Parámetros que más veces ganaron en OOS
        best_key = max(param_oos_pfs, key=lambda k: np.mean(param_oos_pfs[k]) if param_oos_pfs[k] else 0.0)
        best_params_final = next(
            (p for p in self._param_combos if str(p) == best_key),
            self._param_combos[0],
        )

        # Métricas finales: evaluar best_params sobre todos los datos OOS disponibles
        oos_combined = []
        start = 0
        while start + window_size <= total_rows:
            test_df = df.iloc[start + self.train_days:start + window_size]
            try:
                pnls = self.strategy_fn(test_df, best_params_final)
                if pnls:
                    oos_combined.extend(pnls)
            except Exception:
                pass
            start += self.step_days

        oos_arr = np.array(oos_combined, dtype=float) if oos_combined else np.array([])
        if len(oos_arr) > 0:
            wins_   = oos_arr[oos_arr > 0]
            losses_ = oos_arr[oos_arr < 0]
            gp = float(wins_.sum()) if len(wins_) else 0.0
            gl = float(abs(losses_.sum())) if len(losses_) else 0.0
            final_oos_pf = round(gp / gl, 4) if gl > 0 else (999.0 if gp > 0 else 0.0)
            final_oos_wr = round(len(wins_) / len(oos_arr), 4)
            final_oos_n  = len(oos_arr)
        else:
            final_oos_pf, final_oos_wr, final_oos_n = 0.0, 0.0, 0

        # Train PF promedio de todas las ventanas (informativo)
        train_pf_avg = float(np.mean([pf for pf in all_oos_pfs_best if pf > 0])) if all_oos_pfs_best else 0.0

        result = WFResult(
            strategy_name=strategy_name,
            ticker=ticker,
            best_params=best_params_final,
            train_pf=round(train_pf_avg, 4),
            oos_pf=final_oos_pf,
            oos_win_rate=final_oos_wr,
            oos_trades=final_oos_n,
            windows_tested=windows_tested,
            all_oos_pfs=all_oos_pfs_best,
        )

        logger.info(
            f"[WF] {ticker}/{strategy_name} DONE | "
            f"best_params={best_params_final} | "
            f"OOS PF={final_oos_pf:.2f} WR={final_oos_wr:.0%} n={final_oos_n} | "
            f"ventanas={windows_tested}"
        )

        if save_firestore:
            _save_to_firestore(result)

        return result


# ── Firestore persistence ─────────────────────────────────

def _save_to_firestore(result: WFResult) -> None:
    """Guarda el resultado en Firestore (fail-soft)."""
    try:
        from google.cloud import firestore as _fs
        db = _fs.Client(project=GCP_PROJECT)
        doc_id = f"{result.strategy_name}_{result.ticker}"
        data = {
            **asdict(result),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        db.collection(FIRESTORE_COL).document(doc_id).set(data, merge=True)
        logger.info(f"[WF] Resultado guardado en Firestore: {FIRESTORE_COL}/{doc_id}")
    except Exception as e:
        logger.warning(f"[WF] No se pudo guardar en Firestore: {e}")


# ── Convenience wrapper ───────────────────────────────────

def run_walk_forward(
    strategy_fn: Callable,
    param_grid: dict[str, list],
    daily_df: pd.DataFrame,
    ticker: str,
    strategy_name: str,
    train_days:    int = WF_TRAIN_DAYS,
    test_days:     int = WF_TEST_DAYS,
    step_days:     int = WF_STEP_DAYS,
    min_trades:    int = WF_MIN_TRADES,
    save_firestore: bool = True,
) -> WFResult:
    """
    Shortcut para correr walk-forward sin instanciar el calibrador.

    Ejemplo:
        def my_strategy_fn(df, params):
            # ... lógica de backtest ...
            return [pnl1, pnl2, ...]  # lista de PnL % por trade

        result = run_walk_forward(
            strategy_fn=my_strategy_fn,
            param_grid={"rsi_period": [10, 14, 20], "rsi_threshold": [55, 60, 65]},
            daily_df=spy_df,
            ticker="SPY",
            strategy_name="MY_STRAT",
        )
        print(result.best_params, result.oos_pf)
    """
    cal = WalkForwardCalibrator(
        strategy_fn=strategy_fn,
        param_grid=param_grid,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        min_trades=min_trades,
    )
    return cal.run(daily_df, ticker=ticker, strategy_name=strategy_name,
                   save_firestore=save_firestore)
