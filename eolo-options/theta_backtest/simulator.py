# ============================================================
#  Theta Harvest Backtester — Simulator
#
#  Loop principal de simulación día a día.
#
#  Flujo por cada día de trading:
#    1. Macro gate      → skip si news day
#    2. VIX gate        → skip si VIX > VIX_MAX_ENTRY
#    3. VVIX panic      → cerrar todo si VVIX > VVIX_PANIC
#    4. ATR gate        → skip si |open - prev_close| > 2×ATR
#    5. Pivot analysis  → determina risk_zone y spread_type
#    6. Sector direction→ confirma spread_type
#    7. PayoffScore     → filtra por mínimos
#    8. Entry           → registra spread en positions
#    9. Exit checks     → profit target, stop loss, time stop
#   10. Tracking P&L   → acumula resultado diario
# ============================================================
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional
from datetime import date

import pandas as pd

from .config import (
    TICKER_CONFIG,
    TARGET_DTES,
    ENTRY_HOUR_FRACTION,
    TRADING_HOURS_PER_YEAR,
    FORCE_CLOSE_0DTE_HOUR,
    FORCE_CLOSE_1TO4DTE_HOUR,
    PROFIT_TARGET_PCT,
    TRANCHE_PROFIT_TARGETS,
    STOP_LOSS_MULT,
    VIX_MAX_ENTRY,
    VIX_SPIKE_DELTA,
    VVIX_PANIC,
    DELTA_DRIFT_MAX,
    ATR_GATE_MULTIPLIER,
    MIN_PAYOFF_RATIO,
    MIN_BREAKEVEN_MOVE_PCT,
    DELTA_BY_RISK,
    SECTOR_WEIGHTS_SPY,
    SECTOR_WEIGHTS_TQQQ,
    SECTOR_DIRECTION_THRESHOLD,
    RISK_FREE_RATE,
    CONTRACTS_PER_SPREAD,
    BACKTEST_START,
    BACKTEST_END,
    DTE_VOL_MULTIPLIER,
)
from .data_loader import MarketData, compute_sector_direction
from .bs_pricer   import (
    find_strike_for_delta,
    spread_credit,
    spread_value,
    spread_pnl,
    compute_payoff_score,
    bs_delta,
)
from .pivot_engine import compute_pivots, PivotZoneResult
from .macro_engine import is_macro_blocked


# ─────────────────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────────────────

@dataclass
class SpreadPosition:
    """Una posición de credit spread activa."""
    ticker:        str
    spread_type:   str     # put_credit_spread | call_credit_spread
    dte_at_entry:  int
    entry_date:    pd.Timestamp
    expiry_date:   pd.Timestamp
    short_strike:  float
    long_strike:   float
    entry_credit:  float   # por acción
    entry_spot:    float
    entry_sigma:   float
    risk_zone:     str
    payoff_score:  float
    contracts:     int = 1

    # Tranche info — permite múltiples contratos con distintos profit targets
    # por el mismo spread. tranche_id=0 es el más agresivo (target más bajo).
    tranche_id:     int            = 0     # 0, 1, 2 ...
    tranche_target: Optional[float] = 0.65 # % del crédito a capturar; None = aguantar hasta expiry

    # Filled at close
    exit_date:     Optional[pd.Timestamp] = None
    exit_price:    Optional[float] = None  # valor del spread al cerrar
    exit_reason:   Optional[str]   = None
    pnl:           Optional[float] = None  # en dólares


@dataclass
class DailyResult:
    date:          pd.Timestamp
    ticker:        str
    new_positions: int   = 0
    closed_positions: int = 0
    daily_pnl:     float = 0.0
    skip_reason:   Optional[str] = None


# ─────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────

_SECTOR_WEIGHTS = {
    "SPY":  SECTOR_WEIGHTS_SPY,
    "TQQQ": SECTOR_WEIGHTS_TQQQ,
}


