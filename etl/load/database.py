"""
Database Abstraction Layer
Supports PostgreSQL+PostGIS (primary) and DuckDB (analytics backup)
with automatic failover.

In fund management:
- PostgreSQL+PostGIS: Primary operational database for spatial queries
- DuckDB: Embedded analytics engine for rapid prototyping and offline analysis
- Both support the same queries, allowing seamless transition
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
import logging
import json
import os

logger = logging.getLogger(__name__)


class DatabaseBackend(Enum):
    """Supported database backends"""
    POSTGRES = auto()
    DUCKDB = auto()
    SQLITE = auto()  # Lightweight testing


@dataclass
class DatabaseConfig:
    """Configuration for database connections"""
    backend: DatabaseBackend
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    duckdb_path: Optional[Path] = None

    @classmethod
    def from_env(cls, backend: DatabaseBackend) -> 'DatabaseConfig':
        """Create config from environment variables"""
        if backend == DatabaseBackend.POSTGRES:
            return cls(
                backend=backend,
                host=os.getenv('POSTGRES_HOST', 'localhost'),
                port=int(os.getenv('POSTGRES_PORT', 5432)),
                database=os.getenv('POSTGRES_DB', 'retail_analytics'),
                user=os.getenv('POSTGRES_USER', 'analyst'),
                password=os.getenv('POSTGRES_PASSWORD', ''),
            )
        elif backend == DatabaseBackend.DUCKDB:
            return cls(
                backend=backend,
                duckdb_path=Path(os.getenv('DUCKDB_PATH', 'data/retail.db')),
            )


class DatabaseConnection:
    """
    Unified database connection supporting multiple backends.

    Design pattern: Strategy pattern
    Each backend implements the same interface, allowing
    runtime switching between PostgreSQL and DuckDB.
    """

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection = None
        self._connect()

    def _connect(self):
        """Establish connection based on backend type"""
        if self.config.backend == DatabaseBackend.POSTGRES:
            import psycopg2
            import psycopg2.extras

            self._connection = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password
            )
            self._connection.autocommit = False
            logger.info(
                f"Connected to PostgreSQL: {self.config.host}:{self.config.port}"
            )

        elif self.config.backend == DatabaseBackend.DUCKDB:
            import duckdb

            db_path = self.config.duckdb_path or Path("data/retail.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)

            self._connection = duckdb.connect(str(db_path))
            logger.info(f"Connected to DuckDB: {db_path}")

            # Install spatial extension
            try:
                self._connection.execute("INSTALL spatial;")
                self._connection.execute("LOAD spatial;")
            except Exception:
                logger.warning("Could not load DuckDB spatial extension")

    @property
    def connection(self):
        """Get the underlying connection object"""
        return self._connection

    def execute(self, query: str, params: Optional[tuple] = None):
        """Execute a query (write operations)"""
        if self.config.backend == DatabaseBackend.POSTGRES:
            cursor = self._connection.cursor()
            cursor.execute(query, params)
            cursor.close()
        else:
            self._connection.execute(query, params or ())

    def query(self, query: str, params: Optional[tuple] = None) -> pd.DataFrame:
        """Execute query and return DataFrame (read operations)"""
        if self.config.backend == DatabaseBackend.POSTGRES:
            return pd.read_sql_query(query, self._connection, params=params)
        else:
            return self._connection.execute(query, params or ()).df()

    def commit(self):
        """Commit transaction"""
        if self.config.backend == DatabaseBackend.POSTGRES:
            self._connection.commit()

    def rollback(self):
        """Rollback transaction"""
        if self.config.backend == DatabaseBackend.POSTGRES:
            self._connection.rollback()

    def close(self):
        """Close connection"""
        if self._connection:
            if self.config.backend == DatabaseBackend.POSTGRES:
                self._connection.close()
            else:
                self._connection.close()


class DatabaseManager:
    """
    Manages dual-backend database operations with failover.

    Architecture:
    - PostgreSQL+PostGIS: Primary for production (spatial queries)
    - DuckDB: Hot standby for analytics and development
    - Automatic failover on connection failure

    Use cases in fund management:
    - Production: PostgreSQL for multi-user access, spatial queries
    - Development: DuckDB for local testing without server setup
    - Travel/Offline: DuckDB works without internet
    """

    def __init__(
        self,
        primary_config: Optional[DatabaseConfig] = None,
        secondary_config: Optional[DatabaseConfig] = None,
        auto_failover: bool = True
    ):
        self.auto_failover = auto_failover
        self._primary_conn: Optional[DatabaseConnection] = None
        self._secondary_conn: Optional[DatabaseConnection] = None
        self._active_backend: Optional[DatabaseBackend] = None

        # Initialize connections
        if primary_config:
            try:
                self._primary_conn = DatabaseConnection(primary_config)
                self._active_backend = DatabaseBackend.POSTGRES
                self._initialize_postgres_schema()
                logger.info("PostgreSQL initialized as primary")
            except Exception as e:
                logger.error(f"Primary connection failed: {e}")
                if not auto_failover:
                    raise

        if secondary_config and (
            not self._primary_conn or self.auto_failover
        ):
            try:
                self._secondary_conn = DatabaseConnection(secondary_config)
                if not self._primary_conn:
                    self._active_backend = DatabaseBackend.DUCKDB
                    self._initialize_duckdb_schema()
                    logger.info("DuckDB initialized as primary (failover)")
                else:
                    logger.info("DuckDB initialized as standby")
            except Exception as e:
                logger.error(f"Secondary connection failed: {e}")

        if not self._active_backend:
            raise RuntimeError("No database backend available")

    def _initialize_postgres_schema(self):
        """Create PostgreSQL+PostGIS schema for retail analytics"""
        if not self._primary_conn:
            return

        schema_sql = """
        -- Enable PostGIS extension
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS postgis_topology;

        -- Store locations table with spatial support
        CREATE TABLE IF NOT EXISTS stores (
            store_id VARCHAR(50) PRIMARY KEY,
            country VARCHAR(10) NOT NULL,
            region VARCHAR(100),
            economic_region VARCHAR(50),
            urban_rural VARCHAR(20),
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            location GEOGRAPHY(POINT),
            nearest_city VARCHAR(100),
            distance_to_city_km DOUBLE PRECISION,
            cluster_id INTEGER,
            stores_within_10km INTEGER,
            gdp_per_capita_usd DOUBLE PRECISION,
            urbanization_rate DOUBLE PRECISION,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Sales transactions table
        CREATE TABLE IF NOT EXISTS sales_transactions (
            transaction_id SERIAL PRIMARY KEY,
            store_id VARCHAR(50) REFERENCES stores(store_id),
            date DATE NOT NULL,
            product_id VARCHAR(50),
            quantity INTEGER,
            revenue_original DOUBLE PRECISION,
            original_currency VARCHAR(10),
            revenue_usd DOUBLE PRECISION,
            exchange_rate_usd DOUBLE PRECISION,
            is_interpolated BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Regional aggregations table
        CREATE TABLE IF NOT EXISTS regional_summaries (
            summary_id SERIAL PRIMARY KEY,
            economic_region VARCHAR(50) NOT NULL,
            period DATE NOT NULL,
            total_revenue_usd DOUBLE PRECISION,
            total_stores INTEGER,
            total_transactions INTEGER,
            avg_revenue_per_store DOUBLE PRECISION,
            revenue_growth_yoy DOUBLE PRECISION,
            market_concentration_hhi DOUBLE PRECISION,
            currency_exposure JSONB,
            urban_rural_split JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- KPI cache table for map markers
        CREATE TABLE IF NOT EXISTS store_kpis (
            store_id VARCHAR(50) REFERENCES stores(store_id),
            calculation_date DATE NOT NULL,
            same_store_sales_growth DOUBLE PRECISION,
            revenue_mtd DOUBLE PRECISION,
            revenue_ytd DOUBLE PRECISION,
            transaction_count_mtd INTEGER,
            avg_basket_size DOUBLE PRECISION,
            rank_in_region INTEGER,
            PRIMARY KEY (store_id, calculation_date)
        );

        -- Create spatial indexes
        CREATE INDEX IF NOT EXISTS idx_stores_location
            ON stores USING GIST (location);

        CREATE INDEX IF NOT EXISTS idx_sales_date
            ON sales_transactions (date);

        CREATE INDEX IF NOT EXISTS idx_sales_store_date
            ON sales_transactions (store_id, date);

        -- Update location geometry from lat/lon
        CREATE OR REPLACE FUNCTION update_store_location()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.location = ST_SetSRID(
                ST_MakePoint(NEW.longitude, NEW.latitude),
                4326
            )::geography;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_update_location ON stores;
        CREATE TRIGGER trg_update_location
            BEFORE INSERT OR UPDATE ON stores
            FOR EACH ROW EXECUTE FUNCTION update_store_location();
        """

        try:
            self._primary_conn.execute(schema_sql)
            self._primary_conn.commit()
            logger.info("PostgreSQL schema initialized")
        except Exception as e:
            logger.error(f"Schema initialization failed: {e}")
            self._primary_conn.rollback()
            raise

    def _initialize_duckdb_schema(self):
        """Create DuckDB schema mirroring PostgreSQL structure"""
        if not self._secondary_conn:
            return

        schema_sql = """
        CREATE TABLE IF NOT EXISTS stores (
            store_id VARCHAR PRIMARY KEY,
            country VARCHAR,
            region VARCHAR,
            economic_region VARCHAR,
            urban_rural VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE,
            nearest_city VARCHAR,
            distance_to_city_km DOUBLE,
            cluster_id INTEGER,
            stores_within_10km INTEGER,
            gdp_per_capita_usd DOUBLE,
            urbanization_rate DOUBLE,
            created_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sales_transactions (
            transaction_id INTEGER PRIMARY KEY,
            store_id VARCHAR,
            date DATE,
            product_id VARCHAR,
            quantity INTEGER,
            revenue_original DOUBLE,
            original_currency VARCHAR,
            revenue_usd DOUBLE,
            exchange_rate_usd DOUBLE,
            is_interpolated BOOLEAN
        );

        CREATE TABLE IF NOT EXISTS regional_summaries (
            summary_id INTEGER PRIMARY KEY,
            economic_region VARCHAR,
            period DATE,
            total_revenue_usd DOUBLE,
            total_stores INTEGER,
            total_transactions INTEGER,
            avg_revenue_per_store DOUBLE,
            revenue_growth_yoy DOUBLE,
            market_concentration_hhi DOUBLE
        );

        CREATE TABLE IF NOT EXISTS store_kpis (
            store_id VARCHAR,
            calculation_date DATE,
            same_store_sales_growth DOUBLE,
            revenue_mtd DOUBLE,
            revenue_ytd DOUBLE,
            transaction_count_mtd INTEGER,
            avg_basket_size DOUBLE,
            rank_in_region INTEGER,
            PRIMARY KEY (store_id, calculation_date)
        );
        """

        self._secondary_conn.execute(schema_sql)
        logger.info("DuckDB schema initialized")

    @property
    def active_connection(self) -> DatabaseConnection:
        """Get currently active database connection"""
        if self._active_backend == DatabaseBackend.POSTGRES and self._primary_conn:
            return self._primary_conn
        elif self._secondary_conn:
            return self._secondary_conn
        raise RuntimeError("No active database connection")

    def failover_to_secondary(self):
        """Switch to secondary database"""
        if self._secondary_conn:
            self._active_backend = DatabaseBackend.DUCKDB
            logger.warning("Failed over to DuckDB")
            return True
        return False

    def failback_to_primary(self) -> bool:
        """Attempt to restore primary connection"""
        if self._primary_conn:
            try:
                # Test connection
                self._primary_conn.query("SELECT 1")
                self._active_backend = DatabaseBackend.POSTGRES
                logger.info("Failed back to PostgreSQL")
                return True
            except Exception:
                logger.warning("Primary still unavailable")
        return False

    def load_stores(self, df: pd.DataFrame) -> int:
        """Load store data into database"""
        conn = self.active_connection

        if self._active_backend == DatabaseBackend.POSTGRES:
            # Use COPY for fast bulk insert
            from io import StringIO
            import psycopg2.extras

            buffer = StringIO()
            df.to_csv(buffer, index=False, header=False)
            buffer.seek(0)

            cursor = conn.connection.cursor()
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO stores (
                    store_id, country, region, economic_region,
                    urban_rural, latitude, longitude,
                    nearest_city, distance_to_city_km,
                    cluster_id, stores_within_10km,
                    gdp_per_capita_usd, urbanization_rate
                ) VALUES %s
                ON CONFLICT (store_id) DO UPDATE SET
                    economic_region = EXCLUDED.economic_region,
                    urban_rural = EXCLUDED.urban_rural,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [tuple(row) for _, row in df.iterrows()]
            )
            conn.commit()
        else:
            # DuckDB insert
            conn.connection.register('temp_stores', df)
            conn.execute("""
                INSERT OR REPLACE INTO stores
                SELECT * FROM temp_stores
            """)

        logger.info(f"Loaded {len(df)} stores into database")
        return len(df)

    def load_sales(self, df: pd.DataFrame) -> int:
        """Load sales transactions into database"""
        conn = self.active_connection

        if self._active_backend == DatabaseBackend.POSTGRES:
            import psycopg2.extras

            cursor = conn.connection.cursor()
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO sales_transactions (
                    store_id, date, product_id, quantity,
                    revenue_original, original_currency,
                    revenue_usd, exchange_rate_usd
                ) VALUES %s
                """,
                [tuple(row) for _, row in df.iterrows()]
            )
            conn.commit()
        else:
            conn.connection.register('temp_sales', df)
            conn.execute("INSERT INTO sales_transactions SELECT * FROM temp_sales")

        logger.info(f"Loaded {len(df)} sales transactions")
        return len(df)

    def spatial_query_nearby_stores(
        self, lat: float, lon: float, radius_km: float
    ) -> pd.DataFrame:
        """
        Find stores within radius using spatial query.

        PostgreSQL uses PostGIS, DuckDB uses haversine formula.
        """
        if self._active_backend == DatabaseBackend.POSTGRES:
            query = """
                SELECT
                    store_id, country, economic_region,
                    ST_Distance(
                        location,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) / 1000 AS distance_km,
                    revenue_mtd, same_store_sales_growth
                FROM stores
                LEFT JOIN store_kpis USING (store_id)
                WHERE ST_DWithin(
                    location,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    %s
                )
                ORDER BY distance_km
            """
            return self.active_connection.query(
                query, (lon, lat, lon, lat, radius_km * 1000)
            )
        else:
            query = """
                SELECT
                    store_id, country, economic_region,
                    6371 * 2 * ASIN(SQRT(
                        POWER(SIN((latitude - ?) * PI() / 360), 2) +
                        COS(latitude * PI() / 180) * COS(? * PI() / 180) *
                        POWER(SIN((longitude - ?) * PI() / 360), 2)
                    )) AS distance_km
                FROM stores
                WHERE distance_km <= ?
                ORDER BY distance_km
            """
            return self.active_connection.query(
                query, (lat, lat, lon, radius_km)
            )
