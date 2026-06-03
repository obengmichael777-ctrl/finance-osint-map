# tests/test_schema_registry.py
"""
Tests for Schema Registry component
Validates schema loading, column mapping, and validation logic
"""
import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from etl.extract.schema_registry import (
    SchemaRegistry, ColumnMapping, ColumnDataType,
    SheetMapping, StoreSchema
)


class TestColumnMapping:
    """Test individual column mapping and validation"""

    def test_string_type_conversion(self):
        """Test string column type handling"""
        mapping = ColumnMapping(
            source_name='test_col',
            target_name='test_col',
            data_type=ColumnDataType.STRING,
            required=True
        )

        series = pd.Series([1, 2, 3, 'test', None])
        result = mapping.validate_and_transform(series)

        assert result.dtype == object
        assert result.iloc[0] == '1'
        assert pd.isna(result.iloc[4])

    def test_integer_with_commas(self):
        """Test integer conversion with formatted numbers"""
        mapping = ColumnMapping(
            source_name='qty',
            target_name='qty',
            data_type=ColumnDataType.INTEGER,
            required=True
        )

        series = pd.Series(['1,234', '5,678', '999'])
        result = mapping.validate_and_transform(series)

        assert result.iloc[0] == 1234
        assert result.iloc[1] == 5678
        assert result.iloc[2] == 999
        assert result.dtype == 'Int64'

    def test_float_validation_range(self):
        """Test min/max validation on float columns"""
        mapping = ColumnMapping(
            source_name='revenue',
            target_name='revenue',
            data_type=ColumnDataType.FLOAT,
            required=True,
            validation_rules={'min': 0, 'max': 1000000}
        )

        # Valid values
        valid_series = pd.Series([100.0, 200.0, 0.0])
        result = mapping.validate_and_transform(valid_series)
        assert len(result) == 3

        # Invalid - negative
        invalid_series = pd.Series([-50.0])
        with pytest.raises(ValueError, match="below minimum"):
            mapping.validate_and_transform(invalid_series)

    def test_date_parsing_formats(self):
        """Test date column with specified format"""
        mapping = ColumnMapping(
            source_name='date',
            target_name='date',
            data_type=ColumnDataType.DATE,
            required=True,
            date_format='%d/%m/%Y'
        )

        series = pd.Series(['15/01/2024', '31/12/2024'])
        result = mapping.validate_and_transform(series)

        assert result.iloc[0].day == 15
        assert result.iloc[0].month == 1
        assert pd.api.types.is_datetime64_dtype(result)

    def test_categorical_allowed_values(self):
        """Test categorical column with allowed values"""
        mapping = ColumnMapping(
            source_name='payment',
            target_name='payment',
            data_type=ColumnDataType.CATEGORY,
            required=True,
            validation_rules={
                'allowed_values': ['CASH', 'CREDIT', 'DEBIT']
            }
        )

        # Valid
        valid = pd.Series(['CASH', 'CREDIT', 'DEBIT'])
        result = mapping.validate_and_transform(valid)
        assert len(result) == 3

        # Invalid
        invalid = pd.Series(['BITCOIN'])
        with pytest.raises(ValueError, match="invalid values"):
            mapping.validate_and_transform(invalid)

    def test_optional_with_default(self):
        """Test optional column receives default value"""
        mapping = ColumnMapping(
            source_name='optional_col',
            target_name='optional_col',
            data_type=ColumnDataType.STRING,
            required=False,
            default_value='UNKNOWN'
        )

        series = pd.Series(['value1', None, 'value3'])
        result = mapping.validate_and_transform(series)

        assert result.iloc[1] == 'UNKNOWN'
        assert not pd.isna(result.iloc[1])

    def test_not_null_validation(self):
        """Test not_null validation rule"""
        mapping = ColumnMapping(
            source_name='critical_col',
            target_name='critical_col',
            data_type=ColumnDataType.STRING,
            required=True,
            validation_rules={'not_null': True}
        )

        # Valid series
        valid = pd.Series(['A', 'B', 'C'])
        result = mapping.validate_and_transform(valid)
        assert len(result) == 3

        # Series with nulls
        with_nulls = pd.Series(['A', None, 'C'])
        with pytest.raises(ValueError, match="null values"):
            mapping.validate_and_transform(with_nulls)


