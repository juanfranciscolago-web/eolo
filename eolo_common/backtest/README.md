# eolo_common.backtest — Framework de Backtesting Robusto

## Overview

Framework profesional para validar estrategias de trading usando **walk-forward validation** con **fricción real**.

### Features
✅ **Slippage dinámico** — Varía por régimen de mercado  
✅ **Comisiones reales** — Por tipo de activo  
✅ **Walk-forward validation** — Train 12m → Test 3m (rolling)  
✅ **7 regímenes históricos** — Bull 2017, Crash 2020, Rebote 2021, Bear 2022, Mixto 2023, Trend 2024, Reciente  
✅ **Régimen detector LIVE** — Detecta régimen actual para decisiones automáticas  
✅ **Significancia estadística** — Rechaza resultados con <20 trades  
✅ **Degradación OOS detection** — Rechaza overfitting >30%  
✅ **Separación long/short** — Analiza por dirección  
✅ **Score 0-100** — Decisiones binarias ✅ ACTIVAR / ⚠️ CONSIDERAR / ❌ RECHAZAR  

---

## Módulos

### 1. `data_loader.py` — OHLCV Fetcher
Descarga datos históricos desde yfinance (acciones) + Binance API (crypto).

```python
from eolo_common.backtest import BacktestDataLoader

loader = BacktestDataLoader()

# Acciones
equities = loader.load_equities(
    symbols=["SPY", "QQQ", "AAPL"],
    start_date="2017-01-01",
    end_date="2024-12-31"
)

# Crypto
crypto = loader.load_crypto(
    symbols=["BTCUSDT", "ETHUSDT"],
    start_date="2017-01-01"
)
```

### 2. `regime_classifier.py` — Detección de Regímenes
Etiqueta datos históricos y detecta régimen actual.

```python
from eolo_common.backtest import RegimeClassifier

classifier = RegimeClassifier()

# Etiquetar DataFrame
df_labeled = classifier.label_dataframe(df, symbol="SPY")

# Detectar régimen ACTUAL
regime, metrics = classifier.detect_live(df)
print(f"Régimen actual: {regime}")
print(f"Volatilidad: {metrics['volatility']:.4f}")
print(f"Trend: {metrics['trend']}")

# Obtener métricas de un régimen específico
regime_metrics = classifier.get_regime_metrics(df, "bull_2017")
```

### 3. `walk_forward.py` — Motor de Validación
Implementa validación walk-forward sin lookahead bias.

```python
from eolo_common.backtest import WalkForwardValidator

wf = WalkForwardValidator(
    train_months=12,
    test_months=3,
    step_months=3
)

# Generar ventanas
windows = wf.generate_windows("2017-01-01", "2024-12-31")
# → 13-16 ventanas automáticas

# Ejecutar backtests en cada ventana
def my_strategy(train_df, test_df):
    # Tu lógica de estrategia
    return {
        "profit_factor": 1.25,
        "num_trades": 42,
        "sharpe": 0.6,
        "max_drawdown": 0.15,
    }

results = wf.run_backtests(df, my_strategy, windows)

# Agregar resultados
summary = wf.aggregate_results(results)
print(f"PF promedio (OOS): {summary['test_pf_mean']:.2f}")
print(f"Veredicto: {summary['verdict']}")  # "robust" | "overfitted" | "inconsistent"
```

### 4. `backtest_engine.py` — Simulador de Trading
Ejecuta backtests CON fricción real (slippage, comisiones).

```python
from eolo_common.backtest import BacktestEngine

engine = BacktestEngine(
    initial_capital=100000,
    position_size_pct=1.0,
    asset_type="equities"  # o "crypto"
)

# Generar señales (tu estrategia)
signals = {
    "signal": np.array([1, 0, -1, 1, ...]),  # 1=long, -1=short, 0=no
    "entry_prices": df["Close"].values,
    "stop_loss": sl_array,
    "take_profit": tp_array,
}

# Ejecutar
metrics = engine.run(
    df,
    signals,
    regime="bull_2024",  # Para determinar slippage
    symbol="SPY"
)

print(f"PF: {metrics['profit_factor']:.2f}")
print(f"Sharpe: {metrics['sharpe']:.2f}")
print(f"Trades: {metrics['num_trades']}")
```

---

## Flujo Completo: De Datos a Decisiones

