"""
activation_rules.py — Sistema de Scoring 0-100 para Decisiones de Activación

10 reglas ponderadas que transforman métricas de backtesting en score 0-100:

1. PF OOS (25%)          — Profit factor fuera de muestra
2. Num Trades (15%)      — Significancia estadística (mínimo trades)
3. Degradación (20%)     — In-sample vs out-of-sample (overfitting)
4. PF Régimen Actual (20%) — Desempeño en régimen presente
5. Sharpe OOS (10%)      — Retorno ajustado por riesgo
6. Max DD (10%)          — Drawdown máximo tolerable
7. Win Rate (5%)         — Porcentaje de trades ganadores
8. Equity Smoothness (5%) — Suavidad de curva de equity
9. Racha Pérdidas (5%)   — Máxima pérdida consecutiva tolerable
10. Multi-Régimen (5%)   — Desempeño consistente en múltiples regímenes

Veredictos:
  ✅ ACTIVAR      (80-100)
  ⚠️  CONSIDERAR  (60-79)
  ❌ RECHAZAR     (<60)

Con vetos automáticos:
  - PF < 1.0      → RECHAZAR (no rentable)
  - Trades < 20   → RECHAZAR (no significante)
  - Degrad > 50%  → RECHAZAR (overfitted)
"""

import numpy as np
from typing import Dict, Any


