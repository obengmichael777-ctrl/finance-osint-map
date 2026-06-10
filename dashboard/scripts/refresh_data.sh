#!/bin/bash
# Automated daily data refresh
# Schedule: 0 6 * * * /path/to/scripts/refresh_data.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

LOG_FILE="logs/refresh_$(date +%Y%m%d_%H%M).log"
mkdir -p logs

echo "===================================" | tee -a "$LOG_FILE"
echo "Refresh Start: $(date)" | tee -a "$LOG_FILE"
echo "===================================" | tee -a "$LOG_FILE"

source venv/bin/activate

# Step 1: Extract
echo "[1/3] Extracting..." | tee -a "$LOG_FILE"
python -c "
from etl.extract.schema_registry import SchemaRegistry
from etl.extract.excel_watcher import ExtractionOrchestrator, DeadLetterQueue, ExtractionStateTracker
from pathlib import Path
sr = SchemaRegistry(Path('config/store_schemas.yaml'))
orch = ExtractionOrchestrator(sr, Path('data/staging'), DeadLetterQueue(Path('data/dead_letter_queue')), ExtractionStateTracker(db_path=Path('data/state')))
results = orch.process_directory(Path('data/incoming'))
print(f'Extracted {len(results)} files')
" 2>&1 | tee -a "$LOG_FILE"

# Step 2: Transform
echo "[2/3] Transforming..." | tee -a "$LOG_FILE"
python -c "
from etl.transform import TransformPipeline
import pandas as pd
from pathlib import Path
files = sorted(Path('data/staging').rglob('*.parquet'))
if files:
    df = pd.concat([pd.read_parquet(f) for f in files[-20:]], ignore_index=True)
    pipeline = TransformPipeline()
    results = pipeline.transform(df, save_intermediates=True)
    print(f'Transformed {len(results[\"cleaned\"])} rows')
" 2>&1 | tee -a "$LOG_FILE"

# Step 3: Load
echo "[3/3] Loading..." | tee -a "$LOG_FILE"
python -c "
from etl.load.database import DatabaseManager, DatabaseConfig, DatabaseBackend
from etl.load.loader import DataLoader
import pandas as pd
from pathlib import Path
config = DatabaseConfig(backend=DatabaseBackend.DUCKDB, duckdb_path=Path('data/retail.db'))
db = DatabaseManager(primary_config=config, auto_failover=False)
loader = DataLoader(db)
enriched = pd.read_parquet(sorted(Path('data/transformed').glob('enriched_*.parquet'))[-1])
cleaned = pd.read_parquet(sorted(Path('data/transformed').glob('cleaned_*.parquet'))[-1])
loader.load_stores(enriched)
loader.load_sales(cleaned)
loader.refresh_kpis()
print('Database updated')
" 2>&1 | tee -a "$LOG_FILE"

echo "===================================" | tee -a "$LOG_FILE"
echo "Refresh Complete: $(date)" | tee -a "$LOG_FILE"
echo "===================================" | tee -a "$LOG_FILE"
