# ============================================================
#  EOLO v2 — Mispricing Detection Engine
#
#  Escanea la cadena de opciones buscando anomalías de precio
#  que representen ventajas estructurales aprovechables.
#
#  Checks implementados:
#  1. PUT-CALL PARITY    : C - P ≠ S - K·e^(-rT)  → violación de paridad
#  2. IV SKEW JUMP       : Salto brusco de IV entre strikes adyacentes
#  3. BID-ASK vs THEO    : Precio de mercado muy lejos del BSM teórico
#  4. BUTTERFLY CHECK    : B(k) = C(k-Δ) - 2C(k) + C(k+Δ) < 0  (arbitraje)
#  5. CALENDAR SPREAD    : IV muy distinta para mismo strike distintas expiry
#  6. ULTRA WIDE SPREAD  : Bid-ask spread excesivo vs precio del contrato
#
#  Output por anomalía:
#    {
#      "type":       "PUT_CALL_PARITY",
#      "ticker":     "SOXL",
#      "severity":   "HIGH" | "MEDIUM" | "LOW",
#      "description": "...",
#      "action":     "BUY_CALL" | "BUY_PUT" | "SKIP",
#      "details":    { ... datos específicos ... }
#    }
#
#  Uso:
#    scanner = MispricingScanner()
#    alerts = scanner.scan(chain_dict)
# ============================================================
import math
from typing import Literal
from loguru import logger

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from greeks import BSGreeks, implied_vol, DEFAULT_RISK_FREE_RATE

# ── Umbrales configurables ─────────────────────────────────

# 1. Put-Call Parity: diferencia mínima para considerar violación
PCP_MIN_EDGE          = 0.10   # $0.10 mínimo edge neto (después de costs)
PCP_MAX_SPREAD_PCT    = 15.0   # % máximo bid-ask spread para ambas piernas

# 2. IV Skew Jump: salto de IV entre strikes adyacentes
SKEW_JUMP_THRESHOLD   = 0.15   # 15 puntos de IV (ej. 30% → 45%)
SKEW_MIN_IV           = 0.05   # IV mínima para considerar válida

# 3. Bid-Ask vs Theoretical (BSM mispricing)
THEO_EDGE_MIN         = 0.20   # $0.20 por contrato edge mínimo
THEO_EDGE_PCT         = 0.25   # 25% del precio teórico

# 4. Butterfly Check
BUTTERFLY_THRESHOLD   = -0.02  # < -$0.02 → arbitraje de butterfly

# 5. Calendar Spread IV Gap
CALENDAR_IV_GAP       = 0.20   # 20 puntos IV entre expiraciones

# 6. Ultra Wide Spread
WIDE_SPREAD_PCT       = 30.0   # spread > 30% del mid = liquidez pésima
WIDE_SPREAD_MIN_PRICE = 0.10   # no alertar contratos de centavos


