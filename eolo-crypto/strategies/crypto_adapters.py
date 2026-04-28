import importlib
import os
import sys
from datetime import datetime, timezone
import pandas as pd
from loguru import logger
import settings
from runtime_config import config as runtime_config

# ── Agregar directorio actual al sys.path para importar módulos locales ──
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT   = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
# Agregar strategies/ al path para que importlib encuentre módulos nativos
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

try:
    from eolo_common.strategies_v3 import (
        STRATEGY_REGISTRY_V3,
        EQUITY_ONLY,
        list_strategies_for_bot,
    )
    _V3_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    logger.warning(f"[STRAT] eolo_common.strategies_v3 no disponible: {_e}")
    STRATEGY_REGISTRY_V3 = {}
    EQUITY_ONLY = set()
    def list_strategies_for_bot(_): return []
    _V3_AVAILABLE = False


# ── Mapping estrategia → módulo de ../Bot/ ───────────────
STRATEGY_MODULES = {
    "rsi_sma200":    "bot_rsi_sma200_strategy",
    "bollinger":     "bot_bollinger_strategy",
    "macd_bb":       "bot_macd_bb_strategy",
    "supertrend":    "bot_supertrend_strategy",
    "vwap_rsi":      "bot_vwap_rsi_strategy",
    "orb":           "bot_orb_strategy",
    "squeeze":       "bot_squeeze_strategy",
    "hh_ll":         "bot_hh_ll_strategy",
    "ha_cloud":      "bot_ha_cloud_strategy",
    "ema_tsi":       "bot_ema_tsi_strategy",
    "vela_pivot":    "bot_vela_pivot_strategy",
    "gap":           "bot_gap_strategy",
    "base":          "bot_strategy",
    # ── Nivel 1 (trading_strategies_v2.md) — 7 que aplican a crypto.
    # Usan detect_signal(df, ticker) → el wrapper las engancha igual
    # que al resto. El gating on/off sigue por runtime_config.
    "rvol_breakout":       "bot_rvol_breakout_strategy",
    "stop_run":            "bot_stop_run_strategy",
    "vwap_zscore":         "bot_vwap_zscore_strategy",
    "volume_reversal_bar": "bot_volume_reversal_bar_strategy",
    "obv_mtf":             "bot_obv_strategy",
    "tsv":                 "bot_tsv_strategy",
    "vw_macd":             "bot_vw_macd_strategy",
    # ── Crypto-native (2026-04-27) ────────────────────────
    # Módulos propios en strategies/ — sys.path incluye _THIS_DIR
    "liquidation_cascade": "bot_liquidation_cascade_crypto",
    "funding_rate_carry":  "bot_funding_rate_carry_crypto",
    "weekend_breakout":    "bot_weekend_breakout_crypto",
    "btc_lead_lag":        "bot_btc_lead_lag_crypto",
    # ── FASE 4/5/7a winners (2026-04-27) ──────────────────
    # FASE 4: Bollinger_RSI_Sensitive (PF 38.52 SPY, 14.78 AAPL, 14.02 QQQ)
    "bollinger_rsi_sensitive": "bot_bollinger_rsi_sensitive_strategy",
    # FASE 5: XOM_30m (PF 1.38, intraday crypto 24/7 → adaptado a crypto)
    "xom_30m":             "bot_xom_30m_strategy",
    # FASE 7a: MACD Confluence (PF 4.58 QQQ / 3.14 SPY) + Momentum Score (PF 4.58 QQQ / 3.14 SPY)
    "macd_confluence_fase7a":   "bot_macd_confluence_fase7a_strategy",
    "momentum_score_fase7a":    "bot_momentum_score_fase7a_strategy",
}


# ── Monkey-patch: "market open" para crypto siempre True ──

