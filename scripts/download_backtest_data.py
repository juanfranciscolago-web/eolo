#!/usr/bin/env python3
"""
download_backtest_data.py — Descargar datos para backtesting FASE 2

Descarga OHLCV desde:
- yfinance: acciones US (SPY, QQQ, AAPL, MSFT, TSLA)
- Binance API: crypto (BTCUSDT, ETHUSDT, BNBUSDT)

Período: 2017-01-01 a 2024-12-31
Output: Caché local en /tmp/eolo_backtest_cache/
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# Setup
sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from eolo_common.backtest import BacktestDataLoader


def main():
    """Descargar todos los datos necesarios."""

    print("\n" + "="*80)
    print("📥 DESCARGANDO DATOS PARA BACKTESTING FASE 2")
    print("="*80)

    loader = BacktestDataLoader()

    # Activos a descargar
    equities = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
    crypto = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    start_date = "2017-01-01"
    end_date = "2024-12-31"

    print(f"\n📊 Activos a descargar:")
    print(f"   Acciones: {', '.join(equities)}")
    print(f"   Crypto: {', '.join(crypto)}")
    print(f"   Período: {start_date} → {end_date}")

    # Descargar acciones
    print(f"\n📥 Descargando acciones US (yfinance)...")
    try:
        equity_data = loader.load_equities(
            equities,
            start_date=start_date,
            end_date=end_date,
            use_cache=True
        )

        total_equity_velas = sum(len(df) for df in equity_data.values())
        print(f"✅ {len(equity_data)} acciones descargadas")
        print(f"   Total velas: {total_equity_velas}")

    except Exception as e:
        print(f"⚠️ Error descargando acciones: {e}")
        equity_data = {}

    # Descargar crypto
    print(f"\n📥 Descargando crypto (Binance API)...")
    try:
        crypto_data = loader.load_crypto(
            crypto,
            start_date=start_date,
            end_date=end_date,
            use_cache=True
        )

        total_crypto_velas = sum(len(df) for df in crypto_data.values())
        print(f"✅ {len(crypto_data)} pares crypto descargados")
        print(f"   Total velas: {total_crypto_velas}")

    except Exception as e:
        print(f"⚠️ Error descargando crypto: {e}")
        crypto_data = {}

    # Resumen
    print(f"\n" + "="*80)
    print("📊 RESUMEN DE DESCARGA")
    print("="*80)
    print(f"✅ Acciones: {len(equity_data)}")
    print(f"✅ Crypto: {len(crypto_data)}")
    print(f"✅ Total: {len(equity_data) + len(crypto_data)} activos")

    if equity_data or crypto_data:
        print(f"\n✅ Datos descargados exitosamente")
        print(f"📦 Caché guardado en: /tmp/eolo_backtest_cache/")
        return 0
    else:
        print(f"\n⚠️ Advertencia: No se descargaron datos")
        print(f"💡 Solución: Usar datos dummy o verificar conexión de internet")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
