"""
Tests for Map Marker API
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
import json

from api.routes.markers import app, _kpi_cache, _last_refresh
from api.services.kpi_calculator import StoreKPI, PerformanceTier

client = TestClient(app)


@pytest.fixture
def sample_kpis():
    """Create sample KPIs for testing"""
    kpis = []

    for i in range(5):
        kpi = StoreKPI(
            store_id=f"store_{i}",
            store_name=f"Test Store {i}",
            latitude=35.0 + i * 0.1,
            longitude=139.0 + i * 0.1,
            country="JP" if i < 3 else "CN",
            economic_region="East Asia Developed" if i < 3 else "Greater China",
            urban_rural="Urban" if i % 2 == 0 else "Suburban",
            nearest_city="Tokyo" if i < 3 else "Shanghai",
            revenue_mtd_usd=100000 * (i + 1),
            revenue_ytd_usd=500000 * (i + 1),
            revenue_growth_yoy=0.05 * (i - 2),
            same_store_sales_growth=0.05 * (i - 2),
            transaction_count_mtd=1000 * (i + 1),
            avg_basket_size_usd=50 + i * 10,
            avg_items_per_transaction=3.5 + i * 0.5,
            market_share_region=5.0 / (i + 1),
            competition_density=i * 3,
            rank_in_region=i + 1,
            total_stores_in_region=5,
            performance_tier=PerformanceTier.OUTPERFORMING if i == 0 else PerformanceTier.MARKET_PERFORM,
            tooltip_summary=f"Store {i} tooltip",
            alert_flags=["SSS_DECLINE"] if i == 4 else [],
            calculated_at=datetime.now()
        )
        kpis.append(kpi)

    return kpis


@pytest.fixture(autouse=True)
def setup_cache(sample_kpis):
    """Set up KPI cache before each test"""
    global _kpi_cache, _last_refresh

    from api.services.kpi_calculator import KPICalculator
    calc = KPICalculator()

    _kpi_cache = {
        'kpis': sample_kpis,
        'markers': calc.get_marker_data(sample_kpis),
        'region_summary': calc.get_region_summary(sample_kpis)
    }
    _last_refresh = datetime.now()

    yield

    _kpi_cache = {}
    _last_refresh = None


class TestMarkerEndpoints:
    """Test marker-related API endpoints"""

    def test_get_markers(self):
        """Test getting all markers"""
        response = client.get("/markers")
        assert response.status_code == 200

        data = response.json()
        assert 'markers' in data
        assert len(data['markers']) == 5
        assert 'total_count' in data

    def test_filter_by_region(self):
        """Test filtering markers by region"""
        response = client.get("/markers?region=East%20Asia%20Developed")
        assert response.status_code == 200

        data = response.json()
        assert len(data['markers']) == 3

        for marker in data['markers']:
            assert marker['properties']['region'] == 'East Asia Developed'

    def test_filter_by_tier(self):
        """Test filtering by performance tier"""
        response = client.get("/markers?tier=outperforming")
        assert response.status_code == 200

        data = response.json()
        assert len(data['markers']) == 1
        assert data['markers'][0]['properties']['tier'] == 'outperforming'

    def test_filter_by_sss_growth(self):
        """Test filtering by minimum SSS growth"""
        response = client.get("/markers?min_sss_growth=0.0")
        assert response.status_code == 200

        data = response.json()
        # Only stores with positive growth
        for marker in data['markers']:
            store_id = marker['id']
            # Find corresponding KPI
            kpi = next(
                k for k in _kpi_cache['kpis']
                if k.store_id == store_id
            )
            assert kpi.same_store_sales_growth >= 0.0

    def test_limit(self):
        """Test result limiting"""
        response = client.get("/markers?limit=2")
        assert response.status_code == 200

        data = response.json()
        assert len(data['markers']) == 2
        assert data['total_count'] == 5  # Total before limit

    def test_get_store_detail(self):
        """Test getting single store details"""
        response = client.get("/markers/store_0")
        assert response.status_code == 200

        data = response.json()
        assert data['store_id'] == 'store_0'
        assert 'financials' in data
        assert 'competitive' in data
        assert 'performance' in data

    def test_store_not_found(self):
        """Test 404 for non-existent store"""
        response = client.get("/markers/nonexistent")
        assert response.status_code == 404

    def test_get_regions(self):
        """Test region summary endpoint"""
        response = client.get("/regions")
        assert response.status_code == 200

        data = response.json()
        assert 'East Asia Developed' in data
        assert 'Greater China' in data

    def test_get_alerts(self):
        """Test alerts endpoint"""
        response = client.get("/alerts")
        assert response.status_code == 200

        data = response.json()
        assert data['alert_count'] >= 1

    def test_search_stores(self):
        """Test store search"""
        response = client.get("/search?query=store_1")
        assert response.status_code == 200

        data = response.json()
        assert len(data['results']) >= 1
        assert 'store_1' in [r['store_id'] for r in data['results']]

    def test_search_by_city(self):
        """Test search by city name"""
        response = client.get("/search?query=Tokyo&field=city")
        assert response.status_code == 200

        data = response.json()
        assert len(data['results']) == 3

    def test_no_cache_error(self):
        """Test error when no cache available"""
        global _kpi_cache
        _kpi_cache = {}

        response = client.get("/markers")
        assert response.status_code == 503

    def test_health_check(self):
        """Test API health check"""
        response = client.get("/")
        assert response.status_code == 200
        assert 'endpoints' in response.json()


class TestRefreshEndpoint:
    """Test KPI refresh functionality"""

    def test_refresh_request(self):
        """Test refresh trigger"""
        response = client.post("/refresh")
        assert response.status_code == 200

        data = response.json()
        assert data['status'] in ['accepted', 'skipped']
