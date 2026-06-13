"""Tests for Delta Lake compaction helpers."""

from datetime import date

import polars as pl
from deltalake import DeltaTable, write_deltalake


def test_compaction_reduces_files(tmp_path) -> None:
    """Compaction should reduce the number of files in a Delta table."""
    market_dir = tmp_path / "us_equity"

    df1 = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 1)], "close": [100.0]})
    df2 = pl.DataFrame({"ticker": ["MSFT"], "date": [date(2024, 1, 1)], "close": [200.0]})

    write_deltalake(str(market_dir), df1.to_arrow(), mode="append")
    write_deltalake(str(market_dir), df2.to_arrow(), mode="append")

    dt_before = DeltaTable(str(market_dir))
    files_before = dt_before.get_add_actions().num_rows

    from equity_lake.storage.compaction import compact_market

    compact_market(market_dir)

    dt_after = DeltaTable(str(market_dir))
    files_after = dt_after.get_add_actions().num_rows
    assert files_after <= files_before

    result = pl.from_arrow(dt_after.to_pyarrow_table())
    assert result.height == 2


def test_compaction_dry_run_does_not_modify_files(tmp_path) -> None:
    """Dry-run compaction should not change the table."""
    market_dir = tmp_path / "us_equity"

    df1 = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 1)], "close": [100.0]})
    df2 = pl.DataFrame({"ticker": ["MSFT"], "date": [date(2024, 1, 1)], "close": [200.0]})

    write_deltalake(str(market_dir), df1.to_arrow(), mode="append")
    write_deltalake(str(market_dir), df2.to_arrow(), mode="append")

    dt_before = DeltaTable(str(market_dir))
    version_before = dt_before.version()

    from equity_lake.storage.compaction import compact_market

    compact_market(market_dir, dry_run=True)

    dt_after = DeltaTable(str(market_dir))
    assert dt_after.version() == version_before
