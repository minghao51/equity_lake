"""Unit tests for daily EOD data ingestion orchestration."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from equity_lake.ingestion.orchestrator import fetch_market_data, run_daily_ingestion
from equity_lake.ingestion.types import SourceStatus
from equity_lake.ingestion.writers import upsert_dataset, validate_schema
from equity_lake.sources import CNAshareFetcher, HKSGEquityFetcher, USEquityFetcher

# =============================================================================
# Test Market Data Fetchers
# =============================================================================


class TestUSEquityFetcher:
    """Tests for US equity data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = USEquityFetcher()
        assert fetcher.tickers is not None
        assert len(fetcher.tickers) > 0
        assert "AAPL" in fetcher.tickers

    def test_initialization_custom_tickers(self):
        """Test fetcher with custom tickers."""
        custom_tickers = ["AAPL", "GOOGL"]
        fetcher = USEquityFetcher(tickers=custom_tickers)
        assert fetcher.tickers == custom_tickers

    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.network
    def test_fetch_real_data(self):
        """Test fetching real data from yfinance (integration test)."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        trading_date = date(2024, 1, 2)  # Known trading day

        df = fetcher.fetch(trading_date)

        assert not df.is_empty()
        assert "ticker" in df.columns
        assert "AAPL" in df["ticker"].to_list()


class TestCNAshareFetcher:
    """Tests for China A-share data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = CNAshareFetcher()
        assert fetcher is not None

    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.network
    def test_fetch_real_data(self):
        """Test fetching real data from akshare (integration test)."""
        fetcher = CNAshareFetcher()
        trading_date = date(2024, 1, 2)  # Known trading day

        df = fetcher.fetch(trading_date)

        # Note: akshare may return empty data for dates without trading
        # We're mainly testing it doesn't crash
        assert isinstance(df, pl.DataFrame)


class TestHKSGEquityFetcher:
    """Tests for HK/SG equity data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = HKSGEquityFetcher()
        assert fetcher.hk_tickers is not None
        assert fetcher.sg_tickers is not None
        assert "0700.HK" in fetcher.hk_tickers


# =============================================================================
# Test Data Writers
# =============================================================================


class TestPartitionedParquetWriter:
    """Tests for Parquet writer."""

    def test_upsert_dataset(self, tmp_path, sample_ohlcv_data):
        """Test writing DataFrame to Delta storage."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            success = upsert_dataset(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False)

        assert success is True

        from deltalake import DeltaTable

        market_dir = tmp_path / "us_equity"
        dt = DeltaTable(str(market_dir))
        assert dt.version() >= 0

    def test_write_empty_dataframe(self, tmp_path):
        """Test writing empty DataFrame."""
        success = upsert_dataset(pd.DataFrame(), "us_equity", date(2024, 1, 1), dry_run=False)

        assert success is False

    def test_write_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test dry run mode."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            success = upsert_dataset(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=True)

        assert success is True


# =============================================================================
# Test Schema Validation
# =============================================================================


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_valid_schema(self, sample_ohlcv_data):
        """Test validation of valid schema."""
        is_valid = validate_schema(sample_ohlcv_data, "test")
        assert is_valid is True

    def test_missing_required_columns(self):
        """Test validation with missing columns."""
        invalid_data = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                # Missing: open, high, low, close, volume
            }
        )

        is_valid = validate_schema(invalid_data, "test")
        assert is_valid is False

    def test_all_null_column(self):
        """Test validation with all-null column."""
        data_with_nulls = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "open": [150.0],
                "high": [155.0],
                "low": [148.0],
                "close": [152.0],
                "volume": [None],  # All null
            }
        )

        is_valid = validate_schema(data_with_nulls, "test")
        # All-null required column should now fail validation (not just warn)
        assert is_valid is False


# =============================================================================
# Test Pipeline Functions
# =============================================================================


# =============================================================================
# W02 outcome truthfulness and isolation (F08/F09/F10/F25)
# =============================================================================