def _get_sigma(ticker: str, vix: float, dte: int = 4) -> float:
    """
    IV proxy a partir del VIX con corrección de term structure.

    El VIX mide la IV a 30 días. Las opciones de corto plazo
    cotizan con una prima (volatility term structure):
      0DTE: ~2.5× el VIX (gamma premium intraday)
      1DTE: ~1.8×   2DTE: ~1.5×   3DTE: ~1.3×   4DTE: ~1.1×
    También se aplica el iv_multiplier del ticker (SPY=1×, TQQQ=3×).
    """
    base_mult   = TICKER_CONFIG[ticker]["iv_multiplier"]
    dte_mult    = DTE_VOL_MULTIPLIER.get(dte, 1.0)
    return (vix / 100) * base_mult * dte_mult


def _entry_T(dte: int) -> float:
    """
    Tiempo residual al entrar (en años, fracción del día).

    ENTRY_HOUR_FRACTION = 4.5/6.5 representa las HORAS RESTANTES
    como fracción del día total (entrada 10:00, cierre 0DTE a 14:30 = 4.5h).

    Para 0DTE: T = 4.5h / (252*6.5h)
    Para N DTE: T = (N * 6.5h + 4.5h) / (252*6.5h)
    """
    hours_remaining_today = 6.5 * ENTRY_HOUR_FRACTION   # 4.5 horas restantes
    hours_total           = dte * 6.5 + hours_remaining_today
    return hours_total / TRADING_HOURS_PER_YEAR


def _current_T(
    entry_date: pd.Timestamp,
    expiry_date: pd.Timestamp,
    current_date: pd.Timestamp,
    hour_fraction: float = 0.5,   # mitad del día como proxy
) -> float:
    """Tiempo residual desde current_date hasta expiry (en años)."""
    if current_date >= expiry_date:
        return 0.0
    # Días de calendario entre current y expiry
    cal_days = (expiry_date - current_date).days
    # Convertir a horas de trading (aprox)
    hours = cal_days * 6.5 * (5 / 7)   # ajuste por fines de semana
    # Añadir fracción del día actual
    hours += 6.5 * hour_fraction
    return max(hours / TRADING_HOURS_PER_YEAR, 0.0)


def _expiry_date(entry_date: pd.Timestamp, dte: int, calendar: pd.DatetimeIndex) -> Optional[pd.Timestamp]:
    """Encuentra la fecha de vencimiento = entry + dte días de trading."""
    if dte == 0:
        return entry_date
    loc = calendar.searchsorted(entry_date)
    target_loc = loc + dte
    if target_loc >= len(calendar):
        return None
    return calendar[target_loc]


def _determine_spread_type(sector_direction: str, prev_spread_type: Optional[str] = None) -> str:
    """
    Bullish → put_credit_spread (vendemos puts debajo del mercado)
    Bearish → call_credit_spread (vendemos calls encima del mercado)
    Neutral → put_credit_spread (Opción B: CALL solo con señal bearish confirmada)

    Backtest mostró PF 4.21 CALL vs 6.45 PUT. CALL spreads en días neutros
    sin confirmación de dirección bajista son la fuente de underperformance.
    """
    if sector_direction == "bullish":
        return "put_credit_spread"
    if sector_direction == "bearish":
        return "call_credit_spread"
    # neutral → siempre PUT (no heredar CALL de días anteriores)
    return "put_credit_spread"


# ─────────────────────────────────────────────────────────
#  Exit logic para posiciones abiertas
# ─────────────────────────────────────────────────────────

