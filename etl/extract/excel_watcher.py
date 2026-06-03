"""
Excel File Watcher and Extraction Orchestrator
Monitors file systems for new/modified Excel files and orchestrates
the extraction pipeline with comprehensive error handling and DLQ.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import json
import shutil
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
from concurrent.futures import ThreadPoolExecutor, as_completed
import redis  # For state tracking (optional, can use file-based)
from sqlalchemy import create_engine, Table, Column, String, DateTime, JSON, MetaData
from sqlalchemy.dialects.postgresql import UUID
import uuid

from etl.extract.schema_registry import SchemaRegistry, StoreSchema

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Container for extraction results"""
    store_id: str
    file_path: str
    success: bool
    sheets_extracted: List[str]
    output_paths: Dict[str, str]  # sheet_type -> parquet_path
    errors: List[Dict[str, Any]]
    processing_time: float
    file_hash: str
    extraction_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class DeadLetterQueue:
    """
    Dead Letter Queue for failed extractions.
    Stores failed files and error context for later analysis/reprocessing.
    """

    def __init__(self, dlq_path: Path, db_connection_string: Optional[str] = None):
        self.dlq_path = dlq_path
        self.dlq_path.mkdir(parents=True, exist_ok=True)
        self.db_engine = None

        if db_connection_string:
            self.db_engine = create_engine(db_connection_string)
            self._init_db_tables()

    def _init_db_tables(self):
        """Initialize database tables for DLQ tracking"""
        metadata = MetaData()

        self.dlq_table = Table(
            'dead_letter_queue',
            metadata,
            Column('id', UUID, primary_key=True),
            Column('file_path', String, nullable=False),
            Column('store_id', String),
            Column('error_type', String),
            Column('error_message', String),
            Column('error_context', JSON),
            Column('failed_at', DateTime, default=datetime.utcnow),
            Column('reprocessed', String, default='PENDING'),
            Column('reprocessed_at', DateTime),
            Column('retry_count', String, default='0')
        )

        metadata.create_all(self.db_engine)

    def send_to_dlq(
        self,
        file_path: Path,
        error: Exception,
        store_id: Optional[str] = None,
        context: Optional[Dict] = None
    ):
        """
        Send failed file to DLQ with error context.
        Moves file to DLQ directory and records error metadata.
        """
        # Generate unique identifier for this failure
        failure_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

        # Create DLQ entry directory
        dlq_entry_path = self.dlq_path / f"{failure_id}_{timestamp}"
        dlq_entry_path.mkdir(parents=True, exist_ok=True)

        # Copy failed file to DLQ
        dlq_file_path = dlq_entry_path / file_path.name
        shutil.copy2(file_path, dlq_file_path)

        # Create error metadata
        error_metadata = {
            'failure_id': failure_id,
            'original_path': str(file_path.absolute()),
            'store_id': store_id,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': getattr(error, '__traceback__', None),
            'context': context or {},
            'timestamp': timestamp,
            'file_size': file_path.stat().st_size if file_path.exists() else None,
            'file_modified': datetime.fromtimestamp(
                file_path.stat().st_mtime
            ).isoformat() if file_path.exists() else None
        }

        # Write metadata to JSON file
        metadata_path = dlq_entry_path / 'error_metadata.json'
        with open(metadata_path, 'w') as f:
            json.dump(error_metadata, f, indent=2, default=str)

        # Optionally store in database for querying
        if self.db_engine:
            with self.db_engine.connect() as conn:
                conn.execute(
                    self.dlq_table.insert().values(
                        id=failure_id,
                        file_path=str(file_path.absolute()),
                        store_id=store_id,
                        error_type=type(error).__name__,
                        error_message=str(error),
                        error_context=json.dumps(context or {}),
                        failed_at=datetime.utcnow()
                    )
                )

        logger.error(
            f"File sent to DLQ: {file_path} -> {dlq_entry_path}. "
            f"Error: {str(error)}"
        )

        return failure_id

    def get_pending_failures(self, limit: int = 100) -> List[Dict]:
        """Retrieve pending failures for reprocessing"""
        if self.db_engine:
            with self.db_engine.connect() as conn:
                query = self.dlq_table.select().where(
                    self.dlq_table.c.reprocessed == 'PENDING'
                ).limit(limit)
                return [dict(row) for row in conn.execute(query)]

        # File-based fallback
        failures = []
        for entry_dir in self.dlq_path.iterdir():
            if entry_dir.is_dir():
                metadata_file = entry_dir / 'error_metadata.json'
                if metadata_file.exists():
                    with open(metadata_file) as f:
                        metadata = json.load(f)
                    failures.append(metadata)

        return failures[:limit]

    def mark_reprocessed(self, failure_id: str, success: bool):
        """Mark a DLQ entry as reprocessed"""
        if self.db_engine:
            with self.db_engine.connect() as conn:
                conn.execute(
                    self.dlq_table.update().where(
                        self.dlq_table.c.id == failure_id
                    ).values(
                        reprocessed='SUCCESS' if success else 'FAILED',
                        reprocessed_at=datetime.utcnow()
                    )
                )

    def get_dlq_statistics(self) -> Dict[str, Any]:
        """Get statistics about DLQ entries"""
        stats = {
            'total_entries': 0,
            'by_error_type': {},
            'by_store': {},
            'pending': 0,
            'resolved': 0
        }

        for entry_dir in self.dlq_path.iterdir():
            if entry_dir.is_dir():
                metadata_file = entry_dir / 'error_metadata.json'
                if metadata_file.exists():
                    with open(metadata_file) as f:
                        metadata = json.load(f)

                    stats['total_entries'] += 1

                    error_type = metadata.get('error_type', 'Unknown')
                    stats['by_error_type'][error_type] = \
                        stats['by_error_type'].get(error_type, 0) + 1

                    store_id = metadata.get('store_id', 'Unknown')
                    stats['by_store'][store_id] = \
                        stats['by_store'].get(store_id, 0) + 1

        return stats


