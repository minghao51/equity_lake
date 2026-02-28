"""Parquet storage compatibility exports."""

from equity_lake.devtools.test_data import write_partitioned_parquet as write_test_partitioned_parquet
from equity_lake.ingestion.writers import write_to_partitioned_parquet

__all__ = ["write_test_partitioned_parquet", "write_to_partitioned_parquet"]