```python
import pandas as pd
from eolo_common.backtest import (
    BacktestDataLoader,
    RegimeClassifier,
    WalkForwardValidator,
    BacktestEngine
)

# 1. Cargar datos
loader = BacktestDataLoader()
data = loader.load_equities(["SPY"], "2017-01-01", "2024-12-31")
df = data["SPY"]

# 2. Etiquetar regímenes
classifier = RegimeClassifier()
df = classifier.label_dataframe(df)

# 3. Generar ventanas walk-forward
wf = WalkForwardValidator()
windows = wf.generate_windows("2017-01-01", "2024-12-31")

# 4. Ejecutar backtests
engine = BacktestEngine()

def strategy_func(df_period, is_training=True):
    # Tu estrategia aquí
    close = df_period["Close"]
    signal = np.where(close > close.rolling(20).mean(), 1, 0)
    
    metrics = engine.run(
        df_period,
        {"signal": signal},
        regime=df_period.get("regime", "unknown").iloc[0]
    )
    return metrics

results = wf.run_backtests(df, strategy_func, windows)

# 5. Analizar resultados
summary = wf.aggregate_results(results)
print(f"Veredicto: {summary['verdict']}")
print(f"PF promedio (OOS): {summary['test_pf_mean']:.2f}")

# 6. Detectar régimen ACTUAL
current_regime, regime_metrics = classifier.detect_live(df)
print(f"Régimen actual: {current_regime}")

# 7. Decisión: ¿Activar o no?
if summary['test_pf_mean'] >= 1.2 and summary['verdict'] != 'overfitted':
    print("✅ ACTIVAR estrategia")
else:
    print("❌ RECHAZAR estrategia")
```

---

## Criterios de Decisión (10 Reglas)

Ver [Criterios de Activación Real](../../../.auto-memory/activation_criteria_real.md) para la matriz completa de scoring.

**Resumen:**
- ✅ **ACTIVAR** (Score ≥80%): PF OOS ≥1.2, degradation ≤30%, sharpe ≥0.5
- ⚠️ **CONSIDERAR** (60-79%): Resultados intermedios con riesgo tolerable
- ❌ **RECHAZAR** (<60%): PF OOS <1.0, degradation >50%, overfitted

---

## Instalación y Dependencias

```bash
pip install pandas numpy yfinance

# Para Crypto (opcional):
pip install binance-connector
```

---

## Configuración de Fricción

### Slippage por Régimen
Editar en `data_loader.py`:
```python
SLIPPAGE_BY_REGIME = {
    "bull_2017": 0.0005,      # 0.05%
    "crash_2020": 0.005,      # 0.5%
    "rebote_2021": 0.001,     # 0.1%
    # ... etc
}
```

### Comisiones
```python
COMMISSIONS = {
    "equities": 0.001,        # 0.1% Schwab
    "crypto": 0.001,          # 0.1% Binance
}
```

---

## Ejemplos de Uso

### Ejemplo 1: Validar una Estrategia
```python
from eolo_common.backtest import *

# Cargar datos
loader = BacktestDataLoader()
df = loader.load_equities(["SPY"])["SPY"]

# Walk-forward
wf = WalkForwardValidator()
windows = wf.generate_windows()

# Tu estrategia
def my_strategy(df_period, is_training=True):
    # Generar señales
    signal = np.where(df_period["Close"] > df_period["Close"].rolling(20).mean(), 1, 0)
    
    # Ejecutar
    engine = BacktestEngine()
    return engine.run(df_period, {"signal": signal})

# Resultados
results = wf.run_backtests(df, my_strategy, windows)
summary = wf.aggregate_results(results)
print(summary)
```

### Ejemplo 2: Detectar Régimen y Activar
```python
classifier = RegimeClassifier()
regime, metrics = classifier.detect_live(df)

print(f"Régimen actual: {regime}")

# Obtener PF de la estrategia en este régimen
strategy_pf_in_regime = backtest_results[strategy][regime]["pf"]

if strategy_pf_in_regime >= 1.2:
    bot.enable_strategy(strategy)
else:
    bot.disable_strategy(strategy)
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'yfinance'"
```bash
pip install yfinance
```

### Datos vacíos
- Verificar fechas (start_date < end_date)
- Verificar símbolo (SPY, AAPL, BTCUSDT, etc.)
- Verificar conexión a internet

### Walk-forward muy lento
- Reducir período de análisis (ej: 2023-2024 en lugar de 2017-2024)
- Usar menos símbols
- Ejecutar en GCP con VM paralela

---

## Próximas Fases

**FASE 2:** Backtesting de 27 estrategias en 7 regímenes  
**FASE 3:** Red neuronal multi-TF  
**FASE 4:** Auto-generation con algoritmo genético  

---

## Contacto y Feedback

Juan @ Eolo Trading  
juanfranciscolago@gmail.com

---

**Última actualización:** 2026-04-22
