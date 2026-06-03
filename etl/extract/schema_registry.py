"""
Schema Registry for Store Excel Mappings
Centralizes heterogeneous Excel format definitions and provides
validation/transformation logic for the extraction pipeline.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
from enum import Enum
import yaml
import json
from pathlib import Path
import pandas as pd
import re
from datetime import datetime, timezone  


class ColumnDataType(Enum):
    """Supported data types for column mapping validation"""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    DATE = "date"
    DATETIME = "datetime"
    CATEGORY = "category"


@dataclass
class ColumnMapping:
    """Individual column mapping definition"""
    source_name: str
    target_name: str
    data_type: ColumnDataType
    required: bool = True
    default_value: Optional[Any] = None
    date_format: Optional[str] = None
    validation_rules: Dict[str, Any] = field(default_factory=dict)

    def validate_and_transform(self, series: pd.Series) -> pd.Series:
        """Validate and transform a pandas Series according to mapping rules."""
        # Handle missing values
        if self.required and series.isna().any():
            missing_count = series.isna().sum()
            raise ValueError(
                f"Column '{self.source_name}' has {missing_count} missing values "
                f"but is marked as required"
            )

        # Apply default value for optional columns with NaN
        if not self.required and self.default_value is not None:
            series = series.fillna(self.default_value)

        # Type conversion
        try:
            if self.data_type == ColumnDataType.STRING:
                series = series.astype(str)
                series = series.replace('nan', pd.NA)
            elif self.data_type == ColumnDataType.INTEGER:
                if series.dtype == object:
                    series = series.str.replace(',', '').str.extract(r'(\d+)')[0]
                series = pd.to_numeric(series, errors='coerce').astype('Int64')
            elif self.data_type == ColumnDataType.FLOAT:
                if series.dtype == object:
                    series = series.str.replace(',', '')
                series = pd.to_numeric(series, errors='coerce')
            elif self.data_type in (ColumnDataType.DATE, ColumnDataType.DATETIME):
                # Corrected: removed deprecated infer_datetime_format
                series = pd.to_datetime(
                    series,
                    format=self.date_format,
                    errors='coerce'
                )
            elif self.data_type == ColumnDataType.CATEGORY:
                series = series.astype('category')
        except Exception as e:
            raise ValueError(
                f"Type conversion failed for column '{self.source_name}' "
                f"to {self.data_type.value}: {str(e)}"
            )

        # Apply validation rules
        if self.validation_rules:
            if 'min' in self.validation_rules and pd.api.types.is_numeric_dtype(series):
                mask = series < self.validation_rules['min']
                if mask.any():
                    raise ValueError(
                        f"Column '{self.source_name}' has {mask.sum()} values "
                        f"below minimum {self.validation_rules['min']}"
                    )

            if 'max' in self.validation_rules and pd.api.types.is_numeric_dtype(series):
                mask = series > self.validation_rules['max']
                if mask.any():
                    raise ValueError(
                        f"Column '{self.source_name}' has {mask.sum()} values "
                        f"above maximum {self.validation_rules['max']}"
                    )

            # Corrected: more informative error message with count
            if 'not_null' in self.validation_rules and self.validation_rules['not_null']:
                null_count = series.isna().sum()
                if null_count > 0:
                    raise ValueError(
                        f"Column '{self.source_name}' contains {null_count} null values "
                        f"but not_null validation is enforced"
                    )

            if 'allowed_values' in self.validation_rules:
                allowed = set(self.validation_rules['allowed_values'])
                invalid = series[~series.isin(allowed) & ~series.isna()]
                if not invalid.empty:
                    raise ValueError(
                        f"Column '{self.source_name}' contains {len(invalid)} "
                        f"invalid values. Allowed: {allowed}"
                    )

        return series


@dataclass
class SheetMapping:
    """Mapping definition for a single Excel sheet"""
    sheet_name: str
    columns: Dict[str, ColumnMapping]
    skip_rows: int = 0
    header_row: int = 0

    def extract_sheet(self, excel_file: pd.ExcelFile, store_id: str) -> pd.DataFrame:
        """Extract and transform a single sheet from Excel file."""
        # Corrected: re is now imported at module level
        if self.sheet_name.startswith('regex:'):
            pattern = self.sheet_name[6:]
            matching_sheets = [s for s in excel_file.sheet_names if re.match(pattern, s)]
            if not matching_sheets:
                raise ValueError(
                    f"No sheets matching pattern '{pattern}' found in {store_id}"
                )
            if len(matching_sheets) > 1:
                raise ValueError(
                    f"Multiple sheets matching pattern '{pattern}': {matching_sheets}"
                )
            actual_sheet = matching_sheets[0]
        else:
            actual_sheet = self.sheet_name

        df = pd.read_excel(
            excel_file,
            sheet_name=actual_sheet,
            skiprows=self.skip_rows,
            header=self.header_row
        )

        transformed_data = {}
        for target_name, col_mapping in self.columns.items():
            if col_mapping.source_name not in df.columns:
                if col_mapping.required:
                    raise ValueError(
                        f"Required column '{col_mapping.source_name}' "
                        f"not found in sheet '{actual_sheet}'"
                    )
                else:
                    transformed_data[target_name] = pd.Series(
                        col_mapping.default_value,
                        index=df.index
                    )
                    continue

            try:
                transformed_data[target_name] = col_mapping.validate_and_transform(
                    df[col_mapping.source_name].copy()
                )
            except Exception as e:
                raise ValueError(
                    f"Validation failed for column '{col_mapping.source_name}' "
                    f"in sheet '{actual_sheet}': {str(e)}"
                )

        return pd.DataFrame(transformed_data)


@dataclass
class StoreSchema:
    """Complete schema definition for a store's Excel file"""
    store_id: str
    country: str
    region: str
    lat: float
    lon: float
    file_pattern: str
    sheets: Dict[str, SheetMapping]
    file_metadata: Dict[str, Any] = field(default_factory=dict)

    # Corrected: updated return type hint to match actual return value
    def extract_all_sheets(self, file_path: Path) -> tuple[Dict[str, pd.DataFrame], List[Dict]]:
        """Extract all configured sheets from a store's Excel file."""
        excel_file = pd.ExcelFile(file_path, engine='openpyxl')

        results = {}
        errors = []

        for sheet_type, sheet_mapping in self.sheets.items():
            try:
                df = sheet_mapping.extract_sheet(excel_file, self.store_id)
                # Corrected: using timezone-aware UTC datetime
                df['store_id'] = self.store_id
                df['country'] = self.country
                df['region'] = self.region
                df['latitude'] = self.lat
                df['longitude'] = self.lon
                df['extraction_timestamp'] = datetime.now(timezone.utc)
                df['source_file'] = str(file_path)

                results[sheet_type] = df
            except Exception as e:
                errors.append({
                    'sheet_type': sheet_type,
                    'error': str(e),
                    'store_id': self.store_id,
                    'file_path': str(file_path),
                    'timestamp': datetime.now(timezone.utc)  # Corrected
                })

        if errors and not results:
            raise RuntimeError(
                f"No sheets extracted successfully for {self.store_id}. "
                f"Errors: {json.dumps(errors, indent=2)}"
            )

        return results, errors


