# run_pipeline.py
"""Execute the full ETL pipeline with all phases"""

from pathlib import Path
from datetime import datetime
import logging

from etl.extract import run_extraction_pipeline
from etl.transform import TransformPipeline
from etl.load.database import DatabaseManager, DatabaseBackend, DatabaseConfig
from etl.load.loader import DataLoader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_full_pipeline():
    """Execute complete pipeline"""

    # Phase 1: Extract
    logger.info("=== Phase 1: Extraction ===")
    extraction_results = run_extraction_pipeline(
        config_path=Path('config/store_schemas.yaml'),
        mode='batch',
        initial_directory=Path('tests/fixtures'),
        staging_path=Path('data/staging'),
        dlq_path=Path('data/dead_letter_queue')
    )

    # Phase 2: Transform
    logger.info("=== Phase 2: Transformation ===")
    import pandas as pd

    # Load extracted data
    parquet_files = list(Path('data/staging').rglob('*.parquet'))
    if parquet_files:
        df = pd.read_parquet(parquet_files[-1])  # Latest file

        transform = TransformPipeline()
        transformed = transform.transform(df)
        logger.info(f"Transformed {len(transformed['cleaned'])} rows")

    # Phase 3: Load
    logger.info("=== Phase 3: Loading ===")

    # Use DuckDB for local development
    config = DatabaseConfig.from_env(DatabaseBackend.DUCKDB)
    db = DatabaseManager(primary_config=config)
    loader = DataLoader(db)

    # Load stores and sales
    loader.load_stores(transformed['enriched'])
    loader.load_sales(transformed['cleaned'])
    loader.refresh_kpis()

    # Phase 4: Start API
    logger.info("=== Phase 4: API Ready ===")
    logger.info("Run: uvicorn api:app --reload --port 8000")

    return {
        'extraction': len(extraction_results),
        'transformation': len(transformed['cleaned']),
        'database': 'DuckDB (local)'
    }

if __name__ == '__main__':
    results = run_full_pipeline()
    print("\nPipeline Complete!")
    print(f"Files extracted: {results['extraction']}")
    print(f"Rows transformed: {results['transformation']}")
    print(f"Database: {results['database']}")