def _check_exits(
    positions: list[SpreadPosition],
    current_date: pd.Timestamp,
    md: MarketData,
    ticker: str,
    closed_today: list[SpreadPosition],
) -> list[SpreadPosition]:
    """
    Evalúa todas las posiciones abiertas.
    Retorna lista de posiciones que siguen abiertas.
    """
    df        = md.get_ohlcv(ticker)
    if current_date not in df.index:
        return positions   # no hay datos para hoy → no tocar

    row       = df.loc[current_date]
    S_now     = float(row["Close"])   # usamos cierre como proxy del precio de mark
    vix_now   = md.get_vix(current_date) or 20.0
    vvix_now  = md.get_vvix(current_date) or 90.0
    # Para el mark de posiciones existentes usamos el DTE residual actual
    # (calculado después por posición)

    still_open = []

    for pos in positions:
        # ── Determinar DTE residual actual ────────────
        days_left  = max((pos.expiry_date - current_date).days, 0)
        T_now      = _current_T(pos.entry_date, pos.expiry_date, current_date)
        # Sigma con term structure según DTE residual
        sigma_pos  = _get_sigma(ticker, vix_now, min(days_left, 4))

        # ── VVIX panic ────────────────────────────────
        if vvix_now >= VVIX_PANIC:
            cur_val = spread_value(
                S_now, T_now, RISK_FREE_RATE, sigma_pos,
                pos.spread_type, pos.short_strike, pos.long_strike,
            )
            pos.exit_date   = current_date
            pos.exit_price  = cur_val
            pos.exit_reason = "VVIX_PANIC"
            pos.pnl         = spread_pnl(pos.entry_credit, cur_val, pos.contracts)
            closed_today.append(pos)
            continue

        # ── Vencimiento hoy ───────────────────────────
        if current_date >= pos.expiry_date:
            # Valor intrínseco al cierre
            from .bs_pricer import spread_pnl_at_expiry
            p = spread_pnl_at_expiry(
                S_now, pos.spread_type,
                pos.short_strike, pos.long_strike,
                pos.entry_credit, pos.contracts,
            )
            pos.exit_date   = current_date
            pos.exit_price  = 0.0
            pos.exit_reason = "EXPIRY"
            pos.pnl         = p
            closed_today.append(pos)
            continue

        # ── Calcular valor actual del spread ──────────
        cur_val = spread_value(
            S_now, T_now, RISK_FREE_RATE, sigma_pos,
            pos.spread_type, pos.short_strike, pos.long_strike,
        )

        # ── Profit target ─────────────────────────────
        # Cada tranche tiene su propio target. tranche_target=None significa
        # "aguantar hasta vencimiento" → no aplicar profit target check.
        if pos.tranche_target is not None:
            if cur_val <= pos.entry_credit * (1 - pos.tranche_target):
                pos.exit_date   = current_date
                pos.exit_price  = cur_val
                pos.exit_reason = "PROFIT_TARGET"
                pos.pnl         = spread_pnl(pos.entry_credit, cur_val, pos.contracts)
                closed_today.append(pos)
                continue

        # ── Stop loss ─────────────────────────────────
        if cur_val >= pos.entry_credit * STOP_LOSS_MULT:
            pos.exit_date   = current_date
            pos.exit_price  = cur_val
            pos.exit_reason = "STOP_LOSS"
            pos.pnl         = spread_pnl(pos.entry_credit, cur_val, pos.contracts)
            closed_today.append(pos)
            continue

        # ── Delta drift ───────────────────────────────
        cur_delta = bs_delta(
            S_now, pos.short_strike, T_now, RISK_FREE_RATE, sigma_pos,
            "put" if "put" in pos.spread_type else "call",
        )
        if cur_delta >= DELTA_DRIFT_MAX:
            pos.exit_date   = current_date
            pos.exit_price  = cur_val
            pos.exit_reason = "DELTA_DRIFT"
            pos.pnl         = spread_pnl(pos.entry_credit, cur_val, pos.contracts)
            closed_today.append(pos)
            continue

        # ── Time stop (usando CLOSE como proxy de EOD) ──
        # En backtesting usamos cierre del día como proxy.
        # Para 0DTE que llega a expiry_date: ya procesado arriba.
        dte_at_entry = pos.dte_at_entry
        # No implementamos check intraday explícito; para 0DTE
        # el vencimiento coincide con el día de entrada.

        still_open.append(pos)

    return still_open


# ─────────────────────────────────────────────────────────
#  Entry logic
# ─────────────────────────────────────────────────────────