class TestOutcomeTruthfulness:
    """Optional failures stay visible; no-data, dry-run, and failure are distinct."""

    def test_optional_failure_is_failed_not_skipped_existing(self, tmp_path):
        """W02/F08: optional market failures are FAILED with their cause — not a
        success-shaped SKIPPED_EXISTING; the pipeline decides blocking/exit."""
        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=RuntimeError("macro timeout")),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 1), ["macro"], dry_run=False, parallel=False)

        assert results["macro"].status is SourceStatus.FAILED
        assert results["macro"].succeeded is False
        assert "macro timeout" in (results["macro"].error or "")

    def test_empty_fetch_is_no_data(self, tmp_path):
        """W02/F10: a genuine empty fetch is NO_DATA, never a write or a skip."""
        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=pl.DataFrame()),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 1), ["macro"], dry_run=False, parallel=False)

        assert results["macro"].status is SourceStatus.NO_DATA
        assert results["macro"].succeeded is False

    def test_parallel_write_exception_is_isolated_and_other_market_survives(self, tmp_path, sample_ohlcv_data):
        """W02/F09: a write exception in one market must not erase the run; the
        other market writes, the failing market is FAILED with its root cause."""

        def fake_write(df, market, trading_date, dry_run):
            if market == "us_equity":
                raise ValueError("disk full")
            return True

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data_with_config", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator._write_market", side_effect=fake_write),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 1), ["us_equity", "cn_ashare"], dry_run=False, parallel=True)

        assert results["us_equity"].status is SourceStatus.FAILED
        assert "disk full" in (results["us_equity"].error or "")
        assert results["cn_ashare"].status is SourceStatus.WRITTEN
        assert results["cn_ashare"].succeeded is True

    def test_serial_write_exception_matches_parallel_isolation(self, tmp_path, sample_ohlcv_data):
        """W02/F09: the serial path isolates write exceptions with the same
        statuses and retained cause as the parallel path."""

        def fake_write(df, market, trading_date, dry_run):
            if market == "us_equity":
                raise ValueError("disk full")
            return True

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator._write_market", side_effect=fake_write),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 1), ["us_equity", "cn_ashare"], dry_run=False, parallel=False)

        assert results["us_equity"].status is SourceStatus.FAILED
        assert "disk full" in (results["us_equity"].error or "")
        assert results["cn_ashare"].status is SourceStatus.WRITTEN

    def test_parallel_fetch_failure_retains_cause(self, tmp_path):
        """W02/F08/F10: a parallel fetch failure reaches the outcome with cause."""

        def failing_fetch(market, trading_date, **kwargs):
            raise RuntimeError(f"provider down for {market}")

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data_with_config", side_effect=failing_fetch),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 1), ["us_equity", "macro"], dry_run=False, parallel=True)

        assert results["us_equity"].status is SourceStatus.FAILED
        assert "provider down for us_equity" in (results["us_equity"].error or "")
        assert results["macro"].status is SourceStatus.FAILED
        assert "provider down for macro" in (results["macro"].error or "")


