#!/usr/bin/env python3
"""
download_from_bigquery.py — Descargar datos históricos desde GCP BigQuery

Usa gcloud para autenticar y BigQuery para obtener datos de trading
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
print("☁️  DESCARGANDO DATA DESDE GOOGLE CLOUD BIGQUERY")
print("="*80)

# === Verificar instalación gcloud ===
print(f"\n🔍 Verificando herramientas...")

try:
    from google.cloud import bigquery
    print("  ✅ google-cloud-bigquery instalado")
except ImportError:
    print("  ❌ google-cloud-bigquery no instalado")
    print("     Instalando...")
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "google-cloud-bigquery", "--break-system-packages", "-q"
    ])
    from google.cloud import bigquery
    print("  ✅ Instalado")

# === Intentar autenticar con gcloud ===
print(f"\n🔐 Autenticando con gcloud...")

try:
    from google.auth import default
    from google.auth.transport.requests import Request

    try:
        credentials, project = default()
        print(f"  ✅ Autenticado con credenciales por defecto")
        print(f"     Proyecto: {project}")
    except Exception as e:
        print(f"  ⚠️  Credenciales por defecto fallaron: {e}")
        print(f"     Intentando gcloud CLI...")

        import subprocess
        result = subprocess.run(["gcloud", "config", "get-value", "project"],
                              capture_output=True, text=True)
        if result.returncode == 0:
            project = result.stdout.strip()
            print(f"  ✅ gcloud CLI encontrado")
            print(f"     Proyecto: {project}")
            credentials, _ = default()
        else:
            raise Exception("gcloud no configurado")

except Exception as e:
    print(f"  ❌ No se pudo autenticar: {e}")
    print(f"\n  Para autenticar con gcloud:")
    print(f"     gcloud auth application-default login")
    sys.exit(1)

# === Conectar a BigQuery ===
print(f"\n🔗 Conectando a BigQuery...")

try:
    client = bigquery.Client(project=project)
    print(f"  ✅ Conectado a BigQuery")
except Exception as e:
    print(f"  ❌ Error conectando: {e}")
    sys.exit(1)

# === Buscar datasets con datos de trading ===
print(f"\n🔎 Buscando datasets de trading...")

trading_datasets = []

try:
    datasets = list(client.list_datasets())

    for dataset in datasets:
        dataset_id = dataset.dataset_id

        # Buscar keywords de trading
        if any(keyword in dataset_id.lower() for keyword in
               ["trading", "stock", "market", "price", "ohlc", "backtest", "eolo"]):
            trading_datasets.append(dataset_id)
            print(f"  ✅ Encontrado: {dataset_id}")

    if not trading_datasets:
        print(f"  ⚠️  No se encontraron datasets de trading")
        print(f"\n  Datasets disponibles:")
        for dataset in datasets[:10]:
            print(f"     - {dataset.dataset_id}")

except Exception as e:
    print(f"  ❌ Error listando datasets: {e}")
    sys.exit(1)

if not trading_datasets:
    print(f"\n❌ No hay datasets de trading disponibles en BigQuery")
    print(f"\n📋 Próximos pasos:")
    print(f"   1. Crear tabla en BigQuery con datos históricos")
    print(f"   2. O: Usar opción de descarga manual desde Yahoo")
    sys.exit(1)

# === Buscar tabla con precios históricos ===
print(f"\n📊 Buscando tabla de precios...")

price_tables = {}

for dataset_id in trading_datasets:
    try:
        dataset = client.get_dataset(dataset_id)
        tables = list(client.list_tables(dataset))

        for table in tables:
            table_id = table.table_id

            if any(keyword in table_id.lower() for keyword in
                   ["price", "ohlc", "daily", "stock", "quote"]):
                price_tables[f"{dataset_id}.{table_id}"] = table
                print(f"  ✅ Encontrada tabla: {dataset_id}.{table_id}")

    except Exception as e:
        logger.debug(f"  ⚠️  Error en {dataset_id}: {e}")

if not price_tables:
    print(f"  ⚠️  No se encontraron tablas de precios")
    print(f"\n  Creando tabla de ejemplo en BigQuery...")

    # Crear tabla con esquema común
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("symbol", "STRING"),
        bigquery.SchemaField("open", "FLOAT64"),
        bigquery.SchemaField("high", "FLOAT64"),
        bigquery.SchemaField("low", "FLOAT64"),
        bigquery.SchemaField("close", "FLOAT64"),
        bigquery.SchemaField("volume", "INT64"),
    ]

    table_id = f"{project}.trading.daily_prices"

    try:
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table)
        print(f"  ✅ Tabla creada: {table_id}")
        print(f"\n  Próximo paso: Insertar datos históricos en la tabla")

    except Exception as e:
        if "Already Exists" in str(e):
            print(f"  ℹ️  Tabla ya existe: {table_id}")
            price_tables[table_id] = None
        else:
            print(f"  ❌ Error creando tabla: {e}")
            sys.exit(1)

# === Descargar datos ===
print(f"\n📥 Descargando datos...")

symbols = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
data_dir = Path(__file__).parent.parent / "data"
data_dir.mkdir(exist_ok=True)

results = {}

for table_id, table_obj in price_tables.items():
    print(f"\n  Descargando desde {table_id}...")

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=365)

    for symbol in symbols:
        try:
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
                AND date >= '{start_date}'
                AND date <= '{end_date}'
            ORDER BY date ASC
            """

            print(f"    [Query] {symbol}...", end=" ", flush=True)

            df = client.query(query).to_dataframe()

            if len(df) > 0:
                # Renombrar columnas
                df.columns = df.columns.str.lower()

                # Guardar CSV
                csv_path = data_dir / f"{symbol}.csv"
                df.to_csv(csv_path, index=False)

                print(f"✅ {len(df)} barras")

                results[symbol] = {
                    "source": "bigquery",
                    "table": table_id,
                    "bars": len(df),
                    "date_range": f"{start_date} to {end_date}",
                    "file": str(csv_path)
                }
            else:
                print(f"⚠️  Sin datos")

        except Exception as e:
            print(f"❌ {str(e)[:50]}")

# === Resumen ===
print(f"\n" + "="*80)
print("✅ DESCARGA COMPLETADA")
print("="*80)

if len(results) > 0:
    print(f"\n📊 Datos descargados: {len(results)}/{len(symbols)} activos")

    for symbol, info in results.items():
        print(f"   • {symbol:<6} {info['bars']:>4} barras ({info['date_range']:<30})")
        print(f"           Tabla: {info['table']}")
        print(f"           Archivo: {info['file']}")

    # Guardar log
    log_file = data_dir / "bigquery_download_log.json"
    with open(log_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✅ Listos para backtesting con datos REALES")
    print(f"   Próximo: python scripts/backtest_final.py")

else:
    print(f"\n❌ No se descargaron datos")
    print(f"\n📋 Pasos para agregar datos a BigQuery:")
    print(f"   1. Descargar CSV desde Yahoo Finance")
    print(f"   2. Crear tabla en BigQuery")
    print(f"   3. Insertar datos:")
    print(f"""
    bq load --source_format=CSV \\
        --autodetect \\
        trading.daily_prices \\
        data/SPY.csv
    """)

print()
