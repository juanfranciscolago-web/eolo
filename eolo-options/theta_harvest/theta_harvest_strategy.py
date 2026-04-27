# ============================================================
#  EOLO v2 — Theta Harvest Strategy v3 (Credit Spreads)
#
#  Versión "Always-In" multi-DTE con tranche exits y pivot analysis.
#
#  ── Opción B (validado en backtest 2022-2025, SPY PF 9.29 WR 93.2%) ──
#
#  DTEs objetivo: 0, 1, 2 (bajamos de 5 a 3 — DTE 3/4 descartados PF < 3)
#  Contratos: 3 por spread (1 por tranche), mismo strike — distintos targets:
#    T0 (35%):   cierra al capturar 35% del crédito → income rápido, libera capital
#    T1 (65%):   cierra al capturar 65% del crédito → sweet spot theta decay
#    T2 (EXPIRY):aguanta hasta vencimiento → máxima captura de theta
#  Stop Loss: 125% del crédito (bajado de 150% — ahorra ~$4k/1465 spreads en BT)
#  CALL spreads: solo con señal BEARISH confirmada (no en días neutros)
#
#  Capital máximo en papel: 3 DTEs × 3 tranches × $500 = $4,500 por ticker
#  Riesgo real: mucho menor — SL a 125%, T0 y T1 cierran antes de expiry.
#
#  Exits (orden de prioridad, aplica por TRANCHE individualmente):
#    1. VVIX panic (> 110)           → cerrar TODOS los tranches
#    2. Stop loss (≥ 125% crédito)   → cerrar TODOS los tranches del spread
#    3. VIX spike (+3 pts entrada)   → salida preventiva
#    4. Delta drift (short Δ > 0.35) → short leg demasiado ATM
#    5. SPY caída > 0.8% en 30 min   → solo PUT spreads
#    6. Profit target per-tranche:
#         T0 → spread ≤ 65% crédito (capturamos 35%)
#         T1 → spread ≤ 35% crédito (capturamos 65%)
#         T2 → sin profit target (aguanta hasta EOD/TIME_STOP)
#    7. Time stop EOD diferenciado por DTE:
#         0DTE  → 14:30 ET | 1–2 DTE → 15:15 ET
#
#  Spread widths: SPY $5 | TQQQ $5
# ============================================================
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from loguru import logger

# ── Configuración por ticker ──────────────────────────────

TICKER_CONFIG: dict[str, dict] = {
    "SPY": {
        "spread_width":  5.0,   # $5 entre strikes (era $2)
        "delta_min_abs": 0.15,  # piso absoluto (VERY_LOW risk)
        "delta_max_abs": 0.30,  # techo absoluto (MID risk)
        "min_credit":    0.40,  # crédito mínimo neto (más ancho = más crédito esperado)
        "max_dte":       4,     # máximo DTE a abrir
    },
    "TQQQ": {
        "spread_width":  5.0,
        "delta_min_abs": 0.15,
        "delta_max_abs": 0.30,
        "min_credit":    0.35,
        "max_dte":       4,
    },
}

# DTEs objetivo — reducido a los 3 mejores según backtest:
#   DTE 0: PF 9.97 WR 96.7%  |  DTE 1: PF 17.82 WR 98.1%  |  DTE 2: PF 4.26 WR 79.7%
#   DTE 3: PF 2.90 (descartado)  |  DTE 4: PF 1.79 (descartado)
TARGET_DTES: list[int] = [0, 1, 2]

# Tranche profit targets — 3 contratos por spread, cada uno con distinto exit:
#   T0: capturar 35% del crédito (exit rápido, income diario asegurado)
#   T1: capturar 65% del crédito (sweet spot, target estándar)
#   T2: None = aguantar hasta EOD/TIME_STOP (máxima captura de theta)
# Diferencia T0 vs T2 en backtest: solo $2.51/trade → riesgo de T2 no justificado en live.
TRANCHE_PROFIT_TARGETS: list = [0.35, 0.65, None]

# Hora de entrada diaria (ET, formato decimal)
# 9:45 ET: evitar los primeros 15 min de alta volatilidad post-apertura.
# Ventana hasta 14:00 ET: da suficiente margen para que opere sin forzar entradas tardías.
ENTRY_HOUR_ET        = 9.75   # 9:45 ET — tras el gap fill inicial de apertura
ENTRY_WINDOW_MINUTES = 255    # 255 min = 9:45 → 14:00 ET (ventana amplia)

# EOD force-close — diferenciado por DTE
# 0DTE: gamma explota en los últimos 30 min → cerrar a las 15:30 ET.
#   Captura todo el theta decay del día con solo 30 min de riesgo gamma final.
# 1–4 DTE: gamma bajo → 15:50 ET (10 min antes de cierre = buffer seguro).
FORCE_CLOSE_HOUR_0DTE    = 15.50   # 15:30 ET — solo 0DTE
FORCE_CLOSE_HOUR_1TO4DTE = 15.833  # 15:50 ET — 1, 2, 3 y 4 DTE

