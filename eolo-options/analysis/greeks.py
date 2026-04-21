# ============================================================
#  EOLO v2 — Greeks & Black-Scholes Calculator
#
#  Calcula con Black-Scholes-Merton:
#    - Precio teórico de calls y puts
#    - Delta, Gamma, Theta, Vega, Rho
#    - IV implícita (Newton-Raphson sobre precio de mercado)
#
#  Inputs estándar:
#    S  = precio del subyacente
#    K  = strike
#    T  = tiempo a vencimiento en años (dte / 365)
#    r  = tasa libre de riesgo (default 0.05 = Fed Funds ~5%)
#    σ  = volatilidad implícita anualizada (0.30 = 30%)
#    q  = dividendo continuo (default 0.0)
#
#  Uso:
#    g = BSGreeks(S=45.0, K=46.0, T=21/365, r=0.05, sigma=0.35)
#    print(g.call_price())   # precio teórico call
#    print(g.delta("call"))  # delta de la call
#    iv = implied_vol(market_price=2.50, S=45, K=46, T=21/365, r=0.05, flag="call")
# ============================================================
import math
from typing import Literal

# Tasa libre de riesgo default (Fed Funds Rate)
DEFAULT_RISK_FREE_RATE = 0.05

# Límites para IV numerically stable
IV_MIN = 0.0001
IV_MAX = 20.0        # 2000% — amplio para productos apalancados como SOXL
IV_TOL = 1e-6
IV_MAX_ITER = 100


# ── Utilidades estadísticas ────────────────────────────────

def _norm_pdf(x: float) -> float:
    """Función de densidad de la normal estándar N'(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    """Función de distribución acumulada de la normal estándar N(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


# ── Clase principal ────────────────────────────────────────

class BSGreeks:
    """
    Calcula greeks y precios teóricos con Black-Scholes-Merton.

    Parámetros:
        S     : precio del subyacente
        K     : strike
        T     : tiempo a vencimiento en años (dte/365)
        r     : tasa libre de riesgo anual (ej. 0.05)
        sigma : volatilidad implícita anualizada (ej. 0.35)
        q     : tasa de dividendo continuo (ej. 0.0)
    """

    def __init__(self, S: float, K: float, T: float,
                 r: float = DEFAULT_RISK_FREE_RATE,
                 sigma: float = 0.30,
                 q: float = 0.0):
        self.S     = S
        self.K     = K
        self.T     = max(T, 1e-10)   # evitar división por cero
        self.r     = r
        self.sigma = max(sigma, IV_MIN)
        self.q     = q
        self._d1, self._d2 = self._compute_d1_d2()

    def _compute_d1_d2(self) -> tuple[float, float]:
        """Calcula d1 y d2 de la fórmula BSM."""
        sqrt_T = math.sqrt(self.T)
        d1 = (
            math.log(self.S / self.K)
            + (self.r - self.q + 0.5 * self.sigma ** 2) * self.T
        ) / (self.sigma * sqrt_T)
        d2 = d1 - self.sigma * sqrt_T
        return d1, d2

    # ── Precios teóricos ───────────────────────────────────

    def call_price(self) -> float:
        """Precio teórico de una call europea (BSM)."""
        d1, d2 = self._d1, self._d2
        return (
            self.S * math.exp(-self.q * self.T) * _norm_cdf(d1)
            - self.K * math.exp(-self.r * self.T) * _norm_cdf(d2)
        )

    def put_price(self) -> float:
        """Precio teórico de una put europea (BSM)."""
        d1, d2 = self._d1, self._d2
        return (
            self.K * math.exp(-self.r * self.T) * _norm_cdf(-d2)
            - self.S * math.exp(-self.q * self.T) * _norm_cdf(-d1)
        )

    def price(self, flag: Literal["call", "put"]) -> float:
        return self.call_price() if flag == "call" else self.put_price()

    # ── Greeks ─────────────────────────────────────────────

    def delta(self, flag: Literal["call", "put"]) -> float:
        """
        Delta: sensibilidad del precio al movimiento del subyacente.
        Call delta ∈ (0, 1), Put delta ∈ (-1, 0).
        """
        d1 = self._d1
        if flag == "call":
            return math.exp(-self.q * self.T) * _norm_cdf(d1)
        else:
            return math.exp(-self.q * self.T) * (_norm_cdf(d1) - 1)

    def gamma(self) -> float:
        """
        Gamma: tasa de cambio de delta respecto al subyacente.
        Igual para calls y puts.
        """
        d1 = self._d1
        return (
            math.exp(-self.q * self.T) * _norm_pdf(d1)
        ) / (self.S * self.sigma * math.sqrt(self.T))

    def theta(self, flag: Literal["call", "put"]) -> float:
        """
        Theta: decaimiento temporal diario (por día calendario).
        Expresado en dólares por día (positivo = ganancia de tiempo holder).
        Nota: theta es negativo para posiciones long.
        """
        d1, d2 = self._d1, self._d2
        sqrt_T = math.sqrt(self.T)

        term1 = -(
            self.S * math.exp(-self.q * self.T) * _norm_pdf(d1) * self.sigma
        ) / (2 * sqrt_T)

        if flag == "call":
            term2 = -self.r * self.K * math.exp(-self.r * self.T) * _norm_cdf(d2)
            term3 = self.q * self.S * math.exp(-self.q * self.T) * _norm_cdf(d1)
        else:
            term2 = self.r * self.K * math.exp(-self.r * self.T) * _norm_cdf(-d2)
            term3 = -self.q * self.S * math.exp(-self.q * self.T) * _norm_cdf(-d1)

        # Dividir por 365 para theta diario
        return (term1 + term2 + term3) / 365

    def vega(self) -> float:
        """
        Vega: sensibilidad al 1% de cambio en IV.
        Expresado en dólares por 1% de IV.
        """
        d1 = self._d1
        # Vega anual, dividir por 100 para expresar por 1% IV
        return (
            self.S * math.exp(-self.q * self.T)
            * _norm_pdf(d1) * math.sqrt(self.T)
        ) / 100

    def rho(self, flag: Literal["call", "put"]) -> float:
        """
        Rho: sensibilidad al 1% de cambio en tasa libre de riesgo.
        """
        d2 = self._d2
        if flag == "call":
            return (
                self.K * self.T * math.exp(-self.r * self.T)
                * _norm_cdf(d2)
            ) / 100
        else:
            return (
                -self.K * self.T * math.exp(-self.r * self.T)
                * _norm_cdf(-d2)
            ) / 100

    def all_greeks(self, flag: Literal["call", "put"]) -> dict:
        """Retorna todos los Greeks en un dict."""
        return {
            "price":  self.price(flag),
            "delta":  self.delta(flag),
            "gamma":  self.gamma(),
            "theta":  self.theta(flag),
            "vega":   self.vega(),
            "rho":    self.rho(flag),
            "d1":     self._d1,
            "d2":     self._d2,
        }


# ── Volatilidad Implícita ──────────────────────────────────

def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = DEFAULT_RISK_FREE_RATE,
    flag: Literal["call", "put"] = "call",
    q: float = 0.0,
    initial_guess: float = 0.30,
) -> float | None:
    """
    Calcula la Volatilidad Implícita (IV) usando Newton-Raphson.

    Busca σ tal que BSM_price(σ) = market_price.

    Retorna:
        IV anualizada como decimal (0.35 = 35%)
        None si no converge o hay error de input
    """
    if T <= 0 or market_price <= 0 or S <= 0 or K <= 0:
        return None

    # Bounds check: el precio de mercado debe estar dentro de los límites
    # teóricos (evitar inputs imposibles)
    intrinsic = max(0.0, (S - K) if flag == "call" else (K - S))
    upper_bound = S if flag == "call" else K
    if market_price < intrinsic - 0.01 or market_price > upper_bound + 0.01:
        return None

    sigma = initial_guess
    for _ in range(IV_MAX_ITER):
        try:
            bs = BSGreeks(S=S, K=K, T=T, r=r, sigma=sigma, q=q)
            theoretical = bs.price(flag)
            vega_val = bs.vega() * 100   # vega en unidades absolutas (no por 1%)

            if abs(vega_val) < 1e-10:
                break

            diff = theoretical - market_price
            if abs(diff) < IV_TOL:
                break

            # Newton-Raphson step
            sigma = sigma - diff / vega_val
            sigma = max(IV_MIN, min(sigma, IV_MAX))

        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    # Verificar convergencia final
    try:
        bs_final = BSGreeks(S=S, K=K, T=T, r=r, sigma=sigma, q=q)
        if abs(bs_final.price(flag) - market_price) < 0.05:
            return sigma
    except Exception:
        pass

    return None