class ExcelFileHandler(FileSystemEventHandler):
    """
    Watchdog event handler for Excel files.
    Triggers extraction pipeline on new/modified files.
    """

    def __init__(self, orchestrator: 'ExtractionOrchestrator'):
        self.orchestrator = orchestrator
        self.supported_extensions = {'.xlsx', '.xls', '.xlsm', '.xlsb'}

    def on_created(self, event: FileCreatedEvent):
        """Handle file creation events"""
        if not event.is_directory:
            file_path = Path(event.src_path)
            if file_path.suffix.lower() in self.supported_extensions:
                logger.info(f"New Excel file detected: {file_path}")
                self.orchestrator.process_file(file_path)

    def on_modified(self, event: FileModifiedEvent):
        """Handle file modification events"""
        if not event.is_directory:
            file_path = Path(event.src_path)
            if file_path.suffix.lower() in self.supported_extensions:
                logger.info(f"Modified Excel file detected: {file_path}")
                self.orchestrator.process_file(file_path)


class ExtractionOrchestrator:
    """
    Main orchestrator for the extraction pipeline.
    Coordinates file watching, schema matching, extraction, and output.
    """

    def __init__(
        self,
        schema_registry: SchemaRegistry,
        staging_path: Path,
        dlq: DeadLetterQueue,
        state_tracker: Optional['ExtractionStateTracker'] = None,
        max_workers: int = 4,
        file_age_threshold_seconds: int = 30
    ):
        self.schema_registry = schema_registry
        self.staging_path = staging_path
        self.staging_path.mkdir(parents=True, exist_ok=True)
        self.dlq = dlq
        self.state_tracker = state_tracker or ExtractionStateTracker()
        self.max_workers = max_workers
        self.file_age_threshold = file_age_threshold_seconds
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def process_file(self, file_path: Path) -> Optional[ExtractionResult]:
        """
        Process a single Excel file through the extraction pipeline.
        Handles schema matching, extraction, and error management.
        """
        start_time = datetime.utcnow()

        # Check if file is still being written
        if not self._is_file_stable(file_path):
            logger.info(f"File {file_path} is still being written, deferring...")
            # Schedule retry after delay
            import threading
            timer = threading.Timer(
                30.0,
                self.process_file,
                args=[file_path]
            )
            timer.start()
            return None

        # Calculate file hash for deduplication
        file_hash = self._calculate_file_hash(file_path)

        # Check if already processed
        if self.state_tracker.is_processed(file_hash):
            logger.info(f"File {file_path} already processed (hash: {file_hash})")
            return None

        # Find matching schema
        schema = self.schema_registry.find_matching_schema(file_path)
        if not schema:
            error = ValueError(f"No matching schema found for file: {file_path}")
            self.dlq.send_to_dlq(
                file_path,
                error,
                context={'reason': 'no_matching_schema'}
            )
            return None

        logger.info(f"Processing {file_path} with schema for store: {schema.store_id}")

        # Mark as in-progress
        self.state_tracker.mark_in_progress(file_hash, file_path, schema.store_id)

        try:
            # Extract data using schema
            results, errors = schema.extract_all_sheets(file_path)

            # Write extracted data to staging
            output_paths = {}
            for sheet_type, df in results.items():
                parquet_path = self._write_to_staging(
                    df, schema.store_id, file_path, sheet_type
                )
                output_paths[sheet_type] = str(parquet_path)

            # Calculate processing time
            processing_time = (datetime.utcnow() - start_time).total_seconds()

            # Create result
            extraction_result = ExtractionResult(
                store_id=schema.store_id,
                file_path=str(file_path),
                success=len(errors) == 0,
                sheets_extracted=list(results.keys()),
                output_paths=output_paths,
                errors=errors,
                processing_time=processing_time,
                file_hash=file_hash
            )

            # Mark as completed
            self.state_tracker.mark_completed(
                file_hash,
                extraction_result.extraction_id,
                len(results),
                len(errors)
            )

            logger.info(
                f"Successfully extracted {len(results)} sheets from {file_path} "
                f"in {processing_time:.2f}s. Errors: {len(errors)}"
            )

            # Send partial errors to DLQ if any
            for error in errors:
                self.dlq.send_to_dlq(
                    file_path,
                    Exception(error['error']),
                    store_id=schema.store_id,
                    context=error
                )

            return extraction_result

        except Exception as e:
            # Complete failure - send to DLQ
            processing_time = (datetime.utcnow() - start_time).total_seconds()

            self.dlq.send_to_dlq(
                file_path,
                e,
                store_id=schema.store_id,
                context={
                    'processing_time': processing_time,
                    'file_size': file_path.stat().st_size
                }
            )

            self.state_tracker.mark_failed(file_hash, str(e))

            logger.error(
                f"Failed to process {file_path}: {str(e)}",
                exc_info=True
            )

            return ExtractionResult(
                store_id=schema.store_id if schema else None,
                file_path=str(file_path),
                success=False,
                sheets_extracted=[],
                output_paths={},
                errors=[{'error': str(e), 'type': type(e).__name__}],
                processing_time=processing_time,
                file_hash=file_hash
            )

    def process_directory(
        self,
        directory: Path,
        recursive: bool = True,
        pattern: str = "*.xlsx"
    ) -> List[ExtractionResult]:
        """
        Process all Excel files in a directory.
        Useful for initial backfill or batch processing.
        """
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        # Collect all matching files
        if recursive:
            files = list(directory.rglob(pattern))
        else:
            files = list(directory.glob(pattern))

        logger.info(f"Found {len(files)} Excel files to process in {directory}")

        # Process files in parallel
        results = []
        futures = []

        for file_path in files:
            if file_path.suffix.lower() in {'.xlsx', '.xls', '.xlsm'}:
                future = self.executor.submit(self.process_file, file_path)
                futures.append(future)

        # Collect results
        for future in as_completed(futures):
            try:
                result = future.result(timeout=300)  # 5-minute timeout
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Batch processing error: {str(e)}", exc_info=True)

        return results

    def start_watching(self, watch_paths: List[Path]):
        """
        Start file system watchers on specified paths.
        Runs continuously until interrupted.
        """
        observer = Observer()

        for watch_path in watch_paths:
            watch_path = Path(watch_path)
            if not watch_path.exists():
                watch_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created watch directory: {watch_path}")

            event_handler = ExcelFileHandler(self)
            observer.schedule(event_handler, str(watch_path), recursive=True)
            logger.info(f"Started watching: {watch_path}")

        observer.start()
        logger.info("File watcher started. Press Ctrl+C to stop.")

        try:
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            logger.info("File watcher stopped.")

        observer.join()

    def _is_file_stable(self, file_path: Path, check_interval: float = 1.0) -> bool:
        """
        Check if file is stable (not being actively written).
        Returns True if file size/modification time hasn't changed.
        """
        try:
            initial_stat = file_path.stat()
            initial_size = initial_stat.st_size
            initial_mtime = initial_stat.st_mtime

            import time
            time.sleep(check_interval)

            current_stat = file_path.stat()
            return (
                current_stat.st_size == initial_size and
                current_stat.st_mtime == initial_mtime
            )
        except FileNotFoundError:
            return False

    def _calculate_file_hash(self, file_path: Path, algorithm: str = 'sha256') -> str:
        """Calculate hash of file for deduplication"""
        hash_func = hashlib.new(algorithm)

        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hash_func.update(chunk)

        return hash_func.hexdigest()

    def _write_to_staging(
        self,
        df: pd.DataFrame,
        store_id: str,
        source_file: Path,
        sheet_type: str
    ) -> Path:
        """
        Write extracted DataFrame to staging area as Parquet.
        Organizes by date/store/sheet_type for efficient querying.
        """
        extraction_date = datetime.utcnow()
        date_path = extraction_date.strftime('%Y/%m/%d')

        # Create directory structure: staging/store_id/YYYY/MM/DD/
        output_dir = (
            self.staging_path / store_id / date_path / sheet_type
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename with timestamp and hash
        file_hash = hashlib.md5(str(source_file).encode()).hexdigest()[:8]
        timestamp = extraction_date.strftime('%Y%m%d_%H%M%S_%f')
        output_file = output_dir / f"{sheet_type}_{timestamp}_{file_hash}.parquet"

        # Write to Parquet with compression
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(
            table,
            output_file,
            compression='snappy',
            row_group_size=100000,
            write_statistics=True
        )

        # Write metadata file
        metadata = {
            'source_file': str(source_file.absolute()),
            'store_id': store_id,
            'sheet_type': sheet_type,
            'extraction_timestamp': extraction_date.isoformat(),
            'row_count': len(df),
            'column_count': len(df.columns),
            'columns': list(df.columns),
            'file_size_bytes': output_file.stat().st_size
        }

        metadata_path = output_file.with_suffix('.metadata.json')
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

        logger.info(
            f"Written {len(df)} rows to {output_file} "
            f"({output_file.stat().st_size / 1024:.1f} KB)"
        )

        return output_file


class ExtractionStateTracker:
    """
    Tracks extraction state to prevent duplicate processing.
    Can use Redis, database, or file-based storage.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None, db_path: Optional[Path] = None):
        self.redis_client = redis_client
        self.db_path = db_path

        if db_path:
            self._init_file_store()

    def _init_file_store(self):
        """Initialize file-based state store"""
        self.state_file = self.db_path / 'extraction_state.jsonl'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.state_file.exists():
            self.state_file.touch()

    def is_processed(self, file_hash: str) -> bool:
        """Check if file has already been processed"""
        if self.redis_client:
            return bool(self.redis_client.get(f"processed:{file_hash}"))

        # File-based check
        if self.db_path:
            with open(self.state_file, 'r') as f:
                for line in f:
                    if line.strip():
                        state = json.loads(line)
                        if state.get('file_hash') == file_hash and \
                           state.get('status') == 'COMPLETED':
                            return True

        return False

    def mark_in_progress(self, file_hash: str, file_path: Path, store_id: str):
        """Mark extraction as in-progress"""
        state = {
            'file_hash': file_hash,
            'file_path': str(file_path),
            'store_id': store_id,
            'status': 'IN_PROGRESS',
            'started_at': datetime.utcnow().isoformat()
        }

        if self.redis_client:
            self.redis_client.set(
                f"in_progress:{file_hash}",
                json.dumps(state),
                ex=3600  # Expire after 1 hour
            )

        if self.db_path:
            with open(self.state_file, 'a') as f:
                f.write(json.dumps(state) + '\n')

    def mark_completed(self, file_hash: str, extraction_id: str, sheets: int, errors: int):
        """Mark extraction as completed"""
        state = {
            'file_hash': file_hash,
            'extraction_id': extraction_id,
            'status': 'COMPLETED',
            'sheets_extracted': sheets,
            'errors': errors,
            'completed_at': datetime.utcnow().isoformat()
        }

        if self.redis_client:
            self.redis_client.set(
                f"processed:{file_hash}",
                json.dumps(state),
                ex=86400 * 30  # Keep for 30 days
            )
            self.redis_client.delete(f"in_progress:{file_hash}")

        if self.db_path:
            with open(self.state_file, 'a') as f:
                f.write(json.dumps(state) + '\n')

    def mark_failed(self, file_hash: str, error: str):
        """Mark extraction as failed"""
        state = {
            'file_hash': file_hash,
            'status': 'FAILED',
            'error': error,
            'failed_at': datetime.utcnow().isoformat()
        }

        if self.redis_client:
            self.redis_client.set(
                f"failed:{file_hash}",
                json.dumps(state),
                ex=86400 * 7  # Keep for 7 days
            )
            self.redis_client.delete(f"in_progress:{file_hash}")

        if self.db_path:
            with open(self.state_file, 'a') as f:
                f.write(json.dumps(state) + '\n')

    def get_statistics(self) -> Dict[str, Any]:
        """Get extraction statistics"""
        stats = {
            'total_processed': 0,
            'in_progress': 0,
            'failed': 0,
            'by_store': {}
        }

        if self.redis_client:
            for key in self.redis_client.scan_iter("processed:*"):
                state = json.loads(self.redis_client.get(key))
                stats['total_processed'] += 1
                store = state.get('store_id', 'unknown')
                stats['by_store'][store] = stats['by_store'].get(store, 0) + 1

            stats['in_progress'] = len(list(
                self.redis_client.scan_iter("in_progress:*")
            ))
            stats['failed'] = len(list(
                self.redis_client.scan_iter("failed:*")
            ))

        elif self.db_path and self.state_file.exists():
            with open(self.state_file, 'r') as f:
                for line in f:
                    if line.strip():
                        state = json.loads(line)
                        if state['status'] == 'COMPLETED':
                            stats['total_processed'] += 1
                            store = state.get('store_id', 'unknown')
                            stats['by_store'][store] = \
                                stats['by_store'].get(store, 0) + 1
                        elif state['status'] == 'IN_PROGRESS':
                            stats['in_progress'] += 1
                        elif state['status'] == 'FAILED':
                            stats['failed'] += 1

        return stats
