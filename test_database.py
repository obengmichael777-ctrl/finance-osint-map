# test_database.py
"""Test database loading with transformed data"""

from etl.load.database import DatabaseManager, DatabaseConfig, DatabaseBackend
from etl.load.loader import DataLoader
import pandas as pd
from pathlib import Path

# Use DuckDB for local testing
config = DatabaseConfig(
    backend=DatabaseBackend.DUCKDB,
    duckdb_path=Path('data/retail.db')
)

# Initialize database
db = DatabaseManager(primary_config=config, auto_failover=False)
loader = DataLoader(db)

# Load latest transformed data
enriched_files = sorted(Path('data/transformed').glob('enriched_*.parquet'))
cleaned_files = sorted(Path('data/transformed').glob('cleaned_*.parquet'))

if enriched_files and cleaned_files:
    enriched_df = pd.read_parquet(enriched_files[-1])
    cleaned_df = pd.read_parquet(cleaned_files[-1])

    print(f"Loading {len(enriched_df)} stores...")
    count = loader.load_stores(enriched_df)
    print(f"✅ Loaded {count} stores")

    print(f"Loading {len(cleaned_df)} transactions...")
    count = loader.load_sales(cleaned_df)
    print(f"✅ Loaded {count} transactions")

    # Refresh KPIs
    print("Refreshing KPIs...")
    loader.refresh_kpis()
    print("✅ KPIs refreshed")

    # Query some data
    stores = db.active_connection.query("SELECT store_id, country, economic_region FROM stores LIMIT 5")
    print(f"\n📊 Sample stores:\n{stores}")

    kpis = db.active_connection.query("SELECT * FROM store_kpis LIMIT 5")
    print(f"\n📊 Sample KPIs:\n{kpis}")