class ActivationScorer:
    """Calculador de Score 0-100 para estrategias."""

    # Pesos de las 10 reglas (deben sumar 100%)
    WEIGHTS = {
        "pf_oos": 25,           # Regla 1
        "num_trades": 15,       # Regla 2
        "degradation": 20,      # Regla 3
        "pf_regime": 20,        # Regla 4
        "sharpe_oos": 10,       # Regla 5
        "max_dd": 10,           # Regla 6
        "win_rate": 5,          # Regla 7
        "equity_smoothness": 5, # Regla 8
        "drawdown_streak": 5,   # Regla 9
        "multi_regime": 5,      # Regla 10
    }

    # Vetos automáticos (rechazo inmediato)
    VETOS = {
        "pf_oos_min": 1.0,      # PF debe ser >= 1.0
        "num_trades_min": 20,   # Mínimo trades
        "degradation_max": 0.50, # Máximo degradación 50%
    }

    # Umbrales de desempeño por regla
    THRESHOLDS = {
        # Regla 1: PF OOS (0-100 scale)
        "pf_oos": {
            "excellent": 2.0,    # 90+ puntos
            "good": 1.5,         # 70+ puntos
            "fair": 1.2,         # 50+ puntos
            "poor": 1.0,         # 0 puntos (veto)
        },
        # Regla 2: Num Trades
        "num_trades": {
            "excellent": 200,    # 90+ puntos
            "good": 100,         # 70+ puntos
            "fair": 50,          # 50+ puntos
            "poor": 20,          # 0 puntos (veto)
        },
        # Regla 3: Degradación
        "degradation": {
            "excellent": 0.10,   # 90+ puntos (baja degrad)
            "good": 0.20,        # 70+ puntos
            "fair": 0.30,        # 50+ puntos
            "poor": 0.50,        # 0 puntos (veto)
        },
        # Regla 5: Sharpe OOS
        "sharpe_oos": {
            "excellent": 1.5,    # 90+ puntos
            "good": 1.0,         # 70+ puntos
            "fair": 0.5,         # 50+ puntos
            "poor": 0.0,         # 0 puntos
        },
        # Regla 6: Max DD
        "max_dd": {
            "excellent": -0.10,  # 90+ puntos (baja pérdida)
            "good": -0.20,       # 70+ puntos
            "fair": -0.30,       # 50+ puntos
            "poor": -0.50,       # 0 puntos
        },
    }

    @classmethod
    def calculate_score(
        cls,
        metrics: Dict[str, Any],
        num_windows: int = 28,
        regime_pf: float = None
    ) -> Dict[str, Any]:
        """
        Calcular score 0-100 para una estrategia.

        Args:
            metrics: Dict con pf_test_mean, sharpe_test_mean, avg_degradation, etc.
            num_windows: Número de ventanas backtested
            regime_pf: PF específico del régimen actual (opcional)

        Returns:
            {
                "score": 0-100,
                "verdict": "ACTIVAR" | "CONSIDERAR" | "RECHAZAR",
                "rule_scores": {nombre_regla: puntos},
                "vetos_triggered": [lista de vetos],
            }
        """
        rule_scores = {}
        vetos_triggered = []

        # === VETOS AUTOMÁTICOS ===
        pf_oos = metrics.get("pf_test_mean", 0)
        num_trades = metrics.get("num_trades", 0)
        degradation = metrics.get("avg_degradation", 1.0)

        if pf_oos < cls.VETOS["pf_oos_min"]:
            vetos_triggered.append(f"PF < {cls.VETOS['pf_oos_min']}")
        if num_trades < cls.VETOS["num_trades_min"]:
            vetos_triggered.append(f"Trades < {cls.VETOS['num_trades_min']}")
        if degradation > cls.VETOS["degradation_max"]:
            vetos_triggered.append(f"Degradación > {cls.VETOS['degradation_max']:.0%}")

        # Si hay vetos, rechazar inmediatamente
        if vetos_triggered:
            return {
                "score": 0,
                "verdict": "RECHAZAR",
                "rule_scores": {},
                "vetos_triggered": vetos_triggered,
            }

        # === REGLA 1: PF OOS (25%) ===
        rule_scores["pf_oos"] = cls._score_pf_oos(pf_oos)

        # === REGLA 2: Num Trades (15%) ===
        rule_scores["num_trades"] = cls._score_num_trades(num_trades)

        # === REGLA 3: Degradación (20%) ===
        rule_scores["degradation"] = cls._score_degradation(degradation)

        # === REGLA 4: PF Régimen Actual (20%) ===
        if regime_pf is not None:
            rule_scores["pf_regime"] = cls._score_pf_oos(regime_pf)
        else:
            rule_scores["pf_regime"] = rule_scores["pf_oos"]

        # === REGLA 5: Sharpe OOS (10%) ===
        sharpe = metrics.get("sharpe_test_mean", 0)
        rule_scores["sharpe_oos"] = cls._score_sharpe(sharpe)

        # === REGLA 6: Max DD (10%) ===
        max_dd = metrics.get("max_dd", -0.5)
        rule_scores["max_dd"] = cls._score_max_dd(max_dd)

        # === REGLA 7: Win Rate (5%) ===
        win_rate = metrics.get("win_rate", 0)
        rule_scores["win_rate"] = cls._score_win_rate(win_rate)

        # === REGLA 8: Equity Smoothness (5%) ===
        # Placeholder: se calcularía de equity curve
        rule_scores["equity_smoothness"] = 50

        # === REGLA 9: Racha Pérdidas (5%) ===
        # Placeholder: se calcularía de trade sequence
        rule_scores["drawdown_streak"] = 50

        # === REGLA 10: Multi-Régimen (5%) ===
        num_regimes_tested = metrics.get("num_regimes", 1)
        rule_scores["multi_regime"] = min(100, (num_regimes_tested / 7) * 100)

        # === CALCULAR SCORE PONDERADO ===
        total_score = 0
        for rule_name, weight in cls.WEIGHTS.items():
            rule_score = rule_scores.get(rule_name, 50)
            total_score += rule_score * (weight / 100.0)

        total_score = max(0, min(100, total_score))  # Clamp 0-100

        # === VEREDICTO ===
        if total_score >= 80:
            verdict = "ACTIVAR"
        elif total_score >= 60:
            verdict = "CONSIDERAR"
        else:
            verdict = "RECHAZAR"

        return {
            "score": float(total_score),
            "verdict": verdict,
            "rule_scores": rule_scores,
            "vetos_triggered": vetos_triggered,
        }

    @classmethod
    def _score_pf_oos(cls, pf: float) -> float:
        """Calificar PF OOS (Regla 1 y 4)."""
        if pf >= cls.THRESHOLDS["pf_oos"]["excellent"]:
            return 95
        elif pf >= cls.THRESHOLDS["pf_oos"]["good"]:
            return 70 + (pf - 1.5) * 50  # Interpolate 70-90
        elif pf >= cls.THRESHOLDS["pf_oos"]["fair"]:
            return 50 + (pf - 1.2) * 66.7  # Interpolate 50-70
        elif pf >= cls.THRESHOLDS["pf_oos"]["poor"]:
            return 20 + (pf - 1.0) * 30  # Interpolate 20-50
        else:
            return 0

    @classmethod
    def _score_num_trades(cls, num_trades: float) -> float:
        """Calificar Num Trades (Regla 2)."""
        if num_trades >= cls.THRESHOLDS["num_trades"]["excellent"]:
            return 95
        elif num_trades >= cls.THRESHOLDS["num_trades"]["good"]:
            return 70
        elif num_trades >= cls.THRESHOLDS["num_trades"]["fair"]:
            return 50
        elif num_trades >= cls.THRESHOLDS["num_trades"]["poor"]:
            return 20
        else:
            return 0

    @classmethod
    def _score_degradation(cls, degrad: float) -> float:
        """Calificar Degradación (Regla 3). Menor es mejor."""
        if degrad <= cls.THRESHOLDS["degradation"]["excellent"]:
            return 95
        elif degrad <= cls.THRESHOLDS["degradation"]["good"]:
            return 70
        elif degrad <= cls.THRESHOLDS["degradation"]["fair"]:
            return 50
        elif degrad <= cls.THRESHOLDS["degradation"]["poor"]:
            return 20
        else:
            return 0

    @classmethod
    def _score_sharpe(cls, sharpe: float) -> float:
        """Calificar Sharpe OOS (Regla 5)."""
        if sharpe >= cls.THRESHOLDS["sharpe_oos"]["excellent"]:
            return 95
        elif sharpe >= cls.THRESHOLDS["sharpe_oos"]["good"]:
            return 70
        elif sharpe >= cls.THRESHOLDS["sharpe_oos"]["fair"]:
            return 50
        elif sharpe >= 0:
            return 25
        else:
            return 0

    @classmethod
    def _score_max_dd(cls, max_dd: float) -> float:
        """Calificar Max DD (Regla 6). Menor es mejor."""
        if max_dd >= cls.THRESHOLDS["max_dd"]["excellent"]:
            return 95
        elif max_dd >= cls.THRESHOLDS["max_dd"]["good"]:
            return 70
        elif max_dd >= cls.THRESHOLDS["max_dd"]["fair"]:
            return 50
        elif max_dd >= cls.THRESHOLDS["max_dd"]["poor"]:
            return 20
        else:
            return 0

    @classmethod
    def _score_win_rate(cls, win_rate: float) -> float:
        """Calificar Win Rate (Regla 7)."""
        if win_rate >= 0.60:
            return 95
        elif win_rate >= 0.50:
            return 70
        elif win_rate >= 0.40:
            return 50
        elif win_rate >= 0.30:
            return 25
        else:
            return 0


def score_all_strategies(summary_dict: Dict) -> Dict:
    """
    Aplicar scoring a todos los resultados de backtesting.

    Args:
        summary_dict: {symbol: {strategy: {pf_test_mean, sharpe_test_mean, ...}}}

    Returns:
        {symbol: {strategy: {...original metrics..., score: X, verdict: Y}}}
    """
    scored = {}

    for symbol, strategies in summary_dict.items():
        scored[symbol] = {}

        for strategy_name, metrics in strategies.items():
            # Calcular score
            score_result = ActivationScorer.calculate_score(metrics)

            # Combinar métricas originales + score
            scored[symbol][strategy_name] = {
                **metrics,
                "score": score_result["score"],
                "verdict": score_result["verdict"],
                "rule_scores": score_result["rule_scores"],
                "vetos_triggered": score_result["vetos_triggered"],
            }

    return scored
