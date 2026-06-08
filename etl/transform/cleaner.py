"""
Data Cleaning and Currency Normalization Module
Handles multi-currency revenue data and data quality enforcement
for pan-Asian retail analysis.

Financial Relevance: Currency normalization is critical because:
- A 5% revenue increase in JPY might be 2% decrease in USD
- Same-store sales must be compared in constant currency
- Fund performance attribution needs currency impact separation
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import json
from enum import Enum
from forex_python.converter import CurrencyRates
from currency_converter import CurrencyConverter  # Fallback/alternative
import requests
from functools import lru_cache
import time

logger = logging.getLogger(__name__)


class ReportingCurrency(Enum):
    """Reporting currencies for fund analysis"""
    USD = "USD"  # Primary reporting currency for global funds
    SGD = "SGD"  # Common for ASEAN-focused funds
    HKD = "HKD"  # Greater China focused funds
    JPY = "JPY"  # Japan-focused strategies


@dataclass
class CurrencyNormalizationResult:
    """Result of currency normalization"""
    original_currency: str
    original_amount: float
    normalized_amount_usd: float
    normalized_amount_local: float  # In fund's reporting currency
    exchange_rate: float
    rate_date: datetime
    rate_source: str


class CurrencyNormalizer:
    """
    Handles multi-currency revenue normalization.

    In practice, funds typically use:
    1. Bloomberg FXGO rates (enterprise)
    2. Reuters Eikon API (enterprise)
    3. ECB reference rates (free, but limited to European currencies)
    4. Open Exchange Rates API (mid-tier, good for Asian currencies)
    5. forex-python (what we're using - good for development)
    """

    def __init__(
        self,
        primary_currency: ReportingCurrency = ReportingCurrency.USD,
        use_ecb_fallback: bool = True,
        cache_ttl_hours: int = 24
    ):
        self.primary_currency = primary_currency.value
        self.cache_ttl = cache_ttl_hours * 3600

        # Initialize rate providers
        try:
            self.fx_rates = CurrencyRates()
            self.rate_provider = "forex-python"
        except Exception:
            logger.warning("forex-python unavailable, using ECB rates")
            self.rate_provider = "ECB"

        # ECB converter as fallback (covers ~30 currencies)
        if use_ecb_fallback:
            try:
                self.ecb_converter = CurrencyConverter(
                    fallback_on_wrong_date=True,
                    fallback_on_missing_rate=True
                )
            except Exception:
                self.ecb_converter = None

        # Rate cache
        self._rate_cache: Dict[str, Tuple[float, float]] = {}  # pair -> (rate, timestamp)

        # Known currency mappings for Asian markets
        self.country_currency_map = {
            'JP': 'JPY', 'KR': 'KRW', 'CN': 'CNY',
            'HK': 'HKD', 'TW': 'TWD', 'SG': 'SGD',
            'TH': 'THB', 'MY': 'MYR', 'ID': 'IDR',
            'PH': 'PHP', 'VN': 'VND', 'IN': 'INR',
            'AU': 'AUD', 'NZ': 'NZD',
        }

    @lru_cache(maxsize=1000)
    def get_exchange_rate(
        self,
        from_currency: str,
        to_currency: str,
        date: Optional[datetime] = None
    ) -> float:
        """
        Get exchange rate with caching and fallback.

        LRU cache decorator stores results in memory to avoid
        repeated API calls for the same currency pair.
        """
        cache_key = f"{from_currency}_{to_currency}_{date.date() if date else 'latest'}"

        # Check memory cache
        if cache_key in self._rate_cache:
            rate, timestamp = self._rate_cache[cache_key]
            if time.time() - timestamp < self.cache_ttl:
                return rate

        try:
            if self.rate_provider == "forex-python":
                if date:
                    rate = self.fx_rates.get_rate(
                        from_currency, to_currency, date
                    )
                else:
                    rate = self.fx_rates.get_rate(from_currency, to_currency)
            else:
                # Use ECB converter
                rate = self.ecb_converter.convert(
                    1, from_currency, to_currency, date=date
                )

            # Cache the rate
            self._rate_cache[cache_key] = (rate, time.time())
            return rate

        except Exception as e:
            logger.error(
                f"Failed to get {from_currency}/{to_currency} rate: {e}"
            )

            # Try fallback to ECB if available
            if self.ecb_converter and self.rate_provider != "ECB":
                try:
                    rate = self.ecb_converter.convert(
                        1, from_currency, to_currency, date=date
                    )
                    self._rate_cache[cache_key] = (rate, time.time())
                    return rate
                except Exception:
                    pass

            # Last resort: use approximate fixed rates
            return self._get_fallback_rate(from_currency, to_currency)

    def _get_fallback_rate(self, from_currency: str, to_currency: str) -> float:
        """Fallback exchange rates for development/testing"""
        # Approximate rates (not for production use!)
        fallback_rates = {
            'JPY': 0.0067, 'KRW': 0.00075, 'CNY': 0.14,
            'HKD': 0.128, 'TWD': 0.032, 'SGD': 0.74,
            'THB': 0.028, 'MYR': 0.21, 'IDR': 0.000064,
            'PHP': 0.018, 'VND': 0.000041, 'INR': 0.012,
            'AUD': 0.66, 'NZD': 0.61, 'USD': 1.0,
        }

        if from_currency == to_currency:
            return 1.0

        if to_currency == 'USD':
            return fallback_rates.get(from_currency, 1.0)
        elif from_currency == 'USD':
            return 1.0 / fallback_rates.get(to_currency, 1.0)
        else:
            # Cross rate via USD
            from_usd = fallback_rates.get(from_currency, 1.0)
            to_usd = fallback_rates.get(to_currency, 1.0)
            return from_usd / to_usd if to_usd > 0 else 1.0

    def normalize_dataframe(
        self,
        df: pd.DataFrame,
        amount_column: str = 'revenue',
        currency_column: Optional[str] = None,
        country_column: str = 'country',
        date_column: str = 'date'
    ) -> pd.DataFrame:
        """
        Normalize all monetary values to USD and fund reporting currency.
        """
        normalized = df.copy()

        # Determine currency for each row
        if currency_column and currency_column in df.columns:
            normalized['original_currency'] = df[currency_column]
        else:
            # Infer currency from country
            normalized['original_currency'] = df[country_column].map(
                self.country_currency_map
            ).fillna('USD')

        # Calculate USD amounts
        normalized['revenue_usd'] = normalized.apply(
            lambda row: self._convert_to_usd(
                row[amount_column],
                row['original_currency'],
                row.get(date_column)
            ),
            axis=1
        )

        # Calculate fund reporting currency amounts (if different from USD)
        if self.primary_currency != 'USD':
            target_col = f'revenue_{self.primary_currency.lower()}'
            normalized[target_col] = normalized.apply(
                lambda row: self._convert_currency(
                    row[amount_column],
                    row['original_currency'],
                    self.primary_currency,
                    row.get(date_column)
                ),
                axis=1
            )

        # Add exchange rate column
        normalized['exchange_rate_usd'] = normalized.apply(
            lambda row: self._get_rate_for_row(
                row['original_currency'], 'USD', row.get(date_column)
            ),
            axis=1
        )

        logger.info(
            f"Normalized {len(normalized)} transactions across "
            f"{normalized['original_currency'].nunique()} currencies"
        )

        return normalized

    def _convert_to_usd(
        self, amount: float, currency: str, date: Optional[datetime] = None
    ) -> float:
        """Convert amount to USD"""
        if pd.isna(amount) or currency == 'USD':
            return amount

        rate = self.get_exchange_rate(currency, 'USD', date)
        return amount * rate

    def _convert_currency(
        self, amount: float, from_curr: str, to_curr: str,
        date: Optional[datetime] = None
    ) -> float:
        """Convert between any two currencies"""
        if pd.isna(amount) or from_curr == to_curr:
            return amount

        rate = self.get_exchange_rate(from_curr, to_curr, date)
        return amount * rate

    def _get_rate_for_row(
        self, from_curr: str, to_curr: str, date: Optional[datetime] = None
    ) -> float:
        """Get exchange rate used for a row"""
        try:
            return self.get_exchange_rate(from_curr, to_curr, date)
        except Exception:
            return 0.0


class DataCleaner:
    """
    Comprehensive data cleaning for retail sales data.

    Implements financial data quality standards:
    1. Completeness checks
    2. Accuracy validation
    3. Consistency enforcement
    4. Timeliness verification
    5. Uniqueness validation
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._default_config()
        self.quality_report = []

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        """Default cleaning configuration"""
        return {
            'revenue': {
                'min_value': 0,
                'max_value': 1000000,  # $1M per transaction is suspicious
                'allow_zero': True,
                'handle_negative': 'absolute',  # or 'zero', 'raise'
            },
            'quantity': {
                'min_value': 1,
                'max_value': 10000,
                'allow_zero': False,
            },
            'date_range': {
                'min_date': '2020-01-01',
                'max_date': None,  # None = today
            },
            'deduplicate': True,
            'remove_outliers': True,
            'outlier_method': 'iqr',  # or 'zscore'
            'outlier_threshold': 1.5,  # IQR multiplier
            'fill_missing_dates': True,
            'interpolate_missing_values': False,
        }

    def clean(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Execute full cleaning pipeline.

        Returns cleaned DataFrame and quality report.
        """
        self.quality_report = []
        cleaned = df.copy()
        initial_rows = len(cleaned)

        # 1. Remove duplicates
        if self.config['deduplicate']:
            cleaned = self._remove_duplicates(cleaned)

        # 2. Handle missing values
        cleaned = self._handle_missing_values(cleaned)

        # 3. Validate value ranges
        cleaned = self._validate_ranges(cleaned)

        # 4. Validate dates
        cleaned = self._validate_dates(cleaned)

        # 5. Remove outliers
        if self.config['remove_outliers']:
            cleaned = self._remove_outliers(cleaned)

        # 6. Fill missing dates (business day assumption)
        if self.config['fill_missing_dates']:
            cleaned = self._fill_missing_dates(cleaned)

        # 7. Standardize text fields
        cleaned = self._standardize_text(cleaned)

        # Generate quality report
        final_rows = len(cleaned)
        quality_report = {
            'initial_rows': initial_rows,
            'final_rows': final_rows,
            'rows_removed': initial_rows - final_rows,
            'retention_rate': final_rows / initial_rows if initial_rows > 0 else 0,
            'columns_processed': list(cleaned.columns),
            'quality_checks': self.quality_report,
            'timestamp': datetime.now().isoformat()
        }

        logger.info(
            f"Data cleaning complete: {initial_rows} -> {final_rows} rows "
            f"({quality_report['retention_rate']:.1%} retention)"
        )

        return cleaned, quality_report

    def _remove_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove exact and fuzzy duplicates"""
        initial = len(df)

        # Remove exact duplicates
        df = df.drop_duplicates()
        exact_removed = initial - len(df)

        # Check for potential fuzzy duplicates (same store, date, different amounts)
        if all(col in df.columns for col in ['store_id', 'date', 'product_id']):
            dupe_mask = df.duplicated(
                subset=['store_id', 'date', 'product_id'],
                keep='first'
            )
            fuzzy_removed = dupe_mask.sum()
            df = df[~dupe_mask]
        else:
            fuzzy_removed = 0

        total_removed = exact_removed + fuzzy_removed
        self.quality_report.append({
            'check': 'duplicates',
            'exact_removed': exact_removed,
            'fuzzy_removed': fuzzy_removed,
            'total_removed': total_removed
        })

        return df

    def _handle_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """Handle missing values based on column type"""
        missing_report = {}

        for col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                missing_report[col] = missing_count

                if pd.api.types.is_numeric_dtype(df[col]):
                    if self.config['interpolate_missing_values']:
                        df[col] = df[col].interpolate(method='linear')
                    else:
                        df[col] = df[col].fillna(0)
                else:
                    df[col] = df[col].fillna('UNKNOWN')

        self.quality_report.append({
            'check': 'missing_values',
            'columns_with_missing': missing_report,
            'total_missing': sum(missing_report.values())
        })

        return df

    def _validate_ranges(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate numeric columns are within expected ranges"""
        range_violations = {}

        # Check revenue column
        revenue_cols = [c for c in df.columns if 'revenue' in c.lower() or 'sales' in c.lower()]
        for col in revenue_cols:
            if pd.api.types.is_numeric_dtype(df[col]):
                min_val = self.config['revenue']['min_value']
                max_val = self.config['revenue']['max_value']

                # Flag negative values
                negatives = (df[col] < 0).sum()
                if negatives > 0:
                    range_violations[f'{col}_negative'] = negatives

                    if self.config['revenue']['handle_negative'] == 'absolute':
                        df[col] = df[col].abs()
                    elif self.config['revenue']['handle_negative'] == 'zero':
                        df.loc[df[col] < 0, col] = 0
                    elif self.config['revenue']['handle_negative'] == 'raise':
                        raise ValueError(f"Negative {col} values found: {negatives}")

                # Flag out of range
                too_high = (df[col] > max_val).sum()
                if too_high > 0:
                    range_violations[f'{col}_too_high'] = too_high

        # Check quantity columns
        qty_cols = [c for c in df.columns if 'qty' in c.lower() or 'quantity' in c.lower()]
        for col in qty_cols:
            if pd.api.types.is_numeric_dtype(df[col]):
                below_min = (df[col] < self.config['quantity']['min_value']).sum()
                above_max = (df[col] > self.config['quantity']['max_value']).sum()

                if below_min > 0:
                    range_violations[f'{col}_below_min'] = below_min
                if above_max > 0:
                    range_violations[f'{col}_above_max'] = above_max

        self.quality_report.append({
            'check': 'range_validation',
            'violations': range_violations
        })

        return df

    def _validate_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate and clean date columns"""
        date_cols = df.select_dtypes(include=['datetime64']).columns

        for col in date_cols:
            # Check for future dates
            if self.config['date_range']['max_date'] is None:
                max_date = datetime.now()
            else:
                max_date = pd.to_datetime(self.config['date_range']['max_date'])

            future_dates = (df[col] > max_date).sum()

            # Check for too-old dates
            min_date = pd.to_datetime(self.config['date_range']['min_date'])
            too_old = (df[col] < min_date).sum()

            if future_dates > 0 or too_old > 0:
                self.quality_report.append({
                    'check': 'date_validation',
                    'column': col,
                    'future_dates': future_dates,
                    'too_old_dates': too_old
                })

        return df

    def _remove_outliers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove statistical outliers from numeric columns"""
        outlier_report = {}

        numeric_cols = df.select_dtypes(include=[np.number]).columns

        for col in numeric_cols:
            if df[col].nunique() > 2:  # Skip binary columns
                if self.config['outlier_method'] == 'iqr':
                    Q1 = df[col].quantile(0.25)
                    Q3 = df[col].quantile(0.75)
                    IQR = Q3 - Q1

                    lower_bound = Q1 - self.config['outlier_threshold'] * IQR
                    upper_bound = Q3 + self.config['outlier_threshold'] * IQR

                    outliers = ((df[col] < lower_bound) | (df[col] > upper_bound)).sum()

                    if outliers > 0:
                        outlier_report[col] = outliers
                        # Don't remove, just flag (removal requires business judgment)

                elif self.config['outlier_method'] == 'zscore':
                    from scipy import stats
                    z_scores = np.abs(stats.zscore(df[col].dropna()))
                    outliers = (z_scores > 3).sum()  # 3 standard deviations

                    if outliers > 0:
                        outlier_report[col] = outliers

        self.quality_report.append({
            'check': 'outlier_detection',
            'method': self.config['outlier_method'],
            'outliers_detected': outlier_report
        })

        return df

    def _fill_missing_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Fill missing dates assuming daily reporting.
        Only fills if there are gaps in an otherwise regular series.
        """
        if 'date' not in df.columns or 'store_id' not in df.columns:
            return df

        filled_count = 0

        # Group by store and fill date gaps
        for store_id, group in df.groupby('store_id'):
            if len(group) < 3:
                continue

            date_range = pd.date_range(
                start=group['date'].min(),
                end=group['date'].max(),
                freq='D'
            )

            missing_dates = set(date_range) - set(group['date'])

            if 0 < len(missing_dates) < 30:  # Don't fill large gaps
                # Create rows for missing dates with interpolated values
                for missing_date in missing_dates:
                    new_row = group.iloc[-1].copy()
                    new_row['date'] = missing_date
                    new_row['is_interpolated'] = True
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    filled_count += 1

        if filled_count > 0:
            self.quality_report.append({
                'check': 'missing_dates_filled',
                'filled_count': filled_count
            })

        return df

    def _standardize_text(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standardize text fields for consistency"""
        text_cols = df.select_dtypes(include=['object']).columns

        for col in text_cols:
            # Skip ID columns
            if 'id' in col.lower() or 'sku' in col.lower():
                continue

            # Standardize: uppercase, strip whitespace
            df[col] = df[col].str.upper().str.strip()

        return df
