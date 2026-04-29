#!/usr/bin/env python3
# ============================================================
#  Theta Harvest Backtester — Entry Point
#
#  Uso:
#    python -m theta_backtest.run_backtest
#    python -m theta_backtest.run_backtest --refresh   # fuerza re-descarga
#    python -m theta_backtest.run_backtest --verbose
#    python -m theta_backtest.run_backtest --ticker SPY
# ============================================================
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Asegurar que el directorio eolo-options esté en el path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Silenciar logs de loguru del live strategy (macro_news_filter imprime por cada día)
try:
    from loguru import logger as _loguru_logger
    import sys as _sys
    _loguru_logger.remove()
    _loguru_logger.add(_sys.stderr, level="WARNING")
except Exception:
    pass

from theta_backtest.config    import TICKERS, BACKTEST_START, BACKTEST_END
from theta_backtest.data_loader import load_market_data
from theta_backtest.simulator   import run_all_tickers, run_simulation
from theta_backtest.analyzer    import compute_metrics, print_summary
from theta_backtest.report      import generate_html_report


def main():
    parser = argparse.ArgumentParser(description="Theta Harvest Backtester")
    parser.add_argument("--refresh", action="store_true",
                        help="Forzar re-descarga del cache")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar log detallado por día")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Simular solo un ticker (SPY o TQQQ)")
    parser.add_argument("--no-report", action="store_true",
                        help="No generar reporte HTML")
    parser.add_argument("--debug", type=int, default=0, metavar="N",
                        help="Imprimir diagnóstico de los primeros N días con try_enter")
    args = parser.parse_args()

    tickers = [args.ticker] if args.ticker else TICKERS

    print(f"\n{'='*60}")
    print(f"  Theta Harvest Backtester")
    print(f"  Período: {BACKTEST_START} → {BACKTEST_END}")
    print(f"  Tickers: {tickers}")
    print(f"{'='*60}\n")

    # ── 1. Cargar datos ───────────────────────────────────
    md = load_market_data(force_refresh=args.refresh)

    # ── 2. Simular ────────────────────────────────────────
    all_results = {}
    all_metrics = {}

    for ticker in tickers:
        print(f"\n{'─'*60}")
        print(f"  Simulando {ticker} ...")
        positions, daily = run_simulation(md, ticker, verbose=args.verbose, debug_days=args.debug)
        metrics = compute_metrics(positions, daily, ticker=ticker)
        all_results[ticker] = (positions, daily)
        all_metrics[ticker] = metrics
        print_summary(metrics)

    # ── 3. Guardar JSON de resultados ─────────────────────
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)

    # Serializar trades a lista de dicts
    for ticker, (positions, daily) in all_results.items():
        trades_data = []
        for p in positions:
            trades_data.append({
                "ticker":           p.ticker,
                "spread_type":      p.spread_type,
                "dte_at_entry":     p.dte_at_entry,
                "tranche_id":       p.tranche_id,
                "tranche_target":   p.tranche_target,
                "entry_date":       p.entry_date.isoformat(),
                "expiry_date":      p.expiry_date.isoformat(),
                "exit_date":        p.exit_date.isoformat() if p.exit_date else None,
                "short_strike":     round(p.short_strike, 2),
                "long_strike":      round(p.long_strike, 2),
                "entry_credit":     round(p.entry_credit, 4),
                "entry_spot":       round(p.entry_spot, 2),
                "exit_price":       round(p.exit_price, 4) if p.exit_price is not None else None,
                "exit_reason":      p.exit_reason,
                "pnl":              round(p.pnl, 2) if p.pnl is not None else None,
                "risk_zone":        p.risk_zone,
                "payoff_score":     p.payoff_score,
            })

        out_file = out_dir / f"trades_{ticker}.json"
        with open(out_file, "w") as f:
            json.dump(trades_data, f, indent=2)
        print(f"\n  ✓ Trades guardados: {out_file}")

    # Guardar métricas completas
    metrics_file = out_dir / "metrics.json"
    with open(metrics_file, "w") as f:
        # Convertir claves int a str para JSON
        serializable = {}
        for t, m in all_metrics.items():
            m_copy = dict(m)
            m_copy["by_dte"] = {str(k): v for k, v in m_copy.get("by_dte", {}).items()}
            serializable[t] = m_copy
        json.dump(serializable, f, indent=2)
    print(f"  ✓ Métricas guardadas: {metrics_file}")

    # ── 4. Generar reporte HTML ───────────────────────────
    if not args.no_report:
        report_path = generate_html_report(all_metrics, all_results)
        print(f"\n  ✓ Reporte HTML generado: {report_path}")
        print(f"\n  Abrí con: open {report_path}")

    print(f"\n{'='*60}")
    print("  Backtest completado.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
