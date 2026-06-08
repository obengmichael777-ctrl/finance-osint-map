"""
KPI Calculator Service
Computes store-level KPIs for map markers.
Designed for daily refresh to support real-time investment dashboards.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PerformanceTier(Enum):
    """Performance classification for map markers"""
    OUTPERFORMING = "outperforming"  # Top quartile
    MARKET_PERFORM = "market_perform"  # Middle 50%
    UNDERPERFORMING = "underperforming"  # Bottom quartile
    CRITICAL = "critical"  # Negative growth, bottom decile


@dataclass
class StoreKPI:
    """Store-level KPI container for map markers"""
    store_id: str
    store_name: Optional[str]
    latitude: float
    longitude: float
    country: str
    economic_region: str
    urban_rural: str
    nearest_city: str

    # Financial KPIs
    revenue_mtd_usd: float
    revenue_ytd_usd: float
    revenue_growth_yoy: float
    same_store_sales_growth: float

    # Operational KPIs
    transaction_count_mtd: int
    avg_basket_size_usd: float
    avg_items_per_transaction: float

    # Competitive KPIs
    market_share_region: float
    competition_density: int
    rank_in_region: int
    total_stores_in_region: int

    # Performance classification
    performance_tier: PerformanceTier

    # Tooltip data
    tooltip_summary: str
    alert_flags: List[str]

    # Timestamp
    calculated_at: datetime


class KPICalculator:
    """
    Calculates store-level KPIs for interactive map visualization.

    Designed for daily refresh cycle to support:
    - Morning meeting dashboards
    - Portfolio manager alerts
    - Risk monitoring
    """

    def __init__(self, db_manager=None):
        self.db = db_manager

    def calculate_store_kpis(
        self,
        df: pd.DataFrame,
        reference_date: Optional[datetime] = None
    ) -> List[StoreKPI]:
        """
        Calculate comprehensive KPIs for all stores.
        """
        if reference_date is None:
            reference_date = datetime.now()

        kpis = []

        # Calculate regional aggregates for market share
        region_totals = df.groupby('economic_region')['revenue_usd'].sum()
        region_store_counts = df.groupby('economic_region')['store_id'].nunique()

        for store_id, store_data in df.groupby('store_id'):
            if store_data.empty:
                continue

            # Get latest store info
            store_info = store_data.iloc[-1]

            # Filter to current period
            mtd_data = store_data[
                store_data['date'] >= reference_date.replace(day=1)
            ]
            ytd_data = store_data[
                store_data['date'] >= reference_date.replace(month=1, day=1)
            ]

            # Calculate growth
            current_month_revenue = mtd_data['revenue_usd'].sum()
            prior_year_month = reference_date.replace(year=reference_date.year - 1)
            prior_month_data = store_data[
                (store_data['date'] >= prior_year_month.replace(day=1)) &
                (store_data['date'] < prior_year_month.replace(day=1) + timedelta(days=31))
            ]
            prior_month_revenue = prior_month_data['revenue_usd'].sum()

            if prior_month_revenue > 0:
                sss_growth = (current_month_revenue / prior_month_revenue) - 1
            else:
                sss_growth = 0

            # Market share calculation
            region = store_info.get('economic_region', 'Unknown')
            region_total = region_totals.get(region, 0)
            market_share = (ytd_data['revenue_usd'].sum() / region_total * 100) if region_total > 0 else 0

            # Rank in region
            store_revenue = current_month_revenue
            region_stores = store_data[
                store_data['economic_region'] == region
            ]['store_id'].nunique()

            # Performance tier
            all_revenues = df.groupby('store_id')['revenue_usd'].sum()
            percentile = (
                all_revenues.rank(pct=True).get(store_id, 0.5)
            )

            if sss_growth < -0.10 and percentile < 0.10:
                tier = PerformanceTier.CRITICAL
            elif percentile > 0.75:
                tier = PerformanceTier.OUTPERFORMING
            elif percentile < 0.25:
                tier = PerformanceTier.UNDERPERFORMING
            else:
                tier = PerformanceTier.MARKET_PERFORM

            # Alert flags
            alerts = []
            if sss_growth < -0.05:
                alerts.append("SSS_DECLINE")
            if market_share < 1.0 and region_store_counts.get(region, 1) > 10:
                alerts.append("LOW_MARKET_SHARE")
            if store_info.get('competition_density', 0) > 10:
                alerts.append("HIGH_COMPETITION")

            # Tooltip summary
            tooltip = (
                f"{store_id} | {store_info.get('nearest_city', 'Unknown')}\n"
                f"MTD Revenue: ${current_month_revenue:,.0f}\n"
                f"SSS Growth: {sss_growth:+.1%}\n"
                f"Region Rank: #{store_data['store_id'].nunique()}/{region_stores}\n"
                f"Tier: {tier.value.upper()}"
            )

            kpi = StoreKPI(
                store_id=store_id,
                store_name=store_info.get('store_name'),
                latitude=store_info.get('latitude', 0),
                longitude=store_info.get('longitude', 0),
                country=store_info.get('country', ''),
                economic_region=region,
                urban_rural=store_info.get('urban_rural', 'Unknown'),
                nearest_city=store_info.get('nearest_city', 'Unknown'),
                revenue_mtd_usd=current_month_revenue,
                revenue_ytd_usd=ytd_data['revenue_usd'].sum(),
                revenue_growth_yoy=sss_growth,  # Simplified
                same_store_sales_growth=sss_growth,
                transaction_count_mtd=len(mtd_data),
                avg_basket_size_usd=mtd_data['revenue_usd'].mean() if len(mtd_data) > 0 else 0,
                avg_items_per_transaction=mtd_data['quantity'].mean() if 'quantity' in mtd_data.columns else 0,
                market_share_region=market_share,
                competition_density=store_info.get('competition_density', 0),
                rank_in_region=store_data['store_id'].nunique(),
                total_stores_in_region=region_stores,
                performance_tier=tier,
                tooltip_summary=tooltip,
                alert_flags=alerts,
                calculated_at=reference_date
            )

            kpis.append(kpi)

        logger.info(f"Calculated KPIs for {len(kpis)} stores")
        return kpis

    def get_marker_data(self, kpis: List[StoreKPI]) -> List[Dict[str, Any]]:
        """Convert KPIs to marker format for map rendering"""
        markers = []

        for kpi in kpis:
            # Color coding based on performance tier
            color_map = {
                PerformanceTier.OUTPERFORMING: '#22c55e',  # Green
                PerformanceTier.MARKET_PERFORM: '#3b82f6',  # Blue
                PerformanceTier.UNDERPERFORMING: '#f59e0b',  # Amber
                PerformanceTier.CRITICAL: '#ef4444',  # Red
            }

            # Size based on revenue (logarithmic scale for visibility)
            import math
            base_size = 10
            revenue_factor = math.log(max(kpi.revenue_mtd_usd, 1)) / 10
            marker_size = base_size * (1 + revenue_factor)

            marker = {
                'id': kpi.store_id,
                'position': [kpi.latitude, kpi.longitude],
                'properties': {
                    'store_id': kpi.store_id,
                    'country': kpi.country,
                    'region': kpi.economic_region,
                    'tier': kpi.performance_tier.value,
                    'color': color_map.get(kpi.performance_tier, '#gray'),
                    'size': min(marker_size, 40),  # Cap at 40px
                    'tooltip': kpi.tooltip_summary,
                    'alerts': kpi.alert_flags,
                    'revenue_mtd_formatted': f"${kpi.revenue_mtd_usd:,.0f}",
                    'sss_growth_formatted': f"{kpi.same_store_sales_growth:+.1%}",
                }
            }
            markers.append(marker)

        return markers

    def get_region_summary(self, kpis: List[StoreKPI]) -> Dict[str, Any]:
        """Generate region-level summary for dashboard"""
        df = pd.DataFrame([asdict(k) for k in kpis])

        summary = {}

        for region in df['economic_region'].unique():
            region_data = df[df['economic_region'] == region]

            summary[region] = {
                'total_stores': len(region_data),
                'total_revenue_mtd': region_data['revenue_mtd_usd'].sum(),
                'avg_sss_growth': region_data['same_store_sales_growth'].mean(),
                'outperforming': len(region_data[region_data['performance_tier'] == 'outperforming']),
                'critical': len(region_data[region_data['performance_tier'] == 'critical']),
                'top_store': region_data.nlargest(1, 'revenue_mtd_usd')['store_id'].values[0],
                'worst_store': region_data.nsmallest(1, 'same_store_sales_growth')['store_id'].values[0],
            }

        return summary