# Exits
PROFIT_TARGET_PCT = 0.65   # default (T1) — cada tranche usa TRANCHE_PROFIT_TARGETS[i]
STOP_LOSS_MULT    = 1.25   # cerrar cuando spread vale ≥ 125% del crédito (era 1.50)
                            # Backtest: bajó drag SL de -$16,386 → -$12,077 con mismo # de stops

# VIX / volatilidad exits
VIX_MAX_ENTRY        = 40.0   # no entrar si VIX > 40 (era 35)
VIX_SPIKE_DELTA      = 3.0    # salir si VIX sube > 3pts desde la entrada
VVIX_PANIC_THRESHOLD = 110.0  # cerrar TODO si VVIX > 110 (100 era demasiado sensible)
DELTA_DRIFT_MAX      = 0.35   # salir si short leg delta > 0.35 (ITM drift)
SPY_DROP_PCT_30M     = 0.8    # salir PUT spreads si SPY baja > 0.8% en 30 min

# Minutos mínimos hasta expiración para entrar
MIN_MINUTES_TO_EXP = 20

# ── Payoff thresholds para selección de mejor spread ─────
# Payoff ratio = net_credit / max_risk (credit / (width - credit))
# Un $5 spread con $0.50 credit → payoff = 0.50/4.50 = 11.1%
# Mínimos por risk level (baseline, sin VIX override):
MIN_PAYOFF_RATIO = {
    "VERY_LOW": 0.08,   # ≥ 8%  (muy OTM, poco crédito, aceptable)
    "LOW":      0.10,   # ≥ 10%
    "MID":      0.13,   # ≥ 13% (más cerca ATM, exigimos más crédito)
}

# ── Tabla dinámica VIX → min_credit y min_payoff ─────────
# Lógica: VIX bajo = IV baja = créditos menores pero mercado más tranquilo.
# Ajustamos los filtros proporcionalmente para no perdernos setups válidos.
# Cada tupla: (vix_max_excl, min_credit_spy, min_credit_tqqq, min_payoff_multiplier)
#   min_payoff_multiplier se aplica sobre MIN_PAYOFF_RATIO base.
VIX_CREDIT_TABLE: list[tuple[float, float, float, float]] = [
    # vix_ceil  spy_min  tqqq_min  payoff_mult
    (13.0,      0.15,    0.12,     0.55),   # VIX < 13: mercado ultra-calmo
    (16.0,      0.22,    0.18,     0.65),   # VIX 13–16: bajo normal
    (20.0,      0.30,    0.25,     0.80),   # VIX 16–20: normal-bajo
    (25.0,      0.40,    0.35,     1.00),   # VIX 20–25: normal (defaults actuales)
    (30.0,      0.50,    0.42,     1.10),   # VIX 25–30: elevado
    (float("inf"), 0.65, 0.55,    1.20),   # VIX > 30: alta volatilidad
]


def _vix_credit_thresholds(
    ticker: str,
    vix: Optional[float],
    base_payoff_ratios: dict,
) -> tuple[float, dict]:
    """
    Devuelve (min_credit, min_payoff_ratios) ajustados al nivel de VIX actual.
    Si vix es None, usa los defaults sin cambios.
    """
    if vix is None:
        base = TICKER_CONFIG.get(ticker, {}).get("min_credit", 0.40)
        return base, base_payoff_ratios

    for vix_ceil, spy_min, tqqq_min, payoff_mult in VIX_CREDIT_TABLE:
        if vix < vix_ceil:
            credit_min = spy_min if ticker == "SPY" else tqqq_min
            adjusted_payoff = {
                k: round(v * payoff_mult, 4)
                for k, v in base_payoff_ratios.items()
            }
            return credit_min, adjusted_payoff

    # fallback (no debería llegar acá)
    return TICKER_CONFIG.get(ticker, {}).get("min_credit", 0.40), base_payoff_ratios
# Breakeven move máximo tolerable (% del subyacente) para entrar
# Si el mercado solo necesita moverse X% para que el spread expire worthless
# (= nuestra pérdida máxima), ese X es el "margen de seguridad"
MIN_BREAKEVEN_MOVE_PCT = {
    "VERY_LOW": 0.60,   # ≥ 0.60% de margen de seguridad
    "LOW":      0.40,
    "MID":      0.25,
}


# ── Data class del signal ─────────────────────────────────

