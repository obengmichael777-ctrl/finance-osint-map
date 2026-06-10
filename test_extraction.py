# test_extraction.py
"""Test extraction with generated data"""

from etl.extract.schema_registry import SchemaRegistry
from etl.extract.excel_watcher import ExtractionOrchestrator, DeadLetterQueue, ExtractionStateTracker
from pathlib import Path
import logging

# --- Configure logging to write to logs.txt (overwrite each run) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Log to console
        logging.FileHandler('logs.txt', mode='w')  # Log to logs.txt (overwrite)
    ]
)
logger = logging.getLogger(__name__)

# Initialize schema registry
schema_registry = SchemaRegistry(Path('config/store_schemas.yaml'))

# Check loaded stores
print(f"Loaded {len(schema_registry.list_stores())} store schemas")
print("Stores:", schema_registry.list_stores()[:5])

# Initialize orchestrator
dlq = DeadLetterQueue(Path('data/dead_letter_queue'))
tracker = ExtractionStateTracker(db_path=Path('data/state'))
orchestrator = ExtractionOrchestrator(
    schema_registry=schema_registry,
    staging_path=Path('data/staging'),
    dlq=dlq,
    state_tracker=tracker
)

# Process all test files
results = orchestrator.process_directory(Path('tests/fixtures'))

# Print results
print(f"\n{'='*50}")
print(f"EXTRACTION RESULTS")
print(f"{'='*50}")
print(f"Files processed: {len(results)}")
print(f"Successful: {sum(1 for r in results if r.success)}")
print(f"Failed: {sum(1 for r in results if not r.success)}")

for result in results[:5]:
    print(f"\n📄 {Path(result.file_path).name}")
    print(f"   Store: {result.store_id}")
    print(f"   Success: {result.success}")
    print(f"   Sheets: {result.sheets_extracted}")
    print(f"   Processing time: {result.processing_time:.2f}s")

# Check staging
parquet_files = list(Path('data/staging').rglob('*.parquet'))
print(f"\n📦 Staging files: {len(parquet_files)}")
