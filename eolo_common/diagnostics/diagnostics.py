# ============================================================
#  eolo_common/diagnostics — strategy diagnostics compartido
#
#  Corre las 22 variantes direccionales del registry
#  STRATEGY_REGISTRY_V3_DIRECTIONAL sobre un universo de tickers,
#  en modo RAW (sin filtros) y FINAL (con regime + EMA trend + ToD).
#
#  El cómputo es data-source agnostic: acepta un callable
#    get_df(ticker: str) -> pd.DataFrame | None
#  que devuelve un DataFrame con columnas open/high/low/close/volume
#  (o None si no hay datos para ese ticker).
#
#  Usos:
#    - Bot v1 (Schwab MarketData vía marketdata.get_price_history)
#    - Sheets-sync (yfinance, quedó roto por rate-limit de GCP pero
#      la API sigue compatible si se habilita en el futuro)
#    - diag_all_strategies_yf.py (script local de diagnóstico)
#
#  Output (idéntico al que computaba sheets-sync originalmente):
#    {
#      "date":         "YYYY-MM-DD",
#      "generated_at": ISO8601,
#      "interval":     "5m",           (informativo — el caller lo decide)
#      "period":       "2d",           (informativo)
#      "tickers":      [t1, t2, ...],  (solo los que sí trajeron data)
#      "strategies":   [NAME_LONG, NAME_SHORT, ...],
#      "matrix_raw":   {ticker: {strat: signal}},
#      "matrix_final": {ticker: {strat: signal}},
#      "summary":      [{strategy, base_strategy, direction,
#                        raw_count, raw_tickers, final_count,
#                        final_tickers, blocked_count, blocked_tickers}],
#    }
# ============================================================
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Universo canónico — mismos 9 tickers que usaban sheets-sync + el script
# local. Exportado para que los callers no tengan que hardcodearlo.
DIAGNOSTICS_TICKERS_DEFAULT = [
    "AAPL", "NVDA", "NVDL", "SOXL", "SPY",
    "TSLA", "TQQQ", "TSLL", "QQQ",
]


def compute_strategy_diagnostics(
    get_df: Callable[[str], Any],
    tickers: list[str] | None = None,
    interval: str = "5m",
    period: str = "2d",
) -> dict | None:
    """Corre el diagnostic sobre los 22 wrappers direccionales.

    Args:
      get_df: callable que recibe un ticker y devuelve un DataFrame con
              columnas open/high/low/close/volume (o None si falla).
              Ejemplo bot v1: lambda t: market_data.get_price_history(t, days=2)
      tickers: universo. Default: DIAGNOSTICS_TICKERS_DEFAULT.
      interval, period: solo metadata informativa — la resolución real la
                        decide el caller al construir get_df.

    Returns:
      dict con matrix_raw, matrix_final, summary. None si no se pudo
      construir el registry o no hubo data para ningún ticker.
    """
    try:
        from eolo_common.strategies_v3 import (  # noqa: E402
            STRATEGY_REGISTRY_V3_DIRECTIONAL, StrategyConfig,
        )
    except ImportError as e:
        logger.warning(f"[DIAG] imports faltantes ({e}) — skip diagnostics")
        return None

    if tickers is None:
        tickers = list(DIAGNOSTICS_TICKERS_DEFAULT)

    no_filters = StrategyConfig(
        use_regime_filter=False,
        use_ema_trend_filter=False,
        use_tod_filter=False,
    )

    strategy_names = list(STRATEGY_REGISTRY_V3_DIRECTIONAL.keys())
    REGISTRY = STRATEGY_REGISTRY_V3_DIRECTIONAL

    # ── Pull data ───────────────────────────────────────────────
    data: dict[str, Any] = {}
    failed: list[tuple[str, str]] = []
    for t in tickers:
        try:
            df = get_df(t)
        except Exception as e:
            failed.append((t, str(e) or e.__class__.__name__))
            continue
        if df is None:
            failed.append((t, "None"))
            continue
        try:
            if hasattr(df, "empty") and df.empty:
                failed.append((t, "empty"))
                continue
        except Exception:
            pass
        data[t] = df

    if failed:
        for tk, err in failed:
            logger.warning(f"[DIAG] get_df({tk}) falló: {err}")

    if not data:
        logger.warning("[DIAG] ningún ticker devolvió data — abort")
        return None

    logger.info(
        f"[DIAG] data OK para {len(data)}/{len(tickers)} tickers: "
        f"{', '.join(data.keys())}"
    )

    # ── Eval cada wrapper sobre cada DF, en modo raw y final ───
    def _safe_call(fn, df, cfg):
        try:
            res = fn(df, cfg=cfg) if cfg is not None else fn(df)
            return str(res.get("signal", "ERR"))
        except Exception:
            return "ERR"

    matrix_raw:   dict[str, dict[str, str]] = {}
    matrix_final: dict[str, dict[str, str]] = {}
    for ticker, df in data.items():
        matrix_raw[ticker] = {
            n: _safe_call(REGISTRY[n], df, no_filters)
            for n in strategy_names
        }
        matrix_final[ticker] = {
            n: _safe_call(REGISTRY[n], df, None)
            for n in strategy_names
        }

    # ── Summary por wrapper direccional ─────────────────────────
    summary = []
    for name in strategy_names:
        direction = (
            "long"  if name.endswith("_LONG")  else
            "short" if name.endswith("_SHORT") else
            "both"
        )
        base_strategy = (
            name.rsplit("_", 1)[0] if direction in ("long", "short") else name
        )
        expected = (
            "BUY"  if direction == "long"  else
            "SELL" if direction == "short" else
            None
        )

        def _hits(matrix):
            if expected is None:
                return [t for t in matrix if matrix[t][name] in ("BUY", "SELL")]
            return [t for t in matrix if matrix[t][name] == expected]

        raw_hits   = _hits(matrix_raw)
        final_hits = _hits(matrix_final)
        blocked    = [t for t in raw_hits if t not in final_hits]
        summary.append({
            "strategy":        name,
            "base_strategy":   base_strategy,
            "direction":       direction,
            "raw_count":       len(raw_hits),
            "raw_tickers":     raw_hits,
            "final_count":     len(final_hits),
            "final_tickers":   final_hits,
            "blocked_count":   len(blocked),
            "blocked_tickers": blocked,
        })

    return {
        "date":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interval":     interval,
        "period":       period,
        "tickers":      list(data.keys()),
        "strategies":   strategy_names,
        "matrix_raw":   matrix_raw,
        "matrix_final": matrix_final,
        "summary":      summary,
    }
