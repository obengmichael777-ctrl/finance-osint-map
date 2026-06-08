"""
Transform Layer Orchestrator
Coordinates the full transformation pipeline from raw extraction
to analysis-ready datasets.
"""

import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any
import logging
from datetime import datetime

from .geo_enricher import GeoEnricher
from .cleaner import DataCleaner, CurrencyNormalizer, ReportingCurrency
from .aggregator import Aggregator

logger = logging.getLogger(__name__)


class TransformPipeline:
    """
    Complete transformation pipeline for retail data.
    """

    def __init__(
        self,
        reporting_currency: str = "USD",
        output_dir: Optional[Path] = None
    ):
        self.geo_enricher = GeoEnricher()
        self.currency_normalizer = CurrencyNormalizer(
            primary_currency=ReportingCurrency(reporting_currency)
        )
        self.data_cleaner = DataCleaner()
        self.aggregator = Aggregator(reporting_currency=reporting_currency)
        self.output_dir = output_dir or Path("data/transformed")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def transform(
        self,
        df: pd.DataFrame,
        save_intermediates: bool = True
    ) -> Dict[str, pd.DataFrame]:
        """
        Execute full transformation pipeline.

        Returns dictionary of transformed datasets:
        - 'enriched': Geo-enriched store data
        - 'normalized': Currency-normalized data
        - 'cleaned': Final cleaned dataset
        - 'regional_summary': Regional aggregations
        - 'same_store_sales': Same-store sales analysis
        """
        results = {}
        logger.info(f"Starting transformation pipeline: {len(df)} rows")

        # Step 1: Geo-enrichment
        logger.info("Step 1/5: Geographic enrichment")
        enriched = self.geo_enricher.enrich_store_dataframe(df)
        if save_intermediates:
            self._save_intermediate(enriched, "enriched")
        results['enriched'] = enriched

        # Step 2: Currency normalization
        logger.info("Step 2/5: Currency normalization")
        normalized = self.currency_normalizer.normalize_dataframe(enriched)
        if save_intermediates:
            self._save_intermediate(normalized, "normalized")
        results['normalized'] = normalized

        # Step 3: Data cleaning
        logger.info("Step 3/5: Data cleaning")
        cleaned, quality_report = self.data_cleaner.clean(normalized)
        if save_intermediates:
            self._save_intermediate(cleaned, "cleaned")
        results['cleaned'] = cleaned
        results['quality_report'] = quality_report

        # Step 4: Regional aggregation
        logger.info("Step 4/5: Regional aggregation")
        regional = self.aggregator.aggregate_by_region(cleaned)
        if save_intermediates:
            self._save_intermediate(regional, "regional_aggregation")
        results['regional_summary'] = regional

        # Step 5: Same-store sales
        logger.info("Step 5/5: Same-store sales calculation")
        sss = self.aggregator.calculate_same_store_sales(cleaned)
        if save_intermediates:
            self._save_intermediate(sss, "same_store_sales")
        results['same_store_sales'] = sss

        logger.info("Transformation pipeline complete")
        return results

    def _save_intermediate(self, df: pd.DataFrame, name: str):
        """Save intermediate results to Parquet"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = self.output_dir / f"{name}_{timestamp}.parquet"
        df.to_parquet(output_path, compression='snappy')
        logger.info(f"Saved {name} to {output_path}")
