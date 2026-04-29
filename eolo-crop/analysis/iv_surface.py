# ============================================================
#  EOLO v2 — IV Surface Builder
#
#  Construye la superficie de volatilidad implícita (IV Surface)
#  a partir de la cadena de opciones normalizada.
#
#  La superficie IV es esencial para:
#    - Detectar saltos de IV entre strikes (skew) o DTE (term structure)
#    - Calibrar el motor de mispricing
#    - Darle contexto a Claude para decidir estrategias
#
#  Estructura de salida:
#    {
#      "ticker":          "SOXL",
#      "ts":              1713200000.0,
#      "underlying_price": 32.40,
#      "atm_iv":          0.78,            # IV del strike ATM
#      "skew": {
#        "calls": [ {strike, moneyness, iv, dte}, ... ],
#        "puts":  [ {strike, moneyness, iv, dte}, ... ],
#      },
#      "term_structure": [                 # ATM IV por expiración
#        { "expiration": "2025-05-16", "dte": 21, "atm_iv": 0.80 },
#        ...
#      ],
#      "surface": [                        # grid completo (strike × DTE)
#        { "expiration": "2025-05-16", "strike": 30.0,
#          "moneyness": -0.074, "call_iv": 0.85, "put_iv": 0.88,
#          "mid_iv": 0.865, "dte": 21 },
#        ...
#      ],
#      "skew_index":      0.12,    # diferencia IV 25-delta put vs call (put premium)
#      "term_slope":      -0.02,   # pendiente de la term structure (neg = backwardation)
#    }
#
#  Uso:
#    surface = IVSurface.from_chain(chain_dict)
#    print(surface.atm_iv)
#    print(surface.skew_index)
#    print(surface.to_summary())     # string para Claude
# ============================================================
import math
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from greeks import implied_vol, DEFAULT_RISK_FREE_RATE

# ── Configuración ──────────────────────────────────────────
MIN_VALID_IV      = 0.01    # 1% mínimo para considerar IV válida
MAX_VALID_IV      = 5.00    # 500% máximo (instrumentos apalancados)
MIN_MID_PRICE     = 0.05    # contratos más baratos se ignoran
MONEYNESS_RANGE   = 0.30    # ±30% alrededor del ATM para la superficie


@dataclass
class IVPoint:
    """Un punto de la superficie IV: (strike, DTE, IV)."""
    expiration: str
    strike:     float
    dte:        int
    option_type: str          # "call" o "put"
    iv:         float
    moneyness:  float         # log(S/K) — negativo=OTM put, positivo=OTM call
    bid:        float = 0.0
    ask:        float = 0.0
    volume:     int   = 0
    oi:         int   = 0


