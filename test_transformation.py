# test_transformation.py
"""Test transformation with extracted data"""

from etl.transform import TransformPipeline
from etl.transform.geo_enricher import GeoEnricher
from etl.transform.cleaner import CurrencyNormalizer, DataCleaner, ReportingCurrency
from etl.transform.aggregator import Aggregator
import pandas as pd
from pathlib import Path

# Load extracted data
parquet_files = sorted(Path('data/staging').rglob('*.parquet'))
print(f"Found {len(parquet_files)} parquet files")

# Combine all extracted files
dfs = []
for f in parquet_files:
    df = pd.read_parquet(f)
    dfs.append(df)
    print(f"  {f.name}: {len(df)} rows")

combined = pd.concat(dfs, ignore_index=True)
print(f"\nCombined: {len(combined)} rows")

# Run transformation
transform = TransformPipeline()
results = transform.transform(combined, save_intermediates=True)

# Print transformation results
for key, value in results.items():
    if isinstance(value, pd.DataFrame):
        print(f"\n📊 {key}: {len(value)} rows, {len(value.columns)} columns")
        print(f"   Columns: {list(value.columns)[:10]}...")
    else:
        print(f"\n📊 {key}: {value}")

# Check outputs
transformed_files = list(Path('data/transformed').glob('*.parquet'))
print(f"\n📦 Transformed files: {len(transformed_files)}")