@dataclass
class ThetaHarvestSignal:
    """
    Señal de trade para un credit spread generada por scan_theta_harvest.

    Con tranche exits, se crean 3 señales por spread (misma strike, distintos targets).
    Usar scan_theta_harvest_tranches() para obtener la lista completa.
    """
    ticker:           str
    spread_type:      str          # "put_credit_spread" | "call_credit_spread"
    expiration:       str          # YYYY-MM-DD
    dte:              int
    short_strike:     float
    long_strike:      float
    short_delta:      float
    net_credit:       float
    short_mark:       float
    long_mark:        float
    profit_target:    float        # valor en $ del spread para cerrar (baked-in por tranche)
    stop_loss:        float
    spread_width:     float
    max_risk:         float
    risk_level:       str = "MID"  # VERY_LOW / LOW / MID
    payoff_ratio:     float = 0.0
    breakeven_move_pct: float = 0.0
    composite_score:  float = 0.0
    annualized_yield: float = 0.0
    reason:           str = ""
    vix_at_entry:     Optional[float] = None
    underlying_price: Optional[float] = None

    # Tranche info — identifica este contrato dentro del set de 3 por spread
    tranche_id:     int            = 0     # 0=rápido, 1=estándar, 2=expiry
    tranche_target: Optional[float] = 0.65 # fracción del crédito a capturar; None=aguantar EOD

    def to_decision(self, contracts: int = 1) -> dict:
        """
        Serializa la señal como dict para Firestore / orchestrator.
        profit_target ya está computado por tranche en self.profit_target.
        tranche_target=None significa no usar profit target (aguantar hasta TIME_STOP).
        """
        return {
            "action":            "SELL_SPREAD",
            "ticker":            self.ticker,
            "spread_type":       self.spread_type,
            "expiration":        self.expiration,
            "dte":               self.dte,
            "short_strike":      self.short_strike,
            "long_strike":       self.long_strike,
            "short_delta":       self.short_delta,
            "net_credit":        round(self.net_credit, 2),
            "profit_target":     round(self.profit_target, 2),
            "stop_loss":         round(self.stop_loss, 2),
            "spread_width":      self.spread_width,
            "max_risk":          round(self.max_risk, 2),
            "contracts":         contracts,
            "strategy":          "THETA_HARVEST",
            "risk_level":        self.risk_level,
            "payoff_ratio":      round(self.payoff_ratio, 4),
            "breakeven_move_pct": round(self.breakeven_move_pct, 3),
            "composite_score":   round(self.composite_score, 1),
            "annualized_yield":  round(self.annualized_yield, 1),
            "reason":            self.reason,
            "vix_at_entry":      self.vix_at_entry,
            "underlying_price":  self.underlying_price,
            # Tranche fields — usados por evaluate_open_position()
            "tranche_id":        self.tranche_id,
            "tranche_target":    self.tranche_target,
        }


# ── Payoff Score ─────────────────────────────────────────

@dataclass
class PayoffScore:
    """
    Métrica de calidad del spread al momento de entrada.
    Cuanto mayor el score, mejor el trade.
    """
    net_credit:       float    # crédito neto cobrado
    max_risk:         float    # riesgo máximo (spread_width - credit) × 100
    payoff_ratio:     float    # net_credit / (spread_width - net_credit)
    breakeven_move_pct: float  # % que necesita moverse en contra para pérdida max
    annualized_yield: float    # crédito / margen × (365 / dte) si dte > 0
    composite_score:  float    # 0–100, ponderado de los anteriores
    passes:           bool     # True si cumple todos los mínimos
    fail_reason:      str = "" # por qué falló (si passes=False)

    @classmethod
    def calculate(
        cls,
        net_credit:   float,
        spread_width: float,
        short_strike: float,
        underlying:   float,
        dte:          int,
        risk_level:   str,
        min_payoff_override: Optional[dict] = None,
    ) -> "PayoffScore":
        max_loss      = round((spread_width - net_credit) * 100, 2)
        payoff_ratio  = round(net_credit / (spread_width - net_credit), 4) if spread_width > net_credit else 0
        # Breakeven = cuánto tiene que moverse el subyacente para que
        # el spread expire entero en nuestra contra (= short strike hit).
        # Para PUT spread: underlying debe bajar hasta short_strike.
        # breakeven_move = (underlying - short_strike) / underlying
        be_move = round((underlying - short_strike) / underlying * 100, 3) if underlying > 0 else 0.0
        # Annualized yield solo si dte > 0
        ann_yield = round((net_credit / max_loss * 100) * (365 / dte), 1) if dte > 0 and max_loss > 0 else 0.0

        # Composite score (0–100):
        # DTE > 0:  40% payoff_ratio + 40% breakeven_move + 20% annualized_yield
        # DTE == 0: 50% payoff_ratio + 50% breakeven_move  (annualized sin sentido 0DTE)
        score_payoff = min(100, (payoff_ratio / 0.20) * 100)
        score_be     = min(100, (be_move / 1.50) * 100)
        if dte == 0:
            composite = round(score_payoff * 0.50 + score_be * 0.50, 1)
        else:
            score_ann = min(100, (ann_yield / 200) * 100)
            composite = round(score_payoff * 0.40 + score_be * 0.40 + score_ann * 0.20, 1)

        # Check mínimos por risk level (con override VIX si se provee)
        payoff_table = min_payoff_override if min_payoff_override else MIN_PAYOFF_RATIO
        min_pr = payoff_table.get(risk_level, 0.08)
        min_be = MIN_BREAKEVEN_MOVE_PCT.get(risk_level, 0.25)
        passes = True
        fail   = ""
        if payoff_ratio < min_pr:
            passes = False
            fail   = f"payoff {payoff_ratio:.3f} < min {min_pr:.3f}"
        elif be_move < min_be:
            passes = False
            fail   = f"breakeven_move {be_move:.2f}% < min {min_be:.2f}%"

        return cls(
            net_credit        = net_credit,
            max_risk          = max_loss,
            payoff_ratio      = payoff_ratio,
            breakeven_move_pct = be_move,
            annualized_yield  = ann_yield,
            composite_score   = composite,
            passes            = passes,
            fail_reason       = fail,
        )