class TestSchemaRegistry:
    """Test SchemaRegistry loading and lookup"""

    def test_load_yaml_config(self, schema_registry):
        """Test loading schema from YAML file"""
        assert schema_registry is not None
        assert 'test_store' in schema_registry.list_stores()

    def test_get_schema(self, schema_registry):
        """Test retrieving specific store schema"""
        schema = schema_registry.get_schema('test_store')

        assert schema is not None
        assert schema.store_id == 'test_store'
        assert schema.country == 'US'
        assert 'sales' in schema.sheets

        # Verify column mappings
        sales_sheet = schema.sheets['sales']
        assert 'date' in sales_sheet.columns
        assert 'product_id' in sales_sheet.columns
        assert sales_sheet.columns['qty'].validation_rules == {'min': 0}

    def test_find_matching_schema_by_pattern(self, schema_registry, temp_workspace):
        """Test pattern-based schema matching"""
        # Create a file matching the pattern
        test_file = temp_workspace / 'test_20240115.xlsx'
        test_file.touch()

        schema = schema_registry.find_matching_schema(test_file)
        assert schema is not None
        assert schema.store_id == 'test_store'

        # File that doesn't match any pattern
        non_matching = temp_workspace / 'unknown_format.xlsx'
        non_matching.touch()

        no_schema = schema_registry.find_matching_schema(non_matching)
        assert no_schema is None

    def test_validate_config(self, schema_registry):
        """Test schema configuration validation"""
        issues = schema_registry.validate_config()
        # Our test config is minimal but valid
        assert isinstance(issues, list)

    def test_extract_sheet(self, schema_registry, sample_excel_file):
        """Test full sheet extraction"""
        schema = schema_registry.get_schema('test_store')

        results, errors = schema.extract_all_sheets(sample_excel_file)

        assert 'sales' in results
        assert len(results['sales']) > 0
        assert 'date' in results['sales'].columns
        assert 'product_id' in results['sales'].columns
        assert 'store_id' in results['sales'].columns
        # Check metadata columns
        assert 'country' in results['sales'].columns
        assert 'region' in results['sales'].columns
        assert 'extraction_timestamp' in results['sales'].columns

    def test_extract_with_problematic_data(self, schema_registry, temp_workspace, problematic_dataframe):
        """Test handling of problematic data during extraction"""
        # Create a file with problematic data
        file_path = temp_workspace / 'test_problematic.xlsx'
        df = problematic_dataframe.rename(columns={
            'date': 'Transaction Date',
            'product_id': 'SKU',
            'qty': 'Qty Sold',
            'revenue': 'Net Sales'
        })
        df.to_excel(file_path, sheet_name='Sales', index=False)

        schema = schema_registry.get_schema('test_store')

        # Should handle errors gracefully
        results, errors = schema.extract_all_sheets(file_path)

        # May have partial results or all errors
        assert isinstance(results, dict)
        assert isinstance(errors, list)

    def test_handle_missing_sheet(self, schema_registry, temp_workspace):
        """Test error handling for missing sheets"""
        # Create file without expected sheet
        bad_file = temp_workspace / 'test_missing_sheet.xlsx'
        pd.DataFrame({'A': [1]}).to_excel(bad_file, sheet_name='WrongSheet', index=False)

        schema = schema_registry.get_schema('test_store')

        with pytest.raises(RuntimeError, match="No sheets extracted"):
            schema.extract_all_sheets(bad_file)


class TestValidationEdgeCases:
    """Test edge cases in data validation"""

    def test_completely_null_required_column(self):
        """Test all-null required column raises error"""
        mapping = ColumnMapping(
            source_name='required_col',
            target_name='required_col',
            data_type=ColumnDataType.STRING,
            required=True
        )

        all_null = pd.Series([None, None, None])

        with pytest.raises(ValueError, match="missing values"):
            mapping.validate_and_transform(all_null)

    def test_mixed_type_column(self):
        """Test handling columns with mixed data types"""
        mapping = ColumnMapping(
            source_name='qty',
            target_name='qty',
            data_type=ColumnDataType.INTEGER,
            required=True
        )

        mixed = pd.Series(['10', 'twenty', '30', None])
        result = mapping.validate_and_transform(mixed)

        # 'twenty' becomes NA (coerced), None stays NA
        assert result.iloc[0] == 10
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == 30

    def test_date_with_mixed_formats(self):
        """Test date parsing with mixed formats (auto-inference)"""
        mapping = ColumnMapping(
            source_name='date',
            target_name='date',
            data_type=ColumnDataType.DATE,
            required=True,
            date_format=None  # Let pandas infer
        )

        # Common formats pandas can handle
        series = pd.Series(['2024-01-15', '01/15/2024', '15-Jan-2024'])
        result = mapping.validate_and_transform(series)

        assert pd.api.types.is_datetime64_dtype(result)
        # All should parse successfully
        assert result.notna().all()

    def test_validation_with_nulls_in_numeric(self):
        """Test min/max validation ignores null values"""
        mapping = ColumnMapping(
            source_name='value',
            target_name='value',
            data_type=ColumnDataType.FLOAT,
            required=False,
            validation_rules={'min': 0, 'max': 100}
        )

        # Mix of valid values and nulls
        series = pd.Series([10.0, None, 50.0, None])
        result = mapping.validate_and_transform(series)

        assert len(result) == 4
        assert result.iloc[1] is pd.NA
        assert result.iloc[3] is pd.NA

    def test_schema_with_regex_sheet_name(self, temp_workspace):
        """Test regex-based sheet name matching"""
        # This tests the regex functionality in SheetMapping
        mapping = SheetMapping(
            sheet_name='regex:Sales.*',
            columns={},
            skip_rows=0,
            header_row=0
        )

        # Create Excel with multiple sheets
        file_path = temp_workspace / 'test_regex.xlsx'
        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            pd.DataFrame({'A': [1]}).to_excel(writer, sheet_name='Sales_2024', index=False)
            pd.DataFrame({'B': [2]}).to_excel(writer, sheet_name='Other', index=False)

        excel_file = pd.ExcelFile(file_path, engine='openpyxl')

        # Should find the matching sheet
        df = mapping.extract_sheet(excel_file, 'test_store')
        assert 'A' in df.columns
