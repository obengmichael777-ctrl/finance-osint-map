"""
Shared pytest fixtures and configuration for the extraction pipeline tests.
Provides test data, mock objects, and environment setup.
"""
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import tempfile
import shutil
import yaml
import sys

# Add etl module to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'etl'))

from extract.schema_registry import (
    SchemaRegistry, StoreSchema, SheetMapping,
    ColumnMapping, ColumnDataType
)


@pytest.fixture(scope="session")
def test_data_dir():
    """Provide path to test fixtures directory"""
    return Path(__file__).parent / 'fixtures'


@pytest.fixture(scope="session")
def temp_workspace():
    """Create temporary workspace for test outputs"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_sales_dataframe():
    """Create a clean sample sales DataFrame"""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=10, freq='D')

    data = []
    for date in dates:
        for _ in range(5):
            data.append({
                'date': date,
                'product_id': f'SKU-{np.random.randint(1000, 9999)}',
                'qty': np.random.randint(1, 100),
                'revenue': round(np.random.uniform(10, 1000), 2),
                'store_id': 'test_store',
                'country': 'US',
                'region': 'Northeast'
            })

    return pd.DataFrame(data)


@pytest.fixture
def problematic_dataframe():
    """Create DataFrame with intentional data quality issues"""
    return pd.DataFrame({
        'date': ['2024-01-01', 'invalid_date', '2024-01-03', None],
        'product_id': ['SKU-001', 'SKU-002', None, 'SKU-004'],
        'qty': [10, -5, 100, 1000001],  # Negative, too large
        'revenue': [100.0, 200.0, -50.0, None],  # Negative, missing
        'payment_method': ['CASH', 'BITCOIN', 'CREDIT', None]  # Invalid type
    })


@pytest.fixture
def sample_schema_config():
    """Provide minimal schema configuration for testing"""
    return {
        'stores': {
            'test_store': {
                'country': 'US',
                'region': 'Test',
                'lat': 40.0,
                'lon': -74.0,
                'file_pattern': 'test_*.xlsx',
                'sheets': {
                    'sales': {
                        'sheet_name': 'Sales',
                        'skip_rows': 0,
                        'header_row': 0,
                        'columns': {
                            'date': {
                                'source_name': 'Transaction Date',
                                'data_type': 'date',
                                'required': True,
                                'validation_rules': {'not_null': True}
                            },
                            'product_id': {
                                'source_name': 'SKU',
                                'data_type': 'string',
                                'required': True
                            },
                            'qty': {
                                'source_name': 'Qty Sold',
                                'data_type': 'integer',
                                'required': True,
                                'validation_rules': {'min': 0}
                            },
                            'revenue': {
                                'source_name': 'Net Sales',
                                'data_type': 'float',
                                'required': True,
                                'validation_rules': {'min': 0}
                            }
                        }
                    }
                }
            }
        }
    }


@pytest.fixture
def temp_schema_file(temp_workspace, sample_schema_config):
    """Create temporary schema configuration file"""
    config_path = temp_workspace / 'test_schema.yaml'
    with open(config_path, 'w') as f:
        yaml.dump(sample_schema_config, f)
    return config_path


@pytest.fixture
def schema_registry(temp_schema_file):
    """Provide initialized SchemaRegistry"""
    return SchemaRegistry(temp_schema_file)


@pytest.fixture
def sample_excel_file(temp_workspace, sample_sales_dataframe):
    """Create sample Excel file for testing"""
    file_path = temp_workspace / 'test_store_202401.xlsx'

    # Rename columns to match schema
    df = sample_sales_dataframe.rename(columns={
        'date': 'Transaction Date',
        'product_id': 'SKU',
        'qty': 'Qty Sold',
        'revenue': 'Net Sales'
    })

    df.to_excel(file_path, sheet_name='Sales', index=False)
    return file_path
