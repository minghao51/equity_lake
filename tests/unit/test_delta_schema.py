"""Tests for Delta merge schema evolution, idempotency, and read error semantics.

The pre-2026-08 ``merge_delta`` fell back to ``write_delta(mode="append",
schema_mode="merge")`` whenever a merge failed with a schema-classified error,
appending duplicate keyed rows on top of the existing table.  These tests pin
the replacement contract: evolve the table schema, re-merge, never append.
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.storage.delta import (
    DeltaMergeError,
    DeltaReadError,
    merge_delta,
    read_delta,
    write_delta,
)


def _write_table(path: Path, df: pl.DataFrame) -> None:
    from deltalake import write_deltalake

    write_deltalake(str(path), df.to_arrow(), mode="overwrite", partition_by=["date"])


class TestMergeDeltaSchemaEvolution:
    """Schema-mismatch merges evolve the schema and stay idempotent (real Delta tables)."""

    def test_schema_mismatch_merge_preserves_key_uniqueness(self, tmp_path: Path) -> None:
        table_path = tmp_path / "tbl"
        # Target predates the `ticker` key column, so the merge predicate fails
        # with a schema error ("No field named target.ticker").
        existing = pl.DataFrame(
            {
                "id": ["A", "B"],
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [150.0, 380.0],
            }
        )
        _write_table(table_path, existing)

        batch = pl.DataFrame(
            {
                "ticker": ["AAPL", "NVDA"],
                "date": [date(2024, 1, 4), date(2024, 1, 5)],
                "close": [200.0, 900.0],
            }
        )
        assert merge_delta(batch, "tbl", key_columns=["ticker", "date"], lake_dir=tmp_path) is True

        # Merge the same batch again: idempotent, row count stable.
        assert merge_delta(batch, "tbl", key_columns=["ticker", "date"], lake_dir=tmp_path) is True

        out = read_delta("tbl", lake_dir=tmp_path).sort("id", "ticker")
        assert out.height == 4  # 2 preserved rows + 2 inserted, not 2 + 2 + 2
        assert out.unique(subset=["ticker", "date"]).height == 4  # no duplicate keys
        assert set(out["id"].drop_nulls()) == {"A", "B"}  # pre-evolution rows preserved
        inserted = out.filter(pl.col("ticker").is_not_null())
        assert sorted(inserted["ticker"]) == ["AAPL", "NVDA"]
        assert sorted(inserted["close"]) == [200.0, 900.0]

        # Partitioning survived the evolution rewrite.
        assert DeltaTable(str(table_path)).metadata().partition_columns == ["date"]

    def test_non_schema_error_raises_and_leaves_table_intact(self, tmp_path: Path) -> None:
        table_path = tmp_path / "tbl"
        _write_table(
            table_path,
            pl.DataFrame(
                {
                    "ticker": ["AAPL", "MSFT"],
                    "date": [date(2024, 1, 2), date(2024, 1, 2)],
                    "close": [150.0, 380.0],
                }
            ),
        )

        # A malformed key produces a broken SQL predicate -> non-schema failure.
        with pytest.raises(DeltaMergeError):
            merge_delta(
                pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [1.0]}),
                "tbl",
                key_columns=["ticker", "date AND ("],
                lake_dir=tmp_path,
            )

        # Nothing was appended and the original rows are intact.
        assert read_delta("tbl", lake_dir=tmp_path).height == 2

    def test_new_table_is_created_by_merge(self, tmp_path: Path) -> None:
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]})
        assert merge_delta(df, "fresh", key_columns=["ticker", "date"], lake_dir=tmp_path) is True
        assert read_delta("fresh", lake_dir=tmp_path).height == 1


class TestMergeDeltaPartitionBy:
    """partition_by passthrough: non-date-partitioned tables (e.g. corporate actions on ex_date)."""

    def test_partitioned_create_and_idempotent_merge(self, tmp_path: Path) -> None:
        df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "ex_date": [date(2024, 2, 9)],
                "action": ["dividend"],
                "value": [0.25],
            }
        )
        keys = ["ticker", "ex_date", "action"]
        assert merge_delta(df, "corporate_actions", key_columns=keys, lake_dir=tmp_path, partition_by=["ex_date"]) is True
        assert merge_delta(df, "corporate_actions", key_columns=keys, lake_dir=tmp_path, partition_by=["ex_date"]) is True

        out = read_delta("corporate_actions", lake_dir=tmp_path)
        assert out.height == 1  # keyed upsert stayed idempotent
        assert out["value"].to_list() == [0.25]

        table_path = tmp_path / "corporate_actions"
        assert DeltaTable(str(table_path)).metadata().partition_columns == ["ex_date"]
        partition_dirs = [d.name for d in table_path.iterdir() if d.is_dir() and d.name.startswith("ex_date=")]
        assert partition_dirs == ["ex_date=2024-02-09"]

    def test_default_partitioning_is_unchanged(self, tmp_path: Path) -> None:
        """partition_by=None keeps the hardcoded ["date"] behavior for existing callers."""
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]})
        assert merge_delta(df, "prices", key_columns=["ticker", "date"], lake_dir=tmp_path) is True
        assert DeltaTable(str(tmp_path / "prices")).metadata().partition_columns == ["date"]

    def test_partitioned_schema_evolution_preserves_partitioning(self, tmp_path: Path) -> None:
        """Evolving a partitioned table must not silently re-partition it to date."""
        # Target predates the `action` key column, so the merge predicate fails
        # with a schema error and the evolution path rewrites the table.
        existing = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "ex_date": [date(2024, 2, 9)],
                "value": [0.25],
            }
        )
        keys = ["ticker", "ex_date", "action"]
        assert merge_delta(existing, "ca", key_columns=keys, lake_dir=tmp_path, partition_by=["ex_date"]) is True

        batch = pl.DataFrame(
            {
                "ticker": ["MSFT"],
                "ex_date": [date(2024, 3, 14)],
                "action": ["dividend"],
                "value": [0.83],
            }
        )
        assert merge_delta(batch, "ca", key_columns=keys, lake_dir=tmp_path, partition_by=["ex_date"]) is True

        table_path = tmp_path / "ca"
        assert DeltaTable(str(table_path)).metadata().partition_columns == ["ex_date"]
        out = read_delta("ca", lake_dir=tmp_path).sort("ticker")
        assert out.height == 2  # pre-evolution row preserved + inserted, not duplicated
        assert out["ticker"].to_list() == ["AAPL", "MSFT"]
        assert out["action"].to_list() == [None, "dividend"]  # old row null-filled by evolution


class TestMergeDeltaNeverAppends:
    """Mock-level pin: the append fallback is gone for keyed upserts."""

    def _mocked_table(self) -> tuple[MagicMock, MagicMock]:
        mock_dt_instance = MagicMock()
        mock_dt_cls = MagicMock(return_value=mock_dt_instance)
        mock_dt_cls.is_deltatable.return_value = True
        return mock_dt_cls, mock_dt_instance

    def test_schema_error_attempts_evolution_never_append(self) -> None:
        mock_dt_cls, mock_dt_instance = self._mocked_table()
        mock_dt_instance.merge.side_effect = Exception("Schema error: No field named target.ticker")
        # The evolution seed-overwrite reads the current table; serve real rows
        # so the read succeeds and the write attempt is observable.
        existing = pl.DataFrame({"ticker": ["MSFT"], "date": [date(2024, 1, 2)], "close": [1.0]})
        mock_dt_instance.to_pyarrow_table.return_value = existing.to_arrow()

        with (
            patch("equity_lake.storage.delta.DeltaTable", mock_dt_cls),
            patch("equity_lake.storage.delta.write_delta", return_value=True) as mock_write,
            pytest.raises(DeltaMergeError),
        ):
            merge_delta(
                pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [1.0]}),
                "tbl",
                key_columns=["ticker", "date"],
            )

        # The only write is the seed-overwrite schema evolution — never an append.
        assert mock_write.call_count == 1
        assert mock_write.call_args.kwargs["mode"] == "overwrite"
        assert mock_write.call_args.kwargs["schema_mode"] == "overwrite"
        for attempted in mock_write.call_args_list:
            assert attempted.kwargs["mode"] != "append"

    def test_non_schema_error_raises_without_any_write(self) -> None:
        mock_dt_cls, mock_dt_instance = self._mocked_table()
        mock_dt_instance.merge.side_effect = Exception("disk I/O error")

        with (
            patch("equity_lake.storage.delta.DeltaTable", mock_dt_cls),
            patch("equity_lake.storage.delta.write_delta", return_value=True) as mock_write,
            pytest.raises(DeltaMergeError),
        ):
            merge_delta(
                pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [1.0]}),
                "tbl",
                key_columns=["ticker", "date"],
            )

        mock_write.assert_not_called()


class TestReadDelta:
    """read_delta raises DeltaReadError instead of swallowing into an empty frame."""

    def test_missing_table_raises_delta_read_error(self, tmp_path: Path) -> None:
        with pytest.raises(DeltaReadError):
            read_delta("does_not_exist", lake_dir=tmp_path)

    def test_roundtrip_returns_written_rows(self, tmp_path: Path) -> None:
        df = pl.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [150.0, 380.0],
            }
        )
        assert write_delta(df, "tbl", mode="overwrite", lake_dir=tmp_path) is True
        out = read_delta("tbl", lake_dir=tmp_path).sort("ticker")
        assert out["ticker"].to_list() == ["AAPL", "MSFT"]
        assert out["close"].to_list() == [150.0, 380.0]

    def test_delta_read_error_is_a_delta_error(self) -> None:
        from equity_lake.storage.delta import DeltaError

        assert issubclass(DeltaReadError, DeltaError)


class TestRenameTableParam:
    """The lake-relative table parameter is keyword-named ``table`` (was ``market``)."""

    def test_write_and_read_accept_table_keyword(self, tmp_path: Path) -> None:
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]})
        write_delta(df, table="tbl", mode="overwrite", lake_dir=tmp_path)
        assert read_delta(table="tbl", lake_dir=tmp_path).height == 1

    def test_merge_delta_accepts_table_keyword(self, tmp_path: Path) -> None:
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]})
        assert merge_delta(df, table="tbl", key_columns=["ticker", "date"], lake_dir=tmp_path) is True
