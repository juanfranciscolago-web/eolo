"""CLI: python -m backtest.run --start --end --ticker --kb"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "llm_engine_eolo")
from backtest.simulator import run_backtest
from backtest.metrics import compute_metrics_per_rule


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--ticker", default="SPY,QQQ,IWM")
    parser.add_argument("--kb", default="auto")
    parser.add_argument("--data-dir", default="backtest/data")
    parser.add_argument("--out-dir", default="backtest/results")
    args = parser.parse_args()

    kb_path = Path(args.kb)
    if args.kb == "auto":
        import glob
        kbs = sorted(glob.glob("llm_engine_eolo/kb/EOLO_ThetaHarvest_v*.xlsx"))
        kb_path = Path(kbs[-1])

    tickers = args.ticker.split(",")
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running backtest: {args.start} → {args.end}, tickers={tickers}, KB={kb_path.name}")
    result = run_backtest(data_dir, kb_path, args.start, args.end, tickers)
    metrics = compute_metrics_per_rule(result["rule_outcomes"])

    run_id = f"backtest_{kb_path.stem}_{args.start}_{args.end}"
    out_path = out_dir / f"{run_id}.json"
    out_path.write_text(json.dumps({
        "kb": kb_path.name,
        "start": args.start,
        "end": args.end,
        "tickers": tickers,
        "summary": {
            "total_outcomes": result["total_outcomes"],
            "rules_with_outcomes": result["rules_with_outcomes"],
        },
        "metrics_per_rule": metrics,
    }, indent=2))
    print(f"Wrote {out_path}")

    md_path = out_dir / f"{run_id}.md"
    lines = [f"# Backtest {run_id}\n",
             f"- KB: {kb_path.name}",
             f"- Window: {args.start} to {args.end}",
             f"- Tickers: {', '.join(tickers)}",
             f"- Total outcomes: {result['total_outcomes']}",
             f"\n## Metrics per rule\n",
             "| rule_id | n | win_rate | avg_pnl | max_dd | sharpe |",
             "|---|---|---|---|---|---|"]
    for rule_id in sorted(metrics.keys()):
        m = metrics[rule_id]
        lines.append(f"| {rule_id} | {m['n_trades']} | {m['win_rate']:.0%} | ${m['avg_pnl']:.0f} | ${m['max_dd']:.0f} | {m['sharpe']:.2f} |")
    md_path.write_text("\n".join(lines))
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
