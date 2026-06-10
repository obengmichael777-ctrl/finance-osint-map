"""
Supermarket OSINT - Investment Dashboard
Pan-Asian Retail Analytics for Equity Research

Usage:
    streamlit run dashboard/app.py

Features:
    - Interactive map with color-coded store performance
    - Same-store sales growth analysis
    - Currency exposure matrix
    - Regional performance comparison
    - Export to CSV/Excel for investment committees
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import folium
from folium.plugins import MarkerCluster, HeatMap, Fullscreen
from streamlit_folium import st_folium
from datetime import datetime, timedelta
from pathlib import Path
import json
import warnings

warnings.filterwarnings('ignore')

# ============================================================================
# PAGE CONFIGURATION
# ============================================================================
st.set_page_config(
    page_title="Pan-Asian Retail Analytics",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================================
# CUSTOM CSS
# ============================================================================
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #64748b;
        margin-top: 0;
        margin-bottom: 1rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 12px;
        padding: 1.5rem;
        color: white;
    }
    .alert-critical {
        background-color: #fee2e2;
        border-left: 4px solid #ef4444;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .alert-warning {
        background-color: #fef3c7;
        border-left: 4px solid #f59e0b;
        padding: 0.75rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .store-tooltip {
        font-family: 'Courier New', monospace;
        font-size: 0.75rem;
    }
    .stMetric {
        background-color: #f8fafc;
        border-radius: 8px;
        padding: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# DATA LOADING FUNCTIONS
# ============================================================================

@st.cache_data(ttl=3600)
def load_store_data():
    """
    Load transformed data from parquet files.
    Falls back to test data if transformed data not available.
    """
    transformed_dir = Path("data/transformed")

    enriched_files = sorted(transformed_dir.glob("enriched_*.parquet"))
    cleaned_files = sorted(transformed_dir.glob("cleaned_*.parquet"))

    if enriched_files and cleaned_files:
        enriched_df = pd.read_parquet(enriched_files[-1])
        cleaned_df = pd.read_parquet(cleaned_files[-1])
        return enriched_df, cleaned_df

    # Fallback to test data
    test_file = Path("tests/fixtures/combined_test_data.parquet")
    if test_file.exists():
        df = pd.read_parquet(test_file)
        st.info("📋 Using test data. Run the ETL pipeline for production data.")
        return df, df

    return None, None


@st.cache_data(ttl=3600)
def load_kpi_data():
    """Load KPI data from DuckDB if available"""
    try:
        from etl.load.database import DatabaseManager, DatabaseConfig, DatabaseBackend

        config = DatabaseConfig(
            backend=DatabaseBackend.DUCKDB,
            duckdb_path=Path('data/retail.db')
        )
        db = DatabaseManager(primary_config=config, auto_failover=False)
        kpis = db.active_connection.query("SELECT * FROM store_kpis")
        return kpis if not kpis.empty else None
    except Exception:
        return None


@st.cache_data(ttl=300)
def calculate_dashboard_metrics(df):
    """Calculate summary metrics for the dashboard header"""
    if df is None or df.empty:
        return {}

    metrics = {}

    # Revenue - check for normalized column first
    if 'revenue_usd' in df.columns:
        metrics['total_revenue'] = df['revenue_usd'].sum()
        metrics['avg_transaction'] = df['revenue_usd'].mean()
    elif 'Net Sales' in df.columns:
        metrics['total_revenue'] = df['Net Sales'].sum()
        metrics['avg_transaction'] = df['Net Sales'].mean()
    else:
        metrics['total_revenue'] = 0
        metrics['avg_transaction'] = 0

    # Store and transaction counts
    if 'store_id' in df.columns:
        metrics['total_stores'] = df['store_id'].nunique()
    else:
        metrics['total_stores'] = len(df)

    metrics['total_transactions'] = len(df)

    # Same-store sales growth
    if 'same_store_sales_growth' in df.columns:
        metrics['avg_sss_growth'] = df['same_store_sales_growth'].mean()
    else:
        # Estimate from data if possible
        metrics['avg_sss_growth'] = 0.03  # Default placeholder

    # Regional data
    if 'economic_region' in df.columns:
        metrics['num_regions'] = df['economic_region'].nunique()
    else:
        metrics['num_regions'] = 0

    # Currency exposure count
    if 'original_currency' in df.columns:
        metrics['num_currencies'] = df['original_currency'].nunique()
    else:
        metrics['num_currencies'] = 0

    return metrics


# ============================================================================
# MAP CREATION FUNCTIONS
# ============================================================================

def create_store_map(map_df, height=600):
    """
    Create interactive Folium map with store markers.

    Features:
    - Color-coded markers by SSS growth or revenue
    - Marker clustering for performance
    - Rich HTML tooltips
    - Revenue density heatmap layer
    - Full screen support
    """
    if map_df is None or map_df.empty:
        return folium.Map(location=[20, 105], zoom_start=4)

    # Calculate map center
    if 'latitude' in map_df.columns and 'longitude' in map_df.columns:
        center_lat = map_df['latitude'].mean()
        center_lon = map_df['longitude'].mean()
    else:
        center_lat, center_lon = 20, 105

    # Create base map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=4,
        tiles='CartoDB positron',
        control_scale=True
    )

    # Add fullscreen button
    Fullscreen().add_to(m)

    # Performance tier colors
    tier_colors = {
        'outperforming': '#22c55e',
        'market_perform': '#3b82f6',
        'underperforming': '#f59e0b',
        'critical': '#ef4444',
    }

    # Create marker cluster
    marker_cluster = MarkerCluster(
        name='Store Performance',
        overlay=True,
        control=True
    )

    # Add markers for each store
    for idx, row in map_df.iterrows():
        # Skip rows without coordinates
        if pd.isna(row.get('latitude')) or pd.isna(row.get('longitude')):
            continue

        # Determine marker color
        color = '#3b82f6'  # Default blue

        # Check for performance tier
        if 'performance_tier' in row and row['performance_tier'] in tier_colors:
            color = tier_colors[row['performance_tier']]
        # Or color by SSS growth
        elif 'same_store_sales_growth' in row:
            growth = row['same_store_sales_growth']
            if growth > 0.10:
                color = '#22c55e'
            elif growth > 0:
                color = '#3b82f6'
            elif growth > -0.05:
                color = '#f59e0b'
            else:
                color = '#ef4444'
        # Or color by revenue
        elif 'revenue_usd' in row:
            revenue = row['revenue_usd']
            if revenue > 1000000:
                color = '#22c55e'
            elif revenue > 500000:
                color = '#3b82f6'
            elif revenue > 100000:
                color = '#f59e0b'
            else:
                color = '#ef4444'

        # Build store identifier
        store_id = row.get('store_id', f'Store_{idx}')
        city = row.get('nearest_city', row.get('City', 'Unknown'))
        country = row.get('country', row.get('Country', ''))
        region = row.get('economic_region', row.get('region', ''))

        # Revenue value
        revenue = row.get('revenue_usd', row.get('Net Sales', 0))
        sss_growth = row.get('same_store_sales_growth', None)

        # Create tooltip HTML
        tooltip_html = f"""
        <div style="font-family:Arial,sans-serif;padding:12px;min-width:260px;">
            <h4 style="margin:0 0 10px 0;color:{color};border-bottom:2px solid {color};padding-bottom:5px;">
                🏪 {store_id}
            </h4>
            <table style="width:100%;font-size:12px;border-collapse:collapse;">
                <tr>
                    <td style="color:#64748b;padding:3px;">📍 Location</td>
                    <td style="padding:3px;"><strong>{city}, {country}</strong></td>
                </tr>
                <tr>
                    <td style="color:#64748b;padding:3px;">🌏 Region</td>
                    <td style="padding:3px;">{region}</td>
                </tr>
                <tr>
                    <td style="color:#64748b;padding:3px;">💰 Revenue (USD)</td>
                    <td style="padding:3px;"><strong>${revenue:,.0f}</strong></td>
                </tr>
            """

        if sss_growth is not None:
            growth_color = '#22c55e' if sss_growth > 0 else '#ef4444'
            tooltip_html += f"""
                <tr>
                    <td style="color:#64748b;padding:3px;">📈 SSS Growth</td>
                    <td style="padding:3px;color:{growth_color};"><strong>{sss_growth:+.1%}</strong></td>
                </tr>
            """

        # Add competition info if available
        competition = row.get('competition_density', row.get('stores_within_10km', None))
        if competition is not None:
            tooltip_html += f"""
                <tr>
                    <td style="color:#64748b;padding:3px;">🏢 Competition</td>
                    <td style="padding:3px;">{int(competition)} nearby</td>
                </tr>
            """

        # Add urban/rural if available
        urban_rural = row.get('urban_rural', None)
        if urban_rural:
            tooltip_html += f"""
                <tr>
                    <td style="color:#64748b;padding:3px;">🏘️ Type</td>
                    <td style="padding:3px;">{urban_rural}</td>
                </tr>
            """

        tooltip_html += """
            </table>
        </div>
        """

        # Create popup with more detail
        popup_html = f"""
        <div style="font-family:Arial,sans-serif;padding:15px;max-width:350px;">
            <h3 style="color:{color};margin-top:0;">{store_id}</h3>
            <hr>
            <p><strong>📍 City:</strong> {city}</p>
            <p><strong>🌍 Country:</strong> {country}</p>
            <p><strong>🌏 Region:</strong> {region}</p>
            <hr>
            <h4>Financial Summary</h4>
            <p><strong>Revenue:</strong> ${revenue:,.0f}</p>
            """

        if sss_growth is not None:
            popup_html += f"<p><strong>SSS Growth:</strong> {sss_growth:+.1%}</p>"

        if 'gdp_per_capita_usd' in row:
            popup_html += f"<p><strong>GDP/Capita:</strong> ${row['gdp_per_capita_usd']:,.0f}</p>"

        popup_html += """
            </div>
        """

        # Add marker
        folium.CircleMarker(
            location=[row['latitude'], row['longitude']],
            radius=8,
            popup=folium.Popup(popup_html, max_width=380),
            tooltip=folium.Tooltip(tooltip_html),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            weight=2,
        ).add_to(marker_cluster)

    marker_cluster.add_to(m)

    # Add heatmap layer for revenue density
    revenue_col = None
    if 'revenue_usd' in map_df.columns:
        revenue_col = 'revenue_usd'
    elif 'Net Sales' in map_df.columns:
        revenue_col = 'Net Sales'

    if revenue_col:
        heat_data = []
        for _, row in map_df.iterrows():
            if pd.notna(row.get('latitude')) and pd.notna(row.get('longitude')):
                heat_data.append([
                    row['latitude'],
                    row['longitude'],
                    float(row[revenue_col]) / 1000
                ])

        if heat_data:
            HeatMap(
                heat_data,
                name='Revenue Density',
                radius=25,
                blur=15,
                max_zoom=1,
                gradient={0.2: 'blue', 0.4: 'cyan', 0.6: 'lime', 0.8: 'yellow', 1.0: 'red'}
            ).add_to(m)

    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

    return m


def create_pydeck_map(map_df, height=600):
    """
    Alternative: High-performance PyDeck map for larger datasets.
    Uses WebGL for smooth rendering of 10,000+ points.
    """
    if map_df is None or map_df.empty:
        return None

    import pydeck as pdk

    # Determine color column
    if 'same_store_sales_growth' in map_df.columns:
        # Color by growth
        def growth_color(growth):
            if pd.isna(growth):
                return [150, 150, 150]
            elif growth > 0.10:
                return [34, 197, 94]
            elif growth > 0:
                return [59, 130, 246]
            elif growth > -0.05:
                return [245, 158, 11]
            else:
                return [239, 68, 68]

        map_df = map_df.copy()
        map_df['color_rgb'] = map_df['same_store_sales_growth'].apply(growth_color)
    else:
        map_df = map_df.copy()
        map_df['color_rgb'] = [[59, 130, 246]] * len(map_df)

    # Create scatter layer
    scatter_layer = pdk.Layer(
        'ScatterplotLayer',
        data=map_df,
        get_position=['longitude', 'latitude'],
        get_fill_color='color_rgb',
        get_radius=50000,
        pickable=True,
        opacity=0.6,
        stroked=True,
        filled=True,
        radius_scale=1,
        radius_min_pixels=4,
        radius_max_pixels=60,
    )

    # Set view state
    view_state = pdk.ViewState(
        latitude=map_df['latitude'].mean() if 'latitude' in map_df.columns else 20,
        longitude=map_df['longitude'].mean() if 'longitude' in map_df.columns else 105,
        zoom=4,
        pitch=0,
    )

    # Tooltip
    tooltip = {
        "html": """
        <div style="background:white;padding:10px;border-radius:5px;border:1px solid #ddd;">
            <b>{store_id}</b><br/>
            Revenue: ${revenue_usd:,.0f}<br/>
            Region: {economic_region}
        </div>
        """,
        "style": {"backgroundColor": "white", "color": "black"}
    }

    return pdk.Deck(
        layers=[scatter_layer],
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style='mapbox://styles/mapbox/light-v10',
    )


# ============================================================================
# CHART CREATION FUNCTIONS
# ============================================================================

def create_revenue_by_region_chart(df):
    """Horizontal bar chart: Revenue by Economic Region"""
    if 'economic_region' not in df.columns:
        return None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None

    region_revenue = df.groupby('economic_region')[revenue_col].sum().sort_values(ascending=True)

    fig = go.Figure(data=[
        go.Bar(
            x=region_revenue.values,
            y=region_revenue.index,
            orientation='h',
            marker=dict(
                color=region_revenue.values,
                colorscale='Blues',
                showscale=True,
                colorbar=dict(title='Revenue (USD)')
            ),
            text=[f'${x:,.0f}' for x in region_revenue.values],
            textposition='outside',
            hovertemplate='%{y}: $%{x:,.0f}<extra></extra>'
        )
    ])

    fig.update_layout(
        title='Total Revenue by Economic Region',
        xaxis_title='Revenue (USD)',
        yaxis_title='',
        height=400,
        margin=dict(l=20, r=20, t=50, b=20),
        showlegend=False,
        plot_bgcolor='white',
    )

    return fig


def create_sss_growth_chart(df):
    """Scatter plot: SSS Growth vs Competition Density"""
    if 'same_store_sales_growth' not in df.columns:
        return None

    # Prepare data
    plot_df = df.copy()

    # Use competition_density or stores_within_10km for x-axis
    x_col = None
    if 'competition_density' in plot_df.columns:
        x_col = 'competition_density'
    elif 'stores_within_10km' in plot_df.columns:
        x_col = 'stores_within_10km'

    color_col = 'economic_region' if 'economic_region' in plot_df.columns else None
    size_col = 'revenue_usd' if 'revenue_usd' in plot_df.columns else None

    fig = px.scatter(
        plot_df,
        x=x_col if x_col else plot_df.index,
        y='same_store_sales_growth',
        color=color_col,
        size=size_col,
        hover_name='store_id' if 'store_id' in plot_df.columns else None,
        title='Same-Store Sales Growth vs Competition',
        labels={
            x_col: 'Nearby Competitors' if x_col else 'Index',
            'same_store_sales_growth': 'SSS Growth',
            'economic_region': 'Region'
        },
        color_discrete_sequence=px.colors.qualitative.Set2,
    )

    # Add reference lines
    fig.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.5,
                  annotation_text="Zero Growth")

    if x_col:
        fig.add_vline(x=5, line_dash="dash", line_color="orange", opacity=0.5,
                      annotation_text="High Competition")

    fig.update_layout(
        height=450,
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor='white',
    )

    return fig


def create_currency_exposure_chart(df):
    """Treemap showing currency exposure"""
    if 'original_currency' not in df.columns:
        return None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None

    currency_data = df.groupby('original_currency').agg(
        revenue=(revenue_col, 'sum'),
        stores=('store_id', 'nunique') if 'store_id' in df.columns else ('original_currency', 'count')
    ).reset_index()

    currency_data['percentage'] = currency_data['revenue'] / currency_data['revenue'].sum() * 100

    fig = px.treemap(
        currency_data,
        path=['original_currency'],
        values='revenue',
        color='percentage',
        color_continuous_scale='RdYlGn',
        title='Currency Exposure Analysis',
        hover_data={
            'revenue': ':$,.0f',
            'percentage': ':.1f%',
            'stores': True
        }
    )

    fig.update_layout(
        height=400,
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


def create_performance_heatmap(df):
    """Heatmap: Revenue by Region and Urban/Rural classification"""
    if 'economic_region' not in df.columns or 'urban_rural' not in df.columns:
        return None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None

    pivot_data = df.pivot_table(
        values=revenue_col,
        index='economic_region',
        columns='urban_rural',
        aggfunc='sum',
        fill_value=0
    )

    fig = go.Figure(data=go.Heatmap(
        z=pivot_data.values,
        x=pivot_data.columns.tolist(),
        y=pivot_data.index.tolist(),
        colorscale='RdYlGn',
        text=[[f'${v:,.0f}' for v in row] for row in pivot_data.values],
        texttemplate='%{text}',
        textfont={"size": 11},
        hoverongaps=False,
        hovertemplate='Region: %{y}<br>Type: %{x}<br>Revenue: %{text}<extra></extra>'
    ))

    fig.update_layout(
        title='Revenue Heatmap: Region × Urban/Rural',
        xaxis_title='Location Type',
        yaxis_title='Economic Region',
        height=350,
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


def create_time_series_chart(df, date_col='date'):
    """Time series of daily revenue with moving averages"""
    if date_col not in df.columns:
        return None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None

    # Ensure datetime
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # Aggregate by date
    daily = df.groupby(df[date_col].dt.date)[revenue_col].sum().reset_index()
    daily.columns = ['date', 'revenue']
    daily['date'] = pd.to_datetime(daily['date'])

    # Calculate moving averages
    daily['MA_7'] = daily['revenue'].rolling(window=7, min_periods=1).mean()
    daily['MA_30'] = daily['revenue'].rolling(window=30, min_periods=1).mean()

    fig = go.Figure()

    # Daily bars
    fig.add_trace(go.Bar(
        x=daily['date'],
        y=daily['revenue'],
        name='Daily Revenue',
        marker_color='#94a3b8',
        opacity=0.5,
        hovertemplate='%{x}: $%{y:,.0f}<extra></extra>'
    ))

    # 7-day moving average
    fig.add_trace(go.Scatter(
        x=daily['date'],
        y=daily['MA_7'],
        mode='lines',
        name='7-Day MA',
        line=dict(color='#f59e0b', width=2),
        hovertemplate='7-Day MA: $%{y:,.0f}<extra></extra>'
    ))

    # 30-day moving average
    fig.add_trace(go.Scatter(
        x=daily['date'],
        y=daily['MA_30'],
        mode='lines',
        name='30-Day MA',
        line=dict(color='#ef4444', width=2),
        hovertemplate='30-Day MA: $%{y:,.0f}<extra></extra>'
    ))

    fig.update_layout(
        title='Daily Revenue Trend with Moving Averages',
        xaxis_title='Date',
        yaxis_title='Revenue (USD)',
        height=400,
        hovermode='x unified',
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor='white',
        legend=dict(
            orientation='h',
            yanchor='bottom',
            y=1.02,
            xanchor='right',
            x=1
        )
    )

    return fig


def create_region_time_series(df, date_col='date'):
    """Time series by economic region"""
    if date_col not in df.columns or 'economic_region' not in df.columns:
        return None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    region_ts = df.groupby([df[date_col].dt.date, 'economic_region'])[revenue_col].sum().reset_index()
    region_ts.columns = ['date', 'region', 'revenue']
    region_ts['date'] = pd.to_datetime(region_ts['date'])

    fig = px.line(
        region_ts,
        x='date',
        y='revenue',
        color='region',
        title='Revenue by Region Over Time',
        labels={'revenue': 'Revenue (USD)', 'date': 'Date', 'region': 'Region'},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )

    fig.update_layout(
        height=400,
        hovermode='x unified',
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor='white',
        legend=dict(
            orientation='h',
            yanchor='bottom',
            y=1.02,
            xanchor='right',
            x=1
        )
    )

    return fig


def create_store_ranking_table(df, n=10):
    """Create top and bottom store rankings"""
    if 'store_id' not in df.columns:
        return None, None

    revenue_col = 'revenue_usd' if 'revenue_usd' in df.columns else 'Net Sales'
    if revenue_col not in df.columns:
        return None, None

    # Aggregate by store
    agg_dict = {
        revenue_col: 'sum',
    }

    if 'economic_region' in df.columns:
        agg_dict['economic_region'] = 'first'
    if 'nearest_city' in df.columns:
        agg_dict['nearest_city'] = 'first'
    elif 'City' in df.columns:
        agg_dict['City'] = 'first'
    if 'country' in df.columns:
        agg_dict['country'] = 'first'

    store_perf = df.groupby('store_id').agg(agg_dict).reset_index()

    # Add SSS growth if available
    if 'same_store_sales_growth' in df.columns:
        sss = df.groupby('store_id')['same_store_sales_growth'].mean().reset_index()
        store_perf = store_perf.merge(sss, on='store_id', how='left')

    store_perf = store_perf.sort_values(revenue_col, ascending=False)

    top_stores = store_perf.head(n)
    bottom_stores = store_perf.tail(n)

    return top_stores, bottom_stores


# ============================================================================
# FILTER APPLICATION
# ============================================================================

def apply_filters(df, filters):
    """Apply sidebar filters to dataframe"""
    filtered = df.copy()

    if filters.get('region') and filters['region'] != 'All':
        region_col = 'economic_region' if 'economic_region' in filtered.columns else 'region'
        if region_col in filtered.columns:
            filtered = filtered[filtered[region_col] == filters['region']]

    if filters.get('country') and filters['country'] != 'All':
        country_col = 'country' if 'country' in filtered.columns else 'Country'
        if country_col in filtered.columns:
            filtered = filtered[filtered[country_col] == filters['country']]

    if filters.get('tier') and filters['tier'] != 'All':
        if 'performance_tier' in filtered.columns:
            filtered = filtered[filtered['performance_tier'] == filters['tier']]

    if filters.get('urban_rural') and filters['urban_rural'] != 'All':
        if 'urban_rural' in filtered.columns:
            filtered = filtered[filtered['urban_rural'] == filters['urban_rural']]

    return filtered


# ============================================================================
# MAIN DASHBOARD
# ============================================================================

def main():
    """Main dashboard application entry point"""

    # ---- HEADER ----
    st.markdown('<h1 class="main-header">🏪 Pan-Asian Retail Analytics</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Supermarket OSINT Dashboard | Equity Research Platform</p>', unsafe_allow_html=True)

    # ---- LOAD DATA ----
    with st.spinner('📊 Loading data...'):
        enriched_df, cleaned_df = load_store_data()
        kpi_df = load_kpi_data()

    if enriched_df is None and cleaned_df is None:
        st.error("""
        ### ⚠️ No Data Found

        Please run the data pipeline first:

        ```bash
        # Generate test data
        python tests/generate_test_data_v2.py --stores 3 --rows 200

        # Run ETL pipeline
        python test_extraction.py
        python test_transformation.py
        python test_database.py

        # Or run everything at once:
        make data
        make pipeline
        ```
        """)
        return

    # Use most complete dataset
    df = cleaned_df if cleaned_df is not None else enriched_df

    # Calculate metrics
    metrics = calculate_dashboard_metrics(df)

    # ==========================================================================
    # SIDEBAR
    # ==========================================================================
    with st.sidebar:
        st.markdown("## 🎛️ Filters")
        st.markdown("---")

        filters = {}

        # Region filter
        region_col = 'economic_region' if 'economic_region' in df.columns else 'region'
        if region_col in df.columns:
            regions = sorted(df[region_col].dropna().unique().tolist())
            filters['region'] = st.selectbox(
                '🌏 Economic Region',
                ['All'] + regions
            )

        # Country filter
        country_col = 'country' if 'country' in df.columns else 'Country'
        if country_col in df.columns:
            countries = sorted(df[country_col].dropna().unique().tolist())
            filters['country'] = st.selectbox(
                '🌍 Country',
                ['All'] + countries
            )

        # Performance tier filter
        if 'performance_tier' in df.columns:
            tiers = sorted(df['performance_tier'].dropna().unique().tolist())
            filters['tier'] = st.selectbox(
                '📊 Performance Tier',
                ['All'] + tiers
            )

        # Urban/Rural filter
        if 'urban_rural' in df.columns:
            urban_options = sorted(df['urban_rural'].dropna().unique().tolist())
            filters['urban_rural'] = st.selectbox(
                '🏘️ Location Type',
                ['All'] + urban_options
            )

        st.markdown("---")

        # Map options
        st.markdown("## 🗺️ Map Options")
        map_engine = st.radio(
            'Map Engine',
            ['Folium (Feature-rich)', 'PyDeck (Performance)'],
            index=0
        )
        show_heatmap = st.checkbox('Show Revenue Heatmap', value=True)

        st.markdown("---")

        # Refresh info
        st.markdown("## ℹ️ Info")
        st.markdown(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        st.markdown(f"Stores: {metrics.get('total_stores', 'N/A')}")
        st.markdown(f"Regions: {metrics.get('num_regions', 'N/A')}")
        st.markdown(f"Currencies: {metrics.get('num_currencies', 'N/A')}")

        st.markdown("---")

        # Export section
        st.markdown("## 📥 Export")

        export_format = st.selectbox('Format', ['CSV', 'Excel'])

        if st.button('📥 Download Data', use_container_width=True):
            if export_format == 'CSV':
                csv = df.to_csv(index=False)
                st.download_button(
                    label='Click to Download CSV',
                    data=csv,
                    file_name=f'retail_data_{datetime.now().strftime("%Y%m%d")}.csv',
                    mime='text/csv',
                    use_container_width=True
                )
            else:
                from io import BytesIO
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Store Data', index=False)
                st.download_button(
                    label='Click to Download Excel',
                    data=output.getvalue(),
                    file_name=f'retail_data_{datetime.now().strftime("%Y%m%d")}.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True
                )

    # ---- APPLY FILTERS ----
    filtered_df = apply_filters(df, filters)

    # ---- METRICS ROW ----
    st.markdown("---")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Total Revenue (USD)",
            f"${metrics.get('total_revenue', 0):,.0f}"
        )

    with col2:
        st.metric(
            "Total Stores",
            f"{metrics.get('total_stores', 0):,}"
        )

    with col3:
        st.metric(
            "Transactions",
            f"{metrics.get('total_transactions', 0):,}"
        )

    with col4:
        st.metric(
            "Avg Transaction",
            f"${metrics.get('avg_transaction', 0):,.2f}"
        )

    with col5:
        sss = metrics.get('avg_sss_growth', 0)
        st.metric(
            "Avg SSS Growth",
            f"{sss:+.1%}",
            delta=f"{sss:+.1%}" if abs(sss) > 0.001 else None
        )

    # ---- TABS ----
    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🗺️ Map View",
        "📊 Financials",
        "💰 Currency",
        "🏆 Rankings",
        "📈 Trends"
    ])

    # ==========================================================================
    # TAB 1: MAP
    # ==========================================================================
    with tab1:
        st.markdown("### 🗺️ Store Performance Map")
        st.caption("Click markers for store details. Toggle layers with the control panel.")

        map_col1, map_col2 = st.columns([3, 1])

        with map_col2:
            st.markdown("#### Legend")
            st.markdown("""
            🟢 Outperforming (+10% SSS)
            🔵 Market Perform (0-10% SSS)
            🟠 Underperforming (-5-0% SSS)
            🔴 Critical (below -5% SSS)
            """)

            st.markdown("---")
            st.markdown("#### Quick Stats")
            if 'economic_region' in filtered_df.columns:
                for region in filtered_df['economic_region'].unique()[:5]:
                    count = filtered_df[filtered_df['economic_region'] == region]['store_id'].nunique() if 'store_id' in filtered_df.columns else len(filtered_df[filtered_df['economic_region'] == region])
                    st.markdown(f"- {region}: {count} stores")

        with map_col1:
            # Aggregate data per store for map display
            if 'store_id' in filtered_df.columns:
                agg_dict = {
                    'latitude': 'first',
                    'longitude': 'first',
                    'economic_region': 'first',
                    'nearest_city': 'first',
                    'country': 'first',
                    'urban_rural': 'first',
                    'competition_density': 'first',
                }

                if 'revenue_usd' in filtered_df.columns:
                    agg_dict['revenue_usd'] = 'sum'
                elif 'Net Sales' in filtered_df.columns:
                    agg_dict['Net Sales'] = 'sum'

                if 'same_store_sales_growth' in filtered_df.columns:
                    agg_dict['same_store_sales_growth'] = 'mean'

                map_df = filtered_df.groupby('store_id').agg(agg_dict).reset_index()
            else:
                map_df = filtered_df

            if map_engine == 'PyDeck (Performance)':
                deck = create_pydeck_map(map_df)
                if deck:
                    st.pydeck_chart(deck, use_container_width=True)
                else:
                    st.warning("PyDeck map could not be created. Using Folium instead.")
                    store_map = create_store_map(map_df)
                    st_folium(store_map, width=None, height=600)
            else:
                store_map = create_store_map(map_df)
                st_folium(store_map, width=None, height=600)

    # ==========================================================================
    # TAB 2: FINANCIALS
    # ==========================================================================
    with tab2:
        st.markdown("### 📊 Financial Performance Analysis")

        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            fig_revenue = create_revenue_by_region_chart(filtered_df)
            if fig_revenue:
                st.plotly_chart(fig_revenue, use_container_width=True)
            else:
                st.info("Revenue by region data not available.")

            fig_heatmap = create_performance_heatmap(filtered_df)
            if fig_heatmap:
                st.plotly_chart(fig_heatmap, use_container_width=True)

        with chart_col2:
            fig_sss = create_sss_growth_chart(filtered_df)
            if fig_sss:
                st.plotly_chart(fig_sss, use_container_width=True)
            else:
                st.info("Same-store sales growth data not available.")

            # Urban/Rural breakdown
            if 'urban_rural' in filtered_df.columns:
                revenue_col = 'revenue_usd' if 'revenue_usd' in filtered_df.columns else 'Net Sales'
                if revenue_col in filtered_df.columns:
                    urban_data = filtered_df.groupby('urban_rural')[revenue_col].sum()

                    fig_urban = px.pie(
                        values=urban_data.values,
                        names=urban_data.index,
                        title='Revenue: Urban vs Rural Split',
                        color_discrete_sequence=px.colors.qualitative.Set3,
                    )
                    fig_urban.update_traces(textinfo='percent+label')
                    fig_urban.update_layout(height=350, margin=dict(l=20, r=20, t=50, b=20))
                    st.plotly_chart(fig_urban, use_container_width=True)

    # ==========================================================================
    # TAB 3: CURRENCY
    # ==========================================================================
    with tab3:
        st.markdown("### 💰 Currency Exposure Analysis")
        st.caption("Critical for FX hedging decisions and performance attribution.")

        curr_col1, curr_col2 = st.columns([2, 1])

        with curr_col1:
            fig_currency = create_currency_exposure_chart(filtered_df)
            if fig_currency:
                st.plotly_chart(fig_currency, use_container_width=True)
            else:
                st.info("Currency data not available. Run currency normalization first.")

        with curr_col2:
            if 'original_currency' in filtered_df.columns:
                revenue_col = 'revenue_usd' if 'revenue_usd' in filtered_df.columns else 'Net Sales'
                if revenue_col in filtered_df.columns:
                    st.markdown("#### Currency Breakdown")
                    currency_summary = filtered_df.groupby('original_currency').agg(
                        revenue=(revenue_col, 'sum'),
                        stores=('store_id', 'nunique') if 'store_id' in filtered_df.columns else ('original_currency', 'count')
                    ).sort_values('revenue', ascending=False)

                    total = currency_summary['revenue'].sum()

                    for curr, row in currency_summary.iterrows():
                        pct = row['revenue'] / total * 100 if total > 0 else 0
                        st.metric(
                            label=f"💱 {curr}",
                            value=f"${row['revenue']:,.0f}",
                            delta=f"{pct:.1f}% of total"
                        )

    # ==========================================================================
    # TAB 4: RANKINGS
    # ==========================================================================
    with tab4:
        st.markdown("### 🏆 Store Performance Rankings")

        top_stores, bottom_stores = create_store_ranking_table(filtered_df)

        if top_stores is not None and bottom_stores is not None:
            rank_col1, rank_col2 = st.columns(2)

            with rank_col1:
                st.markdown("#### 🟢 Top Performing Stores")
                st.dataframe(
                    top_stores,
                    use_container_width=True,
                    height=400,
                    hide_index=True,
                    column_config={
                        'revenue_usd': st.column_config.NumberColumn(
                            'Revenue (USD)',
                            format='$%.0f'
                        ),
                        'same_store_sales_growth': st.column_config.NumberColumn(
                            'SSS Growth',
                            format='+.1%'
                        )
                    } if 'revenue_usd' in top_stores.columns else None
                )

            with rank_col2:
                st.markdown("#### 🔴 Bottom Performing Stores")
                st.dataframe(
                    bottom_stores,
                    use_container_width=True,
                    height=400,
                    hide_index=True,
                    column_config={
                        'revenue_usd': st.column_config.NumberColumn(
                            'Revenue (USD)',
                            format='$%.0f'
                        ),
                        'same_store_sales_growth': st.column_config.NumberColumn(
                            'SSS Growth',
                            format='+.1%'
                        )
                    } if 'revenue_usd' in bottom_stores.columns else None
                )

            # Alert section
            if 'same_store_sales_growth' in filtered_df.columns:
                st.markdown("---")
                st.markdown("### 🚨 Alert Stores (Negative SSS Growth)")

                alert_df = filtered_df[
                    filtered_df['same_store_sales_growth'] < -0.05
                ]

                if not alert_df.empty:
                    store_alerts = alert_df.groupby('store_id').agg(
                        sss=('same_store_sales_growth', 'mean'),
                        revenue=('revenue_usd', 'sum') if 'revenue_usd' in alert_df.columns else ('Net Sales', 'sum')
                    ).reset_index()

                    for _, row in store_alerts.iterrows():
                        st.markdown(f"""
                        <div class="alert-critical">
                            <strong>⚠️ {row['store_id']}</strong> | SSS Growth: <strong>{row['sss']:+.1%}</strong> | Revenue: <strong>${row['revenue']:,.0f}</strong>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.success("✅ No stores with negative SSS growth below -5%")

    # ==========================================================================
    # TAB 5: TRENDS
    # ==========================================================================
    with tab5:
        st.markdown("### 📈 Revenue Trends")

        date_col = 'date' if 'date' in filtered_df.columns else 'Transaction Date'

        if date_col in filtered_df.columns:
            fig_ts = create_time_series_chart(filtered_df, date_col)
            if fig_ts:
                st.plotly_chart(fig_ts, use_container_width=True)

            fig_region_ts = create_region_time_series(filtered_df, date_col)
            if fig_region_ts:
                st.plotly_chart(fig_region_ts, use_container_width=True)
        else:
            st.info("Time series data requires a date column. Run the full pipeline for time-based analysis.")

    # ---- FOOTER ----
    st.markdown("---")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(f"Data Refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    with col2:
        st.markdown("Source: Supermarket OSINT Pipeline")
    with col3:
        st.markdown("Built for: Pan-Asian Equity Research")


if __name__ == "__main__":
    main()