# ── Helpers de tiempo ─────────────────────────────────────

def _hour_et() -> float:
    try:
        import pytz
        now_et = datetime.now(pytz.timezone("US/Eastern"))
        return now_et.hour + now_et.minute / 60.0 + now_et.second / 3600.0
    except Exception:
        now_utc = datetime.now(timezone.utc)
        return (now_utc.hour - 4) % 24 + now_utc.minute / 60.0


def _minutes_to_expiry(expiration: str) -> float:
    try:
        import pytz
        exp_date = date.fromisoformat(expiration)
        eastern  = pytz.timezone("US/Eastern")
        close_et = eastern.localize(
            datetime(exp_date.year, exp_date.month, exp_date.day, 16, 0, 0)
        )
        now_et = datetime.now(eastern)
        return max(0.0, (close_et - now_et).total_seconds() / 60.0)
    except Exception:
        return 999.0


def _dte(expiration: str) -> int:
    try:
        exp_date = date.fromisoformat(expiration)
        today    = date.today()
        return max(0, (exp_date - today).days)
    except Exception:
        return 999


def _is_in_entry_window(force_entry: bool = False) -> bool:
    """True si estamos dentro de la ventana de entrada configurada."""
    if force_entry:
        return True
    hour_et = _hour_et()
    entry_end = ENTRY_HOUR_ET + ENTRY_WINDOW_MINUTES / 60.0
    return ENTRY_HOUR_ET <= hour_et <= entry_end


# ── Strike selection ──────────────────────────────────────

def _find_best_strike(
    strikes:     dict,
    delta_min:   float,
    delta_max:   float,
    spread_type: str,
) -> tuple[Optional[float], Optional[dict]]:
    """
    Busca el strike cuyo abs(delta) esté entre delta_min y delta_max.
    Retorna el strike más cercano al target_delta = midpoint del rango.
    """
    target_delta = (delta_min + delta_max) / 2.0
    best_strike  = None
    best_contract = None
    best_dist    = float("inf")

    for strike_str, contract in strikes.items():
        raw_delta = contract.get("delta")
        if raw_delta is None:
            continue
        delta_abs = abs(float(raw_delta))
        if delta_min <= delta_abs <= delta_max:
            dist = abs(delta_abs - target_delta)
            if dist < best_dist:
                best_dist     = dist
                best_strike   = float(strike_str)
                best_contract = contract

    return best_strike, best_contract


# ── Determine spread direction ────────────────────────────

def _determine_spread_type(
    signals:     dict,
    macro_feeds: Optional[dict] = None,
) -> str:
    """
    Determina si operar PUT spread (alcista) o CALL spread (bajista).

    Opción B: CALL spreads solo con señal BEARISH fuerte confirmada.
    En días neutros → siempre PUT (backtest mostró CALL en neutros PF 4.2 vs PUT PF 10.9).

    Reglas:
    - VIX > 25: forzar PUT (mercado en modo defensivo → bull bias)
    - SELL > BUY + 2 Y VIX <= 25: CALL credit spread (bearish confirmado)
    - Todo lo demás: PUT credit spread (default conservador)
    """
    vix = None
    if macro_feeds:
        # macro_feeds puede ser dict O un objeto MacroFeeds — manejar ambos
        if isinstance(macro_feeds, dict):
            vix = macro_feeds.get("vix") or macro_feeds.get("VIX")
        elif hasattr(macro_feeds, "latest"):
            vix = macro_feeds.latest("VIX")          # MacroFeeds API correcta
        else:
            vix = getattr(macro_feeds, "vix", None)

    # VIX elevado → sesgo alcista siempre
    if vix and vix > 25:
        return "put_credit_spread"

    sell_count = sum(
        1 for s in signals.values()
        if isinstance(s, dict) and s.get("signal") == "SELL"
    )
    buy_count = sum(
        1 for s in signals.values()
        if isinstance(s, dict) and s.get("signal") == "BUY"
    )

    # CALL solo con señal bearish FUERTE (diferencia > 2, sin ambigüedad)
    if sell_count > buy_count + 2:
        logger.info(f"[ThetaHarvest] SELL signals dominan ({sell_count} vs {buy_count}) → CALL spread")
        return "call_credit_spread"

    # Neutral o BUY → siempre PUT (no heredar CALL de ciclo anterior)
    return "put_credit_spread"


# ── Función principal de escaneo ──────────────────────────