class MispricingScanner:
    """
    Escanea una cadena de opciones normalizada (del OptionChainFetcher)
    y detecta anomalías de precio aprovechables.
    """

    def __init__(self, r: float = DEFAULT_RISK_FREE_RATE):
        self.r = r

    def scan(self, chain: dict) -> list[dict]:
        """
        Escanea todos los checks sobre la cadena.
        Retorna lista de alertas ordenadas por severidad.
        """
        alerts = []
        ticker = chain.get("ticker", "")
        underlying = chain.get("underlying", {})
        S = underlying.get("price") or underlying.get("mark")

        if not S:
            logger.warning(f"[MISPRICE] {ticker} — sin precio subyacente")
            return []

        expirations = chain.get("expirations", [])

        for exp in expirations:
            calls = chain.get("calls", {}).get(exp, {})
            puts  = chain.get("puts",  {}).get(exp, {})

            if not calls or not puts:
                continue

            # DTE desde primer contrato disponible
            sample = next(iter(calls.values()), {})
            dte    = sample.get("dte", 30)
            T      = max(dte, 0.5) / 365

            # ── Check 1: Put-Call Parity ──────────────────
            alerts += self._check_pcp(ticker, exp, T, S, calls, puts)

            # ── Check 2: IV Skew Jumps ────────────────────
            alerts += self._check_skew_jumps(ticker, exp, T, S, calls, "call")
            alerts += self._check_skew_jumps(ticker, exp, T, S, puts,  "put")

            # ── Check 3: BSM Mispricing ────────────────────
            alerts += self._check_theo_mispricing(ticker, exp, T, S, calls, "call")
            alerts += self._check_theo_mispricing(ticker, exp, T, S, puts,  "put")

            # ── Check 4: Butterfly Arbitrage ──────────────
            alerts += self._check_butterfly(ticker, exp, calls, "call")
            alerts += self._check_butterfly(ticker, exp, puts,  "put")

        # ── Check 5: Calendar IV Gap ──────────────────────
        if len(expirations) >= 2:
            alerts += self._check_calendar_gaps(ticker, chain, S)

        # ── Check 6: Ultra Wide Spreads ───────────────────
        for exp in expirations:
            for opt_type, contracts in [("call", chain["calls"].get(exp, {})),
                                         ("put",  chain["puts"].get(exp, {}))]:
                alerts += self._check_wide_spreads(ticker, exp, contracts, opt_type)

        # Ordenar: HIGH primero, luego MEDIUM, LOW
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        alerts.sort(key=lambda a: severity_order.get(a.get("severity", "LOW"), 2))

        if alerts:
            logger.info(f"[MISPRICE] {ticker} — {len(alerts)} anomalías detectadas")

        return alerts

    # ── Check 1: Put-Call Parity ───────────────────────────

    def _check_pcp(self, ticker, exp, T, S, calls, puts) -> list[dict]:
        """
        Put-Call Parity: C - P = S·e^(-qT) - K·e^(-rT)
        Si la diferencia supera PCP_MIN_EDGE, hay edge.

        Edge positivo: call está cara / put está barata → buy put
        Edge negativo: put está cara / call está barata → buy call
        """
        alerts = []
        r = self.r

        # Iteramos strikes que tienen tanto call como put
        common_strikes = set(calls.keys()) & set(puts.keys())

        for strike_str in common_strikes:
            call = calls[strike_str]
            put  = puts[strike_str]
            K    = float(strike_str)

            c_bid, c_ask = call.get("bid"), call.get("ask")
            p_bid, p_ask = put.get("bid"),  put.get("ask")

            if None in (c_bid, c_ask, p_bid, p_ask):
                continue
            if c_bid <= 0 or p_bid <= 0:
                continue

            # Spread check — no entrar si spreads son muy anchos
            c_spread_pct = (c_ask - c_bid) / ((c_bid + c_ask) / 2) * 100
            p_spread_pct = (p_ask - p_bid) / ((p_bid + p_ask) / 2) * 100
            if c_spread_pct > PCP_MAX_SPREAD_PCT or p_spread_pct > PCP_MAX_SPREAD_PCT:
                continue

            # Mids
            c_mid = (c_bid + c_ask) / 2
            p_mid = (p_bid + p_ask) / 2

            # Valor teórico de la diferencia C - P
            # C - P = S·e^(-qT) - K·e^(-rT)  (con q=0 para ETFs sin dividendo)
            pcp_theoretical = S - K * math.exp(-r * T)
            pcp_market      = c_mid - p_mid
            pcp_diff        = pcp_market - pcp_theoretical

            edge = abs(pcp_diff)
            if edge < PCP_MIN_EDGE:
                continue

            # Determinar acción
            if pcp_diff > 0:
                # call cara, put barata → comprar put / vender call sintético
                action = "BUY_PUT"
                desc   = f"Call sobrevaluada vs Put: diferencia ${pcp_diff:.3f} (teórico ${pcp_theoretical:.3f}, mercado ${pcp_market:.3f})"
            else:
                # put cara, call barata → comprar call
                action = "BUY_CALL"
                desc   = f"Put sobrevaluada vs Call: diferencia ${pcp_diff:.3f} (teórico ${pcp_theoretical:.3f}, mercado ${pcp_market:.3f})"

            severity = "HIGH" if edge > 0.30 else "MEDIUM" if edge > 0.15 else "LOW"

            alerts.append({
                "type":       "PUT_CALL_PARITY",
                "ticker":     ticker,
                "expiration": exp,
                "strike":     K,
                "severity":   severity,
                "edge":       round(edge, 4),
                "action":     action,
                "description": desc,
                "details": {
                    "call_bid": c_bid, "call_ask": c_ask, "call_mid": round(c_mid, 4),
                    "put_bid":  p_bid, "put_ask":  p_ask, "put_mid":  round(p_mid, 4),
                    "pcp_theoretical": round(pcp_theoretical, 4),
                    "pcp_market":      round(pcp_market, 4),
                    "pcp_diff":        round(pcp_diff, 4),
                    "S": S, "K": K, "T": round(T, 4),
                },
            })

        return alerts

    # ── Check 2: IV Skew Jumps ─────────────────────────────

    def _check_skew_jumps(self, ticker, exp, T, S,
                           contracts, opt_type) -> list[dict]:
        """
        Detecta saltos bruscos de IV entre strikes adyacentes.
        Un salto ≥ SKEW_JUMP_THRESHOLD en IV puede indicar:
        - Error de market maker
        - Oportunidad de vol spread
        """
        alerts = []

        # Construir lista ordenada de (strike, IV)
        iv_by_strike = []
        for strike_str, c in contracts.items():
            iv_raw = c.get("iv")
            bid = c.get("bid", 0) or 0
            ask = c.get("ask", 0) or 0
            mid = (bid + ask) / 2
            if mid > 0:
                # Recalcular IV desde mid si podemos
                K = float(strike_str)
                iv_calc = implied_vol(mid, S=S, K=K, T=T, r=self.r, flag=opt_type)
                iv = iv_calc if iv_calc else (iv_raw / 100 if iv_raw else None)
            else:
                iv = (iv_raw / 100) if iv_raw else None

            if iv and iv > SKEW_MIN_IV:
                iv_by_strike.append((float(strike_str), iv))

        iv_by_strike.sort(key=lambda x: x[0])

        for i in range(1, len(iv_by_strike)):
            k_prev, iv_prev = iv_by_strike[i - 1]
            k_curr, iv_curr = iv_by_strike[i]

            jump = abs(iv_curr - iv_prev)
            if jump < SKEW_JUMP_THRESHOLD:
                continue

            severity = "HIGH" if jump > 0.30 else "MEDIUM" if jump > 0.20 else "LOW"

            alerts.append({
                "type":       "IV_SKEW_JUMP",
                "ticker":     ticker,
                "expiration": exp,
                "severity":   severity,
                "option_type": opt_type,
                "edge":       round(jump, 4),
                "action":     f"REVIEW_{opt_type.upper()}_SPREAD",
                "description": (
                    f"IV jump de {iv_prev*100:.1f}% → {iv_curr*100:.1f}% "
                    f"entre strikes {k_prev} y {k_curr} ({opt_type}s)"
                ),
                "details": {
                    "strike_low":  k_prev, "iv_low":  round(iv_prev * 100, 2),
                    "strike_high": k_curr, "iv_high": round(iv_curr * 100, 2),
                    "jump_pct":    round(jump * 100, 2),
                },
            })

        return alerts

    # ── Check 3: BSM Mispricing ────────────────────────────

    def _check_theo_mispricing(self, ticker, exp, T, S,
                                contracts, opt_type) -> list[dict]:
        """
        Compara mid price vs precio teórico BSM.
        Edge = |mid - BSM_price|
        Solo alertar cuando el mid está por DEBAJO del teórico
        (opción subvaluada → oportunidad de compra).
        """
        alerts = []

        for strike_str, c in contracts.items():
            bid = c.get("bid") or 0
            ask = c.get("ask") or 0
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            K   = float(strike_str)
            iv_raw = c.get("iv")
            sigma = (iv_raw / 100) if (iv_raw and iv_raw > 0) else 0.30

            try:
                bs    = BSGreeks(S=S, K=K, T=T, r=self.r, sigma=sigma)
                theo  = bs.price(opt_type)
            except Exception:
                continue

            if theo <= 0:
                continue

            edge     = theo - mid   # positivo: opción barata (buy signal)
            edge_pct = edge / theo

            if edge < THEO_EDGE_MIN or edge_pct < THEO_EDGE_PCT:
                continue

            severity = "HIGH" if edge_pct > 0.50 else "MEDIUM" if edge_pct > 0.35 else "LOW"

            alerts.append({
                "type":       "BSM_MISPRICING",
                "ticker":     ticker,
                "expiration": exp,
                "strike":     K,
                "severity":   severity,
                "option_type": opt_type,
                "edge":       round(edge, 4),
                "action":     f"BUY_{opt_type.upper()}",
                "description": (
                    f"{opt_type.upper()} K={K} subvaluada: mid=${mid:.3f}, "
                    f"BSM teórico=${theo:.3f} (edge ${edge:.3f} / {edge_pct*100:.0f}%)"
                ),
                "details": {
                    "strike":   K,
                    "bid":      bid,  "ask": ask,  "mid": round(mid, 4),
                    "theo":     round(theo, 4),
                    "edge":     round(edge, 4),
                    "edge_pct": round(edge_pct * 100, 2),
                    "iv":       round(sigma * 100, 2),
                    "dte":      c.get("dte"),
                },
            })

        return alerts

    # ── Check 4: Butterfly Arbitrage ──────────────────────

    def _check_butterfly(self, ticker, exp, contracts, opt_type) -> list[dict]:
        """
        Butterfly arbitrage: C(K-Δ) - 2·C(K) + C(K+Δ) < 0
        Este valor debe ser ≥ 0 para que no haya arbitraje.
        Si es negativo, existe una violación de convexidad.
        """
        alerts = []

        strikes_sorted = sorted(
            [(float(s), c) for s, c in contracts.items()
             if c.get("bid") and c.get("ask")],
            key=lambda x: x[0]
        )

        if len(strikes_sorted) < 3:
            return []

        for i in range(1, len(strikes_sorted) - 1):
            k_low,  c_low  = strikes_sorted[i - 1]
            k_mid,  c_mid  = strikes_sorted[i]
            k_high, c_high = strikes_sorted[i + 1]

            # Usar mids
            mid_low  = (c_low["bid"]  + c_low["ask"])  / 2
            mid_mid  = (c_mid["bid"]  + c_mid["ask"])  / 2
            mid_high = (c_high["bid"] + c_high["ask"]) / 2

            butterfly = mid_low - 2 * mid_mid + mid_high

            if butterfly >= BUTTERFLY_THRESHOLD:
                continue

            edge = abs(butterfly)
            severity = "HIGH" if edge > 0.10 else "MEDIUM" if edge > 0.05 else "LOW"

            alerts.append({
                "type":       "BUTTERFLY_ARBITRAGE",
                "ticker":     ticker,
                "expiration": exp,
                "severity":   severity,
                "option_type": opt_type,
                "edge":       round(edge, 4),
                "action":     f"BUY_{opt_type.upper()}_BUTTERFLY",
                "description": (
                    f"Butterfly negativo en {opt_type}s K={k_mid}: "
                    f"B={butterfly:.4f} < 0 (arbitraje de convexidad)"
                ),
                "details": {
                    "k_low": k_low,   "mid_low":  round(mid_low, 4),
                    "k_mid": k_mid,   "mid_mid":  round(mid_mid, 4),
                    "k_high": k_high, "mid_high": round(mid_high, 4),
                    "butterfly": round(butterfly, 4),
                },
            })

        return alerts

    # ── Check 5: Calendar IV Gap ───────────────────────────

    def _check_calendar_gaps(self, ticker, chain, S) -> list[dict]:
        """
        Detecta gaps grandes de IV para el mismo strike entre
        expiraciones contiguas. Sugiere calendar spread.
        """
        alerts = []
        expirations = chain.get("expirations", [])

        for opt_type in ["calls", "puts"]:
            for i in range(len(expirations) - 1):
                exp1 = expirations[i]
                exp2 = expirations[i + 1]

                contracts1 = chain[opt_type].get(exp1, {})
                contracts2 = chain[opt_type].get(exp2, {})

                common = set(contracts1.keys()) & set(contracts2.keys())

                for strike_str in common:
                    c1 = contracts1[strike_str]
                    c2 = contracts2[strike_str]

                    K    = float(strike_str)
                    dte1 = c1.get("dte", 30)
                    dte2 = c2.get("dte", 60)
                    T1   = max(dte1, 0.5) / 365
                    T2   = max(dte2, 0.5) / 365

                    # Calcular IV de mercado para ambas expiraciones
                    def get_iv(c, T, flag):
                        bid = c.get("bid", 0) or 0
                        ask = c.get("ask", 0) or 0
                        mid = (bid + ask) / 2
                        if mid <= 0:
                            return None
                        iv = implied_vol(mid, S=S, K=K, T=T, r=self.r, flag=flag)
                        if iv:
                            return iv
                        raw = c.get("iv")
                        return (raw / 100) if raw and raw > 0 else None

                    flag = "call" if opt_type == "calls" else "put"
                    iv1 = get_iv(c1, T1, flag)
                    iv2 = get_iv(c2, T2, flag)

                    if not iv1 or not iv2:
                        continue

                    gap = abs(iv1 - iv2)
                    if gap < CALENDAR_IV_GAP:
                        continue

                    # La exp con IV mayor está sobrevaluada
                    if iv1 > iv2:
                        sell_exp, buy_exp = exp1, exp2
                        sell_iv, buy_iv   = iv1, iv2
                    else:
                        sell_exp, buy_exp = exp2, exp1
                        sell_iv, buy_iv   = iv2, iv1

                    severity = "HIGH" if gap > 0.40 else "MEDIUM" if gap > 0.30 else "LOW"

                    alerts.append({
                        "type":       "CALENDAR_IV_GAP",
                        "ticker":     ticker,
                        "strike":     K,
                        "severity":   severity,
                        "option_type": flag,
                        "edge":       round(gap, 4),
                        "action":     "CALENDAR_SPREAD",
                        "description": (
                            f"Calendar gap {flag} K={K}: "
                            f"IV {sell_exp}={sell_iv*100:.1f}% vs {buy_exp}={buy_iv*100:.1f}% "
                            f"(gap {gap*100:.1f} pts)"
                        ),
                        "details": {
                            "strike":   K,
                            "sell_exp": sell_exp, "sell_iv": round(sell_iv * 100, 2),
                            "buy_exp":  buy_exp,  "buy_iv":  round(buy_iv * 100, 2),
                            "gap_pct":  round(gap * 100, 2),
                        },
                    })

        return alerts

    # ── Check 6: Ultra Wide Spreads ────────────────────────

    def _check_wide_spreads(self, ticker, exp, contracts, opt_type) -> list[dict]:
        """
        Detecta contratos con bid-ask spreads muy anchos.
        No son necesariamente oportunidades, pero son señal de
        baja liquidez y se deben evitar en ejecución.
        """
        alerts = []

        for strike_str, c in contracts.items():
            bid = c.get("bid") or 0
            ask = c.get("ask") or 0
            if bid <= 0 or ask <= 0:
                continue

            mid = (bid + ask) / 2
            if mid < WIDE_SPREAD_MIN_PRICE:
                continue

            spread_pct = (ask - bid) / mid * 100
            if spread_pct <= WIDE_SPREAD_PCT:
                continue

            severity = "HIGH" if spread_pct > 60 else "MEDIUM" if spread_pct > 45 else "LOW"

            alerts.append({
                "type":       "WIDE_BID_ASK",
                "ticker":     ticker,
                "expiration": exp,
                "strike":     float(strike_str),
                "severity":   severity,
                "option_type": opt_type,
                "edge":       0.0,
                "action":     "AVOID",
                "description": (
                    f"{opt_type.upper()} K={strike_str} spread muy ancho: "
                    f"{spread_pct:.1f}% (bid={bid}, ask={ask}, mid={mid:.2f})"
                ),
                "details": {
                    "strike":     float(strike_str),
                    "bid":        bid, "ask": ask,   "mid": round(mid, 4),
                    "spread_pct": round(spread_pct, 2),
                },
            })

        return alerts


# ── Función de conveniencia ────────────────────────────────

def scan_chain(chain: dict,
               r: float = DEFAULT_RISK_FREE_RATE) -> list[dict]:
    """
    Shorthand para escanear una cadena sin instanciar la clase.
    """
    scanner = MispricingScanner(r=r)
    return scanner.scan(chain)


def format_alerts(alerts: list[dict]) -> str:
    """
    Formatea las alertas como texto legible para logs o Claude.
    """
    if not alerts:
        return "Sin anomalías detectadas."

    lines = []
    for a in alerts:
        lines.append(
            f"[{a['severity']}] {a['type']} — {a['ticker']} "
            f"| {a.get('expiration','')} K={a.get('strike','')} "
            f"| Edge: ${a.get('edge', 0):.3f} "
            f"| Acción: {a['action']}\n"
            f"  → {a['description']}"
        )
    return "\n".join(lines)