class SchemaRegistry:
    """Central registry for all store schema definitions."""

    def __init__(self, config_path: Union[str, Path]):
        self.config_path = Path(config_path)
        self.schemas: Dict[str, StoreSchema] = {}
        self._load_config()

    def _load_config(self):
        """Load schema definitions from configuration file"""
        if self.config_path.suffix in ('.yaml', '.yml'):
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
        elif self.config_path.suffix == '.json':
            with open(self.config_path, 'r') as f:
                config = json.load(f)
        else:
            raise ValueError(f"Unsupported config format: {self.config_path.suffix}")

        stores_config = config.get('stores', {})
        for store_id, store_config in stores_config.items():
            sheets = {}
            for sheet_type, sheet_config in store_config.get('sheets', {}).items():
                columns = {}
                for target_name, col_config in sheet_config.get('columns', {}).items():
                    columns[target_name] = ColumnMapping(
                        source_name=col_config['source_name'],
                        target_name=target_name,
                        data_type=ColumnDataType(col_config.get('data_type', 'string')),
                        required=col_config.get('required', True),
                        default_value=col_config.get('default_value'),
                        date_format=col_config.get('date_format'),
                        validation_rules=col_config.get('validation_rules', {})
                    )

                sheets[sheet_type] = SheetMapping(
                    sheet_name=sheet_config['sheet_name'],
                    columns=columns,
                    skip_rows=sheet_config.get('skip_rows', 0),
                    header_row=sheet_config.get('header_row', 0)
                )

            self.schemas[store_id] = StoreSchema(
                store_id=store_id,
                country=store_config['country'],
                region=store_config['region'],
                lat=store_config['lat'],
                lon=store_config['lon'],
                file_pattern=store_config['file_pattern'],
                sheets=sheets,
                file_metadata=store_config.get('file_metadata', {})
            )

    def get_schema(self, store_id: str) -> Optional[StoreSchema]:
        """Retrieve schema for a specific store"""
        return self.schemas.get(store_id)

    def find_matching_schema(self, file_path: Path) -> Optional[StoreSchema]:
        """Find schema matching a file based on patterns"""
        import fnmatch
        file_name = file_path.name
        for schema in self.schemas.values():
            if fnmatch.fnmatch(file_name, schema.file_pattern):
                return schema
        return None

    def list_stores(self) -> List[str]:
        """List all configured store IDs"""
        return list(self.schemas.keys())

    def validate_config(self) -> List[str]:
        """Validate the schema configuration"""
        issues = []

        for store_id, schema in self.schemas.items():
            if not schema.sheets:
                issues.append(f"Store {store_id}: No sheets configured")

            for sheet_type, sheet in schema.sheets.items():
                if not sheet.columns:
                    issues.append(
                        f"Store {store_id}, sheet {sheet_type}: No columns mapped"
                    )

                standard_columns = {'date', 'product_id', 'revenue', 'store_id'}
                missing_standard = standard_columns - set(sheet.columns.keys())
                if missing_standard:
                    issues.append(
                        f"Store {store_id}, sheet {sheet_type}: "
                        f"Missing standard columns: {missing_standard}"
                    )

        return issues