def _try_enter(
    ticker:        str,
    date:          pd.Timestamp,
    dte:           int,
    md:            MarketData,
    pivot:         PivotZoneResult,
    spread_type:   str,
    vix:           float,
    prev_vix:      Optional[float],
    config:        dict,
    calendar:      pd.DatetimeIndex,
    debug:         bool = False,
) -> list[SpreadPosition]:
    """
    Intenta construir nuevas posiciones para `dte`.
    Retorna lista de SpreadPosition (uno por tranche) o lista vacía si no cumple.

    Con TRANCHE_PROFIT_TARGETS = [0.35, 0.65, None] se crean 3 contratos:
      - Tranche 0: cierra al capturar 35% del crédito (income rápido)
      - Tranche 1: cierra al capturar 65% del crédito (target estándar)
      - Tranche 2: aguanta hasta vencimiento (máxima captura de theta)
    Todos tienen el mismo strike y mismo stop loss (STOP_LOSS_MULT).
    """
    df  = md.get_ohlcv(ticker)
    if date not in df.index:
        return []

    row   = df.loc[date]
    S     = float(row["Open"])    # precio de apertura = proxy entrada 10:00 ET
    sigma = _get_sigma(ticker, vix, dte)

    # ── VIX spike check ──────────────────────────────
    if prev_vix and (vix - prev_vix) > VIX_SPIKE_DELTA:
        if debug: print(f"      DTE{dte}: VIX_SPIKE ({vix:.1f} vs {prev_vix:.1f})")
        return []   # VIX spiked → skip

    # ── ATR gate ─────────────────────────────────────
    atr = md.get_atr(ticker, date)
    if atr:
        prev_close = float(df.iloc[df.index.get_loc(date) - 1]["Close"])
        gap = abs(S - prev_close)
        if gap > ATR_GATE_MULTIPLIER * atr:
            if debug: print(f"      DTE{dte}: ATR_GATE (gap={gap:.2f} > 2×ATR={2*atr:.2f})")
            return []

    # ── Risk zone gate ────────────────────────────────
    if pivot.risk_zone == "NO_TRADE":
        if debug: print(f"      DTE{dte}: NO_TRADE zone")
        return []

    delta_min = pivot.delta_min
    delta_max = pivot.delta_max
    if delta_min == 0 and delta_max == 0:
        return []

    # ── Target delta (midpoint del rango) ────────────
    target_delta = (delta_min + delta_max) / 2

    # ── T residual ───────────────────────────────────
    T = _entry_T(dte)
    if T <= 0:
        T = 0.5 / TRADING_HOURS_PER_YEAR   # mínimo

    opt_type = "put" if "put" in spread_type else "call"

    # ── Strike selection ─────────────────────────────
    short_strike, actual_delta = find_strike_for_delta(
        S, T, RISK_FREE_RATE, sigma,
        opt_type, target_delta,
        config["strike_increment"],
    )

    # Long strike = short_strike - width (put) o + width (call)
    width = config["spread_width"]
    if "put" in spread_type:
        long_strike = short_strike - width
    else:
        long_strike = short_strike + width

    # ── Credit ───────────────────────────────────────
    credit = spread_credit(
        S, T, RISK_FREE_RATE, sigma,
        spread_type, short_strike, long_strike,
    )

    if debug:
        print(f"      DTE{dte}: S={S:.2f} σ={sigma:.3f} T={T:.5f} "
              f"K_short={short_strike} K_long={long_strike} "
              f"δ={actual_delta:.3f} credit=${credit:.4f}")

    if credit < config["min_credit"]:
        if debug: print(f"      DTE{dte}: CREDIT_LOW (${credit:.4f} < ${config['min_credit']})")
        return []

    # ── PayoffScore ──────────────────────────────────
    ps = compute_payoff_score(
        credit, width, short_strike, S, spread_type, T, dte,
    )

    min_pr = MIN_PAYOFF_RATIO.get(pivot.risk_zone, 0.10)
    # MIN_BREAKEVEN_MOVE_PCT está en porcentaje (ej: 0.60 = 0.60%)
    # ps["breakeven_move_pct"] está en decimal (ej: 0.006 = 0.6%)
    # Convertimos el umbral a decimal dividiendo por 100
    min_be = MIN_BREAKEVEN_MOVE_PCT.get(pivot.risk_zone, 0.30) / 100

    if ps["payoff_ratio"] < min_pr:
        if debug: print(f"      DTE{dte}: PAYOFF_LOW (pr={ps['payoff_ratio']:.4f} < {min_pr})")
        return []
    if ps["breakeven_move_pct"] < min_be:
        if debug: print(f"      DTE{dte}: BE_LOW (be={ps['breakeven_move_pct']:.4f} < {min_be:.4f})")
        return []

    # ── Expiry date ──────────────────────────────────
    exp_date = _expiry_date(date, dte, calendar)
    if exp_date is None:
        return []

    # ── Crear un SpreadPosition por tranche ──────────
    # Mismo strike, mismo crédito — distintos profit targets de salida.
    positions = []
    for t_id, t_target in enumerate(TRANCHE_PROFIT_TARGETS):
        positions.append(SpreadPosition(
            ticker          = ticker,
            spread_type     = spread_type,
            dte_at_entry    = dte,
            entry_date      = date,
            expiry_date     = exp_date,
            short_strike    = short_strike,
            long_strike     = long_strike,
            entry_credit    = credit,
            entry_spot      = S,
            entry_sigma     = sigma,
            risk_zone       = pivot.risk_zone,
            payoff_score    = ps["composite_score"],
            contracts       = CONTRACTS_PER_SPREAD,
            tranche_id      = t_id,
            tranche_target  = t_target,
        ))
    if debug:
        targets_str = " / ".join(
            f"T{i}:{f'{t*100:.0f}%' if t else 'EXPIRY'}"
            for i, t in enumerate(TRANCHE_PROFIT_TARGETS)
        )
        print(f"      DTE{dte}: ENTER {len(positions)} tranches [{targets_str}] "
              f"credit=${credit:.4f}")
    return positions


