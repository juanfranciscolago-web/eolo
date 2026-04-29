# ============================================================
#  Theta Harvest Backtester — Configuración
#
#  Parámetros EXACTAMENTE iguales al live strategy.
#  Si cambiás algo en theta_harvest_strategy.py, actualizá acá.
# ============================================================
from __future__ import annotations

# ── Tickers a simular ─────────────────────────────────────
TICKERS = ["SPY", "TQQQ"]

# ── Spread configuration ──────────────────────────────────
TICKER_CONFIG: dict[str, dict] = {
    "SPY": {
        "spread_width":  5.0,
        "delta_min_abs": 0.15,
        "delta_max_abs": 0.30,
        "min_credit":    0.40,
        "strike_increment": 1.0,
        # IV multiplier: VIX ≈ 30-day SPY implied vol (annualized %)
        # SPY IV ≈ VIX / 100
        "iv_multiplier": 1.0,
    },
    "TQQQ": {
        "spread_width":  5.0,
        "delta_min_abs": 0.15,
        "delta_max_abs": 0.30,
        "min_credit":    0.05,   # Muy bajo para el modelo B-S sintético.
                                # El B-S subestima créditos en TQQQ (bajo precio + alta vol).
                                # La live strategy usa precios reales de Schwab (0.35 mínimo).
        "strike_increment": 0.5, # TQQQ cotiza en $0.50 increments (vs $1.00 para SPY)
        # iv_multiplier calibrado para el modelo B-S sintético.
        # El teórico sería ~3.0, pero combinado con DTE_VOL_MULTIPLIER (hasta 2.5×)
        # da sigmas de 150%+ que distorsionan el pricing. Usamos 1.5 para que
        # la sigma efectiva sea razonable (75% para 0DTE con VIX=20).
        # En live usamos precios de mercado reales, no B-S.
        "iv_multiplier": 1.5,
    },
}

# ── DTEs objetivo ─────────────────────────────────────────
# Opción B: solo los 3 mejores DTEs según backtest (PF 9.89 / 17.67 / 4.41)
# DTE 3 (PF 2.90) y DTE 4 (PF 1.79) descartados por edge insuficiente.
TARGET_DTES = [0, 1, 2]

# ── Entry timing ─────────────────────────────────────────
# Usamos el precio de apertura como proxy de las 10:00 ET
# T residual al entrar (en años, fracción del día)
ENTRY_HOUR_FRACTION = 4.5 / 6.5   # 4.5 horas restantes de 6.5 totales (10:00→14:30 para 0DTE)
TRADING_HOURS_PER_YEAR = 252 * 6.5  # horas de trading en un año

# ── DTE vol multiplier (term structure correction) ────────
# El VIX mide la IV a 30 días. Las opciones de muy corto plazo
# cotizan con una IV mucho mayor ("volatility term structure").
#   0DTE: ~2.5× el VIX → refleja la prima de gamma intraday
#   1DTE: ~1.8×  2DTE: ~1.5×  3DTE: ~1.3×  4DTE: ~1.1×
# Sin este ajuste el B-S subestima masivamente el precio de
# las opciones cortas y rechaza todos los trades por min_credit.
DTE_VOL_MULTIPLIER: dict[int, float] = {
    0: 2.5,
    1: 1.8,
    2: 1.5,
    3: 1.3,
    4: 1.1,
}

# ── EOD force-close ───────────────────────────────────────
FORCE_CLOSE_0DTE_HOUR    = 14.5    # 14:30 ET
FORCE_CLOSE_1TO4DTE_HOUR = 15.25   # 15:15 ET

