"""
Tests for Excel Watcher and Extraction Orchestrator
"""
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import time
import shutil
from datetime import datetime

from extract.excel_watcher import (
    ExtractionOrchestrator, DeadLetterQueue,
    ExtractionStateTracker, ExtractionResult
)
from extract.schema_registry import SchemaRegistry


class TestDeadLetterQueue:
    """Test Dead Letter Queue functionality"""

    def test_send_to_dlq(self, temp_workspace):
        """Test sending failed file to DLQ"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')

        # Create a test file
        test_file = temp_workspace / 'failed_file.xlsx'
        test_file.write_text('fake excel content')

        # Send to DLQ
        failure_id = dlq.send_to_dlq(
            test_file,
            ValueError("Test error"),
            store_id='test_store',
            context={'sheet': 'Sales'}
        )

        # Verify DLQ entry exists
        dlq_entries = list((temp_workspace / 'dlq').iterdir())
        assert len(dlq_entries) > 0

        # Verify metadata file
        metadata_files = list((temp_workspace / 'dlq').rglob('error_metadata.json'))
        assert len(metadata_files) > 0

    def test_get_pending_failures(self, temp_workspace):
        """Test retrieving pending failures"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')

        # Create multiple failures
        for i in range(3):
            test_file = temp_workspace / f'fail_{i}.xlsx'
            test_file.touch()
            dlq.send_to_dlq(test_file, ValueError(f"Error {i}"))

        pending = dlq.get_pending_failures(limit=10)
        assert len(pending) == 3

    def test_dlq_statistics(self, temp_workspace):
        """Test DLQ statistics generation"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')

        # Create failures with different error types
        test_file = temp_workspace / 'test.xlsx'
        test_file.touch()

        dlq.send_to_dlq(test_file, ValueError("Bad value"), store_id='store_1')
        dlq.send_to_dlq(test_file, KeyError("Missing key"), store_id='store_1')
        dlq.send_to_dlq(test_file, ValueError("Another error"), store_id='store_2')

        stats = dlq.get_dlq_statistics()

        assert stats['total_entries'] == 3
        assert 'ValueError' in stats['by_error_type']
        assert stats['by_error_type']['ValueError'] == 2


class TestExtractionStateTracker:
    """Test state tracking for deduplication"""

    def test_is_processed_file_based(self, temp_workspace):
        """Test file-based state tracking"""
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')

        # Initially not processed
        assert not tracker.is_processed('hash123')

        # Mark as completed
        test_file = temp_workspace / 'test.xlsx'
        test_file.touch()
        tracker.mark_in_progress('hash123', test_file, 'store_1')
        tracker.mark_completed('hash123', 'ext_001', 3, 0)

        # Should now be processed
        assert tracker.is_processed('hash123')
        assert not tracker.is_processed('hash456')

    def test_get_statistics(self, temp_workspace):
        """Test extraction statistics"""
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')

        test_file = temp_workspace / 'test.xlsx'
        test_file.touch()

        # Add some state entries
        for i in range(5):
            tracker.mark_in_progress(f'hash{i}', test_file, f'store_{i%2}')
            tracker.mark_completed(f'hash{i}', f'ext_{i}', 3, 0)

        # Add a failure
        tracker.mark_in_progress('hash_fail', test_file, 'store_1')
        tracker.mark_failed('hash_fail', 'Test error')

        stats = tracker.get_statistics()
        assert stats['total_processed'] == 5
        assert stats['failed'] == 1


class TestExtractionOrchestrator:
    """Test main orchestration logic"""

    def test_process_valid_file(self, temp_workspace, schema_registry, sample_excel_file):
        """Test processing a valid Excel file"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')
        staging = temp_workspace / 'staging'

        orchestrator = ExtractionOrchestrator(
            schema_registry=schema_registry,
            staging_path=staging,
            dlq=dlq,
            state_tracker=tracker
        )

        result = orchestrator.process_file(sample_excel_file)

        assert result is not None
        assert result.success
        assert 'sales' in result.sheets_extracted
        assert len(result.output_paths) > 0

        # Verify parquet files created
        parquet_files = list(staging.rglob('*.parquet'))
        assert len(parquet_files) > 0

    def test_process_file_no_matching_schema(self, temp_workspace, schema_registry):
        """Test handling file with no matching schema"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')

        orchestrator = ExtractionOrchestrator(
            schema_registry=schema_registry,
            staging_path=temp_workspace / 'staging',
            dlq=dlq,
            state_tracker=tracker
        )

        # Create file that won't match any pattern
        unknown_file = temp_workspace / 'unknown_format_2024.xlsx'
        pd.DataFrame({'A': [1]}).to_excel(unknown_file, index=False)

        result = orchestrator.process_file(unknown_file)

        # Should be None (sent to DLQ, no result returned)
        assert result is None

        # Should be in DLQ
        pending = dlq.get_pending_failures()
        assert len(pending) == 1

    def test_deduplication(self, temp_workspace, schema_registry, sample_excel_file):
        """Test that same file isn't processed twice"""
        dlq = DeadLetterQueue(temp_workspace / 'dlq')
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')

        orchestrator = ExtractionOrchestrator(
            schema_registry=schema_registry,
            staging_path=temp_workspace / 'staging',
            dlq=dlq,
            state_tracker=tracker
        )

        # First processing
        result1 = orchestrator.process_file(sample_excel_file)
        assert result1 is not None

        # Second processing should be skipped
        result2 = orchestrator.process_file(sample_excel_file)
        assert result2 is None  # Already processed

    def test_file_stability_check(self, temp_workspace, schema_registry):
        """Test file stability detection"""
        orchestrator = ExtractionOrchestrator(
            schema_registry=schema_registry,
            staging_path=temp_workspace / 'staging',
            dlq=DeadLetterQueue(temp_workspace / 'dlq'),
            state_tracker=ExtractionStateTracker(db_path=temp_workspace / 'state')
        )

        # Create a file
        test_file = temp_workspace / 'changing_file.xlsx'
        test_file.touch()

        # File should be stable (not being modified)
        assert orchestrator._is_file_stable(test_file, check_interval=0.1)


class TestIntegration:
    """Integration tests for full pipeline"""

    def test_end_to_end_batch_processing(self, temp_workspace, schema_registry):
        """Test full batch processing pipeline"""
        # Create multiple test files
        for i in range(3):
            df = pd.DataFrame({
                'Transaction Date': pd.date_range('2024-01-01', periods=5),
                'SKU': [f'SKU-{j}' for j in range(5)],
                'Qty Sold': np.random.randint(1, 50, 5),
                'Net Sales': np.random.uniform(10, 500, 5).round(2)
            })

            file_path = temp_workspace / f'test_{i+1}_202401.xlsx'
            df.to_excel(file_path, sheet_name='Sales', index=False)

        # Process directory
        dlq = DeadLetterQueue(temp_workspace / 'dlq')
        tracker = ExtractionStateTracker(db_path=temp_workspace / 'state')

        orchestrator = ExtractionOrchestrator(
            schema_registry=schema_registry,
            staging_path=temp_workspace / 'staging',
            dlq=dlq,
            state_tracker=tracker
        )

        results = orchestrator.process_directory(temp_workspace)

        # Should process 3 files
        assert len(results) == 3
        assert all(r.success for r in results)

        # Verify parquet output
        parquet_files = list((temp_workspace / 'staging').rglob('*.parquet'))
        assert len(parquet_files) == 3

        # Verify state tracking
        stats = tracker.get_statistics()
        assert stats['total_processed'] == 3
