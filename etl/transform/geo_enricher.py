"""
Geographic Data Enricher for Retail Store Analysis
Adds administrative boundaries, demographic data, and spatial context
to store locations for pan-Asian equity research.

Financial Relevance: Geographic analysis enables:
- Same-store sales comparison within economic clusters
- Exposure analysis to regional economic conditions
- Competition density mapping
- Infrastructure development impact assessment
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import json
import logging
from enum import Enum
import requests
from functools import lru_cache
import time
from shapely.geometry import Point, Polygon
import geopandas as gpd
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class EconomicRegion(Enum):
    """Economic regions relevant for pan-Asian equity analysis"""
    GREATER_CHINA = "Greater China"  # Mainland, HK, Taiwan
    ASEAN_CORE = "ASEAN Core"  # Indonesia, Malaysia, Philippines, Singapore, Thailand
    ASEAN_FRONTIER = "ASEAN Frontier"  # Vietnam, Cambodia, Laos, Myanmar
    EAST_ASIA_DEVELOPED = "East Asia Developed"  # Japan, South Korea
    SOUTH_ASIA = "South Asia"  # India, Sri Lanka, Bangladesh
    OCEANIA = "Oceania"  # Australia, New Zealand


@dataclass
class StoreLocation:
    """Enhanced store location with geographic context"""
    store_id: str
    latitude: float
    longitude: float
    country: str
    city: Optional[str] = None
    administrative_area: Optional[str] = None  # State/Province/Prefecture
    economic_region: Optional[str] = None
    urban_rural_classification: Optional[str] = None
    population_density_km2: Optional[float] = None
    gdp_per_capita_usd: Optional[float] = None
    nearest_major_city: Optional[str] = None
    distance_to_nearest_city_km: Optional[float] = None
    cluster_id: Optional[int] = None  # For grouping nearby stores


class GeoEnricher:
    """
    Enriches store data with geographic context for financial analysis.

    In fund management, geographic analysis answers questions like:
    - What's our exposure to Chinese Tier 2 cities vs Tier 1?
    - How does ASEAN retail growth correlate with urbanization rates?
    - Are stores in special economic zones outperforming?
    """

    def __init__(self, cache_dir: Optional[Path] = None, use_openstreetmap: bool = False):
        self.cache_dir = cache_dir or Path("data/geo_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize geocoder for reverse geocoding
        if use_openstreetmap:
            self.geolocator = Nominatim(user_agent="retail_analysis_fund")
            self.reverse_geocode = RateLimiter(
                self.geolocator.reverse,
                min_delay_seconds=1.0
            )

        # Load reference data (in production, these would come from databases)
        self._load_reference_data()

    def _load_reference_data(self):
        """Load geographic reference data for enrichment"""
        # City coordinates database (simplified - in production, use full GeoNames database)
        self.city_coordinates = {
            # Japan
            'Tokyo': (35.6762, 139.6503),
            'Osaka': (34.6937, 135.5023),
            'Nagoya': (35.1815, 136.9066),
            'Fukuoka': (33.5904, 130.4017),
            'Sapporo': (43.0618, 141.3545),

            # China
            'Shanghai': (31.2304, 121.4737),
            'Beijing': (39.9042, 116.4074),
            'Guangzhou': (23.1291, 113.2644),
            'Shenzhen': (22.5431, 114.0579),
            'Chengdu': (30.5728, 104.0668),
            'Wuhan': (30.5928, 114.3055),
            'Hangzhou': (30.2741, 120.1551),
            'Nanjing': (32.0603, 118.7969),

            # South Korea
            'Seoul': (37.5665, 126.9780),
            'Busan': (35.1796, 129.0756),
            'Incheon': (37.4563, 126.7052),

            # ASEAN
            'Singapore': (1.3521, 103.8198),
            'Bangkok': (13.7563, 100.5018),
            'Jakarta': (-6.2088, 106.8456),
            'Kuala Lumpur': (3.1390, 101.6869),
            'Manila': (14.5995, 120.9842),
            'Ho Chi Minh City': (10.8231, 106.6297),
            'Hanoi': (21.0278, 105.8342),

            # India
            'Mumbai': (19.0760, 72.8777),
            'Delhi': (28.6139, 77.2090),
            'Bangalore': (12.9716, 77.5946),
            'Chennai': (13.0827, 80.2707),

            # Oceania
            'Sydney': (-33.8688, 151.2093),
            'Melbourne': (-37.8136, 144.9631),
        }

        # GDP per capita reference (2023 estimates, USD)
        self.gdp_per_capita = {
            'JP': 33900, 'KR': 33100, 'SG': 82800,
            'CN': 12700, 'HK': 49300, 'TW': 33100,
            'TH': 7200, 'MY': 12000, 'ID': 4800,
            'PH': 3900, 'VN': 4300, 'IN': 2500,
            'AU': 65100, 'NZ': 48500,
        }

        # Urbanization rates by country (World Bank 2022)
        self.urbanization_rates = {
            'JP': 0.92, 'KR': 0.81, 'SG': 1.00,
            'CN': 0.64, 'HK': 1.00, 'TW': 0.79,
            'TH': 0.52, 'MY': 0.78, 'ID': 0.57,
            'PH': 0.48, 'VN': 0.38, 'IN': 0.36,
            'AU': 0.86, 'NZ': 0.87,
        }

    def enrich_store_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Enrich a DataFrame of store data with geographic context.

        Input DataFrame must have: store_id, latitude, longitude, country
        """
        if df.empty:
            logger.warning("Empty DataFrame provided for geo-enrichment")
            return df

        # Ensure required columns exist
        required_cols = ['store_id', 'latitude', 'longitude', 'country']
        missing_cols = set(required_cols) - set(df.columns)
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")

        # Create copy for enrichment
        enriched = df.copy()

        # 1. Assign economic regions
        enriched['economic_region'] = enriched['country'].map(
            self._get_economic_region
        )

        # 2. Find nearest major city
        enriched['nearest_major_city'] = enriched.apply(
            lambda row: self._find_nearest_city(row['latitude'], row['longitude']),
            axis=1
        )

        # 3. Calculate distance to nearest major city
        enriched['distance_to_city_km'] = enriched.apply(
            lambda row: self._calculate_city_distance(
                row['latitude'], row['longitude'], row['nearest_major_city']
            ),
            axis=1
        )

        # 4. Classify urban/rural
        enriched['urban_rural'] = enriched['distance_to_city_km'].apply(
            self._classify_urban_rural
        )

        # 5. Add GDP per capita context
        enriched['gdp_per_capita_usd'] = enriched['country'].map(
            self._get_gdp_per_capita
        )

        # 6. Add urbanization rate
        enriched['urbanization_rate'] = enriched['country'].map(
            self._get_urbanization_rate
        )

        # 7. Generate store clusters
        enriched = self._cluster_stores(enriched)

        # 8. Calculate competition density
        enriched = self._calculate_competition_density(enriched)

        logger.info(
            f"Geo-enriched {len(enriched)} stores with "
            f"{len(enriched.columns) - len(df.columns)} new features"
        )

        return enriched

    def _get_economic_region(self, country_code: str) -> str:
        """Map country code to economic region"""
        region_map = {
            'CN': EconomicRegion.GREATER_CHINA.value,
            'HK': EconomicRegion.GREATER_CHINA.value,
            'TW': EconomicRegion.GREATER_CHINA.value,
            'JP': EconomicRegion.EAST_ASIA_DEVELOPED.value,
            'KR': EconomicRegion.EAST_ASIA_DEVELOPED.value,
            'SG': EconomicRegion.ASEAN_CORE.value,
            'TH': EconomicRegion.ASEAN_CORE.value,
            'MY': EconomicRegion.ASEAN_CORE.value,
            'ID': EconomicRegion.ASEAN_CORE.value,
            'PH': EconomicRegion.ASEAN_CORE.value,
            'VN': EconomicRegion.ASEAN_FRONTIER.value,
            'IN': EconomicRegion.SOUTH_ASIA.value,
            'AU': EconomicRegion.OCEANIA.value,
            'NZ': EconomicRegion.OCEANIA.value,
        }
        return region_map.get(country_code, 'Other')

    def _find_nearest_city(self, lat: float, lon: float) -> str:
        """Find the nearest major city from reference database"""
        min_distance = float('inf')
        nearest_city = 'Unknown'

        for city, (city_lat, city_lon) in self.city_coordinates.items():
            distance = geodesic((lat, lon), (city_lat, city_lon)).kilometers
            if distance < min_distance:
                min_distance = distance
                nearest_city = city

        return nearest_city

    def _calculate_city_distance(
        self, lat: float, lon: float, city_name: str
    ) -> float:
        """Calculate distance from store to its nearest city"""
        if city_name in self.city_coordinates:
            city_lat, city_lon = self.city_coordinates[city_name]
            return geodesic((lat, lon), (city_lat, city_lon)).kilometers
        return float('inf')

    def _classify_urban_rural(self, distance_km: float) -> str:
        """
        Classify store location based on distance to nearest city.

        Thresholds calibrated for Asian urban geography:
        - Within 30km: Urban core (typical Asian city extent)
        - 30-100km: Suburban/peri-urban (commuter belt)
        - 100-300km: Rural town influence
        - >300km: Remote/rural
        """
        if distance_km <= 30:
            return 'Urban'
        elif distance_km <= 100:
            return 'Suburban'
        elif distance_km <= 300:
            return 'Rural'
        else:
            return 'Remote'

    def _get_gdp_per_capita(self, country_code: str) -> Optional[float]:
        """Get GDP per capita for country context"""
        return self.gdp_per_capita.get(country_code)

    def _get_urbanization_rate(self, country_code: str) -> Optional[float]:
        """Get urbanization rate for country context"""
        return self.urbanization_rates.get(country_code)

    def _cluster_stores(self, df: pd.DataFrame, radius_km: float = 5.0) -> pd.DataFrame:
        """
        Group stores into spatial clusters for competitive analysis.

        Uses DBSCAN-like approach: stores within radius_km form a cluster.
        """
        from sklearn.cluster import DBSCAN

        if len(df) < 2:
            df['cluster_id'] = 0
            return df

        # Convert lat/lon to radians for DBSCAN
        coords = df[['latitude', 'longitude']].values
        coords_rad = np.radians(coords)

        # DBSCAN with haversine metric (accounts for Earth's curvature)
        # eps in kilometers, converted to radians
        kms_per_radian = 6371.0088
        epsilon = radius_km / kms_per_radian

        clustering = DBSCAN(
            eps=epsilon,
            min_samples=2,
            metric='haversine'
        ).fit(coords_rad)

        df['cluster_id'] = clustering.labels_

        n_clusters = len(set(clustering.labels_)) - (1 if -1 in clustering.labels_ else 0)
        n_noise = list(clustering.labels_).count(-1)

        logger.info(
            f"Identified {n_clusters} store clusters, "
            f"{n_noise} stores as isolated locations"
        )

        return df

    def _calculate_competition_density(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate competition density - number of stores within 10km radius.

        High competition density areas may indicate:
        - Market saturation (limited growth)
        - Agglomeration benefits (foot traffic)
        """
        df['stores_within_10km'] = 0

        for i, store in df.iterrows():
            count = 0
            for j, other in df.iterrows():
                if i != j:
                    dist = geodesic(
                        (store['latitude'], store['longitude']),
                        (other['latitude'], other['longitude'])
                    ).kilometers
                    if dist <= 10:
                        count += 1
            df.at[i, 'stores_within_10km'] = count

        return df

    def generate_geojson(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Generate GeoJSON for map visualization.

        Used by the marker generation layer for interactive maps.
        """
        features = []

        for _, row in df.iterrows():
            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [float(row['longitude']), float(row['latitude'])]
                },
                'properties': {
                    'store_id': row['store_id'],
                    'country': row.get('country', ''),
                    'economic_region': row.get('economic_region', ''),
                    'urban_rural': row.get('urban_rural', ''),
                    'nearest_city': row.get('nearest_major_city', ''),
                    'distance_to_city_km': row.get('distance_to_city_km', ''),
                    'cluster_id': row.get('cluster_id', -1),
                    'competition_density': row.get('stores_within_10km', 0),
                }
            }
            features.append(feature)

        return {
            'type': 'FeatureCollection',
            'features': features
        }

    def save_geojson(self, df: pd.DataFrame, output_path: Path):
        """Save enriched data as GeoJSON file"""
        geojson = self.generate_geojson(df)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved GeoJSON to {output_path}")
