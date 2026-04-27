#!/usr/bin/env python3
"""
download_real_data.py — Descargar datos reales con múltiples métodos fallback

Intenta métodos en orden:
1. yfinance con diferentes configs
2. Alpha Vantage (free API)
3. Pandas DataReader (Yahoo alternative)
4. Descarga manual HTTP directa
5. Datos sintéticos como fallback
"""

import sys
import os
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("🌐 DESCARGANDO DATOS REALES - MÚLTIPLES MÉTODOS")
print("="*80)

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_dir = Path(__file__).parent.parent / "data"
data_dir.mkdir(exist_ok=True)

results = {}

# ============================================================
# MÉTODO 1: yfinance con timeout y reintentos
# ============================================================

print(f"\n🔄 MÉTODO 1: yfinance con configuraciones...")

def try_yfinance():
    try:
        import yfinance as yf
        print("  [1.1] Intentando yfinance sin proxy...")

        # Intentar sin proxy
        df = yf.download("SPY", period="1y", progress=False)
        if df is not None and len(df) > 0:
            print("  ✅ yfinance SIN PROXY funcionó!")
            return True, "yfinance"

    except Exception as e:
        logger.debug(f"  ❌ yfinance sin proxy: {str(e)[:80]}")

    try:
        print("  [1.2] Intentando yfinance con timeout...")
        import yfinance as yf
        yf.pdr_read.requests_read.get_data_yahoo = None

        df = yf.download("SPY", period="1y", progress=False, timeout=30)
        if df is not None and len(df) > 0:
            print("  ✅ yfinance CON TIMEOUT funcionó!")
            return True, "yfinance"

    except Exception as e:
        logger.debug(f"  ❌ yfinance con timeout: {str(e)[:80]}")

    return False, None

yfinance_works, method = try_yfinance()

if yfinance_works:
    print("  ✅ Descargando con yfinance...")
    import yfinance as yf
    for symbol in symbols:
        try:
            df = yf.download(symbol, period="1y", progress=False)
            if df is not None and len(df) > 0:
                df.columns = df.columns.str.lower()
                csv_path = data_dir / f"{symbol}.csv"
                df.to_csv(csv_path)
                print(f"    ✅ {symbol}: {len(df)} barras → {csv_path}")
                results[symbol] = {"source": "yfinance", "bars": len(df), "file": str(csv_path)}
            else:
                logger.warning(f"    ⚠️ {symbol}: Datos vacíos")
        except Exception as e:
            logger.warning(f"    ❌ {symbol}: {str(e)[:60]}")

    if len(results) > 0:
        print(f"\n✅ ÉXITO: yfinance descargó {len(results)}/{len(symbols)} activos")
        # Salvar y terminar
        with open(data_dir / "download_log.json", 'w') as f:
            json.dump(results, f, indent=2)
        sys.exit(0)

# ============================================================
# MÉTODO 2: Pandas DataReader (Yahoo alternative)
# ============================================================

print(f"\n🔄 MÉTODO 2: Pandas DataReader...")

def try_pandas_datareader():
    try:
        from pandas_datareader import data as pdr
        print("  [2.1] Instalando pandas-datareader...")

        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "pandas-datareader", "--break-system-packages", "-q"
        ])

        print("  [2.2] Descargando con DataReader...")
        from pandas_datareader import data as pdr

        end = datetime.now()
        start = end - timedelta(days=365)

        df = pdr.get_data_yahoo("SPY", start=start, end=end)
        if df is not None and len(df) > 0:
            print("  ✅ Pandas DataReader funcionó!")
            return True

    except Exception as e:
        logger.debug(f"  ❌ DataReader: {str(e)[:80]}")

    return False