def scan_theta_harvest(
    ticker:         str,
    chain:          dict,
    vix:            Optional[float] = None,
    vvix:           Optional[float] = None,
    spread_type:    str = "put_credit_spread",
    dte_preference: int = 0,
    force_entry:    bool = False,
    pivot_result=None,          # PivotAnalysisResult | None
    tranche_id:     int = 0,    # 0=35%, 1=65%, 2=None(expiry)
    tranche_target: Optional[float] = 0.65,  # fracción del crédito a capturar
) -> Optional[ThetaHarvestSignal]:
    """
    Escanea el chain de opciones y retorna un ThetaHarvestSignal si las
    condiciones se cumplen, o None si no hay setup válido.

    Para crear los 3 tranches del mismo spread, usar scan_theta_harvest_tranches().
    Esta función es el motor interno — scan_theta_harvest_tranches() la llama 3 veces.

    Args:
        ticker:         Subyacente ("SPY" o "TQQQ").
        chain:          Chain normalizado de OptionChainFetcher.
        vix:            VIX actual.
        vvix:           VVIX actual (vol-of-vol).
        spread_type:    "put_credit_spread" | "call_credit_spread".
        dte_preference: DTE objetivo (0–2).
        force_entry:    Saltear ventana horaria (tests).
        pivot_result:   PivotAnalysisResult del día — ajusta delta target.
        tranche_id:     0=rápido(35%), 1=estándar(65%), 2=hold-to-expiry.
        tranche_target: fracción del crédito a capturar; None=aguantar EOD.
    """
    ticker = ticker.upper()
    cfg    = TICKER_CONFIG.get(ticker)
    if cfg is None:
        logger.warning(f"[ThetaHarvest] Ticker {ticker} no configurado")
        return None

    # ── 1. VVIX Panic gate ─────────────────────────────────
    if vvix is not None and vvix > VVIX_PANIC_THRESHOLD:
        logger.warning(f"[ThetaHarvest] {ticker} — VVIX={vvix:.1f} > {VVIX_PANIC_THRESHOLD} PANIC, skip entrada")
        return None

    # ── 2. VIX gate ────────────────────────────────────────
    if vix is not None and vix > VIX_MAX_ENTRY:
        logger.info(f"[ThetaHarvest] {ticker} — VIX={vix:.1f} > {VIX_MAX_ENTRY}, skip")
        return None

    # ── 3. Ventana horaria ─────────────────────────────────
    if not _is_in_entry_window(force_entry):
        hour_et = _hour_et()
        logger.debug(
            f"[ThetaHarvest] {ticker} — fuera de ventana "
            f"(hora ET={hour_et:.2f}, ventana={ENTRY_HOUR_ET:.2f}–{ENTRY_HOUR_ET + ENTRY_WINDOW_MINUTES/60:.2f})"
        )
        return None

    # ── 4. Delta target desde pivot analysis ──────────────
    if pivot_result and pivot_result.consensus_risk != "NO_TRADE":
        delta_min = pivot_result.delta_min
        delta_max = pivot_result.delta_max
        risk_level = pivot_result.consensus_risk
    else:
        # Fallback: usar rango completo del ticker
        delta_min  = cfg["delta_min_abs"]
        delta_max  = cfg["delta_max_abs"]
        risk_level = "MID"

    if pivot_result and pivot_result.consensus_risk == "NO_TRADE":
        logger.info(f"[ThetaHarvest] {ticker} — pivots indican NO_TRADE, skip")
        return None

    # Clampear al rango permitido por el ticker
    delta_min = max(delta_min, cfg["delta_min_abs"])
    delta_max = min(delta_max, cfg["delta_max_abs"])

    # ── 5. Seleccionar expiración ──────────────────────────
    opt_side = "puts" if spread_type == "put_credit_spread" else "calls"
    options  = chain.get(opt_side, {})
    if not options:
        logger.warning(f"[ThetaHarvest] {ticker} — chain sin {opt_side}")
        return None

    underlying_price = (chain.get("underlying") or {}).get("mark")

    today = date.today().isoformat()
    valid_exps = [
        exp for exp in options.keys()
        if exp >= today and _dte(exp) <= cfg["max_dte"]
    ]
    if not valid_exps:
        logger.info(f"[ThetaHarvest] {ticker} — sin expiraciones válidas ≤ {cfg['max_dte']} DTE")
        return None

    # Elegir la expiración más cercana al DTE preferido
    valid_exps.sort(key=lambda e: abs(_dte(e) - dte_preference))
    chosen_exp = valid_exps[0]
    chosen_dte = _dte(chosen_exp)

    # Verificar tiempo mínimo hasta expiración
    mins_left = _minutes_to_expiry(chosen_exp)
    if mins_left < MIN_MINUTES_TO_EXP:
        logger.info(
            f"[ThetaHarvest] {ticker} — {mins_left:.0f} min hasta exp {chosen_exp} "
            f"< mínimo {MIN_MINUTES_TO_EXP} min"
        )
        return None

    # ── 6. Seleccionar strike SHORT ────────────────────────
    strikes_for_exp = options.get(chosen_exp, {})
    if not strikes_for_exp:
        logger.warning(f"[ThetaHarvest] {ticker} — sin strikes para {chosen_exp}")
        return None

    short_strike, short_contract = _find_best_strike(
        strikes_for_exp, delta_min, delta_max, spread_type
    )
    if short_strike is None or short_contract is None:
        logger.info(
            f"[ThetaHarvest] {ticker} — sin strike con Δ {delta_min:.2f}–{delta_max:.2f} "
            f"en {chosen_exp} ({risk_level})"
        )
        return None

    # ── 7. Strike LONG (protección) $5 más OTM ────────────
    width = cfg["spread_width"]  # $5
    if spread_type == "put_credit_spread":
        long_strike = round(short_strike - width, 1)
    else:
        long_strike = round(short_strike + width, 1)

    long_key = str(long_strike)
    if long_key not in strikes_for_exp:
        available = [float(k) for k in strikes_for_exp.keys()]
        if not available:
            return None
        long_strike = min(available, key=lambda k: abs(k - long_strike))
        long_key    = str(long_strike)

    long_contract = strikes_for_exp.get(long_key)
    if long_contract is None:
        return None

    # ── 8. Crédito neto ────────────────────────────────────
    short_bid  = short_contract.get("bid", 0) or 0
    long_ask   = long_contract.get("ask", 0)  or 0
    net_credit = round(short_bid - long_ask, 2)

    short_mark = short_contract.get("mark", short_bid) or short_bid
    long_mark  = long_contract.get("mark", long_ask)   or long_ask

    # ── Umbrales dinámicos por VIX ─────────────────────────
    dyn_credit_min, dyn_payoff_ratios = _vix_credit_thresholds(ticker, vix, MIN_PAYOFF_RATIO)

    if net_credit < dyn_credit_min:
        vix_str = f"{vix:.1f}" if vix is not None else "?"
        logger.info(
            f"[ThetaHarvest] {ticker} — crédito ${net_credit:.2f} < "
            f"mínimo ${dyn_credit_min:.2f} (VIX={vix_str}), skip"
        )
        return None

    # ── 9. Calcular exits ──────────────────────────────────
    current_spread_value = round(abs(short_mark - long_mark), 2)
    # profit_target per-tranche: valor del spread al que cerramos (en $)
    # tranche_target=None → T2 aguanta hasta EOD; profit_target se setea a -1.0
    # para que la condición `current_value <= profit_target` nunca se active.
    if tranche_target is None:
        profit_target = -1.0   # sentinela: nunca triggerear por profit en T2
    else:
        profit_target = round(net_credit * (1.0 - tranche_target), 2)
    stop_loss = round(net_credit * STOP_LOSS_MULT, 2)
    max_risk  = round((width - net_credit) * 100, 2)

    # ── 10. Payoff scoring (con payoff mínimos ajustados por VIX) ─────
    short_delta = abs(short_contract.get("delta") or 0)
    up = underlying_price or short_strike + 5.0   # fallback si no hay precio
    payoff = PayoffScore.calculate(
        net_credit          = net_credit,
        spread_width        = width,
        short_strike        = short_strike,
        underlying          = up,
        dte                 = chosen_dte,
        risk_level          = risk_level,
        min_payoff_override = dyn_payoff_ratios,
    )
    if not payoff.passes:
        vix_str = f"{vix:.1f}" if vix is not None else "?"
        logger.info(
            f"[ThetaHarvest] {ticker} {chosen_dte}DTE — payoff rechazado: "
            f"{payoff.fail_reason} (VIX={vix_str}) | "
            f"credit=${net_credit:.2f} Δ={short_delta:.2f}"
        )
        return None

    # ── 11. Construir señal ────────────────────────────────
    tranche_label = (
        f"T{tranche_id}={int(tranche_target*100)}%"
        if tranche_target is not None else f"T{tranche_id}=EXPIRY"
    )
    reason = (
        f"ThetaHarvest {ticker} {spread_type.replace('_', ' ')} | "
        f"{chosen_dte}DTE exp={chosen_exp} | "
        f"K={short_strike}/{long_strike} ($5 width) | "
        f"Δ={short_delta:.2f} [{risk_level}] | "
        f"credit=${net_credit:.2f} target=${profit_target:.2f} [{tranche_label}] "
        f"SL=${stop_loss:.2f} payoff={payoff.payoff_ratio:.1%} "
        f"BE={payoff.breakeven_move_pct:.2f}% score={payoff.composite_score:.0f}"
        + (f" | VIX={vix:.1f}" if vix else "")
    )

    signal = ThetaHarvestSignal(
        ticker              = ticker,
        spread_type         = spread_type,
        expiration          = chosen_exp,
        dte                 = chosen_dte,
        short_strike        = short_strike,
        long_strike         = long_strike,
        short_delta         = short_delta,
        net_credit          = net_credit,
        short_mark          = round(short_mark, 2),
        long_mark           = round(long_mark, 2),
        profit_target       = profit_target,
        stop_loss           = stop_loss,
        spread_width        = width,
        max_risk            = max_risk,
        risk_level          = risk_level,
        payoff_ratio        = payoff.payoff_ratio,
        breakeven_move_pct  = payoff.breakeven_move_pct,
        composite_score     = payoff.composite_score,
        annualized_yield    = payoff.annualized_yield,
        reason              = reason,
        vix_at_entry        = vix,
        underlying_price    = underlying_price,
        tranche_id          = tranche_id,
        tranche_target      = tranche_target,
    )

    logger.info(f"[ThetaHarvest] ✅ {reason}")
    return signal


