"""Tests for ingestion.backfill.backfill_date_range."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from equity_lake.ingestion.backfill import backfill_date_range


@pytest.fixture
def _mock_ingestion():
    """Patch run_daily_ingestion at its source (backfill imports it locally)."""
    with patch("equity_lake.ingestion.orchestrator.run_daily_ingestion") as mock:
        yield mock


class TestBackfillDateRange:
    def test_empty_range_returns_zero(self, _mock_ingestion) -> None:
        total = backfill_date_range(
            start_date=date(2024, 1, 10),
            end_date=date(2024, 1, 1),
            markets=["us"],
        )
        assert total == 0
        _mock_ingestion.assert_not_called()

    def test_single_day_single_market_success(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": True}
        total = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
        )
        assert total == 1
        _mock_ingestion.assert_called_once()
        call = _mock_ingestion.call_args
        assert call.kwargs["trading_date"] == date(2024, 1, 2)
        assert call.kwargs["markets"] == ["us"]
        assert call.kwargs["skip_existing"] is True
        assert call.kwargs["parallel"] is False

    def test_multi_day_multi_market_call_count(self, _mock_ingestion) -> None:
        # run_daily_ingestion returns a dict keyed by the requested market.
        _mock_ingestion.side_effect = lambda trading_date, markets, **kwargs: {markets[0]: True}
        total = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            markets=["us", "cn"],
        )
        # 2 days x 2 markets = 4 calls, all successful.
        assert _mock_ingestion.call_count == 4
        assert total == 4

    def test_partial_failure_counts_successes_only(self, _mock_ingestion) -> None:
        # us succeeds, cn returns False (skipped/not fetched).
        def side_effect(trading_date, markets, **kwargs):
            return {markets[0]: True} if markets[0] == "us" else {markets[0]: False}

        _mock_ingestion.side_effect = side_effect
        total = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us", "cn"],
        )
        assert total == 1  # only us counted

    def test_exception_is_caught_and_loop_continues(self, _mock_ingestion) -> None:
        # Day 1 raises (caught), day 2 succeeds -> loop must continue past the error.
        _mock_ingestion.side_effect = [RuntimeError("network down"), {"us": True}]
        total = backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            markets=["us"],
        )
        assert _mock_ingestion.call_count == 2
        assert total == 1  # only day 2 counted

    def test_dry_run_forwarded(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": True}
        backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
            dry_run=True,
        )
        assert _mock_ingestion.call_args.kwargs["dry_run"] is True

    def test_explicit_tickers_forwarded(self, _mock_ingestion) -> None:
        _mock_ingestion.return_value = {"us": True}
        backfill_date_range(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
            markets=["us"],
            explicit_tickers=["AAPL"],
        )
        assert _mock_ingestion.call_args.kwargs["explicit_tickers"] == ["AAPL"]
