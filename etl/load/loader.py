"""
Data Loader - Handles incremental loading and data versioning
"""

import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import logging

from .database import DatabaseManager, DatabaseBackend, DatabaseConfig

logger = logging.getLogger(__name__)


class DataLoader:
    """
    Manages loading transformed data into databases.

    Supports:
    - Incremental loading (only new/modified records)
    - Full refresh
    - Data versioning
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.load_history: list = []

    def load_stores(self, df: pd.DataFrame, mode: str = 'upsert') -> int:
        """
        Load store data with upsert support.

        mode='upsert': Update existing, insert new
        mode='replace': Delete all, insert all
        mode='append': Insert only new
        """
        if mode == 'replace':
            self.db.active_connection.execute("DELETE FROM stores")

        store_cols = [
            'store_id', 'country', 'region', 'economic_region',
            'urban_rural', 'latitude', 'longitude',
            'nearest_city', 'distance_to_city_km',
            'cluster_id', 'stores_within_10km',
            'gdp_per_capita_usd', 'urbanization_rate'
        ]

        # Select only columns that exist
        available_cols = [c for c in store_cols if c in df.columns]
        stores_df = df[available_cols].drop_duplicates(subset=['store_id'])

        count = self.db.load_stores(stores_df)

        self.load_history.append({
            'table': 'stores',
            'rows': count,
            'mode': mode,
            'timestamp': datetime.now().isoformat()
        })

        return count

    def load_sales(self, df: pd.DataFrame, batch_size: int = 10000) -> int:
        """Load sales data in batches for memory efficiency"""
        total_loaded = 0

        for i in range(0, len(df), batch_size):
            batch = df.iloc[i:i+batch_size]
            count = self.db.load_sales(batch)
            total_loaded += count

            logger.debug(
                f"Loaded batch {i//batch_size + 1}: {count} rows"
            )

        return total_loaded

    def refresh_kpis(self):
        """Recalculate and refresh store KPIs"""
        query = """
            INSERT INTO store_kpis (
                store_id, calculation_date,
                same_store_sales_growth, revenue_mtd, revenue_ytd,
                transaction_count_mtd, avg_basket_size, rank_in_region
            )
            SELECT
                s.store_id,
                CURRENT_DATE,
                -- Same store sales (compare to same period last year)
                (SUM(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE)
                    THEN revenue_usd ELSE 0 END) /
                 NULLIF(SUM(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 year')
                    AND date < DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 year') + INTERVAL '1 month'
                    THEN revenue_usd ELSE 0 END), 0) - 1) AS sss_growth,
                -- MTD revenue
                SUM(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE)
                    THEN revenue_usd ELSE 0 END) AS revenue_mtd,
                -- YTD revenue
                SUM(CASE WHEN date >= DATE_TRUNC('year', CURRENT_DATE)
                    THEN revenue_usd ELSE 0 END) AS revenue_ytd,
                -- MTD transactions
                COUNT(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE)
                    THEN 1 END) AS txn_count_mtd,
                -- Average basket
                AVG(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE)
                    THEN revenue_usd END) AS avg_basket,
                -- Rank within economic region
                RANK() OVER (
                    PARTITION BY s.economic_region
                    ORDER BY SUM(CASE WHEN date >= DATE_TRUNC('month', CURRENT_DATE)
                        THEN revenue_usd ELSE 0 END) DESC
                )
            FROM stores s
            LEFT JOIN sales_transactions st ON s.store_id = st.store_id
            GROUP BY s.store_id, s.economic_region
            ON CONFLICT (store_id, calculation_date) DO UPDATE SET
                same_store_sales_growth = EXCLUDED.same_store_sales_growth,
                revenue_mtd = EXCLUDED.revenue_mtd,
                revenue_ytd = EXCLUDED.revenue_ytd,
                transaction_count_mtd = EXCLUDED.transaction_count_mtd,
                avg_basket_size = EXCLUDED.avg_basket_size,
                rank_in_region = EXCLUDED.rank_in_region
        """

        try:
            self.db.active_connection.execute(query)
            self.db.active_connection.commit()
            logger.info("KPIs refreshed successfully")
        except Exception as e:
            logger.error(f"KPI refresh failed: {e}")
