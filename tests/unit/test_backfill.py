"""Tests for ingestion.backfill.backfill_date_range."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from equity_lake.ingestion.backfill import backfill_date_range
from equity_lake.ingestion.types import SourceOutcome, SourceStatus

_WRITTEN = SourceOutcome(SourceStatus.WRITTEN)
_FAILED = SourceOutcome(SourceStatus.FAILED)


@pytest.fixture
def _mock_ingestion():
    """Patch run_daily_ingestion at its source (backfill imports it locally)."""
    with patch("equity_lake.ingestion.orchestrator.run_daily_ingestion") as mock:
        yield mock


class TestBackfillDateRange:
    def test_empty_range_returns_zero(self, _mock_ingestion) -> None:
        result = backfill_date_range(
            start_date=date(2024, 1, 10),
            end_date=date(2024, 1, 1),
            markets=["us"],
        )
        assert result.total_dates == 0
        assert result.market_date_attempts == 0
        assert result.trading_dates == 0
        _mock_ingestion.assert_not_called()

    def test_single_day_single_market_success(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": _WRITTEN}
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
        )
        assert result.total_dates == 1
        assert result.succeeded == 1
        assert result.failed == 0
        assert result.trading_dates == 1
        assert result.market_date_attempts == 1
        assert result.dates_recovered == 1
        assert result.per_market == {"us": 1}
        # F25: the frozen scope is captured before the date loop.
        assert result.start_date == date(2024, 1, 2)
        assert result.end_date == date(2024, 1, 2)
        assert result.markets == ("us",)
        assert result.instruments is None
        assert result.authorized is False
        _mock_ingestion.assert_called_once()
        call = _mock_ingestion.call_args
        assert call.kwargs["trading_date"] == date(2024, 1, 2)
        assert call.kwargs["markets"] == ["us"]
        assert call.kwargs["skip_existing"] is True
        assert call.kwargs["parallel"] is False

    def test_multi_day_multi_market_call_count(self, _mock_ingestion) -> None:
        # run_daily_ingestion returns a dict keyed by the requested market.
        _mock_ingestion.side_effect = lambda trading_date, markets, **kwargs: {markets[0]: _WRITTEN}
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            markets=["us", "cn"],
        )
        # 2 days x 2 markets = 4 calls, all successful.
        assert _mock_ingestion.call_count == 4
        assert result.total_dates == 4
        assert result.market_date_attempts == 4
        assert result.trading_dates == 2
        assert result.dates_recovered == 2
        assert result.per_market == {"us": 2, "cn": 2}

    def test_partial_failure_counts_successes_only(self, _mock_ingestion) -> None:
        # us succeeds, cn returns False (skipped/not fetched).
        def side_effect(trading_date, markets, **kwargs):
            return {markets[0]: _WRITTEN} if markets[0] == "us" else {markets[0]: _FAILED}

        _mock_ingestion.side_effect = side_effect
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us", "cn"],
        )
        assert result.total_dates == 1  # only us counted
        assert result.failed == 1
        assert result.dates_recovered == 0  # a date is recovered only when every market succeeds

    def test_exception_is_caught_and_loop_continues(self, _mock_ingestion) -> None:
        # Day 1 raises (caught), day 2 succeeds -> loop must continue past the error.
        _mock_ingestion.side_effect = [RuntimeError("network down"), {"us": _WRITTEN}]
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            markets=["us"],
        )
        assert _mock_ingestion.call_count == 2
        assert result.total_dates == 1  # only day 2 counted
        assert result.failed == 1

    def test_frozen_scope_records_instruments_and_authorization(self, _mock_ingestion) -> None:
        """W03/F25: the result freezes the requested instruments and authorization."""
        _mock_ingestion.return_value = {"us": _WRITTEN}
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
            explicit_tickers=["AAPL", "MSFT"],
            authorized=True,
        )
        assert result.instruments == ("AAPL", "MSFT")
        assert result.authorized is True
        assert _mock_ingestion.call_args.kwargs["explicit_tickers"] == ["AAPL", "MSFT"]
        payload = result.to_payload()
        assert payload["instruments"] == ["AAPL", "MSFT"]
        assert payload["authorized"] is True
        assert payload["succeeded"] == 1
        assert payload["per_market"] == {"us": 1}

    def test_zero_recovery_reports_zero_without_fabricating_success(self, _mock_ingestion) -> None:
        """W03/F25: an all-failed recovery reports zero recovered dates and 100% failure."""
        _mock_ingestion.return_value = {"us": _FAILED}
        result = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            markets=["us"],
        )
        assert result.total_dates == 0
        assert result.dates_recovered == 0
        assert result.failed == 2
        assert result.market_date_attempts == 2

    def test_not_scheduled_days_are_skipped_not_recovered_or_failed(self, _mock_ingestion) -> None:
        """W03/D04: a closed exchange is a planned skip, not a recovered date or failure."""
        _mock_ingestion.return_value = {"us": SourceOutcome(SourceStatus.NOT_SCHEDULED)}
        result = backfill_date_range(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            markets=["us"],
        )
        assert result.market_date_attempts == 1
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.dates_recovered == 0
        assert result.per_market == {"us": 0}
        assert result.total_dates == 0

    def test_dry_run_forwarded(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": _WRITTEN}
        backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
            dry_run=True,
        )
        assert _mock_ingestion.call_args.kwargs["dry_run"] is True

    def test_explicit_tickers_forwarded(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": _WRITTEN}
        backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
            explicit_tickers=["AAPL"],
        )
        assert _mock_ingestion.call_args.kwargs["explicit_tickers"] == ["AAPL"]


class TestBackfillDryRunNoWrites:
    """W03: a backfill dry-run must not persist anything (temporary lake)."""

    def test_dry_run_writes_nothing(self, tmp_path, sample_ohlcv_data) -> None:
        with (
            patch("equity_lake.ingestion.orchestrator.fetch_market_data", return_value=sample_ohlcv_data),
            patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
        ):
            result = backfill_date_range(
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
                markets=["us_equity"],
                dry_run=True,
                explicit_tickers=["AAPL"],
            )

        assert result.total_dates == 1
        assert result.market_date_attempts == 1
        assert list(tmp_path.rglob("*")) == []
