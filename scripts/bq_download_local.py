#!/usr/bin/env python3
"""
bq_download_local.py — EJECUTAR EN TU MÁQUINA LOCAL

Descarga datos históricos desde BigQuery usando tus credenciales gcloud
Requiere:
  - gcloud CLI instalado y autenticado
  - google-cloud-bigquery

Instalación:
  pip install google-cloud-bigquery
  gcloud auth application-default login

Ejecución:
  python bq_download_local.py --project TU-PROYECTO
"""

import sys
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import logging
import argparse

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("☁️  BIGQUERY DATA DOWNLOADER (Ejecutar en máquina local con gcloud)")
print("="*80)

# === Argumentos ===
parser = argparse.ArgumentParser(description='Descargar datos de trading desde BigQuery')
parser.add_argument('--project', required=False, help='GCP Project ID')
parser.add_argument('--dataset', default='trading', help='Dataset name')
parser.add_argument('--table', default='daily_prices', help='Table name')
parser.add_argument('--symbols', default='SPY,QQQ,AAPL,MSFT,TSLA', help='Symbols separated by comma')
parser.add_argument('--days', type=int, default=365, help='Days of history to download')
parser.add_argument('--output', default='./data', help='Output directory for CSV files')

args = parser.parse_args()

print(f"\n📋 Configuración:")
print(f"   Project: {args.project or 'default (from gcloud)'}")
print(f"   Dataset: {args.dataset}")
print(f"   Table: {args.table}")
print(f"   Símbolos: {args.symbols}")
print(f"   Período: {args.days} días")
print(f"   Output: {args.output}")

# === Importar BigQuery ===
print(f"\n📦 Importando google-cloud-bigquery...")

try:
    from google.cloud import bigquery
    print(f"   ✅ Instalado")
except ImportError:
    print(f"   ❌ No instalado. Ejecuta:")
    print(f"      pip install google-cloud-bigquery")
    sys.exit(1)

# === Autenticar ===
print(f"\n🔐 Autenticando con gcloud...")

try:
    from google.auth import default
    credentials, project_id = default()

    if not args.project:
        args.project = project_id

    print(f"   ✅ Autenticado")
    print(f"   Proyecto: {args.project}")

except Exception as e:
    print(f"   ❌ Error: {e}")
    print(f"\n   Próximos pasos:")
    print(f"   1. Instala gcloud CLI: https://cloud.google.com/sdk/docs/install")
    print(f"   2. Autentica: gcloud auth application-default login")
    print(f"   3. Ejecuta este script nuevamente")
    sys.exit(1)

# === Conectar a BigQuery ===
print(f"\n🔗 Conectando a BigQuery...")

try:
    client = bigquery.Client(project=args.project)
    print(f"   ✅ Conectado")
except Exception as e:
    print(f"   ❌ Error: {e}")
    sys.exit(1)

# === Crear directorio output ===
output_path = Path(args.output)
output_path.mkdir(parents=True, exist_ok=True)
print(f"\n📁 Output directory: {output_path.absolute()}")

# === Descargar datos ===
print(f"\n📥 Descargando datos...")

symbols = [s.strip() for s in args.symbols.split(',')]
results = {}

end_date = datetime.now().date()
start_date = end_date - timedelta(days=args.days)

table_id = f"{args.project}.{args.dataset}.{args.table}"

for symbol in symbols:
    try:
        print(f"\n   📌 {symbol}...", end=" ", flush=True)

        query = f"""
        SELECT
            date,
            open,
            high,
            low,
            close,
            volume
        FROM `{table_id}`
        WHERE
            symbol = '{symbol}'
            AND date >= DATE('{start_date}')
            AND date <= DATE('{end_date}')
        ORDER BY date ASC
        """

        df = client.query(query, location="US").to_dataframe()

        if len(df) > 0:
            # Guardar CSV
            csv_path = output_path / f"{symbol}.csv"
            df.to_csv(csv_path, index=False)

            print(f"✅ {len(df)} barras")

            results[symbol] = {
                "symbol": symbol,
                "bars": len(df),
                "date_range": f"{start_date} to {end_date}",
                "file": str(csv_path),
                "status": "✅"
            }
        else:
            print(f"⚠️  Sin datos")
            results[symbol] = {
                "symbol": symbol,
                "status": "⚠️ Sin datos en BigQuery"
            }

    except Exception as e:
        print(f"❌ {str(e)[:60]}")
        results[symbol] = {
            "symbol": symbol,
            "status": f"❌ {str(e)[:40]}"
        }

# === Resumen ===
print(f"\n" + "="*80)
print("📊 RESUMEN DESCARGA")
print("="*80)

successful = sum(1 for r in results.values() if r.get('status', '').startswith('✅'))
print(f"\n✅ Descargados: {successful}/{len(symbols)} activos")

for symbol, info in results.items():
    status = info.get('status', '?')
    if status.startswith('✅'):
        print(f"\n   📈 {symbol}")
        print(f"      Barras: {info.get('bars', 0)}")
        print(f"      Rango: {info.get('date_range', '?')}")
        print(f"      Archivo: {info.get('file', '?')}")
    else:
        print(f"\n   ⚠️  {symbol}: {status}")

# === Instrucciones siguientes ===
if successful > 0:
    print(f"\n" + "="*80)
    print("🎉 ¡DATOS LISTOS!")
    print("="*80)
    print(f"\n✅ Archivos descargados en: {output_path}")
    print(f"\n📊 Próximos pasos:")
    print(f"   1. Copia los CSV a: eolo/data/")
    print(f"   2. Ejecuta: python scripts/backtest_final.py")
    print(f"   3. Tendrás resultados con DATOS REALES\n")

    # Guardar log
    log_file = output_path / "bigquery_download_log.json"
    with open(log_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "project": args.project,
            "dataset": args.dataset,
            "table": args.table,
            "results": results
        }, f, indent=2)

    print(f"   Log guardado: {log_file}\n")

else:
    print(f"\n❌ No se descargaron datos")
    print(f"\n💡 Posibles causas:")
    print(f"   1. Tabla no existe: {table_id}")
    print(f"   2. Tabla vacía o sin datos para esos símbolos")
    print(f"   3. Símbolos no coinciden con BigQuery")
    print(f"\n📋 Pasos para crear tabla manualmente:")
    print(f"""
   # 1. Descargar CSVs desde Yahoo Finance (SPY, QQQ, AAPL, MSFT, TSLA)

   # 2. Crear dataset (si no existe)
   bq mk --dataset --location=US {args.project}:{args.dataset}

   # 3. Crear tabla e insertar datos
   bq load --source_format=CSV \\
       --autodetect \\
       {args.project}:{args.dataset}.{args.table} \\
       SPY.csv

   # Repite para cada símbolo
   """)

print()
