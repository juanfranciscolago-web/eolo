# ============================================================
#  Theta Harvest Backtester — Black-Scholes Pricer
#
#  Funciones puras para:
#    - Precio BS de call / put
#    - Delta BS de call / put
#    - Búsqueda de strike para un delta objetivo
#    - Precio de un credit spread (short strike - long strike)
# ============================================================
from __future__ import annotations

import math
from typing import Literal

# ─────────────────────────────────────────────────────────
#  Helpers BS estándar
# ─────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """CDF de la normal estándar (Abramowitz & Stegun approx)."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1_d2(
    S: float,   # precio spot
    K: float,   # strike
    T: float,   # tiempo a vencimiento (años)
    r: float,   # tasa libre de riesgo
    sigma: float,  # volatilidad implícita anualizada
) -> tuple[float, float]:
    """Retorna (d1, d2). Asume T > 0 y sigma > 0."""
    log_sk = math.log(S / K)
    d1 = (log_sk + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


# ─────────────────────────────────────────────────────────
#  Precio BS
# ─────────────────────────────────────────────────────────

def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["call", "put"],
) -> float:
    """
    Precio teórico Black-Scholes.

    Parameters
    ----------
    S           : precio spot del subyacente
    K           : strike de la opción
    T           : tiempo a vencimiento en años (ej: 1/252 para 0DTE EOD)
    r           : tasa libre de riesgo anualizada
    sigma       : volatilidad implícita anualizada
    option_type : 'call' o 'put'

    Returns
    -------
    float : precio teórico ≥ 0
    """
    if T <= 0:
        # Valor intrínseco al vencimiento
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    if sigma <= 0:
        # Sin vol → valor intrínseco descontado
        if option_type == "call":
            return max(S - K * math.exp(-r * T), 0.0)
        return max(K * math.exp(-r * T) - S, 0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma)

    if option_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    # put
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


# ─────────────────────────────────────────────────────────
#  Delta BS
# ─────────────────────────────────────────────────────────

def bs_delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["call", "put"],
) -> float:
    """
    Delta de la opción.
      Call: δ ∈ (0, 1)
      Put:  δ ∈ (-1, 0)

    Se retorna como valor ABSOLUTO para facilitar comparación.
    """
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if S > K else 0.0
        return 1.0 if S < K else 0.0   # abs(put delta)

    d1, _ = _d1_d2(S, K, T, r, sigma)

    if option_type == "call":
        return _norm_cdf(d1)
    # put: delta = N(d1) - 1, retornamos abs
    return abs(_norm_cdf(d1) - 1.0)


# ─────────────────────────────────────────────────────────
#  Strike finder: dado target_delta, busca el strike más
#  cercano en un grid de incrementos
# ─────────────────────────────────────────────────────────

def find_strike_for_delta(
    S: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["call", "put"],
    target_delta: float,          # valor absoluto, ej 0.20
    strike_increment: float = 1.0,
    search_range_pct: float = 0.30,  # busca ±30% del spot
) -> tuple[float, float]:
    """
    Encuentra el strike en el grid de `strike_increment` cuyo |delta|
    es más cercano a `target_delta`.

    Returns
    -------
    (strike, actual_delta)
    """
    # Rango de búsqueda
    lo = S * (1 - search_range_pct)
    hi = S * (1 + search_range_pct)

    # Generar grid de strikes
    import math as _math
    start_k = _math.floor(lo / strike_increment) * strike_increment
    end_k   = _math.ceil(hi  / strike_increment) * strike_increment

    best_strike = S
    best_delta  = 0.5
    best_diff   = float("inf")

    k = start_k
    while k <= end_k:
        d = bs_delta(S, k, T, r, sigma, option_type)
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff   = diff
            best_strike = k
            best_delta  = d
        k = round(k + strike_increment, 10)

    return best_strike, best_delta


# ─────────────────────────────────────────────────────────
#  Credit spread pricer
# ─────────────────────────────────────────────────────────

