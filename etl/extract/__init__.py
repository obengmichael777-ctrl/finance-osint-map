"""
Extraction Module
Orchestrates the Excel file extraction pipeline with
schema-driven processing and comprehensive error handling.
"""

import argparse
from pathlib import Path
from typing import List
import yaml
import logging

from .schema_registry import SchemaRegistry
from .excel_watcher import (
    ExtractionOrchestrator,
    DeadLetterQueue,
    ExtractionStateTracker
)

logger = logging.getLogger(__name__)


def run_extraction_pipeline(
    config_path: Path,
    watch_paths: List[Path] = None,
    staging_path: Path = None,
    dlq_path: Path = None,
    mode: str = "watch",
    initial_directory: Path = None
):
    """
    Initialize and run the extraction pipeline.
    
    Args:
        config_path: Path to YAML schema configuration
        watch_paths: Directories to watch for new files
        staging_path: Output directory for extracted Parquet files
        dlq_path: Dead letter queue directory
        mode: 'watch' for continuous monitoring, 'batch' for one-time processing
        initial_directory: Directory to process initially (batch mode or backfill)
    """
    # Set default paths
    if staging_path is None:
        staging_path = Path("data/staging")
    if dlq_path is None:
        dlq_path = Path("data/dead_letter_queue")
    if watch_paths is None and mode == "watch":
        watch_paths = [Path("data/incoming")]
    
    # Initialize components
    schema_registry = SchemaRegistry(config_path)
    
    # Validate configuration
    validation_issues = schema_registry.validate_config()
    if validation_issues:
        logger.warning("Schema configuration validation issues:")
        for issue in validation_issues:
            logger.warning(f"  - {issue}")
    
    dlq = DeadLetterQueue(dlq_path)
    state_tracker = ExtractionStateTracker(db_path=Path("data/state"))
    
    orchestrator = ExtractionOrchestrator(
        schema_registry=schema_registry,
        staging_path=staging_path,
        dlq=dlq,
        state_tracker=state_tracker,
        max_workers=4
    )
    
    if mode == "batch":
        # Process existing files
        if initial_directory:
            results = orchestrator.process_directory(initial_directory)
            logger.info(f"Batch processing complete. Processed {len(results)} files.")
            
            # Summary statistics
            successful = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)
            logger.info(f"Results: {successful} successful, {failed} failed")
            
            return results
    
    elif mode == "watch":
        # Start file watchers
        logger.info(f"Starting file watchers on: {watch_paths}")
        orchestrator.start_watching(watch_paths)
    
    elif mode == "reprocess-dlq":
        # Reprocess dead letter queue
        failures = dlq.get_pending_failures(limit=100)
        logger.info(f"Reprocessing {len(failures)} DLQ entries")
        
        for failure in failures:
            original_path = Path(failure['original_path'])
            if original_path.exists():
                orchestrator.process_file(original_path)
                dlq.mark_reprocessed(failure['failure_id'], True)
            else:
                logger.warning(f"Original file not found: {original_path}")
                dlq.mark_reprocessed(failure['failure_id'], False)


def main():
    """Command-line entry point"""
    parser = argparse.ArgumentParser(
        description="Excel File Extraction Pipeline"
    )
    
    parser.add_argument(
        '--config',
        type=Path,
        default=Path('config/store_schemas.yaml'),
        help='Path to schema configuration YAML'
    )
    
    parser.add_argument(
        '--mode',
        choices=['watch', 'batch', 'reprocess-dlq'],
        default='watch',
        help='Pipeline mode'
    )
    
    parser.add_argument(
        '--watch-paths',
        nargs='+',
        type=Path,
        help='Directories to watch (for watch mode)'
    )
    
    parser.add_argument(
        '--initial-dir',
        type=Path,
        help='Directory to process initially (for batch mode)'
    )
    
    parser.add_argument(
        '--staging-path',
        type=Path,
        default=Path('data/staging'),
        help='Output directory for Parquet files'
    )
    
    parser.add_argument(
        '--dlq-path',
        type=Path,
        default=Path('data/dead_letter_queue'),
        help='Dead letter queue directory'
    )
    
    args = parser.parse_args()
    
    run_extraction_pipeline(
        config_path=args.config,
        watch_paths=args.watch_paths,
        staging_path=args.staging_path,
        dlq_path=args.dlq_path,
        mode=args.mode,
        initial_directory=args.initial_dir
    )


if __name__ == "__main__":
    main()