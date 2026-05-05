# ============================================================
#  EOLO Bot — Main Runner
#  10 estrategias activables desde el dashboard o Firestore:
#    - EMA 3/8 Crossover   (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Gap Fade/Follow      (SPY, QQQ, AAPL, TSLA, NVDA)
#    - VWAP + RSI           (SOXL, TSLL, NVDL, TQQQ)
#    - Bollinger Bands      (SOXL, TSLL, NVDL, TQQQ)
#    - Opening Range Break  (SOXL, TSLL, NVDL, TQQQ)
#    - RSI + SMA200         (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Supertrend           (SOXL, TSLL, NVDL, TQQQ)
#    - High/Low Breakout    (SPY, QQQ, AAPL, TSLA, NVDA)
#    - Heikin Ashi Cloud    (SOXL, TSLL, NVDL, TQQQ)
#    - Bollinger/Keltner Squeeze (SOXL, TSLL, NVDL, TQQQ)
#  Correr: python bot_main.py
# ============================================================
import os
import sys
import time
from datetime import datetime, timedelta
import pytz
from loguru import logger
from google.cloud import firestore

# ── eolo_common (multi-TF + confluencia compartido) ──────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from eolo_common.multi_tf import ConfluenceFilter  # noqa: E402
from eolo_common.multi_tf.settings import load_multi_tf_config  # noqa: E402
from eolo_common.trading_hours import (  # noqa: E402
    DEFAULTS_EQUITY,
    load_schedule,
    is_within_trading_window,
    is_after_auto_close as _tr_is_after_auto_close,
    now_et,
)
from eolo_common.diagnostics import (  # noqa: E402
    compute_strategy_diagnostics,
    DIAGNOSTICS_TICKERS_DEFAULT,
)

from marketdata import MarketData
from macro_polling import start_macro_feeds  # Nivel 2 (VIX/TICK/TRIN polling)
import bot_strategy         as ema_strategy
import bot_gap_strategy     as gap_strategy
import bot_vwap_rsi_strategy as vwap_strategy
import bot_bollinger_strategy as bollinger_strategy
import bot_orb_strategy        as orb_strategy
import bot_rsi_sma200_strategy as rsi_sma200_strategy
import bot_supertrend_strategy as supertrend_strategy
import bot_hh_ll_strategy     as hh_ll_strategy
import bot_ha_cloud_strategy  as ha_cloud_strategy
import bot_squeeze_strategy   as squeeze_strategy
import bot_macd_bb_strategy   as macd_bb_strategy
import bot_ema_tsi_strategy   as ema_tsi_strategy
import bot_vela_pivot_strategy as vela_pivot_strategy
# ── Nivel 1 (trading_strategies_v2.md) ──────────────────
import bot_rvol_breakout_strategy        as rvol_strategy
import bot_stop_run_strategy             as stop_run_strategy
import bot_vwap_zscore_strategy          as vwap_z_strategy
import bot_volume_reversal_bar_strategy  as vrb_strategy
import bot_anchor_vwap_strategy          as anchor_vwap_strategy
import bot_obv_strategy                  as obv_strategy
import bot_tsv_strategy                  as tsv_strategy
import bot_vw_macd_strategy              as vw_macd_strategy
import bot_opening_drive_strategy        as opening_drive_strategy
# ── Nivel 2 (requieren macro feeds) ─────────────────────
import bot_vix_mean_reversion_strategy   as vix_mr_strategy
import bot_vix_correlation_strategy      as vix_corr_strategy
import bot_vix_squeeze_strategy          as vix_sq_strategy
import bot_tick_trin_fade_strategy       as tt_strategy
import bot_vrp_strategy                  as vrp_strategy
# ── FASE 5 Winner (30m intraday) ────────────────────────
import bot_xom_30m_strategy              as xom_30m_strategy
# ── FASE 4 Winner (daily mean-revert, SPY/AAPL/QQQ) ──────
import bot_bollinger_rsi_sensitive_strategy as bb_rsi_sens_strategy
# ── FASE 7a Winners (30m intraday, SPY/QQQ) ──────────────
import bot_macd_confluence_fase7a_strategy as macd_conf_strategy
import bot_momentum_score_fase7a_strategy as momentum_strategy
# ── Nuevas estrategias (2026-04-27) ───────────────────────
import bot_overnight_drift_strategy    as overnight_drift_strategy
import bot_vix_spike_fade_strategy     as vix_spike_fade_strategy
import bot_spy_qqq_divergence_strategy as sqd_strategy
import bot_sector_rrg_strategy         as sector_rrg_strategy
from eolo_common.risk import get_regime_multiplier  # Macro Regime Bridge
from eolo_common.routing import AutoRouter as _AutoRouter  # Strategy Auto-Router

# Auto-router instance (actualiza toggles cada 30 min según régimen)
_auto_router_v1 = _AutoRouter(bot_id="v1", update_interval_min=30)
# ── "EMA 3/8 y MACD" suite (v3) — dispatcher compartido ──
import bot_strategies_v3_dispatcher      as v3_strategy
import bot_trader             as trader
from strategy_router import should_run_strategy  # FASE 6: Asset/TF routing

# ── Tickers por grupo ─────────────────────────────────────
TICKERS_EMA_GAP   = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA"]
TICKERS_LEVERAGED = ["SOXL", "TSLL", "NVDL", "TQQQ"]
TICKERS_SECTORS   = ["XLK", "XLE", "XLF", "XLV", "XLI", "XLP", "XLU", "XLY", "XLC", "XLRE"]

# ── Timing ───────────────────────────────────────────────
CANDLE_MINUTES        = 1   # velas de 1 minuto — señales 5x más frecuentes
CANDLE_BUFFER_S       = 5   # 5s de buffer — Schwab tarda 2-10s en publicar la vela
# Los horarios de trading (start/end/auto-close) ahora se leen desde
# Firestore vía eolo_common.trading_hours. Defaults preservan el
# comportamiento histórico: 09:30-15:27 ET con auto-close 15:27.
# El hard-ceiling MARKET_CLOSE_ET (16:00) queda como límite absoluto
# del auto-close window (nunca cerrar fuera de market hours).
MARKET_CLOSE_CEILING  = (16, 0)
EASTERN               = pytz.timezone("America/New_York")
GCP_PROJECT      = "eolo-schwab-agent"

# ── Config por defecto ────────────────────────────────────
DEFAULT_STRATEGIES = {
    "ema_crossover":   True,
    "sma200_filter":   True,   # filtro de tendencia para EMA crossover
    "gap_fade":        True,
    "vwap_rsi":        True,
    "bollinger":       True,
    "orb":             True,
    "rsi_sma200":      True,
    "supertrend":      True,
    "hh_ll":           True,
    "ha_cloud":        True,
    "squeeze":         True,
    "macd_bb":         True,
    "ema_tsi":         True,
    "vela_pivot":      True,
    # ── Nivel 1 (trading_strategies_v2.md) — todas ON por decisión Juan 2026-04-20
    "rvol_breakout":       True,
    "stop_run":            True,
    "vwap_zscore":         True,
    "volume_reversal_bar": True,
    "anchor_vwap":         True,
    "obv_mtf":             True,
    "tsv":                 True,
    "vw_macd":             True,
    "opening_drive":       True,
    # ── Nivel 2 (requieren macro feeds; devuelven HOLD si macro=None)
    "vix_mean_rev":        True,
    "vix_correlation":     True,
    "vix_squeeze":         True,
    "tick_trin_fade":      True,
    "vrp_intraday":        True,
    # ── FASE 5 Winner: XOM 30m Bollinger (PF 1.38, backtest 60d real data)
    "xom_30m":             True,
    # ── FASE 4 Winner: Bollinger+RSI Sensitive (PF 38.52 SPY / 14.78 AAPL / 14.02 QQQ, 252d)
    "bollinger_rsi_sensitive": True,
    # ── FASE 7a Winners: MACD Confluence + Momentum Score (PF 4.58 QQQ / 3.14 SPY, 30m)
    "macd_confluence_fase7a": True,
    "momentum_score_fase7a": True,
    # ── Suite "EMA 3/8 y MACD" (v3) ─────────────────────────
    "ema_3_8":             True,
    "ema_8_21":            True,
    "macd_accel":          True,
    "volume_breakout":     True,
    "buy_pressure":        True,
    "sell_pressure":       True,
    "vwap_momentum":       True,
    "orb_v3":              True,
    "donchian_turtle":     True,
    "bulls_bsp":           True,
    "net_bsv":             True,
    # ── Nuevas estrategias (2026-04-27) ──────────────────────
    "overnight_drift":     True,   # Overnight carry: BUY 15:45, SELL 9:35 ET
    "vix_spike_fade":      True,   # Fade pánico intraday de VIX (>5% spike → BUY)
    "spy_qqq_divergence":  True,   # Mean-reversion: SPY/QQQ ratio z-score ±2σ
    "sector_rrg":          True,   # Sector rotation: Leading quadrant (RS-Ratio > 100, RS-Mom > 100)
    # ── Combos Ganadores (2026-04) ────────────────────────────
    "combo1_ema_scalper":  True,   # ALTA  — EMA3/8 + EMA stack 8>21>34>55>89
    "combo2_rubber_band":  True,   # ALTA  — Rubber Band VWAP 09:45-14:30 ET
    "combo3_nino_squeeze": True,   # MEDIA — Nino Squeeze ⭐ (stack+TTM+vol+MACD)
    "combo4_slimribbon":   True,   # MEDIA — SlimRibbon EMA8>13>21 + MACD + TDI
    "combo5_btd":          True,   # MEDIA — BTD % cross0 + stack + PullBack34
    "combo6_fractalccix":  True,   # BAJA  — TEMA/HA + CCI extremo + squeeze≥4
    "combo7_campbell":     True,   # BAJA  — EMA34 trend + EMA3 cross + Supertrend
}


