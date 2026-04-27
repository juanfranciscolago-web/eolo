#!/usr/bin/env python3
"""
download_real_data_fixed.py — Descargar datos REALES desde yfinance (sin MultiIndex)

Descarga cada símbolo individualmente para evitar MultiIndex de pandas
"""

import sys
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("🌐 DESCARGANDO DATOS REALES - YFINANCE (CORREGIDO)")
print("="*80)

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_dir = Path(__file__).parent.parent / "data"
data_dir.mkdir(exist_ok=True)

results = {}

# ============================================================
# Descargar con yfinance (uno a uno, sin MultiIndex)
# ============================================================

print(f"\n📦 Importando yfinance...")

try:
    import yfinance as yf
    print(f"   ✅ Importado correctamente")
except ImportError:
    print(f"   ❌ yfinance no instalado. Instalando...")
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "yfinance", "-q"
    ])
    import yfinance as yf
    print(f"   ✅ Instalado y cargado")

print(f"\n🔄 Descargando datos REALES desde Yahoo Finance...")

for symbol in symbols:
    try:
        print(f"\n   📌 {symbol}...", end=" ", flush=True)

        # Descargar INDIVIDUALMENTE (evita MultiIndex)
        df = yf.download(symbol, period="1y", progress=False)

        # Verificar que sea DataFrame válido
        if df is None or len(df) == 0:
            print(f"⚠️  Sin datos")
            continue

        # Si tiene MultiIndex (columnas), flatten
        if isinstance(df.columns, pd.MultiIndex):
            print(f"⚠️  MultiIndex detectado, limpiando...", end=" ")
            # Tomar solo la primera columna de cada level si es MultiIndex
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

        # Columnas estándar: Date, Open, High, Low, Close, Volume
        # yfinance típicamente retorna: Open, High, Low, Close, Volume, Adj Close
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']

        # Verificar que tenga las columnas necesarias
        if not all(col in df.columns for col in required_cols):
            print(f"❌ Columnas faltantes")
            continue

        # Seleccionar solo OHLCV (sin Adj Close)
        df = df[required_cols].copy()

        # Resetear index (Date pasa a columna normal)
        df = df.reset_index()
        df.rename(columns={'Date': 'Date'}, inplace=True)

        # Asegurar que Date es string YYYY-MM-DD
        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')

        # Ordenar por fecha (ASC)
        df = df.sort_values('Date').reset_index(drop=True)

        # Guardar CSV
        csv_path = data_dir / f"{symbol}.csv"
        df.to_csv(csv_path, index=False)

        print(f"✅ {len(df)} barras")

        results[symbol] = {
            "source": "yfinance_real",
            "bars": len(df),
            "date_range": f"{df['Date'].iloc[0]} to {df['Date'].iloc[-1]}",
            "file": str(csv_path)
        }

    except Exception as e:
        print(f"❌ {str(e)[:80]}")

# ============================================================
# RESUMEN
# ============================================================

print(f"\n" + "="*80)
print("📊 RESUMEN DESCARGA")
print("="*80)

if len(results) > 0:
    print(f"\n✅ DATOS REALES DESCARGADOS: {len(results)}/{len(symbols)} activos")

    for symbol, info in results.items():
        print(f"\n   📈 {symbol}")
        print(f"      Barras: {info['bars']}")
        print(f"      Período: {info['date_range']}")
        print(f"      Archivo: {info['file']}")

    # Guardar log
    log_file = data_dir / "download_log.json"
    with open(log_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "source": "yfinance_real",
            "results": results
        }, f, indent=2)

    print(f"\n✅ Log guardado: {log_file}")
    print(f"\n🎉 ¡DATOS REALES LISTOS PARA BACKTESTING!")
    print(f"   Próximo: python3 scripts/backtest_final.py")

else:
    print(f"\n❌ No se descargaron datos")
    print(f"\n📋 Soluciones:")
    print(f"   1. Verificar conexión a internet")
    print(f"   2. Comprobar que yfinance esté actualizado: pip install --upgrade yfinance")
    print(f"   3. Si Yahoo bloquea, usar Alpha Vantage con API key gratuita")

print()