# ── Exits ─────────────────────────────────────────────────
# Tranche exits: cada contrato tiene un target distinto para suavizar income.
#   Tranche 0 → 35% (salida rápida, asegura income temprano)
#   Tranche 1 → 65% (target estándar, el "sweet spot" de theta)
#   Tranche 2 → None (aguanta hasta vencimiento, captura máximo theta)
# Stop Loss aplica uniformemente a todos los tranches (25% de pérdida adicional
# sobre el crédito — más ajustado que el 50% anterior para reducir drag).
# El backtest mostró que SL era el mayor drag: -$16,386 en 213 trades con 1.50×.
PROFIT_TARGET_PCT   = 0.65   # default para compatibilidad (tranche 1)
TRANCHE_PROFIT_TARGETS: list = [0.35, 0.65, None]  # None = hold to expiry
STOP_LOSS_MULT      = 1.25   # cerrar si spread vale ≥ 125% crédito (era 1.50)
VIX_MAX_ENTRY       = 40.0
VIX_SPIKE_DELTA     = 3.0
VVIX_PANIC          = 110.0
DELTA_DRIFT_MAX     = 0.35
SPY_DROP_PCT_1D     = 0.008  # 0.8% en el día (proxy del drop 30m)

# ── Pivot risk zones ──────────────────────────────────────
DIST_VERY_LOW_PCT = 0.80
DIST_LOW_PCT      = 0.51
DIST_MID_PCT      = 0.25

DELTA_BY_RISK: dict[str, tuple[float, float]] = {
    "VERY_LOW": (0.10, 0.16),
    "LOW":      (0.16, 0.22),
    "MID":      (0.22, 0.30),
    "NO_TRADE": (0.0,  0.0),
}

# ── ATR gate ──────────────────────────────────────────────
ATR_GATE_MULTIPLIER = 2.0

# ── PayoffScore mínimos ───────────────────────────────────
# Nota: payoff_ratio = credit / (spread_width - credit)
# Con spread $5, credit $0.37 → ratio ≈ 8%
# El live strategy usa precios reales de Schwab (créditos más altos).
# El modelo B-S subestima los créditos de TQQQ, por eso umbrales menores.
MIN_PAYOFF_RATIO = {"VERY_LOW": 0.03, "LOW": 0.04, "MID": 0.06}
MIN_BREAKEVEN_MOVE_PCT = {"VERY_LOW": 0.60, "LOW": 0.40, "MID": 0.25}

# ── Sector direction threshold ────────────────────────────
SECTOR_DIRECTION_THRESHOLD = 0.15  # % ponderado mínimo para señal

SECTOR_WEIGHTS_SPY: dict[str, float] = {
    "XLK": 0.30, "XLF": 0.13, "XLV": 0.13, "XLY": 0.10,
    "XLC": 0.08, "XLI": 0.08, "XLP": 0.06, "XLE": 0.04,
    "XLU": 0.03, "XLB": 0.025, "XLRE": 0.025,
}
SECTOR_WEIGHTS_TQQQ: dict[str, float] = {
    "XLK": 0.48, "XLC": 0.16, "XLY": 0.14, "XLV": 0.06,
    "XLI": 0.05, "XLF": 0.04, "XLP": 0.03, "XLE": 0.02,
    "XLB": 0.01, "XLU": 0.01,
}

ALL_SECTOR_ETFS = list(set(list(SECTOR_WEIGHTS_SPY.keys()) + list(SECTOR_WEIGHTS_TQQQ.keys())))

# ── Risk-free rate (proxy) ────────────────────────────────
RISK_FREE_RATE = 0.045   # 4.5% — promedio razonable 2022-2025

# ── Backtesting period ────────────────────────────────────
BACKTEST_START = "2022-01-01"
BACKTEST_END   = "2025-04-25"

# ── Capital simulation ────────────────────────────────────
# Con tranche exits, cada "spread" se divide en N contratos independientes.
# CONTRACTS_PER_SPREAD = 1 significa 1 contrato por tranche.
# Total máximo en papel: 3 DTEs × 3 tranches × $500 = $4,500 de riesgo máximo por ticker.
CONTRACTS_PER_SPREAD = 1   # 1 contrato por tranche (× len(TRANCHE_PROFIT_TARGETS) al entrar)