def _patch_market_hours_for_crypto():
    """
    Algunas estrategias Eolo v1 chequean is_market_open() o similares.
    En crypto eso siempre es True. Seteamos variables de entorno o
    patcheamos módulos conocidos. Best-effort — si fallase, igual se
    loggea y se sigue.
    """
    # Pattern 1: variable de entorno que usan algunos bots
    import os
    os.environ.setdefault("FORCE_MARKET_OPEN", "1")
    # Pattern 2: monkey-patch si existe un módulo helpers con is_market_open
    try:
        helpers_mod = importlib.import_module("helpers_market")
        if hasattr(helpers_mod, "is_market_open"):
            helpers_mod.is_market_open = lambda *a, **k: True
            logger.debug("[STRAT] helpers_market.is_market_open patched → True")
    except ImportError:
        pass


# ── Crypto-specific feature preprocessing ────────────────

def add_utc_session_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega columnas al DataFrame para que ORB y Gap puedan
    computar su lógica a partir de la "sesión diaria UTC":
      - session_open:  True si es la primera vela del día UTC
      - session_high/low: high/low acumulado de la sesión UTC actual
      - prev_session_close: close de la última vela del día UTC anterior
    """
    if df.empty:
        return df

    df = df.copy()
    df["utc_date"] = df.index.tz_convert("UTC").date if hasattr(df.index, "tz_convert") else df.index.date
    df["session_open"] = df["utc_date"] != df["utc_date"].shift(1)

    # High/low/volume acumulados por día UTC
    df["session_high"]   = df.groupby("utc_date")["high"].cummax()
    df["session_low"]    = df.groupby("utc_date")["low"].cummin()
    df["session_volume"] = df.groupby("utc_date")["volume"].cumsum()

    # Previous session close: close de la última vela del día anterior
    daily_closes = df.groupby("utc_date")["close"].last()
    df["prev_session_close"] = df["utc_date"].map(
        daily_closes.shift(1).to_dict()
    )

    return df


def compute_daily_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP de la sesión UTC actual (reset a las 00:00 UTC)."""
    if df.empty or "utc_date" not in df.columns:
        df = add_utc_session_flags(df)
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = tp * df["volume"]
    vwap = tpv.groupby(df["utc_date"]).cumsum() / df["volume"].groupby(df["utc_date"]).cumsum()
    return vwap


# ── StrategyRunner ────────────────────────────────────────

