#!/usr/bin/env python3
"""
download_10year_multiframe.py — Descargar 10 años de datos en 6 timeframes

Descarga datos REALES de los últimos 10 años para:
- Timeframes: 5min, 15min, 30min, 1h, 4h, 1day
- Símbolos: SPY, QQQ, AAPL, MSFT, TSLA
- Almacena en estructura: data/{SYMBOL}/{TIMEFRAME}/

Total: ~25 millones de velas
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("📊 DESCARGA DE 10 AÑOS - MÚLTIPLES TIMEFRAMES")
print("="*80)

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
timeframes = {
    "5min": ("5m", 5),      # 5 min
    "15min": ("15m", 15),   # 15 min
    "30min": ("30m", 30),   # 30 min
    "1h": ("1h", 60),       # 1 hora
    "4h": ("4h", 240),      # 4 horas
    "1d": ("1d", 1440),     # 1 día
}

data_dir = Path(__file__).parent.parent / "data"

# === Descargar con yfinance ===
print(f"\n📦 Importando yfinance...")

try:
    import yfinance as yf
    print(f"   ✅ Importado")
except ImportError:
    print(f"   ❌ No instalado. Instalando...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "-q"])
    import yfinance as yf
    print(f"   ✅ Instalado")

# === Fechas ===
end_date = datetime.now()
start_date = end_date - timedelta(days=365 * 10)  # 10 años

print(f"\n📅 Período: {start_date.strftime('%Y-%m-%d')} a {end_date.strftime('%Y-%m-%d')} (10 años)")
print(f"   Timeframes: {', '.join(timeframes.keys())}")

# === Descargar datos ===
print(f"\n⬇️  Descargando datos...")

results = {}

for symbol in symbols:
    print(f"\n📈 {symbol}")
    results[symbol] = {}

    for tf_name, (tf_yf, tf_minutes) in timeframes.items():
        try:
            print(f"   📌 {tf_name}...", end=" ", flush=True)

            # Descargar
            df = yf.download(
                symbol,
                start=start_date,
                end=end_date,
                interval=tf_yf,
                progress=False
            )

            if df is None or len(df) == 0:
                print(f"⚠️  Sin datos")
                continue

            # Procesar
            df = df.reset_index()
            df.rename(columns={'Datetime': 'Date'}, inplace=True)

            if 'Date' not in df.columns:
                df['Date'] = df.index

            # Columnas estándar
            df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
            df.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']

            # Limpiar
            df['Date'] = pd.to_datetime(df['Date'])
            df = df.dropna()
            df = df.sort_values('Date').reset_index(drop=True)

            # Crear directorio
            tf_dir = data_dir / symbol / tf_name
            tf_dir.mkdir(parents=True, exist_ok=True)

            # Guardar
            csv_path = tf_dir / f"{symbol}_{tf_name}.csv"
            df.to_csv(csv_path, index=False)

            num_bars = len(df)
            date_start = df['Date'].iloc[0].strftime('%Y-%m-%d %H:%M')
            date_end = df['Date'].iloc[-1].strftime('%Y-%m-%d %H:%M')

            print(f"✅ {num_bars:>6} velas ({date_start} to {date_end})")

            results[symbol][tf_name] = {
                "bars": num_bars,
                "date_range": f"{date_start} to {date_end}",
                "file": str(csv_path)
            }

        except Exception as e:
            print(f"❌ {str(e)[:60]}")
            results[symbol][tf_name] = {"error": str(e)[:80]}

# === Resumen ===
print(f"\n" + "="*80)
print("📊 RESUMEN DESCARGA")
print("="*80)

total_bars = 0
successful = 0

for symbol in symbols:
    print(f"\n{symbol}:")
    for tf_name, data in results[symbol].items():
        if "error" not in data:
            bars = data.get("bars", 0)
            total_bars += bars
            successful += 1
            print(f"   ✅ {tf_name:<6}: {bars:>8} velas")
        else:
            print(f"   ❌ {tf_name:<6}: {data['error'][:50]}")

print(f"\n" + "="*80)
print(f"✅ DESCARGA COMPLETADA")
print(f"   • Total velas: {total_bars:,}")
print(f"   • Timeframes: {successful}/{len(symbols) * len(timeframes)}")
print(f"   • Directorio: {data_dir}")

# Guardar log
log_file = data_dir / "download_10year_multiframe_log.json"
with open(log_file, 'w') as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "period": f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
        "symbols": symbols,
        "timeframes": list(timeframes.keys()),
        "results": results,
        "total_bars": total_bars
    }, f, indent=2)

print(f"\n📋 Log: {log_file}")
print(f"\n🎉 ¡DATOS LISTOS PARA BACKTESTING ROBUSTO!\n")