class TestPipelineIntegration:
    """Integration tests for the ingestion pipeline."""

    def test_fetch_market_data_invalid_market(self):
        """W02/F10: an unknown market raises instead of returning a misleading None."""
        with pytest.raises(ValueError, match="Unknown market"):
            fetch_market_data("invalid_market", date(2024, 1, 1))

    def test_fetch_market_data_cn_uses_hybrid_fetcher(self):
        """CN ingestion should use the hybrid fetcher with built-in fallback."""
        sample = pd.DataFrame(
            {
                "ticker": ["000001"],
                "date": [date(2024, 1, 1)],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "volume": [1000],
                "adj_close": [10.5],
            }
        )

        with patch("equity_lake.sources.cn_hybrid.CNHybridFetcher") as mock_fetcher:
            mock_fetcher.return_value.fetch.return_value = sample

            result = fetch_market_data("cn_ashare", date(2024, 1, 1))

        mock_fetcher.assert_called_once()
        assert result is not None
        assert not result.is_empty()

    def test_run_daily_ingestion_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test daily ingestion in dry-run mode."""
        with patch("equity_lake.ingestion.orchestrator.fetch_market_data") as mock_fetch:
            mock_fetch.return_value = sample_ohlcv_data

            with (
                patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
                patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            ):
                results = run_daily_ingestion(date(2024, 1, 1), ["us"], dry_run=True)

        # Results are keyed by the canonical long key (ADR-0010): short input is
        # normalized at the orchestrator boundary.
        assert "us_equity" in results
        assert results["us_equity"].succeeded is True
        # W02/F25: a direct dry-run never claims a write; the status is DRY_RUN.
        assert results["us_equity"].status is SourceStatus.DRY_RUN

    def test_skip_existing_marks_already_present_as_success(self, tmp_path, sample_ohlcv_data):
        """Regression test (P0): an idempotent rerun must not drop already-present markets.

        Previously ``_filter_markets_with_gaps`` dropped already-present markets
        entirely from the results dict, so the pipeline classified them as
        required-price failures on the next run.

        With the SourceOutcome refactor, the test also asserts the skipped vs
        written distinction is observable — the original motivation for the
        structured outcome.
        """
        # ``us`` already has the date -> skipped as already-present.
        # ``cn`` is missing -> fetched (we return sample data so the write succeeds).
        from equity_lake.core.config import TickerConfig

        us_tickers = set(TickerConfig().get_tickers_for_market("us", active_only=True))

        def coverage(market, market_dir, trading_date, con):
            return (True, us_tickers) if market == "us_equity" else (False, set())

        with (
            patch("equity_lake.ingestion.orchestrator._read_partition_coverage", side_effect=coverage),
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(
                date(2024, 1, 1),
                ["us", "cn"],
                dry_run=True,
                parallel=False,
            )

        assert results["us_equity"].succeeded is True  # already-present, not re-fetched
        assert results["us_equity"].status is SourceStatus.SKIPPED_EXISTING
        assert results["cn_ashare"].succeeded is True  # newly fetched
        # W02/F25: dry-run cn is simulated (DRY_RUN), never WRITTEN.
        assert results["cn_ashare"].status is SourceStatus.DRY_RUN

    def test_corrupt_partition_not_treated_as_present(self, tmp_path, sample_ohlcv_data):
        """A partition that exists but fails schema validation must be re-fetched.

        Regression guard for the partition-validity check: previously any
        ``COUNT(*) > 0`` was treated as "present", so a corrupt or all-null-key
        partition would be trusted as ``SKIPPED_EXISTING`` success. With the
        read-back validator, an invalid partition falls through to re-fetch.
        """
        # ``us`` exists (has_date True) but is corrupt (validate_schema False).
        # ``cn`` is missing entirely -> fetched.
        with (
            patch("equity_lake.ingestion.orchestrator._read_partition_coverage", return_value=(False, set())),
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(
                date(2024, 1, 1),
                ["us", "cn"],
                dry_run=True,
                parallel=False,
            )

        # Both markets were re-fetched (not skipped) and simulated as DRY_RUN
        # writes in dry-run mode.
        assert "us_equity" not in {m for m in results if results[m].status is SourceStatus.SKIPPED_EXISTING}
        assert results["us_equity"].status is SourceStatus.DRY_RUN
        assert results["cn_ashare"].status is SourceStatus.DRY_RUN

    def test_write_partition_structure(self, tmp_path, sample_ohlcv_data):
        """Test that Delta table is created on write."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            upsert_dataset(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False)

        from deltalake import DeltaTable

        market_dir = tmp_path / "us_equity"
        dt = DeltaTable(str(market_dir))
        assert dt.version() >= 0
        pdf = dt.to_pandas()
        assert len(pdf) > 0


# =============================================================================
# Test Date Handling
# =============================================================================


class TestDateHandling:
    """Tests for date handling and edge cases."""

    @pytest.mark.network
    def test_weekend_date(self):
        """Test handling of weekend dates."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        weekend_date = date(2024, 1, 6)  # Saturday

        # Should not crash, may return empty DataFrame
        df = fetcher.fetch(weekend_date)
        assert isinstance(df, pl.DataFrame)

    @pytest.mark.network
    def test_future_date(self):
        """Test handling of future dates."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        future_date = date(2030, 1, 1)

        # Should not crash, will return empty DataFrame
        df = fetcher.fetch(future_date)
        assert isinstance(df, pl.DataFrame)


# =============================================================================
# Performance Tests
# =============================================================================