# ── Greeks desde contrato de cadena ───────────────────────

def enrich_contract(contract: dict, S: float,
                    r: float = DEFAULT_RISK_FREE_RATE,
                    flag: Literal["call", "put"] = "call") -> dict:
    """
    Recibe un contrato normalizado de options_chain y calcula
    Greeks propios (más precisos que los de Schwab a veces).

    Agrega al dict:
        bs_price, bs_delta, bs_gamma, bs_theta, bs_vega, iv_calc
    """
    c = dict(contract)

    try:
        K   = c.get("strike")
        dte = c.get("dte")
        bid = c.get("bid") or 0
        ask = c.get("ask") or 0
        mid = (bid + ask) / 2 if bid and ask else None

        if not (K and dte is not None and S and mid):
            return c

        T      = max(dte, 0.5) / 365
        iv_raw = c.get("iv")

        # IV de mercado (Schwab la da como porcentaje: 35.0 → 0.35)
        sigma = (iv_raw / 100) if (iv_raw and iv_raw > 0) else 0.30

        # Intentar recalcular IV desde el mid price
        iv_calc = implied_vol(mid, S=S, K=K, T=T, r=r, flag=flag)
        if iv_calc:
            sigma = iv_calc
            c["iv_calc"] = round(iv_calc * 100, 2)   # en %

        bs = BSGreeks(S=S, K=K, T=T, r=r, sigma=sigma)
        g  = bs.all_greeks(flag)

        c["bs_price"] = round(g["price"], 4)
        c["bs_delta"] = round(g["delta"], 4)
        c["bs_gamma"] = round(g["gamma"], 6)
        c["bs_theta"] = round(g["theta"], 4)
        c["bs_vega"]  = round(g["vega"],  4)

    except Exception as e:
        c["_greeks_error"] = str(e)

    return c


# ── Utilidades ─────────────────────────────────────────────

def dte_to_years(dte: float) -> float:
    """Convierte días a vencimiento en fracción de año."""
    return max(dte, 0.5) / 365


def spread_value(bid: float | None, ask: float | None) -> float | None:
    """Bid-ask spread absoluto."""
    if bid is None or ask is None:
        return None
    return round(ask - bid, 4)


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    """Bid-ask spread como % del mid price."""
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2
    if mid == 0:
        return None
    return round((ask - bid) / mid * 100, 2)