if try_pandas_datareader():
    print("  ✅ Descargando con DataReader...")
    from pandas_datareader import data as pdr
    end = datetime.now()
    start = end - timedelta(days=365)

    for symbol in symbols:
        try:
            df = pdr.get_data_yahoo(symbol, start=start, end=end)
            if df is not None and len(df) > 0:
                df.columns = df.columns.str.lower()
                csv_path = data_dir / f"{symbol}.csv"
                df.to_csv(csv_path)
                print(f"    ✅ {symbol}: {len(df)} barras → {csv_path}")
                results[symbol] = {"source": "datareader", "bars": len(df), "file": str(csv_path)}
        except Exception as e:
            logger.warning(f"    ❌ {symbol}: {str(e)[:60]}")

    if len(results) > 0:
        print(f"\n✅ ÉXITO: DataReader descargó {len(results)}/{len(symbols)} activos")
        with open(data_dir / "download_log.json", 'w') as f:
            json.dump(results, f, indent=2)
        sys.exit(0)

# ============================================================
# MÉTODO 3: Alpha Vantage (API libre)
# ============================================================

print(f"\n🔄 MÉTODO 3: Alpha Vantage API...")

# Generar API key temporal (free tier)
ALPHA_VANTAGE_KEY = "demo"  # demo key para testing

def try_alpha_vantage():
    try:
        import requests
        print("  [3.1] Probando Alpha Vantage con demo key...")

        url = f"https://www.alphavantage.co/query"
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": "SPY",
            "apikey": ALPHA_VANTAGE_KEY,
            "outputsize": "full"
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "Time Series (Daily)" in data:
            print("  ✅ Alpha Vantage API funcionó!")
            return True
        elif "Error Message" in data or "Note" in data:
            print(f"  ⚠️  {data.get('Note', data.get('Error Message', 'Unknown error')[:80])}")
            return False

    except Exception as e:
        logger.debug(f"  ❌ Alpha Vantage: {str(e)[:80]}")

    return False

if try_alpha_vantage():
    print("  ✅ Descargando con Alpha Vantage...")
    import requests

    for i, symbol in enumerate(symbols):
        try:
            # Rate limit: 5 calls/min para free tier
            if i > 0:
                time.sleep(13)  # 13 segundos entre calls

            url = f"https://www.alphavantage.co/query"
            params = {
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "apikey": ALPHA_VANTAGE_KEY,
                "outputsize": "full"
            }

            print(f"    [Descargando {symbol}...]", end=" ", flush=True)
            response = requests.get(url, params=params, timeout=15)
            data = response.json()

            if "Time Series (Daily)" in data:
                ts = data["Time Series (Daily)"]

                # Convertir a DataFrame
                records = []
                for date_str, values in ts.items():
                    records.append({
                        "Date": pd.to_datetime(date_str),
                        "Open": float(values["1. open"]),
                        "High": float(values["2. high"]),
                        "Low": float(values["3. low"]),
                        "Close": float(values["4. close"]),
                        "Volume": int(float(values["5. volume"]))
                    })

                df = pd.DataFrame(records).set_index("Date").sort_index()

                if len(df) > 0:
                    csv_path = data_dir / f"{symbol}.csv"
                    df.to_csv(csv_path)
                    print(f"✅ {len(df)} barras")
                    results[symbol] = {"source": "alpha_vantage", "bars": len(df), "file": str(csv_path)}
                else:
                    print(f"⚠️  Vacío")
            else:
                print(f"❌ Error API")

        except Exception as e:
            print(f"❌ {str(e)[:40]}")

    if len(results) > 0:
        print(f"\n✅ ÉXITO: Alpha Vantage descargó {len(results)}/{len(symbols)} activos")
        with open(data_dir / "download_log.json", 'w') as f:
            json.dump(results, f, indent=2)
        sys.exit(0)

# ============================================================
# MÉTODO 4: Descarga manual HTTP (sin librerías)
# ============================================================

print(f"\n🔄 MÉTODO 4: Descarga HTTP directa...")

def try_direct_http():
    try:
        import urllib.request
        import ssl

        print("  [4.1] Creando contexto SSL sin verificación...")

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Intentar descargar CSV de Yahoo directamente
        symbol = "SPY"
        end = datetime.now()
        start = end - timedelta(days=365)

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        url = f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}?period1={start_ts}&period2={end_ts}&interval=1d&events=history&includeAdjustedClose=true"

        print(f"  [4.2] Descargando desde: {url[:60]}...")

        request = urllib.request.Request(url)
        request.add_header('User-Agent', 'Mozilla/5.0')

        with urllib.request.urlopen(request, context=ssl_context, timeout=10) as response:
            data = response.read().decode('utf-8')
            print("  ✅ Descarga HTTP directa funcionó!")
            return True, data

    except Exception as e:
        logger.debug(f"  ❌ HTTP directo: {str(e)[:80]}")

    return False, None

