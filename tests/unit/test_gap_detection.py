"""Regression tests for gap detection.

Covers the P0 bug where ``find_missing_dates`` silently returned ``{}`` when
``business_days_only=True`` (the default) because the IN-list filter referenced
alias ``d`` inside the ``date_range`` CTE where it was not yet in scope.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.ingestion.gap_detection import GapDetector


@pytest.fixture
def lake_with_gaps(tmp_path):
    """Build a Delta table under ``tmp_path/us_equity`` with a known gap.

    Writes AAPL data for 2024-01-02, 2024-01-03, 2024-01-05 — missing 2024-01-04.
    All four dates are US trading days (no weekends/holidays in this window).
    """
    market_dir = tmp_path / "us_equity"
    df = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)],
            "close": [150.0, 151.0, 152.0],
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")
    return tmp_path


def test_find_missing_dates_business_days_only(lake_with_gaps) -> None:
    """business_days_only=True (the default) must detect the 2024-01-04 gap."""
    with GapDetector(lake_path=lake_with_gaps) as det:
        missing = det.find_missing_dates(
            "us_equity",
            ticker="AAPL",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=True,
        )
    assert "AAPL" in missing
    assert date(2024, 1, 4) in missing["AAPL"]


def test_find_missing_dates_all_days(lake_with_gaps) -> None:
    """business_days_only=False should also detect the gap."""
    with GapDetector(lake_path=lake_with_gaps) as det:
        missing = det.find_missing_dates(
            "us_equity",
            ticker="AAPL",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=False,
        )
    assert "AAPL" in missing
    assert date(2024, 1, 4) in missing["AAPL"]


def test_find_missing_dates_no_gaps(tmp_path) -> None:
    """When all trading days are present, no gaps should be reported."""
    market_dir = tmp_path / "us_equity"
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"] * 4,
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
            "close": [150.0, 151.0, 149.0, 152.0],
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")

    with GapDetector(lake_path=tmp_path) as det:
        missing = det.find_missing_dates(
            "us_equity",
            ticker="AAPL",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=True,
        )
    assert missing.get("AAPL", []) == []


def test_find_missing_dates_all_tickers(lake_with_gaps) -> None:
    """The all-tickers path (ticker=None) must also detect gaps."""
    with GapDetector(lake_path=lake_with_gaps) as det:
        missing = det.find_missing_dates(
            "us_equity",
            ticker=None,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=True,
        )
    assert "AAPL" in missing
    assert date(2024, 1, 4) in missing["AAPL"]