# ── Firestore strategy config ─────────────────────────────

def get_active_strategies() -> dict:
    """
    Lee qué estrategias están habilitadas desde Firestore.
    Se actualiza desde el dashboard sin reiniciar el bot.
    """
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("strategies").get()
        if doc.exists:
            config = doc.to_dict()
            # Asegurar que todos los keys existan
            for k, v in DEFAULT_STRATEGIES.items():
                config.setdefault(k, v)
            return config
    except Exception as e:
        logger.warning(f"Could not read strategy config: {e}")
    return DEFAULT_STRATEGIES.copy()


def get_global_settings() -> dict:
    """
    Lee settings globales desde Firestore (eolo-config/settings):
      - bot_active              : bool  — si es False el bot no opera (pausa desde dashboard)
      - close_all               : bool  — si es True cierra todas las posiciones abiertas
      - budget                  : float — USD por trade
      - active_timeframes       : list  — timeframes activos simultáneamente, e.g. [1, 5, 15, 30, 60, 240]
      - confluence_mode         : bool  — si True, exige agreement entre TFs (vía eolo_common)
      - confluence_min_agree    : int   — N TFs que deben coincidir (default 2)
      - max_positions           : int   — posiciones concurrentes totales (opcional, default 5)
      - default_stop_loss_pct   : float — SL default % (opcional, usado si la estrategia no fija uno)
      - default_take_profit_pct : float — TP default % (opcional)
      - daily_loss_cap_pct      : float — cap diario negativo sobre equity (opcional)
    """
    defaults = {"bot_active": True, "close_all": False, "budget": 100,
                "active_timeframes":       [1, 5, 15, 30, 60, 240],
                "confluence_mode":         False,
                "confluence_min_agree":    2,
                "max_positions":           5,
                "default_stop_loss_pct":   2.0,
                "default_take_profit_pct": 4.0,
                "allow_short_selling":     False,  # OFF por defecto — requiere cuenta con margen
                "daily_loss_cap_pct":     -5.0,
                # trading_hours defaults (equity): 09:30-15:27 ET con auto-close 15:27
                "trading_start_et":       "09:30",
                "trading_end_et":         "15:27",
                "auto_close_et":          "15:27",
                "trading_hours_enabled":  True}
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("settings").get()
        if doc.exists:
            data = doc.to_dict() or {}
            defaults.update(data)
            # Migración: candle_minutes legado → active_timeframes
            if "active_timeframes" not in data and "candle_minutes" in data:
                defaults["active_timeframes"] = [int(data["candle_minutes"])]
    except Exception as e:
        logger.warning(f"Could not read global settings: {e}")

    # ── Sanitize multi-TF vía eolo_common (idéntico a v1.2/v2/crypto) ──
    mt = load_multi_tf_config(defaults)
    defaults["active_timeframes"]    = list(mt.active_timeframes)
    defaults["confluence_mode"]      = bool(mt.confluence_mode)
    defaults["confluence_min_agree"] = int(mt.confluence_min_agree)
    return defaults


def clear_close_all_flag():
    """Limpia el flag close_all en Firestore después de ejecutarlo."""
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("settings")
        doc.set({"close_all": False}, merge=True)
    except Exception as e:
        logger.warning(f"Could not clear close_all flag: {e}")


def get_active_tickers(group: list) -> list:
    """
    Filtra la lista de tickers según lo habilitado en Firestore (eolo-config/tickers).
    Si un ticker no aparece en config → se asume activo (True).
    Si Firestore no responde → devuelve el grupo completo.
    """
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("tickers").get()
        if doc.exists:
            config = doc.to_dict()
            active = [t for t in group if config.get(t, True)]
            return active if active else group  # nunca devolver lista vacía
    except Exception as e:
        logger.warning(f"Could not read ticker config: {e}")
    return group  # fallback: todos activos


# ── Candle-aligned timing ─────────────────────────────────

def seconds_to_next_candle(candle_minutes: int = None) -> float:
    """
    Calcula cuántos segundos faltan para el próximo cierre de vela,
    alineado al reloj real del mercado (9:30, 9:35, 9:40...).
    Agrega CANDLE_BUFFER_S de margen para que el dato llegue de Schwab.
    candle_minutes: usar este valor o CANDLE_MINUTES si es None.
    """
    tf  = candle_minutes if candle_minutes else CANDLE_MINUTES
    # Para timeframes ≥ 60min usar 1min como resolución de espera
    tf  = min(tf, 60)
    now = datetime.now(EASTERN)
    elapsed = (now.minute % tf) * 60 + now.second + now.microsecond / 1e6
    return (tf * 60 - elapsed) + CANDLE_BUFFER_S


def tf_bar_has_closed(tf: int, now: datetime, last_eval: "datetime | None") -> bool:
    """
    True si una vela de `tf` minutos cerró desde la última evaluación.

    Se usa para evitar re-evaluar el mismo bar muchas veces cuando el loop
    corre a cadencia de 1min pero hay TFs más grandes activos. Ejemplo:
    con min_tf=1 y TF=240m activo, sin este gate el bot evalúa el mismo
    bar de 4h ~240 veces → desperdicio de CPU + falsos positivos en
    estrategias que miran el last candle en vivo.

    Reglas de alineación:
      - tf=1    → siempre True (cada minuto es un bar nuevo).
      - tf=1440 → True si cambió la fecha.
      - otros   → True si el bucket `(hour*60 + minute) // tf` cambió.
                  Esto matchea el origin de midnight que usa pandas
                  `resample()` por default en get_price_history (60/240m).
      - last_eval=None → True (primera evaluación desde el arranque).
    """
    if last_eval is None:
        return True
    if tf <= 1:
        return True
    if tf >= 1440:
        return now.date() != last_eval.date()
    cur_bucket  = (now.hour * 60 + now.minute) // tf
    prev_bucket = (last_eval.hour * 60 + last_eval.minute) // tf
    same_day    = now.date() == last_eval.date()
    return (not same_day) or (cur_bucket > prev_bucket)


# ── Market hours (schedule-aware) ─────────────────────────
# Las funciones leen el schedule desde settings (Firestore) vía
# eolo_common.trading_hours. Si settings=None usa los defaults
# equity (09:30 / 15:27 / 15:27). Mantienen el check de weekday
# porque NYSE no opera sábados/domingos.

def is_auto_close_time(settings: dict = None) -> bool:
    """
    True si ya pasó el `auto_close_et` configurado y todavía estamos
    dentro del día de mercado (antes de 16:00 ET, L-V).

    Se usa una sola vez por día para cerrar todas las posiciones.
    """
    now = now_et()
    if now.weekday() >= 5:
        return False
    sch = load_schedule(settings or {}, defaults=DEFAULTS_EQUITY)
    if not sch.enabled:
        return False
    if not _tr_is_after_auto_close(now, sch):
        return False
    # Ceiling absoluto: no cerramos fuera del día de mercado
    ceiling = now.replace(hour=MARKET_CLOSE_CEILING[0],
                          minute=MARKET_CLOSE_CEILING[1],
                          second=0, microsecond=0)
    return now < ceiling


def is_market_open(settings: dict = None) -> bool:
    """
    True si estamos dentro del rango `[start, end)` configurado (L-V).
    Si settings=None, usa defaults equity (09:30-15:27 ET).
    Si schedule.enabled=False, retorna True siempre (ignora horarios).
    """
    now = now_et()
    if now.weekday() >= 5:
        return False
    sch = load_schedule(settings or {}, defaults=DEFAULTS_EQUITY)
    return is_within_trading_window(now, sch)


# ── One scan cycle ────────────────────────────────────────

