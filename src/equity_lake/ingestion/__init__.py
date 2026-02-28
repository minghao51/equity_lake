"""Ingestion domain APIs."""

from equity_lake.ingestion.orchestrator import (
    fetch_market_data,
    fetch_market_data_with_config,
    run_daily_ingestion,
)
from equity_lake.ingestion.sources import (
    CNAshareFetcher,
    HKSGEquityFetcher,
    MarketDataFetcher,
    USEquityFetcher,
)
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.ingestion_jobs import run_ingestion_job

__all__ = [
    "CNAshareFetcher",
    "HKSGEquityFetcher",
    "MarketDataFetcher",
    "USEquityFetcher",
    "fetch_market_data",
    "fetch_market_data_with_config",
    "run_daily_ingestion",
    "run_ingestion_job",
    "validate_schema",
    "write_to_partitioned_parquet",
]
