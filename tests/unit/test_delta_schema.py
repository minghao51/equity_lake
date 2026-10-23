"""Tests for Delta merge schema evolution, idempotency, and read error semantics.

The pre-2026-08 ``merge_delta`` fell back to ``write_delta(mode="append",
schema_mode="merge")`` whenever a merge failed with a schema-classified error,
appending duplicate keyed rows on top of the existing table.  These tests pin
the replacement contract: evolve the table schema, re-merge, never append.
"""

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.storage.delta import (  # type: ignore[import-untyped]
    DeltaMergeError,
    DeltaReadError,
    merge_delta,
    read_delta,
    write_delta,
)


def _write_table(path: Path, df: pl.DataFrame, partition_by: list[str] | None = None) -> None:
    from deltalake import write_deltalake

    write_deltalake(str(path), df.to_arrow(), mode="overwrite", partition_by=partition_by or ["date"])


class TestMergeDeltaSchemaEvolution:
    """Schema-mismatch merges evolve the schema and stay idempotent (real Delta tables)."""

    def test_schema_mismatch_merge_preserves_key_uniqueness(self, tmp_path: Path) -> None:
        table_path = tmp_path / "tbl"
        # Target predates the `ticker` key column; it is added as a nullable
        # Delta schema field without reading or rewriting the existing rows.
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

        target = DeltaTable(str(table_path))
        assert target.metadata().partition_columns == ["date"]
        assert next(field for field in target.schema().fields if field.name == "ticker").nullable is True

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

    def test_null_ticker_natural_key_is_null_safe_and_idempotent(self, tmp_path: Path) -> None:
        first = pl.DataFrame(
            {
                "article_id": ["article-1"],
                "ticker": pl.Series("ticker", [None], dtype=pl.String),
                "date": [date(2024, 1, 2)],
                "summary": ["first"],
            }
        )
        second = first.with_columns(pl.lit("updated").alias("summary"))

        assert merge_delta(first, "nullable-article", key_columns=["article_id", "ticker"], lake_dir=tmp_path)
        assert merge_delta(second, "nullable-article", key_columns=["article_id", "ticker"], lake_dir=tmp_path)

        out = read_delta("nullable-article", lake_dir=tmp_path)
        assert out.height == 1
        assert out["ticker"].to_list() == [None]
        assert out["summary"].to_list() == ["updated"]

    def test_duplicate_null_natural_keys_fail_before_new_table_creation(self, tmp_path: Path) -> None:
        duplicate = pl.DataFrame(
            {
                "article_id": ["article-1", "article-1"],
                "ticker": [None, None],
                "date": [date(2024, 1, 2), date(2024, 1, 2)],
            }
        )

        with pytest.raises(DeltaMergeError) as exc_info:
            merge_delta(duplicate, "duplicate-articles", key_columns=["article_id", "ticker"], lake_dir=tmp_path)
        assert exc_info.value.__cause__ is not None
        assert "duplicate natural keys" in str(exc_info.value.__cause__)
        assert not (tmp_path / "duplicate-articles").exists()


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
        """Native additive evolution preserves a non-date partition layout."""
        # Target predates the `action` key column; native nullable addition
        # keeps its existing partition layout and null-fills old rows.
        existing = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "ex_date": [date(2024, 2, 9)],
                "value": [0.25],
            }
        )
        keys = ["ticker", "ex_date", "action"]
        _write_table(tmp_path / "ca", existing, partition_by=["ex_date"])

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


class TestMergeDeltaAdditiveOnly:
    """Native schema addition never rewrites or drops pre-existing columns."""

    def test_additive_merge_does_not_call_whole_table_writer(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write_table(
            tmp_path / "additive",
            pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]}),
        )

        def reject_rewrite(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("schema evolution must not call the full-table writer")

        monkeypatch.setattr("equity_lake.storage.delta.write_delta", reject_rewrite)
        incoming = pl.DataFrame({"ticker": ["MSFT"], "date": [date(2024, 1, 3)], "close": [380.0], "provider_flag": [True]})

        assert merge_delta(incoming, "additive", key_columns=["ticker", "date"], lake_dir=tmp_path)

        out = read_delta("additive", lake_dir=tmp_path).sort("ticker")
        assert out["ticker"].to_list() == ["AAPL", "MSFT"]
        assert out["provider_flag"].to_list() == [None, True]
        assert next(field for field in DeltaTable(str(tmp_path / "additive")).schema().fields if field.name == "provider_flag").nullable

    def test_partial_source_keeps_existing_target_columns(self, tmp_path: Path) -> None:
        _write_table(
            tmp_path / "partial",
            pl.DataFrame(
                {
                    "ticker": ["AAPL", "MSFT"],
                    "date": [date(2024, 1, 2), date(2024, 1, 2)],
                    "close": [150.0, 380.0],
                    "provider_note": ["keep-a", "keep-m"],
                }
            ),
        )
        incoming = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [151.0]})

        assert merge_delta(incoming, "partial", key_columns=["ticker", "date"], lake_dir=tmp_path)

        out = read_delta("partial", lake_dir=tmp_path).sort("ticker")
        assert out["ticker"].to_list() == ["AAPL", "MSFT"]
        assert out["close"].to_list() == [151.0, 380.0]
        assert out["provider_note"].to_list() == ["keep-a", "keep-m"]

    def test_incompatible_existing_column_type_fails_without_rewrite(self, tmp_path: Path) -> None:
        _write_table(
            tmp_path / "typed",
            pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": [150.0]}),
        )
        incoming = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)], "close": ["not-a-price"]})

        with pytest.raises(DeltaMergeError):
            merge_delta(incoming, "typed", key_columns=["ticker", "date"], lake_dir=tmp_path)

        out = read_delta("typed", lake_dir=tmp_path)
        assert out["close"].to_list() == [150.0]
        assert DeltaTable(str(tmp_path / "typed")).version() == 0

    def test_existing_non_delta_path_is_not_treated_as_absent(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "not-a-delta"
        bad_path.mkdir()
        (bad_path / "unrecognized.txt").write_text("preserve")

        with pytest.raises(DeltaMergeError):
            merge_delta(
                pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 2)]}),
                "not-a-delta",
                key_columns=["ticker", "date"],
                lake_dir=tmp_path,
            )

        assert (bad_path / "unrecognized.txt").read_text() == "preserve"
        assert not (bad_path / "_delta_log").exists()


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