# ── Evaluación de posición abierta ────────────────────────

def evaluate_open_position(
    position:      dict,
    current_chain: dict,
    vix_current:   Optional[float] = None,
    vvix_current:  Optional[float] = None,
    spy_drop_30m:  Optional[float] = None,   # % caída SPY últimos 30 min (positivo = caída)
) -> Optional[str]:
    """
    Evalúa si una posición theta harvest abierta debe cerrarse.
    Retorna: "PROFIT" | "STOP_LOSS" | "VIX_SPIKE" | "DELTA_DRIFT" |
             "SPY_DROP" | "VVIX_PANIC" | "TIME_STOP" | None

    Orden de prioridad (mayor a menor):
      1. VVIX_PANIC  → VVIX > 110
      2. STOP_LOSS   → spread ≥ 150% crédito
      3. VIX_SPIKE   → VIX subió > 3 pts desde entrada
      4. DELTA_DRIFT → short delta > 0.35
      5. SPY_DROP    → SPY cayó > 0.8% en 30 min (solo PUT spreads)
      6. PROFIT      → spread ≤ 35% crédito (tomamos 65%)
      7. TIME_STOP   → 14:30 ET (0DTE) | 15:15 ET (1–4 DTE) — hard close sin excepción
    """
    ticker     = position.get("ticker", "")
    spread_type = position.get("spread_type", "put_credit_spread")

    # ── 1. VVIX Panic ─────────────────────────────────────
    if vvix_current is not None and vvix_current > VVIX_PANIC_THRESHOLD:
        logger.warning(f"[ThetaHarvest] {ticker} VVIX_PANIC — VVIX={vvix_current:.1f}")
        return "VVIX_PANIC"

    # ── Mark price actual del spread ───────────────────────
    exp      = position.get("expiration", "")
    opt_side = "puts" if "put" in spread_type else "calls"
    options  = current_chain.get(opt_side, {})
    strikes  = options.get(exp, {})

    short_k  = str(position.get("short_strike", ""))
    long_k   = str(position.get("long_strike",  ""))
    short_c  = strikes.get(short_k) or {}
    long_c   = strikes.get(long_k)  or {}

    short_mark    = short_c.get("mark") or short_c.get("ask") or 0
    long_mark     = long_c.get("mark")  or long_c.get("bid")  or 0
    current_value = round(abs(short_mark - long_mark), 2)

    net_credit    = position.get("net_credit", 1.0)
    profit_target = position.get("profit_target", 0.0)
    stop_loss_val = position.get("stop_loss", float("inf"))

    # ── 2. Stop loss mecánico ──────────────────────────────
    if current_value >= stop_loss_val:
        logger.warning(
            f"[ThetaHarvest] {ticker} STOP_LOSS — "
            f"valor ${current_value:.2f} ≥ stop ${stop_loss_val:.2f}"
        )
        return "STOP_LOSS"

    # ── 3. VIX Spike ──────────────────────────────────────
    vix_at_entry = position.get("vix_at_entry")
    if vix_current is not None and vix_at_entry is not None:
        vix_delta = vix_current - float(vix_at_entry)
        if vix_delta >= VIX_SPIKE_DELTA:
            logger.warning(
                f"[ThetaHarvest] {ticker} VIX_SPIKE — "
                f"VIX entrada={vix_at_entry:.1f} ahora={vix_current:.1f} "
                f"(+{vix_delta:.1f} pts)"
            )
            return "VIX_SPIKE"

    # ── 4. Delta drift del short leg ───────────────────────
    current_short_delta = abs(short_c.get("delta") or 0)
    if current_short_delta > DELTA_DRIFT_MAX:
        logger.warning(
            f"[ThetaHarvest] {ticker} DELTA_DRIFT — "
            f"short delta={current_short_delta:.3f} > {DELTA_DRIFT_MAX}"
        )
        return "DELTA_DRIFT"

    # ── 5. SPY momentum contra PUT spreads ────────────────
    if (spy_drop_30m is not None
            and spy_drop_30m >= SPY_DROP_PCT_30M
            and "put" in spread_type):
        logger.warning(
            f"[ThetaHarvest] {ticker} SPY_DROP — "
            f"SPY cayó {spy_drop_30m:.2f}% en 30 min (threshold={SPY_DROP_PCT_30M}%)"
        )
        return "SPY_DROP"

    # ── 6. Profit target (per-tranche) ───────────────────
    # tranche_target=None (T2) → profit_target=-1.0 en to_decision() → nunca dispara.
    # T2 solo cierra por TIME_STOP o STOP_LOSS.
    tranche_target_val = position.get("tranche_target", 0.65)
    tranche_id_val     = position.get("tranche_id", 1)
    if tranche_target_val is not None and current_value <= profit_target:
        logger.info(
            f"[ThetaHarvest] {ticker} PROFIT T{tranche_id_val} "
            f"({int(tranche_target_val*100)}% capturado) — "
            f"spread valor ${current_value:.2f} ≤ target ${profit_target:.2f}"
        )
        return "PROFIT"

    # ── 7. Time stop EOD (diferenciado por DTE) ──────────
    hour_et = _hour_et()
    dte = position.get("dte", 0)
    force_close_hour = FORCE_CLOSE_HOUR_0DTE if dte == 0 else FORCE_CLOSE_HOUR_1TO4DTE

    if hour_et >= force_close_hour:
        logger.info(
            f"[ThetaHarvest] {ticker} TIME_STOP — "
            f"hora ET={hour_et:.2f} ≥ {force_close_hour:.2f} "
            f"({'0DTE' if dte == 0 else f'{dte}DTE'})"
        )
        return "TIME_STOP"

    # Tiempo mínimo hasta expiración
    mins_left = _minutes_to_expiry(exp)
    if mins_left < MIN_MINUTES_TO_EXP:
        logger.info(
            f"[ThetaHarvest] {ticker} TIME_STOP — "
            f"solo {mins_left:.0f} min hasta expiración {exp}"
        )
        return "TIME_STOP"

    logger.debug(
        f"[ThetaHarvest] {ticker} HOLD — "
        f"valor=${current_value:.2f} | target=${profit_target:.2f} stop=${stop_loss_val:.2f} | "
        f"shortΔ={current_short_delta:.3f} | VIX={vix_current}"
    )
    return None