class TestPerformance:
    """Performance tests for data ingestion."""

    @pytest.mark.slow
    def test_large_ticker_list(self):
        """Test fetching with large ticker list."""
        large_ticker_list = [f"TICKER{i}" for i in range(100)]
        fetcher = USEquityFetcher(tickers=large_ticker_list)

        # Should initialize without issues
        assert len(fetcher.tickers) == 100

    @pytest.mark.network
    def test_concurrent_fetches(self):
        """Test that multiple fetches don't interfere."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        trading_date = date(2024, 1, 2)

        # Fetch multiple times
        df1 = fetcher.fetch(trading_date)
        df2 = fetcher.fetch(trading_date)

        # Both should return DataFrames
        assert isinstance(df1, pl.DataFrame)
        assert isinstance(df2, pl.DataFrame)


# =============================================================================
# W03/F04/F05: explicit scope through all five markets + ticker-aware existing data
# =============================================================================


def _ohlcv_frame(tickers: list[str], trading_date: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": list(tickers),
            "date": [trading_date] * len(tickers),
            "open": [100.0] * len(tickers),
            "high": [101.0] * len(tickers),
            "low": [99.0] * len(tickers),
            "close": [100.5] * len(tickers),
            "volume": [1000.0] * len(tickers),
            "adj_close": [100.5] * len(tickers),
        },
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "adj_close": pl.Float64,
        },
    )


class TestExplicitScopeOrchestration:
    """Explicit scope is resolved once and forwarded exactly to each price market."""

    @pytest.mark.parametrize(
        ("market", "tickers"),
        [
            ("us_equity", ["AAPL"]),
            ("cn_ashare", ["600000"]),
            ("hk_sg_equity", ["0700.HK"]),
            ("jpx_equity", ["7203.T"]),
            ("krx_equity", ["005930"]),
        ],
    )
    def test_each_price_market_receives_exact_scoped_tickers(self, tmp_path, market, tickers) -> None:
        """W03/F04: explicit scope reaches all five price markets, not just US."""
        captured: list[tuple[str, list[str] | None]] = []

        def spy(mkt, trading_date, **kwargs):
            captured.append((mkt, kwargs.get("explicit_tickers")))
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            run_daily_ingestion(date(2024, 1, 2), [market], dry_run=True, parallel=False, explicit_tickers=tickers)

        assert captured == [(market, tickers)]

    def test_empty_explicit_list_never_broadens(self, tmp_path) -> None:
        """W03/F04: ``[]`` is preserved end to end and never becomes the configured universe."""
        captured: list[list[str] | None] = []

        def spy(mkt, trading_date, **kwargs):
            captured.append(kwargs.get("explicit_tickers"))
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 2), ["us_equity"], dry_run=True, parallel=False, explicit_tickers=[])

        assert captured == [[]]
        assert results["us_equity"].status is SourceStatus.NO_DATA

    def test_ambiguous_multi_market_rejects_before_any_fetch(self, tmp_path) -> None:
        """W03/F04: an ambiguous multi-market ticker raises and performs no fetch."""
        from equity_lake.core.config_models import MarketConfig, TickerConfigRoot, TickerMetadata

        def meta(symbol: str) -> TickerMetadata:
            return TickerMetadata(symbol=symbol, name=symbol, exchange="TEST", sector="Tech")

        config = TickerConfigRoot(
            markets={
                "us": MarketConfig(currency="USD", tickers=[meta("DUP")]),
                "cn": MarketConfig(currency="CNY", tickers=[meta("DUP")]),
            }
        )
        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data") as mock_fetch,
            pytest.raises(ValueError, match="ambiguous: DUP"),
        ):
            run_daily_ingestion(date(2024, 1, 2), ["us_equity", "cn_ashare"], ticker_config=config, explicit_tickers=["DUP"])
        mock_fetch.assert_not_called()

    def test_parallel_branch_uses_per_market_scope(self, tmp_path) -> None:
        """W03/F04: the parallel branch also receives the per-market resolved list."""
        from equity_lake.core.config import TickerConfig

        config = TickerConfig()
        us_ticker = config.get_tickers_for_market("us", active_only=True)[0]
        cn_ticker = config.get_tickers_for_market("cn", active_only=True)[0]
        captured: dict[str, list[str] | None] = {}

        def spy(mkt, trading_date, **kwargs):
            captured[mkt] = kwargs.get("explicit_tickers")
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data_with_config", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            run_daily_ingestion(
                date(2024, 1, 2),
                ["us_equity", "cn_ashare"],
                dry_run=True,
                parallel=True,
                ticker_config=config,
                explicit_tickers=[us_ticker, cn_ticker],
            )

        assert captured == {"us_equity": [us_ticker], "cn_ashare": [cn_ticker]}


class TestTickerAwareExistingData:
    """W03/F05: existing-date checks must prove requested-ticker completeness."""

    @staticmethod
    def _write_partition(tmp_path, tickers: list[str], trading_date: date) -> None:
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            assert upsert_dataset(_ohlcv_frame(tickers, trading_date), "01_bronze/market_data/us_equity", trading_date, dry_run=False) is True

    def test_partial_partition_is_not_skipped_and_fetches_only_missing(self, tmp_path) -> None:
        """One ticker present + one absent -> not skipped; scoped fetch targets the missing ticker."""
        trading_date = date(2024, 1, 2)
        self._write_partition(tmp_path, ["AAPL"], trading_date)
        captured: list[list[str] | None] = []

        def spy(mkt, trading_date, **kwargs):
            captured.append(kwargs.get("explicit_tickers"))
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(trading_date, ["us_equity"], dry_run=True, parallel=False, explicit_tickers=["AAPL", "MSFT"])

        assert results["us_equity"].status is not SourceStatus.SKIPPED_EXISTING
        assert captured == [["MSFT"]]  # the late-added ticker is the only fetch scope

    def test_unspecified_scope_incomplete_partition_fetches_fetcher_default(self, tmp_path) -> None:
        """W03 review: an unspecified (None) request never fetches an over-broad explicit list.

        The configured-scope coverage check decides *whether* to fetch; the fetch
        itself stays ``None`` so the fetcher applies its own filtered/fallback
        default exactly as before W03 (requested scope never broadens).
        """
        from equity_lake.core.config import TickerConfig
        from equity_lake.core.config_models import MarketConfig, TickerMetadata

        def meta(symbol: str) -> TickerMetadata:
            return TickerMetadata(symbol=symbol, name=symbol, exchange="TEST", sector="Tech")

        config = TickerConfig(markets={"us": MarketConfig(currency="USD", tickers=[meta("AAPL"), meta("MSFT")])})
        trading_date = date(2024, 1, 2)
        self._write_partition(tmp_path, ["AAPL"], trading_date)
        captured: list[list[str] | None] = []

        def spy(mkt, trading_date, **kwargs):
            captured.append(kwargs.get("explicit_tickers"))
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(trading_date, ["us_equity"], dry_run=True, parallel=False, ticker_config=config)

        assert results["us_equity"].status is not SourceStatus.SKIPPED_EXISTING
        assert captured == [None]  # fetcher default, not an explicit ticker list

    def test_complete_partition_is_skipped_without_fetch(self, tmp_path) -> None:
        """Every requested ticker present in a valid partition -> SKIPPED_EXISTING."""
        trading_date = date(2024, 1, 2)
        self._write_partition(tmp_path, ["AAPL", "MSFT"], trading_date)

        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data") as mock_fetch,
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(trading_date, ["us_equity"], dry_run=True, parallel=False, explicit_tickers=["AAPL", "MSFT"])

        assert results["us_equity"].status is SourceStatus.SKIPPED_EXISTING
        mock_fetch.assert_not_called()

    def test_corrupt_partition_is_refetched_in_full(self, tmp_path) -> None:
        """A schema-invalid partition cannot prove presence, so the full scope is fetched."""
        trading_date = date(2024, 1, 2)
        self._write_partition(tmp_path, ["AAPL"], trading_date)
        captured: list[list[str] | None] = []

        def spy(mkt, trading_date, **kwargs):
            captured.append(kwargs.get("explicit_tickers"))
            return pl.DataFrame()

        with (
            patch("equity_lake.ingestion.orchestrator._read_partition_coverage", return_value=(False, {"AAPL"})),
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", side_effect=spy),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            run_daily_ingestion(trading_date, ["us_equity"], dry_run=True, parallel=False, explicit_tickers=["AAPL", "MSFT"])

        assert captured == [["AAPL", "MSFT"]]

    def test_direct_ingest_dry_run_writes_nothing(self, tmp_path, sample_ohlcv_data) -> None:
        """W03: a direct ingest dry-run leaves no partition on disk."""
        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            results = run_daily_ingestion(date(2024, 1, 2), ["us_equity"], dry_run=True, parallel=False, explicit_tickers=["AAPL"])

        assert results["us_equity"].status is SourceStatus.DRY_RUN
        assert list(tmp_path.rglob("*")) == []