# ─────────────────────────────────────────────────────────
#  Main simulation loop
# ─────────────────────────────────────────────────────────

def run_simulation(
    md: MarketData,
    ticker: str,
    verbose: bool = False,
    debug_days: int = 0,   # si > 0, imprime diagnóstico de los primeros N días con try_enter
) -> tuple[list[SpreadPosition], list[DailyResult]]:
    """
    Corre la simulación completa para un ticker.

    Returns
    -------
    (all_closed_positions, daily_results)
    """
    config    = TICKER_CONFIG[ticker]
    df        = md.get_ohlcv(ticker)
    calendar  = md.calendar
    weights   = _SECTOR_WEIGHTS[ticker]

    open_positions: list[SpreadPosition] = []
    all_closed:     list[SpreadPosition] = []
    daily_results:  list[DailyResult]    = []

    prev_vix        = None
    prev_spread_type: Optional[str] = None
    debug_count     = 0

    for date in calendar:
        result = DailyResult(date=date, ticker=ticker)

        # ── 1. Macro gate ──────────────────────────────
        if is_macro_blocked(date.date()):
            result.skip_reason = "MACRO_BLOCK"
            daily_results.append(result)
            if verbose:
                print(f"  {date.date()} MACRO_BLOCK")
            continue

        # ── 2. Obtener VIX / VVIX ─────────────────────
        vix  = md.get_vix(date)
        vvix = md.get_vvix(date)

        if vix is None:
            result.skip_reason = "NO_VIX"
            daily_results.append(result)
            continue

        # ── 3. VVIX panic → cerrar todo ───────────────
        if vvix and vvix >= VVIX_PANIC:
            closed_today: list[SpreadPosition] = []
            for pos in open_positions:
                pos.exit_date   = date
                pos.exit_price  = 0.0   # aproximado
                pos.exit_reason = "VVIX_PANIC"
                pos.pnl         = spread_pnl(pos.entry_credit, 0.0, pos.contracts)
                closed_today.append(pos)
            all_closed.extend(closed_today)
            open_positions = []
            result.closed_positions = len(closed_today)
            result.daily_pnl = sum(p.pnl for p in closed_today)
            result.skip_reason = "VVIX_PANIC_CLOSE"
            daily_results.append(result)
            if verbose:
                print(f"  {date.date()} VVIX PANIC {vvix:.0f}")
            prev_vix = vix
            continue

        # ── 4. VIX gate ───────────────────────────────
        if vix > VIX_MAX_ENTRY:
            # Procesar exits pero no entrar
            closed_today = []
            open_positions = _check_exits(
                open_positions, date, md, ticker, closed_today,
            )
            all_closed.extend(closed_today)
            result.closed_positions = len(closed_today)
            result.daily_pnl        = sum(p.pnl for p in closed_today if p.pnl)
            result.skip_reason      = "VIX_HIGH"
            daily_results.append(result)
            prev_vix = vix
            continue

        # ── 5. Process exits ──────────────────────────
        closed_today = []
        open_positions = _check_exits(
            open_positions, date, md, ticker, closed_today,
        )
        all_closed.extend(closed_today)
        result.closed_positions = len(closed_today)
        result.daily_pnl        = sum(p.pnl for p in closed_today if p.pnl)

        # ── 6. Sector direction ───────────────────────
        sector_dir = compute_sector_direction(
            md, ticker, date, weights, SECTOR_DIRECTION_THRESHOLD,
        )
        spread_type = _determine_spread_type(sector_dir, prev_spread_type)
        prev_spread_type = spread_type

        # ── 7. Pivot analysis ─────────────────────────
        row = df.loc[date] if date in df.index else None
        if row is None:
            daily_results.append(result)
            prev_vix = vix
            continue

        S = float(row["Open"])
        pivot = compute_pivots(df, date, S, spread_type)
        if pivot is None or pivot.risk_zone == "NO_TRADE":
            result.skip_reason = f"PIVOT_{pivot.risk_zone if pivot else 'NONE'}"
            daily_results.append(result)
            prev_vix = vix
            continue

        # ── 8. Try entry for each DTE ─────────────────
        # "Always-In": un set de tranches por DTE si no hay ya ninguno abierto.
        # existing_dtes: cualquier tranche abierto para ese DTE bloquea re-entrada.
        existing_dtes = {
            p.dte_at_entry for p in open_positions
            if p.ticker == ticker and p.expiry_date >= date
        }

        do_debug = (debug_days > 0 and debug_count < debug_days)
        if do_debug:
            debug_count += 1
            print(f"  [DBG] {date.date()} VIX={vix:.1f} S={float(df.loc[date]['Open']):.2f} "
                  f"zone={pivot.risk_zone} dir={sector_dir} type={spread_type}")

        new_positions = []
        for dte in TARGET_DTES:
            if dte in existing_dtes:
                continue   # ya hay tranches abiertos para este DTE

            # _try_enter retorna lista de SpreadPosition (uno por tranche)
            tranches = _try_enter(
                ticker, date, dte, md, pivot, spread_type,
                vix, prev_vix, config, calendar,
                debug=do_debug,
            )
            new_positions.extend(tranches)

        open_positions.extend(new_positions)
        # Contar en términos de "spreads" únicos (grupos de tranches), no contratos
        n_tranches = len(TRANCHE_PROFIT_TARGETS)
        result.new_positions = len(new_positions) // n_tranches if n_tranches else len(new_positions)
        result.daily_pnl    += 0.0   # entradas no generan P&L hasta cerrar

        if verbose and (new_positions or closed_today):
            pnl_str = f"  PnL={result.daily_pnl:+.0f}"
            print(
                f"  {date.date()} {sector_dir:8s} "
                f"zone={pivot.risk_zone:8s} "
                f"enter={len(new_positions)} close={len(closed_today)}"
                f"{pnl_str}"
            )

        daily_results.append(result)
        prev_vix = vix

    # Cerrar posiciones que quedaron abiertas al final del período
    last_date = calendar[-1]
    for pos in open_positions:
        if pos.exit_date is None:
            df  = md.get_ohlcv(ticker)
            S   = float(df.iloc[-1]["Close"])
            sigma = _get_sigma(ticker, md.get_vix(last_date) or 20.0)
            T   = 0.0
            cur_val = spread_value(
                S, T, RISK_FREE_RATE, sigma,
                pos.spread_type, pos.short_strike, pos.long_strike,
            )
            pos.exit_date   = last_date
            pos.exit_price  = cur_val
            pos.exit_reason = "END_OF_DATA"
            pos.pnl         = spread_pnl(pos.entry_credit, cur_val, pos.contracts)
            all_closed.append(pos)

    return all_closed, daily_results


def run_all_tickers(
    md: MarketData,
    tickers: Optional[list[str]] = None,
    verbose: bool = False,
) -> dict[str, tuple[list[SpreadPosition], list[DailyResult]]]:
    """Corre la simulación para todos los tickers y retorna resultados por ticker."""
    from .config import TICKERS as _DEFAULT_TICKERS
    tickers = tickers or _DEFAULT_TICKERS

    results = {}
    for ticker in tickers:
        print(f"\n{'='*60}")
        print(f"  Simulando {ticker} ...")
        print(f"{'='*60}")
        positions, daily = run_simulation(md, ticker, verbose=verbose)
        results[ticker] = (positions, daily)
        print(f"  → {len(positions)} trades cerrados")

    return results