# ── Multi-tranche entry (función principal para paper/live) ───────────────

def scan_theta_harvest_tranches(
    ticker:         str,
    chain:          dict,
    vix:            Optional[float] = None,
    vvix:           Optional[float] = None,
    spread_type:    str = "put_credit_spread",
    dte_preference: int = 0,
    force_entry:    bool = False,
    pivot_result=None,
) -> list[ThetaHarvestSignal]:
    """
    Crea un set de 3 ThetaHarvestSignal para el mismo spread con distintos
    profit targets (tranche exits).

    Llama scan_theta_harvest() una vez para validar el setup (gates, credit,
    payoff score). Si pasa, duplica la señal con los 3 tranche configs:
      T0: capturar 35% → cierre rápido, income diario
      T1: capturar 65% → sweet spot theta
      T2: aguantar EOD → máxima captura (sin profit target check)

    El orchestrator (eolo_v2_main.py) debe:
      1. Llamar esta función en lugar de scan_theta_harvest()
      2. Abrir 3 posiciones Schwab simultáneas al mismo strike
      3. Gestionar cada posición independientemente con evaluate_open_position()

    Returns:
        Lista de 1–3 ThetaHarvestSignal, o lista vacía si no hay setup válido.
        (En paper trading podés ajustar TRANCHE_PROFIT_TARGETS para usar solo 1 ó 2.)
    """
    # Validar con T1 (target estándar 65%) — si no pasa los gates, ningún tranche pasa
    base_signal = scan_theta_harvest(
        ticker         = ticker,
        chain          = chain,
        vix            = vix,
        vvix           = vvix,
        spread_type    = spread_type,
        dte_preference = dte_preference,
        force_entry    = force_entry,
        pivot_result   = pivot_result,
        tranche_id     = 1,
        tranche_target = 0.65,
    )
    if base_signal is None:
        return []

    signals: list[ThetaHarvestSignal] = []
    for t_id, t_target in enumerate(TRANCHE_PROFIT_TARGETS):
        if t_id == 1:
            # T1 ya está calculado como base_signal — reusar
            sig = base_signal
        else:
            # Recalcular profit_target para este tranche (mismos datos de chain)
            sig = scan_theta_harvest(
                ticker         = ticker,
                chain          = chain,
                vix            = vix,
                vvix           = vvix,
                spread_type    = spread_type,
                dte_preference = dte_preference,
                force_entry    = True,    # ya validado → saltear gates
                pivot_result   = pivot_result,
                tranche_id     = t_id,
                tranche_target = t_target,
            )
        if sig is not None:
            signals.append(sig)

    if signals:
        targets_str = " | ".join(
            f"T{s.tranche_id}→${s.profit_target:.2f}" for s in signals
        )
        logger.info(
            f"[ThetaHarvest] ✅ {ticker} {dte_preference}DTE — "
            f"{len(signals)} tranches: {targets_str} | "
            f"K={signals[0].short_strike}/{signals[0].long_strike} "
            f"credit=${signals[0].net_credit:.2f}"
        )

    return signals


# ── Helpers públicos ──────────────────────────────────────

def should_close_for_eod(dte: int = 0, hour_et: Optional[float] = None) -> bool:
    """True si estamos en horario de cierre EOD para el DTE dado.
    0DTE → 14:30 ET | 1–4 DTE → 15:15 ET
    """
    if hour_et is None:
        hour_et = _hour_et()
    threshold = FORCE_CLOSE_HOUR_0DTE if dte == 0 else FORCE_CLOSE_HOUR_1TO4DTE
    return hour_et >= threshold


def get_delta_range_for_risk(risk_level: str) -> tuple[float, float]:
    """Retorna (delta_min, delta_max) para el risk level dado."""
    from .pivot_analysis import DELTA_BY_RISK
    return DELTA_BY_RISK.get(risk_level, (0.22, 0.30))