def spread_credit(
    S: float,
    T: float,
    r: float,
    sigma: float,
    spread_type: Literal["put_credit_spread", "call_credit_spread"],
    short_strike: float,
    long_strike: float,
) -> float:
    """
    Crédito recibido al vender el spread (por acción, sin multiplicar por 100).

    PUT credit spread:
      short_strike < S  (vendemos put OTM)
      long_strike  < short_strike (compramos put más OTM)
      credit = price(short_put) - price(long_put)

    CALL credit spread:
      short_strike > S  (vendemos call OTM)
      long_strike  > short_strike (compramos call más OTM)
      credit = price(short_call) - price(long_call)
    """
    if "put" in spread_type:
        p_short = bs_price(S, short_strike, T, r, sigma, "put")
        p_long  = bs_price(S, long_strike,  T, r, sigma, "put")
    else:
        p_short = bs_price(S, short_strike, T, r, sigma, "call")
        p_long  = bs_price(S, long_strike,  T, r, sigma, "call")

    credit = p_short - p_long
    return max(credit, 0.0)


def spread_value(
    S: float,
    T: float,
    r: float,
    sigma: float,
    spread_type: Literal["put_credit_spread", "call_credit_spread"],
    short_strike: float,
    long_strike: float,
) -> float:
    """
    Valor actual del spread (costo de recompra).
    Igual que spread_credit pero evaluado en el precio actual.
    """
    return spread_credit(S, T, r, sigma, spread_type, short_strike, long_strike)


def spread_pnl(
    entry_credit: float,
    current_value: float,
    contracts: int = 1,
) -> float:
    """
    P&L realizado si se cierra el spread ahora.
    Positivo = ganancia (pagamos menos de lo que recibimos).
    """
    return (entry_credit - current_value) * 100 * contracts


# ─────────────────────────────────────────────────────────
#  Expiry P&L (sin cierre anticipado)
# ─────────────────────────────────────────────────────────

def spread_pnl_at_expiry(
    S_expiry: float,
    spread_type: Literal["put_credit_spread", "call_credit_spread"],
    short_strike: float,
    long_strike: float,
    entry_credit: float,
    contracts: int = 1,
) -> float:
    """
    P&L al vencimiento sin cierre anticipado.

    PUT credit spread:
      max_profit = entry_credit         (S > short_strike)
      max_loss   = width - entry_credit (S < long_strike)

    CALL credit spread:
      max_profit = entry_credit         (S < short_strike)
      max_loss   = width - entry_credit (S > long_strike)
    """
    if "put" in spread_type:
        intrinsic_short = max(short_strike - S_expiry, 0.0)
        intrinsic_long  = max(long_strike  - S_expiry, 0.0)
    else:
        intrinsic_short = max(S_expiry - short_strike, 0.0)
        intrinsic_long  = max(S_expiry - long_strike,  0.0)

    expiry_cost = intrinsic_short - intrinsic_long
    return (entry_credit - expiry_cost) * 100 * contracts


# ─────────────────────────────────────────────────────────
#  PayoffScore (versión offline para backtester)
# ─────────────────────────────────────────────────────────

def compute_payoff_score(
    credit: float,
    spread_width: float,
    short_strike: float,
    S: float,
    spread_type: Literal["put_credit_spread", "call_credit_spread"],
    T_years: float,
    dte: int,
) -> dict:
    """
    Replica la lógica de PayoffScore del live strategy.

    Returns dict con: payoff_ratio, breakeven_move_pct,
                      annualized_yield, composite_score
    """
    max_risk    = spread_width - credit
    payoff_ratio = credit / max_risk if max_risk > 0 else 0.0

    # Breakeven distance como % del spot
    if "put" in spread_type:
        breakeven_price = short_strike - credit
        be_move_pct     = abs(S - breakeven_price) / S
    else:
        breakeven_price = short_strike + credit
        be_move_pct     = abs(breakeven_price - S) / S

    # Yield anualizado
    ann_yield = (credit / spread_width) / max(T_years, 1e-6) if T_years > 0 else 0.0

    # Scores 0-100
    score_payoff = min(100, (payoff_ratio / 0.30) * 100)
    score_be     = min(100, (be_move_pct  / 0.05) * 100)

    if dte == 0:
        composite = round(score_payoff * 0.50 + score_be * 0.50, 1)
    else:
        score_ann = min(100, (ann_yield / 200) * 100)
        composite = round(score_payoff * 0.40 + score_be * 0.40 + score_ann * 0.20, 1)

    return {
        "payoff_ratio":        round(payoff_ratio,  4),
        "breakeven_move_pct":  round(be_move_pct,   4),
        "annualized_yield":    round(ann_yield,      2),
        "composite_score":     composite,
    }