class StrategyRunner:
    """
    Loader y runner de las 13 estrategias Eolo v1. Intenta
    descubrir la función evaluadora de cada módulo dinámicamente:
        1. evaluate(df, symbol=None) → dict o str
        2. check_signal(df) → dict o str
        3. should_buy/should_sell(df) → bool
    Si ninguna está disponible, la estrategia se desactiva y loggea warning.
    """

    def __init__(self):
        self._handlers: dict[str, callable] = {}
        _patch_market_hours_for_crypto()
        self._load_all()

    def _load_all(self):
        """
        Carga TODAS las estrategias conocidas — NO filtramos por
        STRATEGIES_ENABLED acá. El gating se hace en evaluate_all() contra
        runtime_config para que el toggle del dashboard tenga efecto
        inmediato sin reload. (Las que no tienen signature válida, como
        'base', se skipean una sola vez al arranque.)
        """
        loaded = []
        skipped = []
        for name, module_name in STRATEGY_MODULES.items():
            fn = self._load_strategy(name, module_name)
            if fn is not None:
                self._handlers[name] = fn
                loaded.append(name)
            else:
                skipped.append(name)
        logger.info(
            f"[STRAT] Cargadas {len(loaded)}/{len(STRATEGY_MODULES)} estrategias: {loaded}"
        )
        if skipped:
            logger.warning(f"[STRAT] Skipped (no se encontró signature): {skipped}")

        # ── Suite "EMA 3/8 y MACD" (v3) ──────────────────────
        # Pure-logic shared module → wrapper delgado con tod_filter=False
        # (crypto es 24/7 UTC, no tiene sesión RTH). ORB_V3 se excluye
        # por diseño (requiere RTH equity).
        if _V3_AVAILABLE:
            v3_loaded = []
            for v3_key in list_strategies_for_bot("crypto"):
                # v3 registra en UPPER_SNAKE_CASE; nuestro runtime_config
                # usa lower_snake para el toggle — mapeo 1:1.
                canonical = v3_key.lower()
                fn = self._make_v3_handler(v3_key, canonical)
                self._handlers[canonical] = fn
                v3_loaded.append(canonical)
            logger.info(f"[STRAT] v3 suite cargadas: {v3_loaded}")

    def _make_v3_handler(self, v3_key: str, canonical: str):
        """
        Wrapper para una estrategia de eolo_common.strategies_v3.
        Las v3 son puras: fn(df, cfg=None, tod_filter=None, **params).
        En crypto forzamos tod_filter=False y las features específicas
        (add_utc_session_flags) ya se aplicaron antes en evaluate_all().
        """
        v3_fn = STRATEGY_REGISTRY_V3.get(v3_key)
        if v3_fn is None:
            def _missing(_df, _sym):
                return {"signal": "HOLD", "reason": "v3 missing", "strategy": canonical}
            return _missing

        def wrapper_v3(df, symbol):
            try:
                # Las v3 esperan índice datetime (para session_vwap, etc.).
                # df ya viene indexado por 'datetime' en evaluate_all() —
                # si algún caller pasa sin indexar, lo fix-eamos aquí.
                work = df
                if "datetime" in work.columns:
                    work = work.set_index("datetime")
                out = v3_fn(work, tod_filter=False)
            except Exception as e:
                return {"signal": "HOLD", "reason": f"err:{type(e).__name__}:{e}",
                        "strategy": canonical}
            return self._normalize_output(out, canonical)
        return wrapper_v3

    def _load_strategy(self, name: str, module_name: str):
        """
        Importa el módulo y devuelve una función wrapper con firma uniforme:
           wrapper(df, symbol) → dict{"signal", "reason", "strategy"}

        Las estrategias Eolo v1 exponen detect_signal(df, ticker) → str
        (opcionalmente precedido de calculate_indicators(df) que agrega
        columnas como rsi/sma200/bb_*/etc.). Ese es el camino preferido;
        los demás intentos son best-effort para variantes futuras.
        """
        try:
            mod = importlib.import_module(module_name)
        except ImportError as e:
            logger.warning(f"[STRAT] No pude importar {module_name}: {e}")
            return None

        # Intento 1 (principal): detect_signal(df, ticker) + calculate_indicators
        if hasattr(mod, "detect_signal"):
            detect = mod.detect_signal
            calc   = getattr(mod, "calculate_indicators", None)
            def wrapper_ds(df, symbol):
                try:
                    df_local = calc(df.copy()) if calc is not None else df
                    out = detect(df_local, symbol)
                except Exception as e:
                    return {"signal": "HOLD",
                            "reason": f"err:{type(e).__name__}:{e}",
                            "strategy": name}
                return self._normalize_output(out, name)
            return wrapper_ds

        # Intento 2: evaluate(df, symbol=None)
        if hasattr(mod, "evaluate"):
            fn = mod.evaluate
            def wrapper_ev(df, symbol):
                try:
                    out = fn(df, symbol=symbol) if "symbol" in fn.__code__.co_varnames else fn(df)
                except Exception as e:
                    return {"signal": "HOLD", "reason": f"err:{e}", "strategy": name}
                return self._normalize_output(out, name)
            return wrapper_ev

        # Intento 3: check_signal(df)
        if hasattr(mod, "check_signal"):
            fn = mod.check_signal
            def wrapper_cs(df, symbol):
                try:
                    out = fn(df)
                except Exception as e:
                    return {"signal": "HOLD", "reason": f"err:{e}", "strategy": name}
                return self._normalize_output(out, name)
            return wrapper_cs

        # Intento 4: should_buy / should_sell (boolean)
        has_buy  = hasattr(mod, "should_buy")
        has_sell = hasattr(mod, "should_sell")
        if has_buy or has_sell:
            def wrapper_bs(df, symbol):
                try:
                    if has_buy and mod.should_buy(df):
                        return {"signal": "BUY", "reason": "should_buy=True", "strategy": name}
                    if has_sell and mod.should_sell(df):
                        return {"signal": "SELL", "reason": "should_sell=True", "strategy": name}
                except Exception as e:
                    return {"signal": "HOLD", "reason": f"err:{e}", "strategy": name}
                return {"signal": "HOLD", "reason": "no signal", "strategy": name}
            return wrapper_bs

        return None

    def _normalize_output(self, out, name: str) -> dict:
        """Normaliza distintos formatos de salida a un dict estándar."""
        if isinstance(out, dict):
            signal = str(out.get("signal", out.get("action", "HOLD"))).upper()
            if signal not in ("BUY", "SELL", "HOLD"):
                signal = "HOLD"
            return {
                "signal":   signal,
                "reason":   out.get("reason", ""),
                "strategy": name,
            }
        if isinstance(out, str):
            s = out.upper()
            if s in ("BUY", "SELL", "HOLD"):
                return {"signal": s, "reason": "", "strategy": name}
        if isinstance(out, (tuple, list)) and len(out) >= 1:
            s = str(out[0]).upper()
            reason = str(out[1]) if len(out) > 1 else ""
            return {"signal": s if s in ("BUY","SELL","HOLD") else "HOLD",
                    "reason": reason, "strategy": name}
        return {"signal": "HOLD", "reason": "unknown output", "strategy": name}

    # ── API pública ───────────────────────────────────────

    def enabled_strategies(self) -> list[str]:
        """Loaded ∩ habilitadas en runtime_config (defaults + override Firestore)."""
        return [n for n in self._handlers if runtime_config.is_strategy_enabled(n)]

    def evaluate_all(self, df: pd.DataFrame, symbol: str) -> list[dict]:
        """
        Ejecuta las estrategias cargadas Y habilitadas en runtime (config
        dinámica del dashboard) sobre el DataFrame y retorna lista de
        {signal, reason, strategy} — una entry por strategy activa.
        """
        if df is None or df.empty:
            return []
        # Agregar features crypto (session_open, VWAP daily, etc.)
        df = add_utc_session_flags(df)
        df["vwap_daily"] = compute_daily_vwap(df)

        results = []
        for name, fn in self._handlers.items():
            if not runtime_config.is_strategy_enabled(name):
                continue
            res = fn(df, symbol)
            if res and res["signal"] != "HOLD":
                results.append(res)
        return results

    def aggregate_consensus(self, signals: list[dict]) -> dict:
        """
        Combina N signals individuales en una decisión global.
        Lógica: si hay ≥2 BUY y 0 SELL → BUY. Si hay ≥2 SELL → SELL.
        Si conflicto o insuficiente → HOLD.
        """
        buys  = [s for s in signals if s["signal"] == "BUY"]
        sells = [s for s in signals if s["signal"] == "SELL"]

        if len(buys) >= 2 and len(sells) == 0:
            return {
                "signal": "BUY",
                "confidence": min(1.0, len(buys) / 5.0),
                "contributing": [s["strategy"] for s in buys],
                "reason": " | ".join(f"{s['strategy']}:{s.get('reason','')}" for s in buys),
            }
        if len(sells) >= 2:
            return {
                "signal": "SELL",
                "confidence": min(1.0, len(sells) / 5.0),
                "contributing": [s["strategy"] for s in sells],
                "reason": " | ".join(f"{s['strategy']}:{s.get('reason','')}" for s in sells),
            }
        return {"signal": "HOLD", "confidence": 0.0, "contributing": [], "reason": ""}
