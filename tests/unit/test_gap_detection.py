"""Regression tests for gap detection.

Covers the P0 bug where ``find_missing_dates`` silently returned ``{}`` when
``business_days_only=True`` (the default) because the IN-list filter referenced
alias ``d`` inside the ``date_range`` CTE where it was not yet in scope.
"""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.ingestion.gap_detection import GapDetector, _calendar_key
from equity_lake.ingestion.types import MARKET_DIR_MAP, OPTIONAL_ENRICHMENT_MARKETS


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


def test_calendar_key_resolves_via_market_map() -> None:
    """Calendar keys come from the market map, not substring slicing of the table dir.

    Substring derivation turned ``02_silver/analyst_ratings`` into the bogus
    calendar key ``analyst_ratings`` (zero trading days → silent no-op).
    """
    assert _calendar_key(MARKET_DIR_MAP["us"]) == "us"
    assert _calendar_key(MARKET_DIR_MAP["hk_sg"]) == "hk_sg"
    # Direct market keys pass through unchanged.
    assert _calendar_key("us_equity") == "us_equity"
    # Non-price tables resolve to their market key, which has no calendar.
    assert _calendar_key(MARKET_DIR_MAP["us_analyst_ratings"]) == "us_analyst_ratings"
    # Tables shared by several markets resolve to one of their market keys — an enrichment key either way.
    assert _calendar_key(MARKET_DIR_MAP["rss_news"]) in OPTIONAL_ENRICHMENT_MARKETS


def test_find_missing_dates_accepts_medallion_dir_path(tmp_path) -> None:
    """auto_backfill passes full medallion dirs (e.g. 01_bronze/market_data/us_equity) — these must resolve to a calendar."""
    market_rel = MARKET_DIR_MAP["us"]
    market_dir = tmp_path / market_rel
    market_dir.mkdir(parents=True)
    df = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)],
            "close": [150.0, 151.0, 152.0],
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")

    with GapDetector(lake_path=tmp_path) as det:
        missing = det.find_missing_dates(
            market_rel,
            ticker="AAPL",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=True,
        )
    assert date(2024, 1, 4) in missing["AAPL"]


def test_find_missing_dates_table_without_calendar_returns_empty(tmp_path) -> None:
    """A ticker-bearing table with no mapped calendar yields no expected days (and no query)."""
    market_dir = tmp_path / "02_silver" / "analyst_ratings"
    market_dir.mkdir(parents=True)
    df = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 2), date(2024, 1, 4)],
            "rating": ["buy", "hold"],
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")

    with GapDetector(lake_path=tmp_path) as det:
        missing = det.find_missing_dates(
            "02_silver/analyst_ratings",
            ticker=None,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=True,
        )
    assert missing == {}


def test_find_missing_dates_tickerless_table_raises_without_calendar_filter(tmp_path) -> None:
    """Documents why auto_backfill skips non-price markets: ticker-less bronze tables
    (e.g. raw_articles) make the SELECT DISTINCT ticker path raise a BinderException
    once no trading-calendar filter short-circuits the query.
    """
    market_dir = tmp_path / "01_bronze" / "raw_articles"
    market_dir.mkdir(parents=True)
    df = pl.DataFrame(
        {
            "article_id": ["a1", "a2"],
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "title": ["t1", "t2"],
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")

    with GapDetector(lake_path=tmp_path) as det, pytest.raises(duckdb.Error):
        det.find_missing_dates(
            "01_bronze/raw_articles",
            ticker=None,
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 5),
            business_days_only=False,
        )


def test_hk_sg_intersection_removes_false_gaps_for_si_tickers(tmp_path) -> None:
    """SG-holiday/HK-trading dates must not be reported as gaps for .SI tickers.

    2024-08-09 is Singapore National Day: XHKG trades but XSES is closed. With
    XHKG-only expected dates, a Singapore ticker present on every common session
    was falsely flagged as missing 2024-08-09.
    """
    market_dir = tmp_path / MARKET_DIR_MAP["hk_sg"]
    market_dir.mkdir(parents=True)
    common_sessions = [date(2024, 8, 5), date(2024, 8, 6), date(2024, 8, 7), date(2024, 8, 8), date(2024, 8, 12)]
    df = pl.DataFrame(
        {
            "ticker": ["D05.SI"] * len(common_sessions),
            "date": common_sessions,
            "close": [10.0] * len(common_sessions),
        }
    )
    write_deltalake(str(market_dir), df.to_arrow(), mode="append")

    with GapDetector(lake_path=tmp_path) as det:
        missing = det.find_missing_dates(
            MARKET_DIR_MAP["hk_sg"],
            ticker=None,
            start_date=date(2024, 8, 5),
            end_date=date(2024, 8, 12),
            business_days_only=True,
        )
    assert missing.get("D05.SI", []) == []
