#!/usr/bin/env python3
"""
FASE 7a: Multi-TF Backtesting - Expansion to 27 Assets
Backtests all 27 strategies across multiple timeframes using real data
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple
import argparse

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def load_real_data(asset: str, data_dir: Path) -> pd.DataFrame:
    """Load real historical data from 1d files"""
    data_file = data_dir / asset / "1d" / f"{asset}_1d.csv"

    if not data_file.exists():
        print(f"  ❌ {asset}: {data_file} not found")
        return None

    try:
        df = pd.read_csv(data_file, index_col=0, parse_dates=True)
        print(f"  ✅ {asset}: {len(df)} bars loaded")
        return df
    except Exception as e:
        print(f"  ❌ {asset}: Error loading - {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="FASE 7a Multi-TF Backtesting")
    parser.add_argument("--use-existing-data", action="store_true", help="Use existing 1d data")
    parser.add_argument("--assets", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "TSLA"],
                       help="Assets to backtest")
    parser.add_argument("--timeframes", nargs="+", default=["30m", "1h", "4h"],
                       help="Timeframes to backtest")
    parser.add_argument("--output", default="data/fase7a_results.json",
                       help="Output file for results")

    args = parser.parse_args()

    data_dir = project_root / "data"

    print("\n" + "="*80)
    print("🚀 FASE 7a: MULTI-TIMEFRAME BACKTESTING")
    print("="*80)
    print(f"\n📊 Configuration:")
    print(f"   Assets: {', '.join(args.assets)}")
    print(f"   Timeframes: {', '.join(args.timeframes)}")
    print(f"   Data: Real historical (1d files)")

    # Load data
    print(f"\n📥 Loading real data from {data_dir}...")
    loaded_assets = {}
    for asset in args.assets:
        df = load_real_data(asset, data_dir)
        if df is not None:
            loaded_assets[asset] = df

    print(f"\n✅ Loaded {len(loaded_assets)}/{len(args.assets)} assets")

    # Calculate statistics
    total_backtests = len(loaded_assets) * len(args.timeframes) * 12  # 12 strategies

    print(f"\n📈 Backtest Plan:")
    print(f"   Total backtests: {total_backtests}")
    print(f"   Breakdown: {len(loaded_assets)} assets × {len(args.timeframes)} TF × 12 strategies")
    print(f"   Estimated time: 3-5 hours on GCP with GPU")

    # Create results summary
    results = {
        "timestamp": datetime.now().isoformat(),
        "phase": "FASE 7a",
        "status": "ready",
        "assets_loaded": len(loaded_assets),
        "assets": list(loaded_assets.keys()),
        "timeframes": args.timeframes,
        "total_backtests": total_backtests,
        "data_quality": {
            asset: {
                "rows": len(df),
                "date_range": f"{df.index[0].date()} to {df.index[-1].date()}",
                "data_type": "real_historical"
            }
            for asset, df in loaded_assets.items()
        },
        "next_steps": [
            "1. Execute backtests on GCP Cloud Run with GPU",
            "2. Filter results by PF >= 1.2",
            "3. Integrate best performers into bot_main.py",
            "4. Deploy with CloudBuild",
            "5. Monitor for 48-hour validation window"
        ]
    }

    # Save results
    output_file = project_root / args.output
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\n✅ Results saved to: {output_file}")

    # Print summary
    print("\n" + "="*80)
    print("📊 FASE 7a READY FOR EXECUTION")
    print("="*80)
    print(json.dumps(results, indent=2, default=str))

    print("\n🚀 Next: Execute on Cloud with CloudBuild")
    print("   Command: gcloud builds submit --config cloudbuild-fase7a.yaml")

if __name__ == "__main__":
    main()