def close_all_open_positions(market_data: MarketData, budget: float,
                               settings: dict | None = None):
    """Cierra en market order todas las posiciones con estado LONG."""
    logger.warning("🚨 CLOSE ALL — cerrando todas las posiciones abiertas")
    all_tickers = TICKERS_EMA_GAP + TICKERS_LEVERAGED
    closed = 0
    for ticker in all_tickers:
        if trader.positions.get(ticker) == "LONG":
            try:
                # Usar precio en tiempo real para el cierre
                price = market_data.get_quote(ticker)
                if not price:
                    # Fallback: último cierre de vela
                    candles = market_data.get_candles(ticker, period_type="day", period=1)
                    price = float(candles["close"].iloc[-1]) if candles is not None and not candles.empty else 0
                if price and price > 0:
                    result = {
                        "ticker":       ticker,
                        "signal":       "SELL",
                        "price":        price,
                        "strategy":     "CLOSE_ALL",
                        "_budget":      budget,
                        "reason":       "CLOSE_ALL manual flag",
                        "_macro_feeds": (settings or {}).get("_macro_feeds"),
                    }
                    trader.execute(result)
                    closed += 1
            except Exception as e:
                logger.error(f"  Error closing {ticker}: {e}")
    logger.info(f"🚨 CLOSE ALL — {closed} posiciones cerradas")
    clear_close_all_flag()


def compute_today_pnl() -> float:
    """
    Suma USD del P&L realizado del día leyendo eolo-trades/<today>.
    Hace pairs BUY→SELL por ticker+estrategia. Ignora operaciones sueltas.
    Fail-soft: si Firestore falla, retorna 0.0.
    """
    try:
        today = datetime.now(EASTERN).strftime("%Y-%m-%d")
        db    = firestore.Client(project=GCP_PROJECT)
        doc   = db.collection("eolo-trades").document(today).get()
        if not doc.exists:
            return 0.0
        trades = list((doc.to_dict() or {}).values())
        trades.sort(key=lambda t: t.get("timestamp", ""))
        open_buys = {}  # (ticker, strategy) → {price, shares}
        total_pnl = 0.0
        for t in trades:
            ticker   = t.get("ticker", "")
            strategy = t.get("strategy", "")
            action   = t.get("action", "")
            try:
                price  = float(t.get("price", 0))
                shares = int(t.get("shares", 1))
            except (TypeError, ValueError):
                continue
            key = (ticker, strategy)
            if action == "BUY":
                open_buys[key] = {"price": price, "shares": shares}
            elif action == "SELL" and key in open_buys:
                b = open_buys.pop(key)
                total_pnl += (price - b["price"]) * b["shares"]
        return round(total_pnl, 2)
    except Exception as e:
        logger.debug(f"compute_today_pnl error: {e}")
        return 0.0


def is_daily_loss_cap_hit(settings: dict) -> bool:
    """
    True si el P&L del día (compute_today_pnl) cayó por debajo del
    daily_loss_cap_pct (negativo, %) aplicado sobre el capital nocional
    (budget * max_positions). Setter: modal Config del dashboard.
    """
    try:
        cap = float(settings.get("daily_loss_cap_pct", 0))
        if cap >= 0:
            return False
        budget   = float(settings.get("budget", 100))
        max_pos  = int(settings.get("max_positions", 5))
        nominal  = budget * max(1, max_pos)
        if nominal <= 0:
            return False
        pnl      = compute_today_pnl()
        pnl_pct  = pnl / nominal * 100.0
        return pnl_pct <= cap
    except Exception as e:
        logger.debug(f"is_daily_loss_cap_hit error: {e}")
        return False


def enforce_risk_exits(market_data: "MarketData", settings: dict, budget: float):
    """
    Para cada posición abierta (trader.positions == LONG), compara
    quote actual vs entry_price y fuerza SELL si cruza el SL o TP
    globales seteados desde el modal (default_stop_loss_pct /
    default_take_profit_pct, en %).

    Nota: esto aplica por encima de la lógica propia de cada estrategia.
    Si una estrategia ya definió sus propios SL/TP hardcodeados más
    estrictos, se disparará el que llegue primero. Los defaults del
    modal actúan como "safety net" global.
    """
    try:
        sl_pct = settings.get("default_stop_loss_pct")
        tp_pct = settings.get("default_take_profit_pct")
        if sl_pct is None and tp_pct is None:
            return
        sl_pct = abs(float(sl_pct)) if sl_pct is not None else None
        tp_pct = abs(float(tp_pct)) if tp_pct is not None else None
    except (TypeError, ValueError):
        return

    for ticker, state in list(trader.positions.items()):
        if state not in ("LONG", "SHORT"):
            continue
        entry = trader.entry_prices.get(ticker)
        if not entry or entry <= 0:
            continue
        try:
            price = market_data.get_quote(ticker)
        except Exception:
            price = None
        if not price or price <= 0:
            continue

        reason = None
        if state == "LONG":
            pnl_pct = (price - entry) / entry * 100.0
            if sl_pct is not None and pnl_pct <= -sl_pct:
                reason = f"RISK_SL {pnl_pct:+.2f}% ≤ -{sl_pct:.2f}%"
            elif tp_pct is not None and pnl_pct >= tp_pct:
                reason = f"RISK_TP {pnl_pct:+.2f}% ≥ +{tp_pct:.2f}%"
            exit_signal = "SELL"

        else:  # SHORT — P&L invertido: ganás cuando el precio baja
            pnl_pct = (entry - price) / entry * 100.0
            if sl_pct is not None and pnl_pct <= -sl_pct:
                reason = f"RISK_SL_SHORT {pnl_pct:+.2f}% ≤ -{sl_pct:.2f}% (precio subió)"
            elif tp_pct is not None and pnl_pct >= tp_pct:
                reason = f"RISK_TP_SHORT {pnl_pct:+.2f}% ≥ +{tp_pct:.2f}% (precio bajó)"
            exit_signal = "BUY"  # BUY_TO_COVER

        if reason:
            logger.warning(f"[RISK-WATCHDOG] {ticker} ({state}) {reason} — forzando {exit_signal}")
            trader.execute({
                "ticker":        ticker,
                "signal":        exit_signal,
                "price":         price,
                "strategy":      "RISK_WATCHDOG",
                "_budget":       budget,
                "reason":        reason,
                "_macro_feeds":  settings.get("_macro_feeds"),
                "_allow_short":  False,  # watchdog nunca abre nuevas posiciones
            })


