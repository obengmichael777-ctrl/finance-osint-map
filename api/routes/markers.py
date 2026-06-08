"""
FastAPI Routes for Map Markers
Provides REST API endpoints for interactive store map visualization.
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path
import logging
import json

from ..services.kpi_calculator import KPICalculator, StoreKPI, PerformanceTier

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Supermarket OSINT - Map Marker API",
    description="Interactive map markers for pan-Asian retail equity analysis",
    version="1.0.0"
)

# CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
kpi_calculator = KPICalculator()


# Pydantic models for request/response
class MarkerRequest(BaseModel):
    """Request parameters for marker data"""
    regions: Optional[List[str]] = None
    performance_tiers: Optional[List[str]] = None
    countries: Optional[List[str]] = None
    min_revenue: Optional[float] = None
    limit: int = Field(default=1000, le=5000)


class MarkerResponse(BaseModel):
    """Marker data response"""
    markers: List[Dict[str, Any]]
    total_count: int
    region_summary: Dict[str, Any]
    calculated_at: datetime
    filters_applied: Dict[str, Any]


class RefreshRequest(BaseModel):
    """KPI refresh request"""
    force: bool = False
    reference_date: Optional[datetime] = None


# In-memory KPI cache (in production, use Redis)
_kpi_cache: Dict[str, Any] = {}
_last_refresh: Optional[datetime] = None


@app.get("/")
async def root():
    """API root with available endpoints"""
    return {
        "service": "Supermarket OSINT Map API",
        "endpoints": {
            "/markers": "Get store markers for map",
            "/markers/{store_id}": "Get single store details",
            "/regions": "Get region summaries",
            "/refresh": "Refresh KPI calculations",
            "/alerts": "Get stores with active alerts",
            "/search": "Search stores by criteria"
        },
        "last_refresh": _last_refresh.isoformat() if _last_refresh else "Never"
    }


@app.get("/markers", response_model=MarkerResponse)
async def get_markers(
    region: Optional[str] = Query(None, description="Filter by economic region"),
    country: Optional[str] = Query(None, description="Filter by country"),
    tier: Optional[str] = Query(None, description="Filter by performance tier"),
    min_sss_growth: Optional[float] = Query(None, description="Minimum SSS growth"),
    limit: int = Query(1000, le=5000)
):
    """
    Get store markers for map visualization.

    Supports filtering by region, country, performance tier,
    and minimum same-store sales growth.
    """
    global _kpi_cache

    if not _kpi_cache:
        raise HTTPException(
            status_code=503,
            detail="KPI data not available. Please refresh first."
        )

    markers = _kpi_cache.get('markers', [])
    kpis = _kpi_cache.get('kpis', [])

    # Apply filters
    if region:
        markers = [m for m in markers if m['properties']['region'] == region]

    if country:
        markers = [m for m in markers if m['properties']['country'] == country]

    if tier:
        markers = [m for m in markers if m['properties']['tier'] == tier]

    if min_sss_growth is not None:
        filtered_kpis = [
            k for k in kpis
            if k.same_store_sales_growth >= min_sss_growth
        ]
        filtered_ids = {k.store_id for k in filtered_kpis}
        markers = [m for m in markers if m['id'] in filtered_ids]

    # Apply limit
    total_count = len(markers)
    markers = markers[:limit]

    return MarkerResponse(
        markers=markers,
        total_count=total_count,
        region_summary=_kpi_cache.get('region_summary', {}),
        calculated_at=_last_refresh,
        filters_applied={
            'region': region,
            'country': country,
            'tier': tier,
            'min_sss_growth': min_sss_growth
        }
    )


@app.get("/markers/{store_id}")
async def get_store_detail(store_id: str):
    """Get detailed information for a single store"""
    global _kpi_cache

    kpis = _kpi_cache.get('kpis', [])

    for kpi in kpis:
        if kpi.store_id == store_id:
            return {
                'store_id': kpi.store_id,
                'store_name': kpi.store_name,
                'location': {
                    'latitude': kpi.latitude,
                    'longitude': kpi.longitude,
                    'country': kpi.country,
                    'region': kpi.economic_region,
                    'urban_rural': kpi.urban_rural,
                    'nearest_city': kpi.nearest_city
                },
                'financials': {
                    'revenue_mtd_usd': kpi.revenue_mtd_usd,
                    'revenue_ytd_usd': kpi.revenue_ytd_usd,
                    'same_store_sales_growth': kpi.same_store_sales_growth,
                    'avg_basket_size_usd': kpi.avg_basket_size_usd
                },
                'competitive': {
                    'market_share_region': kpi.market_share_region,
                    'rank_in_region': kpi.rank_in_region,
                    'total_stores_in_region': kpi.total_stores_in_region,
                    'competition_density': kpi.competition_density
                },
                'performance': {
                    'tier': kpi.performance_tier.value,
                    'alerts': kpi.alert_flags
                },
                'calculated_at': kpi.calculated_at.isoformat()
            }

    raise HTTPException(status_code=404, detail="Store not found")


@app.get("/regions")
async def get_regions():
    """Get summary for all economic regions"""
    global _kpi_cache

    if not _kpi_cache:
        raise HTTPException(status_code=503, detail="Data not available")

    return _kpi_cache.get('region_summary', {})


@app.get("/alerts")
async def get_alerts(
    min_severity: str = Query("critical", description="Minimum alert severity")
):
    """
    Get stores with active alerts.

    Severity levels: critical, warning, info
    """
    global _kpi_cache

    kpis = _kpi_cache.get('kpis', [])

    alert_stores = []

    severity_order = {'critical': 0, 'warning': 1, 'info': 2}
    min_sev = severity_order.get(min_severity.lower(), 2)

    for kpi in kpis:
        if kpi.alert_flags:
            max_sev = 2  # info
            if 'CRITICAL' in str(kpi.performance_tier):
                max_sev = 0
            elif kpi.same_store_sales_growth < -0.05:
                max_sev = 1

            if max_sev <= min_sev:
                alert_stores.append({
                    'store_id': kpi.store_id,
                    'alerts': kpi.alert_flags,
                    'tier': kpi.performance_tier.value,
                    'sss_growth': kpi.same_store_sales_growth,
                    'revenue_mtd': kpi.revenue_mtd_usd,
                    'city': kpi.nearest_city
                })

    return {'alert_count': len(alert_stores), 'alerts': alert_stores}


@app.post("/refresh")
async def refresh_kpis(
    background_tasks: BackgroundTasks,
    request: RefreshRequest = RefreshRequest()
):
    """
    Trigger KPI recalculation.

    Runs in background to avoid timeout on large datasets.
    """
    global _kpi_cache, _last_refresh

    # Check if recent refresh exists
    if (
        not request.force
        and _last_refresh
        and datetime.now() - _last_refresh < timedelta(hours=1)
    ):
        return {
            'status': 'skipped',
            'message': 'KPIs were refreshed less than 1 hour ago',
            'last_refresh': _last_refresh.isoformat()
        }

    # Trigger background refresh
    background_tasks.add_task(
        _refresh_kpi_cache,
        request.reference_date
    )

    return {
        'status': 'accepted',
        'message': 'KPI refresh started in background',
        'estimated_completion': (
            datetime.now() + timedelta(minutes=2)
        ).isoformat()
    }


@app.get("/search")
async def search_stores(
    query: str = Query(..., description="Search term (store ID, city, etc.)"),
    field: str = Query("all", description="Field to search in")
):
    """Search for stores by various criteria"""
    global _kpi_cache

    kpis = _kpi_cache.get('kpis', [])
    results = []

    query_lower = query.lower()

    for kpi in kpis:
        match = False

        if field == "all" or field == "store_id":
            if query_lower in kpi.store_id.lower():
                match = True
        if field == "all" or field == "city":
            if query_lower in kpi.nearest_city.lower():
                match = True
        if field == "all" or field == "region":
            if query_lower in kpi.economic_region.lower():
                match = True

        if match:
            results.append({
                'store_id': kpi.store_id,
                'city': kpi.nearest_city,
                'region': kpi.economic_region,
                'tier': kpi.performance_tier.value,
                'sss_growth': kpi.same_store_sales_growth
            })

    return {'query': query, 'results': results[:50], 'total_found': len(results)}


async def _refresh_kpi_cache(reference_date: Optional[datetime] = None):
    """
    Background task to refresh KPI cache.

    In production, this would:
    1. Pull latest data from database
    2. Recalculate all KPIs
    3. Update Redis cache
    4. Notify websocket clients of updates
    """
    global _kpi_cache, _last_refresh

    logger.info("Starting KPI cache refresh")

    try:
        # Load data from database or file
        from etl.load.database import DatabaseManager

        # This would connect to your database
        # For now, load from transformed parquet
        data_path = Path("data/transformed")
        parquet_files = list(data_path.glob("cleaned_*.parquet"))

        if parquet_files:
            import pandas as pd
            df = pd.read_parquet(parquet_files[-1])  # Latest file

            kpis = kpi_calculator.calculate_store_kpis(df, reference_date)
            markers = kpi_calculator.get_marker_data(kpis)
            region_summary = kpi_calculator.get_region_summary(kpis)

            _kpi_cache = {
                'kpis': kpis,
                'markers': markers,
                'region_summary': region_summary
            }
            _last_refresh = datetime.now()

            logger.info(
                f"KPI cache refreshed: {len(markers)} markers, "
                f"{len(region_summary)} regions"
            )
        else:
            logger.warning("No transformed data found for KPI calculation")

    except Exception as e:
        logger.error(f"KPI refresh failed: {e}", exc_info=True)
