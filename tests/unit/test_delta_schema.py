"""Tests for Delta Lake merge_delta schema evolution fallback."""

from unittest.mock import MagicMock, patch

import polars as pl

from equity_lake.storage.delta import merge_delta


class TestMergeDeltaSchemaEvolution:
    """Verify merge_delta gracefully handles schema mismatches."""

    def test_schema_mismatch_falls_back_to_append(self):
        mock_dt_instance = MagicMock()
        mock_dt_instance.merge.side_effect = Exception("schema mismatch: column 'foo' not found")

        with (
            patch("equity_lake.storage.delta.DeltaTable") as mock_dt_cls,
            patch("equity_lake.storage.delta.write_delta", return_value=True) as mock_write,
        ):
            mock_dt_cls.is_deltatable.return_value = True
            mock_dt_cls.return_value = mock_dt_instance

            df = pl.DataFrame({"ticker": ["AAPL"], "date": [None]})

            result = merge_delta(df, "test_market", key_columns=["ticker", "date"])

            assert result is True
            mock_write.assert_called_once()
            call_kwargs = mock_write.call_args
            assert call_kwargs.kwargs["mode"] == "append"
            assert call_kwargs.kwargs["schema_mode"] == "merge"

    def test_non_schema_error_returns_false(self):
        mock_dt_instance = MagicMock()
        mock_dt_instance.merge.side_effect = Exception("disk I/O error")

        with (
            patch("equity_lake.storage.delta.DeltaTable") as mock_dt_cls,
            patch("equity_lake.storage.delta.write_delta", return_value=True) as mock_write,
        ):
            mock_dt_cls.is_deltatable.return_value = True
            mock_dt_cls.return_value = mock_dt_instance

            df = pl.DataFrame({"ticker": ["AAPL"], "date": [None]})

            result = merge_delta(df, "test_market", key_columns=["ticker", "date"])

            assert result is False
            mock_write.assert_not_called()

    def test_new_table_falls_back_to_write(self):
        with (
            patch("equity_lake.storage.delta.DeltaTable") as mock_dt_cls,
            patch("equity_lake.storage.delta.write_delta", return_value=True) as mock_write,
        ):
            mock_dt_cls.is_deltatable.return_value = False

            df = pl.DataFrame({"ticker": ["AAPL"], "date": [None]})

            result = merge_delta(df, "test_market", key_columns=["ticker", "date"])

            assert result is True
            mock_write.assert_called_once()
            call_kwargs = mock_write.call_args
            assert call_kwargs.kwargs["mode"] == "append"