def run_cycle(market_data: MarketData, settings: dict, timeframe: int = 1,
              signal_registrar=None):
    """
    Corre todas las estrategias habilitadas sobre market_data al timeframe
    indicado.

    signal_registrar: callable opcional con firma (strat_name, result_dict).
        Si se pasa, en vez de ejecutar trades, cada resultado se reporta a
        este callback — útil para acumular señales multi-TF y consolidarlas
        luego via ConfluenceFilter. Si es None (default), se ejecutan los
        trades inmediatamente como siempre.
    """
    tf_labels  = {1:"1m", 5:"5m", 15:"15m", 30:"30m", 60:"1h", 240:"4h", 1440:"1d"}
    tf_label   = tf_labels.get(timeframe, f"{timeframe}m")
    now        = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")
    mode       = "📄 PAPER" if trader.PAPER_TRADING else "💰 LIVE"
    strategies = get_active_strategies()
    budget     = float(settings.get("budget", 100))

    # ── Auto-Router: ajusta toggles según régimen cada 30 min ──
    try:
        if _auto_router_v1.should_update():
            _spy_df = market_data.get_candles(
                "SPY", period_type="day", period=1,
                frequency_type="minute", frequency=timeframe,
            )
            _vix = None
            try:
                _mac = settings.get("_macro_feeds")
                if _mac:
                    _vix = float(_mac.latest("VIX") or 20.0)
            except Exception:
                pass
            if _spy_df is not None and not _spy_df.empty and _vix is not None:
                _new_toggles = _auto_router_v1.update(vix=_vix, spy_df=_spy_df, save_firestore=False)
                # Solo aplicar si el toggle no fue sobreescrito manualmente en Firestore
                for _k, _v in _new_toggles.items():
                    if _k not in strategies:
                        strategies[_k] = _v
                logger.debug(f"[AUTO_ROUTER] v1 toggles actualizados: regime={_auto_router_v1.last_toggles}")
    except Exception as _ar_e:
        logger.debug(f"[AUTO_ROUTER] v1 skip: {_ar_e}")

    # Leer tickers activos desde Firestore (dashboard toggle)
    tickers_ema_gap   = get_active_tickers(TICKERS_EMA_GAP)
    tickers_leveraged = get_active_tickers(TICKERS_LEVERAGED)

    active = [name for name, enabled in strategies.items() if enabled]
    active_str = " + ".join(active) if active else "ninguna activa"

    print(f"\n{'='*76}")
    print(f"  {mode}  |  {now}  |  ⏱ {tf_label}")
    print(f"  Estrategias activas : {active_str}")
    print(f"  Budget por trade    : ${budget:.0f}")
    print(f"  Clásicas activas   : {tickers_ema_gap}")
    print(f"  Apalancadas activas : {tickers_leveraged}")
    print(f"{'='*76}")

    # ── Risk watchdog: SL/TP globales sobre posiciones abiertas ──
    # Corre ANTES de las estrategias para dar salida inmediata si
    # el P&L de alguna posición cruzó los defaults del modal Config.
    try:
        enforce_risk_exits(market_data, settings, budget)
    except Exception as e:
        logger.warning(f"enforce_risk_exits error: {e}")

    # ── Daily loss cap: bloquea NUEVAS aperturas si P&L del día
    # cayó por debajo del cap. No bloquea cierres: las estrategias
    # y el watchdog pueden seguir sacando posiciones abiertas.
    daily_cap_hit = is_daily_loss_cap_hit(settings)
    if daily_cap_hit:
        logger.warning(
            f"🛑 DAILY LOSS CAP alcanzado "
            f"(pnl={compute_today_pnl()} USD, cap={settings.get('daily_loss_cap_pct')}%) "
            f"— nuevas aperturas bloqueadas en este ciclo"
        )

    def _exec(result, strat_name):
        # Si el cap del día está activo, dejamos pasar SOLO las SELL
        # (cerrar posiciones existentes); los BUY se suprimen.
        if daily_cap_hit and result.get("signal") == "BUY":
            result["signal"] = "HOLD"
            result["_daily_cap_suppressed"] = True
        # ── Macro Regime Bridge: ajuste dinámico del budget ──
        # VIX<15 → 1.5× | VIX 15-25 → 1.0× | VIX>25 → 0.5×
        regime_mult = get_regime_multiplier(settings.get("_macro_feeds"))
        adj_budget  = budget * regime_mult
        result["strategy"]     = strat_name
        result["_budget"]      = adj_budget
        result["_timeframe"]   = timeframe
        result["_macro_feeds"] = settings.get("_macro_feeds")
        # Habilita apertura de shorts si el setting lo permite
        result["_allow_short"] = bool(settings.get("allow_short_selling", False))
        trader.print_status(result)

        # ── Modo "collect" para confluencia multi-TF ──
        # Cuando el orquestador pasa un signal_registrar, no ejecutamos
        # aquí: dejamos que el caller consolide señales a través de TFs
        # (vía ConfluenceFilter) y ejecute solo las finales.
        if signal_registrar is not None:
            try:
                signal_registrar(strat_name, result)
            except Exception as e:
                logger.warning(f"signal_registrar({strat_name}) falló: {e}")
            return

        if result["signal"] not in ("HOLD", "ERROR"):
            ticker = result["ticker"]

            # ── Precio de ejecución en tiempo real ────────
            # La señal se detecta sobre el cierre de la vela (histórico),
            # pero la orden se ejecuta al precio de mercado ACTUAL.
            # Esto evita comprar al precio de una vela cerrada hace 15-30s.
            live_price = market_data.get_quote(ticker)
            if live_price and live_price > 0:
                candle_price = result["price"]
                slippage     = round(live_price - candle_price, 4)
                logger.info(
                    f"[{strat_name}] {ticker} — "
                    f"vela={candle_price} quote={live_price} "
                    f"slippage={slippage:+.4f}"
                )
                result["price"] = live_price   # ejecutar al precio real
            else:
                logger.warning(f"[{strat_name}] {ticker} — quote falló, usando precio de vela")

            trader.execute(result)

    # ── EMA 3/8 Crossover (+ filtro SMA200 opcional) ─────
    if strategies.get("ema_crossover"):
        use_sma200 = strategies.get("sma200_filter", True)
        filter_str = "SMA200 ON" if use_sma200 else "SMA200 OFF"
        print(f"\n  [EMA 3/8 | {filter_str}] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = ema_strategy.analyze(market_data, ticker,
                                          use_sma200_filter=use_sma200)
            _exec(result, "EMA")

    # ── Gap Fade ─────────────────────────────────────────
    if strategies.get("gap_fade"):
        print(f"\n  [GAP] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            # FASE 6: gap only works on 1h+ (skip 30m)
            if should_run_strategy("gap_fade", ticker, timeframe):
                result = gap_strategy.analyze(market_data, ticker)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "GAP", "reason": "below_1h_tf"}
            _exec(result, "GAP")

    # ── VWAP + RSI ───────────────────────────────────────
    if strategies.get("vwap_rsi"):
        print(f"\n  [VWAP_RSI] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = vwap_strategy.analyze(market_data, ticker)
            _exec(result, "VWAP+RSI")

    # ── Bollinger Bands ──────────────────────────────────
    if strategies.get("bollinger"):
        print(f"\n  [BOLLINGER] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = bollinger_strategy.analyze(market_data, ticker)
            _exec(result, "BOLLINGER")

    # ── Opening Range Breakout ───────────────────────────
    if strategies.get("orb"):
        # SL/TP overrides desde el modal Config (si están seteados).
        # El dashboard guarda en % (ej 2.0 = 2%); ORB trabaja en fracciones.
        orb_tp = settings.get("default_take_profit_pct")
        orb_sl = settings.get("default_stop_loss_pct")
        orb_tp = float(orb_tp) / 100.0 if orb_tp is not None else None
        orb_sl = float(orb_sl) / 100.0 if orb_sl is not None else None
        print(f"\n  [ORB] Tickers: {tickers_leveraged}  | TP={orb_tp} SL={orb_sl}")
        for ticker in tickers_leveraged:
            entry_price = trader.entry_prices.get(ticker)
            result      = orb_strategy.analyze(
                market_data, ticker, entry_price,
                profit_target=orb_tp, stop_loss=orb_sl,
            )
            _exec(result, "ORB")

    # ── RSI + SMA200 ─────────────────────────────────────
    if strategies.get("rsi_sma200"):
        print(f"\n  [RSI_SMA200] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = rsi_sma200_strategy.analyze(market_data, ticker)
            _exec(result, "RSI_SMA200")

    # ── Supertrend ───────────────────────────────────────
    if strategies.get("supertrend"):
        print(f"\n  [SUPERTREND] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            # FASE 6 Tier 2: Asset-specific ATR tuning (QQQ=7, UNH=6)
            if should_run_strategy("supertrend", ticker, timeframe):
                result = supertrend_strategy.analyze(market_data, ticker)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "SUPERTREND", "reason": "not_in_tier2_map"}
            _exec(result, "SUPERTREND")

    # ── High/Low Breakout ────────────────────────────────
    if strategies.get("hh_ll"):
        print(f"\n  [HH_LL] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = hh_ll_strategy.analyze(market_data, ticker)
            _exec(result, "HH_LL")

    # ── Heikin Ashi + EMA Cloud ──────────────────────────
    if strategies.get("ha_cloud"):
        print(f"\n  [HA_CLOUD] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = ha_cloud_strategy.analyze(market_data, ticker)
            _exec(result, "HA_CLOUD")

    # ── Bollinger/Keltner Squeeze ────────────────────────
    if strategies.get("squeeze"):
        print(f"\n  [SQUEEZE] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = squeeze_strategy.analyze(market_data, ticker)
            _exec(result, "SQUEEZE")

    # ── MACD + Bollinger Upper Breakout ──────────────────
    if strategies.get("macd_bb"):
        print(f"\n  [MACD_BB] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            # FASE 6 Tier 2: Asset/TF specific routing
            if should_run_strategy("macd_bb", ticker, timeframe):
                result = macd_bb_strategy.analyze(market_data, ticker)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "MACD_BB", "reason": "not_in_tier2_map"}
            _exec(result, "MACD_BB")

    # ── EMA Cloud + TSI + MACD ───────────────────────────
    if strategies.get("ema_tsi"):
        print(f"\n  [EMA_TSI] Tickers: {tickers_ema_gap}")
        for ticker in tickers_ema_gap:
            result = ema_tsi_strategy.analyze(market_data, ticker)
            _exec(result, "EMA_TSI")

    # ── Vela Pivot (Volume Breakout + Daily Pivot) ───────
    if strategies.get("vela_pivot"):
        print(f"\n  [VELA_PIVOT] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            result = vela_pivot_strategy.analyze(market_data, ticker)
            _exec(result, "VELA_PIVOT")

    # ═══════════════════════════════════════════════════════════
    #  Nivel 1 (trading_strategies_v2.md) — todas long-only
    # ═══════════════════════════════════════════════════════════
    # Universo combinado: clásicas + leveraged. Cada estrategia
    # aplica sus propios thresholds / filtros internos por ticker.
    tickers_all = list(dict.fromkeys(tickers_ema_gap + tickers_leveraged))

    # ── RVOL Breakout (#2) ───────────────────────────────
    if strategies.get("rvol_breakout"):
        print(f"\n  [RVOL_BREAKOUT] Tickers: {tickers_all}")
        for ticker in tickers_all:
            entry = trader.entry_prices.get(ticker)
            result = rvol_strategy.analyze(market_data, ticker, entry)
            _exec(result, "RVOL_BREAKOUT")

    # ── Stop Run Reversal (#5) ───────────────────────────
    if strategies.get("stop_run"):
        print(f"\n  [STOP_RUN] Tickers: {tickers_all}")
        for ticker in tickers_all:
            # FASE 6 Tier 2: Asset-specific lookback (JPM=20, XOM=10)
            if should_run_strategy("stop_run", ticker, timeframe):
                entry = trader.entry_prices.get(ticker)
                result = stop_run_strategy.analyze(market_data, ticker, entry)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "STOP_RUN", "reason": "not_in_tier2_map"}
            _exec(result, "STOP_RUN")

    # ── VWAP Z-Score (#11) ───────────────────────────────
    if strategies.get("vwap_zscore"):
        print(f"\n  [VWAP_ZSCORE] Tickers: {tickers_all}")
        for ticker in tickers_all:
            # FASE 6 Tier 2: Tighter Z-score for JPM (1.2 instead of 2.5)
            if should_run_strategy("vwap_zscore", ticker, timeframe):
                entry = trader.entry_prices.get(ticker)
                result = vwap_z_strategy.analyze(market_data, ticker, entry)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "VWAP_ZSCORE", "reason": "not_in_tier2_map"}
            _exec(result, "VWAP_ZSCORE")

    # ── Volume Reversal Bar (#15r) ───────────────────────
    if strategies.get("volume_reversal_bar"):
        print(f"\n  [VOL_REVERSAL_BAR] Tickers: {tickers_all}")
        for ticker in tickers_all:
            # FASE 6 Tier 2: Higher vol multiplier for TSLA (2.0 vs 1.5)
            if should_run_strategy("volume_reversal_bar", ticker, timeframe):
                entry = trader.entry_prices.get(ticker)
                result = vrb_strategy.analyze(market_data, ticker, entry)
            else:
                result = {"ticker": ticker, "signal": "HOLD", "strategy": "VOL_REVERSAL_BAR", "reason": "not_in_tier2_map"}
            _exec(result, "VOL_REVERSAL_BAR")

    # ── Anchor VWAP (#16) ────────────────────────────────
    if strategies.get("anchor_vwap"):
        print(f"\n  [ANCHOR_VWAP] Tickers: {tickers_all}")
        for ticker in tickers_all:
            entry = trader.entry_prices.get(ticker)
            result = anchor_vwap_strategy.analyze(market_data, ticker, entry)
            _exec(result, "ANCHOR_VWAP")

    # ── OBV Multi-TF (#17) ───────────────────────────────
    if strategies.get("obv_mtf"):
        print(f"\n  [OBV_MTF] Tickers: {tickers_all}")
        for ticker in tickers_all:
            entry = trader.entry_prices.get(ticker)
            result = obv_strategy.analyze(market_data, ticker, entry)
            _exec(result, "OBV_MTF")

    # ── TSV Cross (#20) ──────────────────────────────────
    if strategies.get("tsv"):
        print(f"\n  [TSV] Tickers: {tickers_all}")
        for ticker in tickers_all:
            entry = trader.entry_prices.get(ticker)
            result = tsv_strategy.analyze(market_data, ticker, entry)
            _exec(result, "TSV")

    # ── Volume-Weighted MACD (#21) ───────────────────────
    if strategies.get("vw_macd"):
        print(f"\n  [VW_MACD] Tickers: {tickers_all}")
        for ticker in tickers_all:
            entry = trader.entry_prices.get(ticker)
            result = vw_macd_strategy.analyze(market_data, ticker, entry)
            _exec(result, "VW_MACD")

    # ── Opening Drive Exhaustion (#25; solo leveraged) ───
    if strategies.get("opening_drive"):
        print(f"\n  [OPENING_DRIVE] Tickers: {tickers_leveraged}")
        for ticker in tickers_leveraged:
            entry = trader.entry_prices.get(ticker)
            result = opening_drive_strategy.analyze(market_data, ticker, entry)
            _exec(result, "OPENING_DRIVE")

    # ═══════════════════════════════════════════════════════════
    #  Nivel 2 — macro feeds (VIX/VIX9D/VIX3M/TICK/TRIN)
    #  macro=None hasta que MacroFeeds esté wireado; las strategies
    #  devuelven HOLD silenciosamente cuando no hay macro.
    # ═══════════════════════════════════════════════════════════
    macro = settings.get("_macro_feeds")   # inyectado desde main() cuando esté disponible
    tickers_macro = [t for t in tickers_all if t in {"SPY", "QQQ", "TQQQ"}]

    # ── VIX Mean Reversion (#6) ──────────────────────────
    if strategies.get("vix_mean_rev"):
        print(f"\n  [VIX_MEAN_REV] Tickers: {tickers_macro}")
        for ticker in tickers_macro:
            entry = trader.entry_prices.get(ticker)
            result = vix_mr_strategy.analyze(market_data, ticker,
                                             macro=macro, entry_price=entry)
            _exec(result, "VIX_MEAN_REV")

    # ── VIX Correlation Flip (#7) ────────────────────────
    if strategies.get("vix_correlation"):
        print(f"\n  [VIX_CORRELATION] Tickers: {tickers_macro}")
        for ticker in tickers_macro:
            entry = trader.entry_prices.get(ticker)
            result = vix_corr_strategy.analyze(market_data, ticker,
                                               macro=macro, entry_price=entry)
            _exec(result, "VIX_CORRELATION")

    # ── VIX Squeeze Breakout (#8) ────────────────────────
    if strategies.get("vix_squeeze"):
        print(f"\n  [VIX_SQUEEZE] Tickers: {tickers_macro}")
        for ticker in tickers_macro:
            entry = trader.entry_prices.get(ticker)
            result = vix_sq_strategy.analyze(market_data, ticker,
                                             macro=macro, entry_price=entry)
            _exec(result, "VIX_SQUEEZE")

    # ── TICK/TRIN Extreme Fade (#22) ─────────────────────
    if strategies.get("tick_trin_fade"):
        print(f"\n  [TICK_TRIN_FADE] Tickers: {tickers_macro}")
        for ticker in tickers_macro:
            entry = trader.entry_prices.get(ticker)
            result = tt_strategy.analyze(market_data, ticker,
                                         macro=macro, entry_price=entry)
            _exec(result, "TICK_TRIN_FADE")

    # ── VRP Intraday (#23) ───────────────────────────────
    if strategies.get("vrp_intraday"):
        print(f"\n  [VRP_INTRADAY] Tickers: {tickers_macro}")
        for ticker in tickers_macro:
            entry = trader.entry_prices.get(ticker)
            result = vrp_strategy.analyze(market_data, ticker,
                                          macro=macro, entry_price=entry)
            _exec(result, "VRP_INTRADAY")

    # ═══════════════════════════════════════════════════════════
    #  Suite "EMA 3/8 y MACD" (v3) — 11 estrategias port del upload
    #  Universo: clásicas + leveraged (EQUITY_ONLY solo ORB_V3).
    # ═══════════════════════════════════════════════════════════
    tickers_v3 = list(dict.fromkeys(tickers_ema_gap + tickers_leveraged))

    # ── EMA Crossover 3/8 ───────────────────────────────
    if strategies.get("ema_3_8"):
        print(f"\n  [EMA_3_8] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_ema_3_8(market_data, ticker)
            _exec(result, "EMA_3_8")

    # ── EMA Crossover 8/21 ──────────────────────────────
    if strategies.get("ema_8_21"):
        print(f"\n  [EMA_8_21] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_ema_8_21(market_data, ticker)
            _exec(result, "EMA_8_21")

    # ── MACD Accel ──────────────────────────────────────
    if strategies.get("macd_accel"):
        print(f"\n  [MACD_ACCEL] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_macd_accel(market_data, ticker)
            _exec(result, "MACD_ACCEL")

    # ── Volume Breakout (v3) ────────────────────────────
    if strategies.get("volume_breakout"):
        print(f"\n  [VOLUME_BREAKOUT] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_volume_breakout(market_data, ticker)
            _exec(result, "VOLUME_BREAKOUT")

    # ── Buy Pressure Trend ──────────────────────────────
    if strategies.get("buy_pressure"):
        print(f"\n  [BUY_PRESSURE] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_buy_pressure(market_data, ticker)
            _exec(result, "BUY_PRESSURE")

    # ── Sell Pressure / Net Pressure ────────────────────
    if strategies.get("sell_pressure"):
        print(f"\n  [SELL_PRESSURE] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_sell_pressure(market_data, ticker)
            _exec(result, "SELL_PRESSURE")

    # ── VWAP Momentum ───────────────────────────────────
    if strategies.get("vwap_momentum"):
        print(f"\n  [VWAP_MOMENTUM] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_vwap_momentum(market_data, ticker)
            _exec(result, "VWAP_MOMENTUM")

    # ── Opening Range Breakout (v3) — equity-only ───────
    if strategies.get("orb_v3"):
        print(f"\n  [ORB_V3] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_orb_v3(market_data, ticker)
            _exec(result, "ORB_V3")

    # ── Donchian Turtle ─────────────────────────────────
    if strategies.get("donchian_turtle"):
        print(f"\n  [DONCHIAN_TURTLE] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_donchian_turtle(market_data, ticker)
            _exec(result, "DONCHIAN_TURTLE")

    # ── Bulls-gated BSP ─────────────────────────────────
    if strategies.get("bulls_bsp"):
        print(f"\n  [BULLS_BSP] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_bulls_bsp(market_data, ticker)
            _exec(result, "BULLS_BSP")

    # ── Net BSV Trend ───────────────────────────────────
    if strategies.get("net_bsv"):
        print(f"\n  [NET_BSV] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_net_bsv(market_data, ticker)
            _exec(result, "NET_BSV")

    # ═══════════════════════════════════════════════════════════
    #  Combos Ganadores (2026-04) — 7 estrategias
    #  Universo: mismo que v3 (clásicas + leveraged).
    #  COMBO2_RUBBER_BAND es equity-only (excluida de crypto).
    # ═══════════════════════════════════════════════════════════

    # ── Combo 1 — EMA Scalper 3/8 ──────────────────────
    if strategies.get("combo1_ema_scalper"):
        print(f"\n  [COMBO1_EMA_SCALPER] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo1_ema_scalper(market_data, ticker)
            _exec(result, "COMBO1_EMA_SCALPER")

    # ── Combo 2 — Rubber Band VWAP (equity only) ───────
    if strategies.get("combo2_rubber_band"):
        print(f"\n  [COMBO2_RUBBER_BAND] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo2_rubber_band(market_data, ticker)
            _exec(result, "COMBO2_RUBBER_BAND")

    # ── Combo 3 — Nino Squeeze ⭐ ────────────────────────
    if strategies.get("combo3_nino_squeeze"):
        print(f"\n  [COMBO3_NINO_SQUEEZE] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo3_nino_squeeze(market_data, ticker)
            _exec(result, "COMBO3_NINO_SQUEEZE")

    # ── Combo 4 — SlimRibbon + MACD ─────────────────────
    if strategies.get("combo4_slimribbon"):
        print(f"\n  [COMBO4_SLIMRIBBON] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo4_slimribbon(market_data, ticker)
            _exec(result, "COMBO4_SLIMRIBBON")

    # ── Combo 5 — BTD + Stacked + PullBack34 ────────────
    if strategies.get("combo5_btd"):
        print(f"\n  [COMBO5_BTD] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo5_btd(market_data, ticker)
            _exec(result, "COMBO5_BTD")

    # ── Combo 6 — FractalCCIx Premium ───────────────────
    if strategies.get("combo6_fractalccix"):
        print(f"\n  [COMBO6_FRACTALCCIX] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo6_fractalccix(market_data, ticker)
            _exec(result, "COMBO6_FRACTALCCIX")

    # ── Combo 7 — Campbell Swing ─────────────────────────
    if strategies.get("combo7_campbell"):
        print(f"\n  [COMBO7_CAMPBELL] Tickers: {tickers_v3}")
        for ticker in tickers_v3:
            result = v3_strategy.analyze_combo7_campbell(market_data, ticker)
            _exec(result, "COMBO7_CAMPBELL")

    # ═══════════════════════════════════════════════════════════
    #  FASE 5 Winner: XOM 30m Bollinger (PF 1.38, real backtest)
    # ═══════════════════════════════════════════════════════════
    if strategies.get("xom_30m"):
        print(f"\n  [XOM_30M_BOLLINGER] FASE 5 Winner | PF=1.38 (60d backtest)")
        result = xom_30m_strategy.analyze(market_data, "XOM")
        _exec(result, "XOM_30M")

    # ═══════════════════════════════════════════════════════════
    #  FASE 4 Winner: Bollinger + RSI Sensitive (daily)
    #  PF 38.52 SPY | 14.78 AAPL | 14.02 QQQ (backtest 252d real)
    # ═══════════════════════════════════════════════════════════
    if strategies.get("bollinger_rsi_sensitive"):
        bb_rsi_tickers = ["SPY", "AAPL", "QQQ"]
        print(f"\n  [BOLLINGER_RSI_SENSITIVE] FASE 4 Winner | Tickers: {bb_rsi_tickers}")
        for ticker in bb_rsi_tickers:
            result = bb_rsi_sens_strategy.analyze(market_data, ticker)
            _exec(result, "BOLLINGER_RSI_SENSITIVE")

    # ═══════════════════════════════════════════════════════════
    #  FASE 7a Winners: MACD Confluence (30m intraday)
    #  PF 4.58 QQQ | 3.14 SPY (backtest 2016-2026 real)
    # ═══════════════════════════════════════════════════════════
    if strategies.get("macd_confluence_fase7a"):
        macd_tickers = ["QQQ", "SPY"]
        print(f"\n  [MACD_CONFLUENCE] FASE 7a Winner | Tickers: {macd_tickers}")
        for ticker in macd_tickers:
            result = macd_conf_strategy.analyze(market_data, ticker)
            _exec(result, "MACD_CONFLUENCE")

    # ═══════════════════════════════════════════════════════════
    #  FASE 7a Winners: Momentum Score (30m intraday)
    #  PF 4.58 QQQ | 3.14 SPY (backtest 2016-2026 real)
    # ═══════════════════════════════════════════════════════════
    if strategies.get("momentum_score_fase7a"):
        momentum_tickers = ["SPY"]
        print(f"\n  [MOMENTUM_SCORE] FASE 7a Winner | Tickers: {momentum_tickers}")
        for ticker in momentum_tickers:
            result = momentum_strategy.analyze(market_data, ticker)
            _exec(result, "MOMENTUM_SCORE")

    # ═══════════════════════════════════════════════════════════
    #  Nuevas estrategias (2026-04-27)
    # ═══════════════════════════════════════════════════════════

    # ── Overnight Drift — BUY 15:40-15:55 ET, SELL 09:30-09:45 ET
    if strategies.get("overnight_drift"):
        od_tickers = ["SPY", "QQQ"]
        print(f"\n  [OVERNIGHT_DRIFT] Tickers: {od_tickers}")
        for ticker in od_tickers:
            entry = trader.entry_prices.get(ticker)
            result = overnight_drift_strategy.analyze(
                market_data, ticker, macro=macro,
                entry_price=entry, timeframe=timeframe,
            )
            _exec(result, "OVERNIGHT_DRIFT")

    # ── VIX Spike Fade — BUY cuando VIX sube >5% intraday
    if strategies.get("vix_spike_fade"):
        vsf_tickers = [t for t in tickers_all if t in {"SPY", "QQQ", "TQQQ"}]
        print(f"\n  [VIX_SPIKE_FADE] Tickers: {vsf_tickers}")
        for ticker in vsf_tickers:
            entry = trader.entry_prices.get(ticker)
            result = vix_spike_fade_strategy.analyze(
                market_data, ticker, macro=macro,
                entry_price=entry, timeframe=timeframe,
            )
            _exec(result, "VIX_SPIKE_FADE")

    if strategies.get("spy_qqq_divergence"):
        print(f"\n  [SPY_QQQ_DIV] SPY / QQQ")
        for ticker in ["SPY", "QQQ"]:
            entry = trader.entry_prices.get(ticker)
            result = sqd_strategy.analyze(
                market_data, ticker, macro=macro,
                entry_price=entry, timeframe=timeframe,
            )
            _exec(result, "SPY_QQQ_DIV")

    if strategies.get("sector_rrg"):
        print(f"\n  [SECTOR_RRG] Sectors: {TICKERS_SECTORS}")
        for ticker in TICKERS_SECTORS:
            entry = trader.entry_prices.get(ticker)
            result = sector_rrg_strategy.analyze(
                market_data, ticker, macro=macro,
                entry_price=entry, timeframe=timeframe,
            )
            _exec(result, "SECTOR_RRG")

    # Note: wait display is informational — actual sleep happens in main()
    print(f"\n{'='*76}")


def run_multi_tf_confluence_cycle(market_data: MarketData, settings: dict,
                                  active_tfs: list[int]):
    """
    Ciclo multi-TF con filtro de confluencia.

    Para cada TF activo corre todas las estrategias en modo "collect",
    registra cada BUY/SELL en un ConfluenceFilter, y al terminar consolida
    — sólo ejecuta los trades que pasen el filtro (min_agree TFs de acuerdo
    en modo confluence; SELL-preempt en modo non-confluence).

    Se usa cuando settings.confluence_mode == True. Cuando es False, el
    loop principal llama a run_cycle() directamente por cada TF (path viejo).
    """
    tf_labels = {1:"1m", 5:"5m", 15:"15m", 30:"30m", 60:"1h", 240:"4h", 1440:"1d"}
    budget    = float(settings.get("budget", 100))

    cfilter = ConfluenceFilter(
        mode=bool(settings.get("confluence_mode", False)),
        min_agree=int(settings.get("confluence_min_agree", 2)),
    )

    # Acumuladores para reporting / logging del reason de la orden final
    tf_map: dict[tuple[str, str], list[int]] = {}
    last_prices: dict[str, float] = {}

    def _register(strat_name, result):
        ticker = result.get("ticker")
        signal = result.get("signal", "HOLD")
        tf     = result.get("_timeframe", 1)
        price  = result.get("price")
        if ticker and price:
            last_prices[ticker] = price
        if ticker and signal in ("BUY", "SELL"):
            cfilter.register(ticker, strat_name, tf, signal)
            tf_map.setdefault((ticker, strat_name), []).append(tf)

    # ── Fase 1: colectar señales por cada TF ──
    logger.info(
        f"⚡ Confluencia | TFs={[tf_labels.get(t,str(t)) for t in active_tfs]} | "
        f"min_agree={cfilter.min_agree}"
    )
    for tf in active_tfs:
        market_data.frequency = tf
        try:
            run_cycle(market_data, settings, timeframe=tf, signal_registrar=_register)
        except Exception as e:
            logger.error(f"Cycle error (tf={tf}m, collect): {e}")

    # ── Fase 2: consolidar ──
    consolidated = cfilter.consolidate()
    if not consolidated:
        logger.info("⚡ Confluencia: sin señales accionables este ciclo")
        return

    logger.info(f"⚡ Confluencia consolidada: {len(consolidated)} firmas finales")

    # ── Fase 3: ejecutar solo las firmas consolidadas ──
    # SELL precede BUY (cerrar antes de abrir en caso de flip).
    actionable = [(tk, st, final) for (tk, st), final in consolidated.items()
                  if final in ("BUY", "SELL")]
    sells = [(tk, st) for tk, st, f in actionable if f == "SELL"]
    buys  = [(tk, st) for tk, st, f in actionable if f == "BUY"]

    # Daily loss cap — reutilizar la misma lógica (sólo suprimir BUYs)
    daily_cap_hit = is_daily_loss_cap_hit(settings)

    def _execute(ticker, strat_name, signal):
        if daily_cap_hit and signal == "BUY":
            logger.info(f"🛑 daily cap hit — BUY {ticker}/{strat_name} suprimida")
            return
        # Precio real de ejecución (quote live)
        live = market_data.get_quote(ticker)
        price = live or last_prices.get(ticker)
        if not price or price <= 0:
            logger.warning(f"[CONFLUENCE] {ticker}/{strat_name} sin precio — skip")
            return
        tfs_used = sorted(tf_map.get((ticker, strat_name), []))
        reason   = (f"multi-tf consensus | {strat_name} | "
                    f"tfs={tfs_used} | "
                    f"mode={'confluence' if cfilter.mode else 'any'}")
        trader.execute({
            "ticker":       ticker,
            "signal":       signal,
            "price":        price,
            "strategy":     f"CONFLUENCE:{strat_name}",
            "_budget":      budget,
            "reason":       reason,                          # persiste entry/exit_reason
            "_macro_feeds": settings.get("_macro_feeds"),
        })
        logger.info(f"[CONFLUENCE] {signal} {ticker} @ {price} ({reason})")

    for ticker, strat in sells:
        _execute(ticker, strat, "SELL")
    for ticker, strat in buys:
        _execute(ticker, strat, "BUY")


def interruptible_sleep(seconds: float, check_interval: float = 3.0):
    """
    Duerme `seconds` segundos pero chequea Firestore cada `check_interval`
    para reaccionar rápido a close_all o bot_active=False desde el dashboard.
    Retorna True si se interrumpió (close_all detectado), False si durmió completo.
    """
    elapsed = 0.0
    while elapsed < seconds:
        chunk = min(check_interval, seconds - elapsed)
        time.sleep(chunk)
        elapsed += chunk

        # Chequeo rápido de flags urgentes
        try:
            db   = firestore.Client(project=GCP_PROJECT)
            snap = db.collection("eolo-config").document("settings").get()
            if snap.exists:
                cfg = snap.to_dict() or {}
                if cfg.get("close_all"):
                    logger.info("⚡ close_all detectado durante sleep — interrumpiendo espera")
                    return True   # salir del sleep inmediatamente
        except Exception:
            pass   # si Firestore falla, seguir durmiendo normal

    return False


# ── Strategy Diagnostics (Plan B — 2026-04-21) ─────────────
#
# Corre los 22 wrappers direccionales sobre un universo de 9 tickers vía
# Schwab MarketData (yfinance quedó bloqueado por Yahoo desde IPs GCP).
# Persiste a Firestore `eolo-strategy-diagnostics/{YYYY-MM-DD}`, overwrite
# cada vez. Sheets-sync lo lee desde ahí.

DIAGNOSTICS_INTERVAL_MIN = 15   # cada N minutos dentro del trading window
DIAGNOSTICS_COLLECTION   = "eolo-strategy-diagnostics"


def _diagnostics_get_df(market_data: MarketData, ticker: str):
    """Adapter Schwab → DataFrame para el módulo común.

    Usa frequency=5m (matching lo que hacía sheets-sync con yfinance) y
    days=2 — el auto-scale de marketdata expande a 3 días cuando hace falta.
    Devuelve None si Schwab no responde.
    """
    try:
        prev_freq = market_data.frequency
        market_data.frequency = 5   # 5 minutos
        df = market_data.get_price_history(ticker, candles=0, days=2)
        market_data.frequency = prev_freq
    except Exception as e:
        logger.warning(f"[DIAG] Schwab get_price_history({ticker}) falló: {e}")
        return None
    if df is None or df.empty:
        return None
    # Asegurar index datetime si viene en columna.
    if "datetime" in df.columns and df.index.name != "datetime":
        try:
            df = df.set_index("datetime").sort_index()
        except Exception:
            pass
    return df


def _run_and_persist_diagnostics(market_data: MarketData) -> None:
    """Computa los 22 wrappers × 9 tickers y persiste a Firestore.

    Seguro contra fallos: cualquier excepción se loguea y se traga — no puede
    romper el ciclo de trading. Si Schwab está caído para todos los tickers,
    el compute devuelve None y no persiste nada.
    """
    try:
        diag = compute_strategy_diagnostics(
            get_df=lambda t: _diagnostics_get_df(market_data, t),
            tickers=DIAGNOSTICS_TICKERS_DEFAULT,
            interval="5m",
            period="2d",
        )
    except Exception as e:
        logger.error(f"[DIAG] compute falló: {e}")
        return
    if diag is None:
        return
    try:
        fs = firestore.Client()
        fs.collection(DIAGNOSTICS_COLLECTION).document(diag["date"]).set(
            diag, merge=False,
        )
        logger.info(
            f"[DIAG] persistido a Firestore: {diag['date']} — "
            f"{len(diag['tickers'])} tickers × {len(diag['strategies'])} wrappers "
            f"(raw_total={sum(s['raw_count'] for s in diag['summary'])}, "
            f"final_total={sum(s['final_count'] for s in diag['summary'])})"
        )
    except Exception as e:
        logger.error(f"[DIAG] persist Firestore falló: {e}")


# ── Main ──────────────────────────────────────────────────

def main():
    logger.info("🚀 EOLO Bot arrancando... (10 estrategias)")
    logger.info(f"   EMA/Gap/RSI_SMA200/HH_LL : {TICKERS_EMA_GAP}")
    logger.info(f"   VWAP/BB/ORB/ST/HA/SQ     : {TICKERS_LEVERAGED}")
    logger.info(f"   Budget   : ${trader.TRADE_BUDGET_USD} por trade")
    logger.info(f"   Modo     : {'PAPER' if trader.PAPER_TRADING else 'LIVE'}")

    # Inicializar / sincronizar config de estrategias en Firestore
    try:
        db  = firestore.Client(project=GCP_PROJECT)
        doc = db.collection("eolo-config").document("strategies")
        snap = doc.get()
        if not snap.exists:
            doc.set(DEFAULT_STRATEGIES)
            logger.info(f"   Firestore config inicializado: {DEFAULT_STRATEGIES}")
        else:
            existing = snap.to_dict()
            # Agregar keys que faltan (nuevas estrategias agregadas al código)
            missing = {k: v for k, v in DEFAULT_STRATEGIES.items() if k not in existing}
            if missing:
                doc.set(missing, merge=True)
                logger.info(f"   Firestore config: nuevas estrategias agregadas: {missing}")
            # Log del estado actual
            active = [k for k, v in existing.items() if v and k != "sma200_filter"]
            logger.info(f"   Estrategias activas en Firestore: {active}")
    except Exception as e:
        logger.warning(f"   No se pudo inicializar Firestore config: {e}")

    # ── Restaurar posiciones desde Firestore ──────────────
    # Previene doble-compra si Cloud Run reinicia el container
    trader.load_positions()

    market_data          = MarketData()
    auto_close_done_date = None   # evita ejecutar el cierre automático más de una vez por día
    prev_timeframes      = []     # para loguear cambios de timeframe
    last_tf_eval: dict   = {}     # {tf_minutos: datetime_ET} — gate per-TF anti re-evaluar
    last_diag_ts: datetime | None = None  # Plan B 2026-04-21 — diagnostic cada 15 min

    # ── MacroFeeds: polling VIX/VIX9D/VIX3M/TICK/TRIN ─────
    # Alimenta las 5 estrategias Nivel 2 (VIX_MEAN_REV, VIX_CORRELATION,
    # VIX_SQUEEZE, TICK_TRIN_FADE, VRP_INTRADAY). Si Schwab no responde,
    # las strategies devuelven HOLD silenciosamente.
    try:
        macro_feeds = start_macro_feeds(market_data, poll_sec=60)
    except Exception as e:
        logger.error(f"⚠️  No se pudo iniciar MacroFeeds: {e} — Nivel 2 quedará en HOLD")
        macro_feeds = None

    while True:
        # ── Leer settings globales al inicio de cada ciclo ─────
        # Incluye el trading_hours schedule. Necesario aun fuera
        # del rango para poder disparar auto-close y mostrar el
        # motivo del pause en los logs.
        try:
            settings = get_global_settings()
        except Exception as e:
            logger.warning(f"Could not read settings: {e}")
            settings = {}
        settings["_macro_feeds"] = macro_feeds   # inyección Nivel 2
        today             = datetime.now(EASTERN).date()
        active_timeframes = settings.get("active_timeframes", [1])

        # ── Cierre automático (independiente del rango) ────────
        # Se chequea siempre para que funcione incluso cuando end
        # y auto_close coinciden (en ese caso is_market_open=False).
        if is_auto_close_time(settings) and auto_close_done_date != today:
            budget = float(settings.get("budget", 100))
            logger.warning("⏰ AUTO-CLOSE — cerrando todas las posiciones")
            try:
                close_all_open_positions(market_data, budget, settings=settings)
            except Exception as e:
                logger.error(f"Auto-close error: {e}")
            auto_close_done_date = today   # no volver a ejecutar hoy

        # ── Close All flag manual (dashboard) ─────────────────
        if settings.get("close_all"):
            try:
                close_all_open_positions(market_data,
                                         float(settings.get("budget", 100)),
                                         settings=settings)
            except Exception as e:
                logger.error(f"Close all error: {e}")
                clear_close_all_flag()

        # ── Bot Active check (pausa manual desde dashboard) ────
        if not settings.get("bot_active", True):
            now_str = datetime.now(EASTERN).strftime("%H:%M ET")
            logger.info(f"[{now_str}] ⏸  Bot PAUSADO desde el dashboard — skipping cycle")
            min_tf = min(active_timeframes) if active_timeframes else 1
            interruptible_sleep(seconds_to_next_candle(min_tf))
            continue

        # ── Chequeo de ventana de trading (schedule-aware) ────
        if not is_market_open(settings):
            now = now_et()
            now_str = now.strftime("%H:%M ET")
            if now.weekday() >= 5:
                logger.info(f"[{now_str}] Fin de semana — mercado cerrado, esperando...")
            else:
                sch = load_schedule(settings, defaults=DEFAULTS_EQUITY)
                if now.time() < sch.start:
                    reason = f"antes del start ({sch.start.strftime('%H:%M')} ET)"
                else:
                    reason = f"después del end ({sch.end.strftime('%H:%M')} ET)"
                logger.info(f"[{now_str}] ⏸  Pause time limit — {reason}")
            interruptible_sleep(60)
            continue

        # Defense-in-depth: gate hardcodeado contra trading_hours_enabled=False.
        # Usa los mismos start/end que el UI pero ignora el flag enabled.
        # Bug original (5-may-26): cuando trading_hours_enabled=False,
        # is_within_trading_window() retornaba True incondicionalmente, dejando
        # operar al bot 24/7 (incluyendo trades overnight con tf=1440).
        sch_guard = load_schedule(settings, defaults=DEFAULTS_EQUITY)
        _now_g    = now_et()
        _t_g      = _now_g.time().replace(second=0, microsecond=0)
        if _now_g.weekday() >= 5 or not (sch_guard.start <= _t_g < sch_guard.end):
            interruptible_sleep(60)
            continue

        # ─── A partir de aquí estamos DENTRO del rango de trading ───
        budget = float(settings.get("budget", 100))

        # ── Log si cambiaron los timeframes activos ────
        if active_timeframes != prev_timeframes:
            tf_labels = {1:"1m",5:"5m",15:"15m",30:"30m",60:"1h",240:"4h",1440:"1d"}
            before = "+".join(tf_labels.get(t,'?') for t in prev_timeframes) or "—"
            after  = "+".join(tf_labels.get(t,'?') for t in active_timeframes)
            conf   = "ON"  if settings.get("confluence_mode") else "OFF"
            min_ag = int(settings.get("confluence_min_agree", 2))
            logger.info(
                f"⏱  Timeframes activos: {before} → {after} | "
                f"confluence={conf} (min_agree={min_ag})"
            )
            prev_timeframes = active_timeframes[:]

        # ── Run un ciclo por cada timeframe activo ─────
        # Dos paths según confluence_mode (leído desde Firestore):
        #   - ON:  multi-TF collect + ConfluenceFilter + execute consolidado
        #   - OFF: un run_cycle por TF (path histórico, retro-compat)
        #
        # Anti re-evaluación (2026-04-21): gateamos cada TF con
        # `tf_bar_has_closed()` contra `last_tf_eval[tf]`. Evita que, con
        # min_tf=1m y TF=240m activos, la barra de 4h se evalúe ~240 veces.
        now_eval = datetime.now(EASTERN)
        confluence_on = bool(settings.get("confluence_mode", False))
        if confluence_on and len(active_timeframes) > 1:
            # Confluencia: solo dispara si AL MENOS un TF activo cerró su bar.
            tfs_closed = [tf for tf in active_timeframes
                          if tf_bar_has_closed(tf, now_eval, last_tf_eval.get(tf))]
            if tfs_closed:
                try:
                    run_multi_tf_confluence_cycle(market_data, settings,
                                                  active_timeframes)
                    for tf in tfs_closed:
                        last_tf_eval[tf] = now_eval
                except Exception as e:
                    logger.error(f"Confluence cycle error: {e}")
        else:
            for tf in active_timeframes:
                if not tf_bar_has_closed(tf, now_eval, last_tf_eval.get(tf)):
                    continue
                market_data.frequency = tf
                try:
                    run_cycle(market_data, settings, timeframe=tf)
                    last_tf_eval[tf] = now_eval
                except Exception as e:
                    logger.error(f"Cycle error (tf={tf}m): {e}")

        # ── Strategy Diagnostics (Plan B — 2026-04-21) ─────────
        # Corre cada DIAGNOSTICS_INTERVAL_MIN minutos durante trading. En el
        # primer ciclo del día (last_diag_ts=None) se dispara también para
        # que sheets-sync tenga data recién el bot arranca, sin esperar 15 min.
        try:
            now_et_local = datetime.now(EASTERN)
            should_run = (
                last_diag_ts is None or
                (now_et_local - last_diag_ts).total_seconds()
                    >= DIAGNOSTICS_INTERVAL_MIN * 60
            )
            if should_run:
                _run_and_persist_diagnostics(market_data)
                last_diag_ts = now_et_local
        except Exception as e:
            logger.warning(f"[DIAG] hook falló: {e}")

        # ── Sleep hasta la próxima vela del TF más corto ──
        # Ejemplo: si activos son [1, 5, 15] → dormir hasta la próxima vela de 1m
        min_tf    = min(active_timeframes) if active_timeframes else 1
        wait      = seconds_to_next_candle(min_tf)
        tf_labels = {1:"1m",5:"5m",15:"15m",30:"30m",60:"1h",240:"4h",1440:"1d"}
        next_time = (datetime.now(EASTERN) + timedelta(seconds=wait)).strftime("%H:%M:%S")
        print(f"  ⏱  Próxima vela ({tf_labels.get(min_tf,f'{min_tf}m')}): {next_time} ET  ({wait:.0f}s)\n")
        interruptible_sleep(wait)


if __name__ == "__main__":
    main()
