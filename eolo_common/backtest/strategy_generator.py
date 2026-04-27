"""
strategy_generator.py — Auto-generador de Estrategias basado en NN

Genera nuevas estrategias combinando:
  1. Patrones aprendidos por NN (confluencia multi-TF)
  2. Reglas de scoring FASE 3
  3. Heurísticas de entrada/salida

Output: N estrategias Python listos para backtest
"""

import numpy as np
from typing import List, Dict, Tuple, Any
from dataclasses import dataclass


@dataclass
class GeneratedStrategy:
    """Una estrategia generada automáticamente."""
    name: str
    entry_rule: str  # Descripción: "RSI < 30 AND MACD bullish on 1h"
    exit_rule: str   # Descripción: "RSI > 70 OR Bollinger upper touch"
    parameters: Dict[str, float]  # {threshold_rsi: 30, threshold_macd: 0.1, ...}
    code_template: str  # Código Python para detect_signal()
    confluence_strength: float  # 0-1, puntuación NN


class StrategyGenerator:
    """Genera nuevas estrategias basadas en NN confluencia."""

    # Umbrales de entrada/salida
    THRESHOLDS = {
        "rsi_low": [20, 25, 30, 35],
        "rsi_high": [65, 70, 75, 80],
        "macd_threshold": [0.05, 0.10, 0.15, 0.20],
        "bb_threshold": [0.5, 0.7, 1.0, 1.5],
        "atr_multiplier": [1.0, 1.5, 2.0, 2.5],
    }

    @staticmethod
    def _rsi_entry_rule(threshold: float, direction: str = "bullish") -> Tuple[str, str]:
        """Genera regla de entrada basada en RSI."""
        if direction == "bullish":
            desc = f"RSI < {threshold} (sobreventa)"
            code = f"""
def detect_signal(df):
    from eolo_common.indicators import rsi
    rsi_val = rsi(df['close'], 14).iloc[-1]
    if rsi_val < {threshold}:
        return "BUY"
    return "HOLD"
"""
        else:
            desc = f"RSI > {threshold} (sobreventa)"
            code = f"""
def detect_signal(df):
    from eolo_common.indicators import rsi
    rsi_val = rsi(df['close'], 14).iloc[-1]
    if rsi_val > {threshold}:
        return "SELL"
    return "HOLD"
"""
        return desc, code

    @staticmethod
    def _macd_entry_rule(threshold: float) -> Tuple[str, str]:
        """Genera regla de entrada basada en MACD."""
        desc = f"MACD bullish crossover (threshold: {threshold:.3f})"
        code = f"""
def detect_signal(df):
    from eolo_common.indicators import macd
    macd_line, signal, hist = macd(df['close'])
    if hist.iloc[-1] > {threshold} and hist.iloc[-2] < {threshold}:
        return "BUY"
    elif hist.iloc[-1] < -{threshold} and hist.iloc[-2] > -{threshold}:
        return "SELL"
    return "HOLD"
"""
        return desc, code

    @staticmethod
    def _bollinger_entry_rule(multiplier: float) -> Tuple[str, str]:
        """Genera regla de entrada basada en Bollinger Bands."""
        desc = f"Bollinger mean reversion (multiplier: {multiplier})"
        code = f"""
def detect_signal(df):
    from eolo_common.indicators import bollinger_bands
    sma, upper, lower = bollinger_bands(df['close'], multiplier={multiplier})
    close = df['close'].iloc[-1]
    if close < lower:
        return "BUY"
    elif close > upper:
        return "SELL"
    return "HOLD"
"""
        return desc, code

    @staticmethod
    def _atr_exit_rule(threshold: float) -> Tuple[str, str]:
        """Genera regla de salida basada en ATR."""
        desc = f"ATR-based stop loss (threshold: {threshold:.2f})"
        code = f"""
def detect_exit(entry_price, current_price, atr):
    # Stop loss at -1.5x ATR, take profit at +2x ATR
    stop_loss = entry_price - {threshold} * atr
    take_profit = entry_price + {threshold * 1.33} * atr

    if current_price < stop_loss:
        return "SELL"  # Stop loss
    elif current_price > take_profit:
        return "SELL"  # Take profit
    return "HOLD"
"""
        return desc, code

    @staticmethod
    def _confluence_entry_rule(score: float) -> Tuple[str, str]:
        """Genera regla de entrada basada en NN confluency score."""
        desc = f"NN Confluency score > {score:.2f}"
        code = f"""
def detect_signal(df, nn_predictor):
    # Requiere features multi-TF y predictor NN
    confluence_score = nn_predictor.predict(df)
    if confluence_score > {score}:
        return "BUY"
    return "HOLD"
"""
        return desc, code

    @classmethod
    def generate_strategies(
        cls,
        num_strategies: int = 20,
        nn_predictions: Dict[str, float] = None
    ) -> List[GeneratedStrategy]:
        """
        Genera N estrategias nuevas.

        Args:
            num_strategies: Número de estrategias a generar
            nn_predictions: {symbol: confluency_score} del NN entrenado

        Returns:
            Lista de estrategias generadas
        """
        strategies = []
        np.random.seed(42)

        for i in range(num_strategies):
            # Seleccionar componentes aleatorios
            entry_type = np.random.choice(["rsi", "macd", "bollinger", "confluence"])
            exit_type = np.random.choice(["atr", "fixed_tp", "trailing_stop"])

            # ENTRY RULE
            if entry_type == "rsi":
                direction = np.random.choice(["bullish", "bearish"])
                threshold = np.random.choice(cls.THRESHOLDS["rsi_low"]) \
                    if direction == "bullish" else np.random.choice(cls.THRESHOLDS["rsi_high"])
                entry_desc, entry_code = cls._rsi_entry_rule(threshold, direction)
                confluence_strength = 0.5

            elif entry_type == "macd":
                threshold = np.random.choice(cls.THRESHOLDS["macd_threshold"])
                entry_desc, entry_code = cls._macd_entry_rule(threshold)
                confluence_strength = 0.6

            elif entry_type == "bollinger":
                multiplier = np.random.choice(cls.THRESHOLDS["bb_threshold"])
                entry_desc, entry_code = cls._bollinger_entry_rule(multiplier)
                confluence_strength = 0.55

            else:  # confluence
                score = np.random.uniform(0.5, 0.8)
                entry_desc, entry_code = cls._confluence_entry_rule(score)
                confluence_strength = score

            # EXIT RULE
            if exit_type == "atr":
                mult = np.random.choice(cls.THRESHOLDS["atr_multiplier"])
                exit_desc, exit_code = cls._atr_exit_rule(mult)
            elif exit_type == "fixed_tp":
                exit_desc = "Fixed TP at 2% gain, SL at 1% loss"
                exit_code = "# Fixed SL/TP"
            else:  # trailing_stop
                exit_desc = "Trailing stop at 0.5% ATR"
                exit_code = "# Trailing stop"

            # Crear estrategia
            strategy_name = f"gen_{entry_type}_x_{exit_type}_{i:02d}"

            generated = GeneratedStrategy(
                name=strategy_name,
                entry_rule=entry_desc,
                exit_rule=exit_desc,
                parameters={
                    f"entry_type": entry_type,
                    f"exit_type": exit_type,
                    f"threshold_rsi": threshold if entry_type == "rsi" else 0,
                    f"threshold_macd": threshold if entry_type == "macd" else 0,
                },
                code_template=entry_code,
                confluence_strength=confluence_strength
            )

            strategies.append(generated)

        return strategies

    @classmethod
    def export_strategies_to_python(
        cls,
        strategies: List[GeneratedStrategy],
        output_dir: str = "generated_strategies"
    ) -> List[str]:
        """
        Exporta estrategias generadas a archivos Python.

        Args:
            strategies: Lista de estrategias generadas
            output_dir: Directorio donde guardar

        Returns:
            Lista de rutas de archivos creados
        """
        from pathlib import Path

        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        created_files = []

        for strategy in strategies:
            filename = output_path / f"{strategy.name}.py"

            # Crear código Python completo
            code = f'''"""
Auto-generated strategy: {strategy.name}

Entry: {strategy.entry_rule}
Exit: {strategy.exit_rule}

Confidence: {strategy.confluence_strength:.2%}
"""

import pandas as pd
import numpy as np

{strategy.code_template}

def detect_signal(df, ticker=None):
    """Señal de trading generada automáticamente."""
    try:
        signal = detect_signal(df)
        return signal
    except Exception as e:
        return "HOLD"
'''

            with open(filename, 'w') as f:
                f.write(code)

            created_files.append(str(filename))

        return created_files

    @classmethod
    def score_generated_strategies(
        cls,
        strategies: List[GeneratedStrategy],
        activation_scorer: Any  # ActivationScorer
    ) -> Dict[str, Dict]:
        """
        Califica estrategias generadas usando scoring FASE 3.

        Args:
            strategies: Lista de estrategias generadas
            activation_scorer: Instancia de ActivationScorer

        Returns:
            {strategy_name: {score: X, verdict: Y, ...}}
        """
        # En producción, esto backtestaría cada estrategia
        # Por ahora, retorna scores ficticios basados en confluencia

        scores = {}

        for strategy in strategies:
            # Score teórico: combina confluencia NN + heurística
            base_score = strategy.confluence_strength * 80  # Escala a 0-80

            # Ajustar por tipo de entrada/salida
            if strategy.parameters.get("entry_type") == "confluence":
                bonus = 10
            else:
                bonus = np.random.uniform(-5, 5)

            final_score = max(0, min(100, base_score + bonus))

            # Verdict
            if final_score >= 80:
                verdict = "ACTIVAR"
            elif final_score >= 60:
                verdict = "CONSIDERAR"
            else:
                verdict = "RECHAZAR"

            scores[strategy.name] = {
                "score": float(final_score),
                "verdict": verdict,
                "confluence_strength": float(strategy.confluence_strength),
                "entry_rule": strategy.entry_rule,
                "exit_rule": strategy.exit_rule,
            }

        return scores


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🔬 TEST: Strategy Generator")
    print("="*80)

    generator = StrategyGenerator()

    print("\n📝 Generando 10 estrategias...")
    strategies = generator.generate_strategies(num_strategies=10)

    for strat in strategies[:3]:
        print(f"\n✅ {strat.name}")
        print(f"   Entry: {strat.entry_rule}")
        print(f"   Exit: {strat.exit_rule}")
        print(f"   Confluence: {strat.confluence_strength:.2%}")

    print(f"\n📦 Total generadas: {len(strategies)} estrategias")
