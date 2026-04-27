#!/bin/bash
# FASE 7a - Quick Start Script
# Run this to execute FASE 7a backtests with corrected script

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "🚀 FASE 7a - BACKTESTING QUICK START"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Check if data exists
if [ ! -f "data/SPY/1d/SPY_1d.csv" ]; then
    echo "❌ ERROR: Data not found at data/SPY/1d/SPY_1d.csv"
    echo ""
    echo "Make sure you're running from the correct directory:"
    echo "  cd /Users/JUAN/PycharmProjects/eolo"
    echo ""
    echo "Or copy data from:"
    echo "  cp -r /sessions/optimistic-brave-ritchie/mnt/PycharmProjects/eolo/data data/"
    exit 1
fi

echo "✅ Data found"
echo "📊 Configuration:"
echo "   Assets: SPY, QQQ, AAPL, MSFT, TSLA"
echo "   Timeframes: 30m, 1h, 4h"
echo "   Strategies: 12 core strategies"
echo "   Total Backtests: 180"
echo ""

# Get max workers
MAX_WORKERS=${1:-4}
echo "⚙️  Max Workers: $MAX_WORKERS"
echo ""

# Run backtests
echo "🔄 Executing backtests..."
python run_backtests_fase7a.py --max-workers=$MAX_WORKERS

# Check results
echo ""
echo "✅ Execution complete!"
echo ""
echo "📊 Results saved to:"
echo "   - data/fase7a_results/backtest_results_full.json"
echo "   - data/fase7a_results/backtest_winners.json"
echo "   - data/fase7a_results/backtest_summary.json"
echo ""

# Show summary
echo "📈 Summary:"
cat data/fase7a_results/backtest_summary.json | jq '{
  total: .total_backtests,
  winners: .winners,
  percentage: .winner_percentage,
  avg_pf: .average_pf,
  max_pf: .max_pf
}'

echo ""
echo "🏆 Top 5 Performers:"
cat data/fase7a_results/backtest_winners.json | jq -r '.[:5] | .[] | "\(.asset) / \(.timeframe) / \(.strategy) → PF: \(.pf | tostring | split(".")[0:2] | join("."))"'

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ FASE 7a COMPLETE"
echo "════════════════════════════════════════════════════════════════"
echo ""
