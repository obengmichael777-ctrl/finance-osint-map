"""
Regional Aggregation Engine for Retail Analytics
Generates region-wide summaries critical for equity research:
- Same-store sales growth by economic region
- Revenue per square kilometer (market penetration)
- Urban vs rural performance comparisons
- Currency-adjusted growth rates

Financial Relevance: These aggregations directly feed:
- Comparable company analysis (comp sheets)
- Industry reports for fund marketing
- Risk factor identification (geographic concentration)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import json

logger = logging.getLogger(__name__)


@dataclass
class RegionalSummary:
    """Container for regional aggregation results"""
    region: str
    period_start: datetime
    period_end: datetime
    total_revenue_usd: float
    total_stores: int
    total_transactions: int
    avg_revenue_per_store: float
    avg_transaction_value: float
    revenue_growth_qoq: float
    revenue_growth_yoy: float
    top_performing_stores: List[str]
    bottom_performing_stores: List[str]
    currency_exposure: Dict[str, float]
    urban_rural_split: Dict[str, float]
    competition_index: float
    market_concentration_hhi: float


class Aggregator:
    """
    Generates regional summaries for investment analysis.

    Common fund requests this satisfies:
    - "Give me ASEAN ex-Singapore retail growth"
    - "What's our Japan exposure by prefecture?"
    - "Show me urban vs rural same-store sales trends"
    """

    def __init__(self, reporting_currency: str = "USD"):
        self.reporting_currency = reporting_currency

    def aggregate_by_region(
        self,
        df: pd.DataFrame,
        group_by: str = 'economic_region',
        date_column: str = 'date',
        revenue_column: str = 'revenue_usd',
        store_column: str = 'store_id',
        period: str = 'M'  # M=Monthly, W=Weekly, Q=Quarterly
    ) -> pd.DataFrame:
        """
        Generate regional aggregations for time-series analysis.
        """
        # Ensure date is datetime
        df = df.copy()
        df[date_column] = pd.to_datetime(df[date_column])

        # Set date as index for resampling
        df = df.set_index(date_column)

        aggregations = []

        for region, group in df.groupby(group_by):
            # Resample to desired period
            resampled = group.resample(period).agg({
                revenue_column: ['sum', 'mean', 'count'],
                store_column: 'nunique'
            }).fillna(0)

            # Flatten multi-level columns
            resampled.columns = [
                f"{col[0]}_{col[1]}" for col in resampled.columns
            ]

            # Calculate growth rates
            resampled['revenue_growth_mom'] = resampled[
                f'{revenue_column}_sum'
            ].pct_change()

            resampled['revenue_growth_yoy'] = resampled[
                f'{revenue_column}_sum'
            ].pct_change(periods=12 if period == 'M' else 4)

            # Add region identifier
            resampled[group_by] = region

            aggregations.append(resampled.reset_index())

        result = pd.concat(aggregations, ignore_index=True)

        logger.info(
            f"Generated {len(result)} regional aggregates "
            f"across {len(aggregations)} regions"
        )

        return result

    def calculate_same_store_sales(
        self,
        df: pd.DataFrame,
        comparison_period: str = 'YoY',  # or 'QoQ', 'MoM'
        min_days_operating: int = 365
    ) -> pd.DataFrame:
        """
        Calculate same-store sales growth - THE key retail metric.

        Same-store sales = (Current period sales / Prior period sales) - 1
        Only includes stores open in both periods.

        This is what equity analysts obsess over.
        """
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])

        if comparison_period == 'YoY':
            period_days = 365
        elif comparison_period == 'QoQ':
            period_days = 90
        else:  # MoM
            period_days = 30

        results = []

        for store_id, store_data in df.groupby('store_id'):
            if len(store_data) < period_days:
                continue

            store_data = store_data.sort_values('date')

            # Get current period (most recent)
            current_period = store_data.iloc[-period_days:]
            current_revenue = current_period['revenue_usd'].sum()

            # Get comparison period
            comparison_period_data = store_data.iloc[-(period_days*2):-period_days]
            if len(comparison_period_data) == 0:
                continue

            comparison_revenue = comparison_period_data['revenue_usd'].sum()

            if comparison_revenue > 0:
                sss_growth = (current_revenue / comparison_revenue) - 1
            else:
                sss_growth = None

            results.append({
                'store_id': store_id,
                'same_store_sales_growth': sss_growth,
                'current_revenue': current_revenue,
                'prior_revenue': comparison_revenue,
                'comparison_period': comparison_period
            })

        return pd.DataFrame(results)

    def generate_market_penetration_analysis(
        self,
        df: pd.DataFrame,
        population_data: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Calculate revenue per capita and market penetration metrics.

        In equity research, this helps answer:
        - Is this retailer saturated or still growing in a region?
        - What's the TAM (Total Addressable Market) per store?
        """
        penetration = df.groupby(['economic_region', 'country']).agg(
            total_revenue_usd=('revenue_usd', 'sum'),
            store_count=('store_id', 'nunique'),
            avg_revenue_per_store=('revenue_usd', 'mean'),
            avg_transaction_value=('revenue_usd', 'mean')
        ).reset_index()

        # Add GDP per capita context if available
        if 'gdp_per_capita_usd' in df.columns:
            penetration['gdp_per_capita'] = df.groupby('country')[
                'gdp_per_capita_usd'
            ].first().values

            # Revenue as % of GDP per capita (spending power indicator)
            penetration['revenue_to_gdp_ratio'] = (
                penetration['avg_revenue_per_store'] / penetration['gdp_per_capita']
            )

        return penetration

    def calculate_herfindahl_index(
        self, df: pd.DataFrame, group_column: str, value_column: str
    ) -> float:
        """
        Calculate Herfindahl-Hirschman Index for market concentration.

        HHI = Sum of squared market shares
        < 1500: Unconcentrated
        1500-2500: Moderately concentrated
        > 2500: Highly concentrated

        Used by funds to assess competitive dynamics.
        """
        market_shares = (
            df.groupby(group_column)[value_column].sum() /
            df[value_column].sum()
        )

        hhi = (market_shares ** 2).sum() * 10000  # Scale to HHI range

        return hhi

    def generate_currency_exposure_report(
        self,
        df: pd.DataFrame,
        revenue_column: str = 'revenue_usd',
        currency_column: str = 'original_currency'
    ) -> pd.DataFrame:
        """
        Generate currency exposure analysis.

        Critical for fund risk management:
        - FX hedging decisions
        - Portfolio currency overlay
        - Performance attribution
        """
        exposure = df.groupby(currency_column).agg(
            revenue_usd=('revenue_usd', 'sum'),
            transaction_count=('revenue_usd', 'count'),
            avg_transaction=('revenue_usd', 'mean')
        ).reset_index()

        # Calculate percentage of total
        total_revenue = exposure['revenue_usd'].sum()
        exposure['percentage_of_total'] = (
            exposure['revenue_usd'] / total_revenue * 100
        )

        # Add volatility flag (simplified - in production use historical FX vol)
        high_vol_currencies = ['IDR', 'INR', 'PHP', 'VND', 'THB']
        exposure['high_volatility'] = exposure[currency_column].isin(
            high_vol_currencies
        )

        return exposure.sort_values('revenue_usd', ascending=False)

    def generate_full_regional_summary(
        self,
        df: pd.DataFrame,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None
    ) -> List[RegionalSummary]:
        """
        Generate comprehensive regional summaries for all economic regions.
        """
        if period_start is None:
            period_start = df['date'].min()
        if period_end is None:
            period_end = df['date'].max()

        summaries = []

        for region in df['economic_region'].unique():
            region_data = df[
                (df['economic_region'] == region) &
                (df['date'] >= period_start) &
                (df['date'] <= period_end)
            ]

            if region_data.empty:
                continue

            # Calculate metrics
            total_revenue = region_data['revenue_usd'].sum()
            total_stores = region_data['store_id'].nunique()

            # Top/bottom stores
            store_performance = region_data.groupby('store_id')['revenue_usd'].sum()
            top_stores = store_performance.nlargest(5).index.tolist()
            bottom_stores = store_performance.nsmallest(5).index.tolist()

            # Currency exposure
            currency_exposure = (
                region_data.groupby('original_currency')['revenue_usd']
                .sum()
                .to_dict()
            )

            # Urban/rural split
            urban_rural = (
                region_data.groupby('urban_rural')['revenue_usd']
                .sum()
                .to_dict()
            )

            # Competition index (average stores within 10km)
            if 'stores_within_10km' in region_data.columns:
                competition_index = region_data['stores_within_10km'].mean()
            else:
                competition_index = 0

            # Market concentration
            if len(store_performance) > 1:
                hhi = self.calculate_herfindahl_index(
                    region_data, 'store_id', 'revenue_usd'
                )
            else:
                hhi = 10000  # Monopoly

            summary = RegionalSummary(
                region=region,
                period_start=period_start,
                period_end=period_end,
                total_revenue_usd=total_revenue,
                total_stores=total_stores,
                total_transactions=len(region_data),
                avg_revenue_per_store=total_revenue / total_stores if total_stores > 0 else 0,
                avg_transaction_value=region_data['revenue_usd'].mean(),
                revenue_growth_qoq=0.0,  # Requires prior period data
                revenue_growth_yoy=0.0,
                top_performing_stores=top_stores,
                bottom_performing_stores=bottom_stores,
                currency_exposure=currency_exposure,
                urban_rural_split=urban_rural,
                competition_index=competition_index,
                market_concentration_hhi=hhi
            )

            summaries.append(summary)

        return summaries