@dataclass
class IVSurface:
    """
    Superficie de volatilidad implícita completa para un ticker.
    """
    ticker:           str
    ts:               float
    underlying_price: float
    atm_iv:           Optional[float]   = None
    skew_index:       Optional[float]   = None   # 25Δ put IV − 25Δ call IV
    term_slope:       Optional[float]   = None   # pendiente term structure
    points:           list = field(default_factory=list)  # list[IVPoint]

    # ── Constructor desde chain dict ──────────────────────

    @classmethod
    def from_chain(cls, chain: dict,
                   r: float = DEFAULT_RISK_FREE_RATE) -> "IVSurface":
        """
        Construye la superficie IV desde un chain normalizado
        (output de OptionChainFetcher).
        """
        import time

        ticker  = chain.get("ticker", "")
        S       = (chain.get("underlying", {}).get("price") or
                   chain.get("underlying", {}).get("mark") or 0)

        if not S:
            logger.warning(f"[IVSURFACE] {ticker} — sin precio subyacente")
            return cls(ticker=ticker, ts=time.time(), underlying_price=0)

        surface = cls(
            ticker=ticker,
            ts=chain.get("ts", time.time()),
            underlying_price=S,
        )

        for exp in chain.get("expirations", []):
            for opt_type in ["calls", "puts"]:
                contracts = chain.get(opt_type, {}).get(exp, {})
                for strike_str, c in contracts.items():
                    K   = float(strike_str)
                    dte = c.get("dte", 30)
                    bid = c.get("bid") or 0
                    ask = c.get("ask") or 0

                    if bid <= 0 or ask <= 0:
                        continue
                    mid = (bid + ask) / 2
                    if mid < MIN_MID_PRICE:
                        continue

                    # Sólo calcular IV dentro de ±MONEYNESS_RANGE del ATM
                    moneyness = math.log(S / K) if K > 0 else 0
                    if abs(moneyness) > MONEYNESS_RANGE:
                        continue

                    T    = max(dte, 0.5) / 365
                    flag = "call" if opt_type == "calls" else "put"

                    iv = implied_vol(mid, S=S, K=K, T=T, r=r, flag=flag)
                    if not iv:
                        # Fallback: usar IV de Schwab
                        raw = c.get("iv")
                        iv  = (raw / 100) if raw and raw > 0 else None

                    if not iv or iv < MIN_VALID_IV or iv > MAX_VALID_IV:
                        continue

                    point = IVPoint(
                        expiration  = exp,
                        strike      = K,
                        dte         = dte,
                        option_type = flag,
                        iv          = iv,
                        moneyness   = round(moneyness, 4),
                        bid         = bid,
                        ask         = ask,
                        volume      = c.get("volume") or 0,
                        oi          = c.get("oi") or 0,
                    )
                    surface.points.append(point)

        # Calcular métricas derivadas
        surface._compute_atm_iv()
        surface._compute_skew_index()
        surface._compute_term_slope()

        # Log robusto — cada métrica puede ser None si no hubo suficientes
        # puntos para calcularla. Evitamos `None * 100` y similares.
        def _fmt_pct(x):
            return f"{x*100:.1f}" if isinstance(x, (int, float)) else "—"

        def _fmt_num(x, nd=3):
            return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "—"

        logger.info(
            f"[IVSURFACE] {ticker} — {len(surface.points)} puntos | "
            f"ATM IV={_fmt_pct(surface.atm_iv)}% | "
            f"skew={_fmt_pct(surface.skew_index)}pts | "
            f"term slope={_fmt_num(surface.term_slope)}"
        )

        return surface

    # ── Métricas derivadas ─────────────────────────────────

    def _compute_atm_iv(self):
        """IV del strike más cercano al ATM en la primera expiración."""
        if not self.points:
            return

        # Tomar la primera expiración con puntos
        first_exp = self._first_exp()
        if not first_exp:
            return

        exp_points = [p for p in self.points if p.expiration == first_exp]
        if not exp_points:
            return

        # Strike más cercano al ATM (moneyness más cercano a 0)
        atm_candidates = sorted(exp_points, key=lambda p: abs(p.moneyness))
        if atm_candidates:
            # Promedio de call/put en el strike ATM
            atm_strike = atm_candidates[0].strike
            atm_pts = [p for p in exp_points if p.strike == atm_strike]
            self.atm_iv = sum(p.iv for p in atm_pts) / len(atm_pts)

    def _compute_skew_index(self):
        """
        Skew index = IV(25Δ put) - IV(25Δ call)
        Aproximación: puts OTM con moneyness ≈ -0.10 vs calls OTM con moneyness ≈ +0.10
        Positivo = put premium (normal), negativo = call skew (raro, mercado alcista extremo)

        Estrategia de expiración:
        1. Probamos expiraciones en orden cronológico hasta encontrar una que
           tenga AMBOS calls y puts en el rango 25Δ (±0.05..±0.15).
           En ETFs de alto precio (SPY/QQQ/IWM) las 0DTE/1DTE tienen opciones
           OTM muy baratas (mid < $0.05) que se descartan en el constructor,
           así que hay que saltar a expiraciones más lejanas.
        2. Si ninguna expiración sola cumple, hacemos un fallback con TODAS
           las expiraciones promediadas en el rango 0.05..0.15 (ancho).
        """
        if not self.points:
            return

        exps = sorted(set(p.expiration for p in self.points))

        # Intento 1: por expiración, tomamos la primera con call+put OTM válidos
        for exp in exps:
            exp_points = [p for p in self.points if p.expiration == exp]
            otm_calls = [p for p in exp_points
                         if p.option_type == "call" and 0.05 < p.moneyness < 0.15]
            otm_puts  = [p for p in exp_points
                         if p.option_type == "put"  and -0.15 < p.moneyness < -0.05]

            if otm_calls and otm_puts:
                avg_call_iv = sum(p.iv for p in otm_calls) / len(otm_calls)
                avg_put_iv  = sum(p.iv for p in otm_puts)  / len(otm_puts)
                self.skew_index = round(avg_put_iv - avg_call_iv, 4)
                return

        # Fallback: agregamos todas las expiraciones al cálculo
        otm_calls = [p for p in self.points
                     if p.option_type == "call" and 0.05 < p.moneyness < 0.15]
        otm_puts  = [p for p in self.points
                     if p.option_type == "put"  and -0.15 < p.moneyness < -0.05]
        if otm_calls and otm_puts:
            avg_call_iv = sum(p.iv for p in otm_calls) / len(otm_calls)
            avg_put_iv  = sum(p.iv for p in otm_puts)  / len(otm_puts)
            self.skew_index = round(avg_put_iv - avg_call_iv, 4)

    def _compute_term_slope(self):
        """
        Pendiente de la term structure: cambio promedio de ATM IV por DTE.
        Negativo = backwardation (IV cae con DTE = tensión corto plazo)
        Positivo = contango normal
        """
        atm_by_exp = self.get_term_structure()
        if len(atm_by_exp) < 2:
            return

        # Regresión lineal simple: pendiente de IV vs DTE
        n   = len(atm_by_exp)
        xs  = [p["dte"] for p in atm_by_exp]
        ys  = [p["atm_iv"] for p in atm_by_exp]
        x_m = sum(xs) / n
        y_m = sum(ys) / n
        num = sum((xs[i] - x_m) * (ys[i] - y_m) for i in range(n))
        den = sum((xs[i] - x_m) ** 2 for i in range(n))

        if den > 0:
            self.term_slope = round(num / den, 6)

    def _first_exp(self) -> str | None:
        if not self.points:
            return None
        exps = sorted(set(p.expiration for p in self.points))
        return exps[0] if exps else None

    # ── Queries útiles ─────────────────────────────────────

    def get_skew(self, expiration: str | None = None) -> dict:
        """
        Retorna la sonrisa de volatilidad para una expiración.
        {calls: [{strike, moneyness, iv}], puts: [...]}
        """
        exp = expiration or self._first_exp()
        if not exp:
            return {"calls": [], "puts": []}

        calls = sorted(
            [{"strike": p.strike, "moneyness": p.moneyness,
              "iv": round(p.iv * 100, 2), "dte": p.dte}
             for p in self.points
             if p.expiration == exp and p.option_type == "call"],
            key=lambda x: x["strike"]
        )
        puts = sorted(
            [{"strike": p.strike, "moneyness": p.moneyness,
              "iv": round(p.iv * 100, 2), "dte": p.dte}
             for p in self.points
             if p.expiration == exp and p.option_type == "put"],
            key=lambda x: x["strike"]
        )
        return {"calls": calls, "puts": puts}

    def get_term_structure(self) -> list[dict]:
        """
        IV ATM por expiración (term structure).
        [{expiration, dte, atm_iv}]
        """
        exps = sorted(set(p.expiration for p in self.points))
        result = []

        for exp in exps:
            exp_pts = [p for p in self.points if p.expiration == exp]
            if not exp_pts:
                continue

            dte = exp_pts[0].dte

            # Strike más cercano ATM
            atm_pts = sorted(exp_pts, key=lambda p: abs(p.moneyness))
            if not atm_pts:
                continue

            atm_strike = atm_pts[0].strike
            atm_strike_pts = [p for p in exp_pts if p.strike == atm_strike]
            atm_iv = sum(p.iv for p in atm_strike_pts) / len(atm_strike_pts)

            result.append({
                "expiration": exp,
                "dte":        dte,
                "atm_iv":     round(atm_iv * 100, 2),  # en %
            })

        return result

    def get_surface_grid(self) -> list[dict]:
        """
        Grid completo de la superficie (strike × DTE).
        [{expiration, strike, moneyness, call_iv, put_iv, mid_iv, dte}]
        """
        from collections import defaultdict
        grid = defaultdict(dict)

        for p in self.points:
            key = (p.expiration, p.strike)
            grid[key]["expiration"] = p.expiration
            grid[key]["strike"]     = p.strike
            grid[key]["moneyness"]  = p.moneyness
            grid[key]["dte"]        = p.dte
            if p.option_type == "call":
                grid[key]["call_iv"] = round(p.iv * 100, 2)
            else:
                grid[key]["put_iv"]  = round(p.iv * 100, 2)

        result = []
        for row in grid.values():
            c_iv = row.get("call_iv")
            p_iv = row.get("put_iv")
            if c_iv and p_iv:
                row["mid_iv"] = round((c_iv + p_iv) / 2, 2)
            elif c_iv:
                row["mid_iv"] = c_iv
            else:
                row["mid_iv"] = p_iv
            result.append(row)

        return sorted(result, key=lambda x: (x["expiration"], x["strike"]))

    def get_iv_at(self, strike: float, expiration: str,
                  option_type: str = "call") -> float | None:
        """IV para un strike/exp/tipo específico."""
        matches = [
            p.iv for p in self.points
            if abs(p.strike - strike) < 0.01
            and p.expiration == expiration
            and p.option_type == option_type
        ]
        return round(sum(matches) / len(matches) * 100, 2) if matches else None

    # ── Resumen para Claude ────────────────────────────────

    def to_summary(self) -> str:
        """
        Genera un resumen en texto de la superficie IV para
        incluir en el prompt de Claude como contexto de mercado.
        """
        lines = [
            f"=== IV Surface: {self.ticker} | Precio: ${self.underlying_price:.2f} ===",
        ]

        if self.atm_iv:
            lines.append(f"ATM IV: {self.atm_iv*100:.1f}%")

        if self.skew_index is not None:
            skew_desc = "Put premium (bearish skew)" if self.skew_index > 0 else "Call skew (bullish)"
            lines.append(f"Skew index: {self.skew_index*100:.1f} pts → {skew_desc}")

        if self.term_slope is not None:
            if self.term_slope < 0:
                lines.append(f"Term structure: BACKWARDATION (slope={self.term_slope:.4f}) — tensión a corto plazo")
            else:
                lines.append(f"Term structure: contango normal (slope={self.term_slope:.4f})")

        # Term structure
        ts = self.get_term_structure()
        if ts:
            lines.append("\nTerm Structure (ATM IV por vencimiento):")
            for row in ts:
                lines.append(
                    f"  {row['expiration']} ({row['dte']} DTE): IV={row['atm_iv']:.1f}%"
                )

        # Skew de la primera expiración
        skew = self.get_skew()
        if skew["calls"] or skew["puts"]:
            first_exp = self._first_exp()
            lines.append(f"\nSmile {first_exp}:")
            # Mostrar solo 5 strikes relevantes alrededor del ATM
            all_pts = sorted(
                skew["calls"] + skew["puts"],
                key=lambda x: abs(x["moneyness"])
            )[:10]
            all_pts.sort(key=lambda x: x["strike"])
            for pt in all_pts:
                lines.append(
                    f"  K={pt['strike']:.1f} ({pt['moneyness']:+.2f}) → IV={pt['iv']:.1f}%"
                )

        return "\n".join(lines)