http_works, csv_data = try_direct_http()

if http_works and csv_data:
    print("  ✅ Descargando con HTTP directo...")
    import urllib.request
    import ssl

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    for symbol in symbols:
        try:
            end = datetime.now()
            start = end - timedelta(days=365)

            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())

            url = f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}?period1={start_ts}&period2={end_ts}&interval=1d&events=history&includeAdjustedClose=true"

            request = urllib.request.Request(url)
            request.add_header('User-Agent', 'Mozilla/5.0')

            with urllib.request.urlopen(request, context=ssl_context, timeout=15) as response:
                csv_data = response.read().decode('utf-8')

                # Parsear CSV
                from io import StringIO
                df = pd.read_csv(StringIO(csv_data), index_col='Date', parse_dates=True)
                df.columns = df.columns.str.lower()

                if len(df) > 0:
                    csv_path = data_dir / f"{symbol}.csv"
                    df.to_csv(csv_path)
                    print(f"    ✅ {symbol}: {len(df)} barras")
                    results[symbol] = {"source": "http_direct", "bars": len(df), "file": str(csv_path)}

        except Exception as e:
            logger.warning(f"    ❌ {symbol}: {str(e)[:60]}")

    if len(results) > 0:
        print(f"\n✅ ÉXITO: HTTP descargó {len(results)}/{len(symbols)} activos")
        with open(data_dir / "download_log.json", 'w') as f:
            json.dump(results, f, indent=2)
        sys.exit(0)

# ============================================================
# FALLBACK: Datos Sintéticos
# ============================================================

print(f"\n⚠️  FALLBACK: Generando datos sintéticos realistas...")
print(f"   (Ningún método de descarga funcionó)")

sys.path.insert(0, str(Path(__file__).parent.parent))

from eolo_common.backtest.data_generator import SyntheticOHLCVGenerator

gen = SyntheticOHLCVGenerator(seed=42)

for i, symbol in enumerate(symbols):
    try:
        volatility = 0.15 + (i * 0.02)  # 15%, 17%, 19%, 21%, 23%

        df = gen.generate_timeseries(
            start_price=100,
            days=365,
            volatility=volatility,
            drift=0.0003,
            regime="bull",
            volume_base=1000000
        )

        df.columns = df.columns.str.lower()
        csv_path = data_dir / f"{symbol}_synthetic.csv"
        df.to_csv(csv_path)

        print(f"  ✅ {symbol}: {len(df)} días sintéticos → {csv_path}")
        results[symbol] = {"source": "synthetic", "bars": len(df), "file": str(csv_path)}

    except Exception as e:
        print(f"  ❌ {symbol}: {e}")

# ============================================================
# RESUMEN
# ============================================================

print(f"\n" + "="*80)
print("📊 RESUMEN DESCARGA")
print("="*80)

if len(results) > 0:
    print(f"\n✅ DATOS DISPONIBLES: {len(results)}/{len(symbols)} activos")

    for symbol, info in results.items():
        print(f"   • {symbol:<6} {info['bars']:>4} barras ({info['source']:<15}) → {info['file']}")

    with open(data_dir / "download_log.json", 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✅ Log guardado: {data_dir / 'download_log.json'}")
    print(f"✅ Listos para backtesting con datos {'REALES' if 'synthetic' not in str(results.get(symbols[0], {}).get('source', '')) else 'SINTÉTICOS'}")

else:
    print(f"\n❌ No se pudieron descargar datos")
    print(f"   Próximos pasos:")
    print(f"   1. Verificar conexión a Internet")
    print(f"   2. Comprobar firewall/proxy")
    print(f"   3. Contactar IT para whitelist yahoo.com/alphavantage.co")

print()
