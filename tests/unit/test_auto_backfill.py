"""Tests for ingestion.auto_backfill.find_and_fill_gaps.

Covers the P0 handoff items: enrichment markets (several of which share the
ticker-less ``01_bronze/raw_articles`` table) must be skipped gracefully
instead of crashing gap detection, and the run must iterate only the required
price markets.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.ingestion.auto_backfill import find_and_fill_gaps
from equity_lake.ingestion.gap_detection import GapDetector
from equity_lake.ingestion.types import MARKET_DIR_MAP, REQUIRED_PRICE_MARKETS, SourceOutcome, SourceStatus

_WRITTEN = SourceOutcome(SourceStatus.WRITTEN)


def _write_table(lake: Path, rel_dir: str, frame: pl.DataFrame) -> None:
    target = lake / rel_dir
    target.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(target), frame.to_arrow(), mode="append")


@pytest.fixture
def lake(tmp_path: Path) -> Path:
    """Tmp lake: us_equity missing 2024-01-04, plus a ticker-less raw_articles table."""
    _write_table(
        tmp_path,
        MARKET_DIR_MAP["us"],
        pl.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)],
                "close": [150.0, 151.0, 152.0],
            }
        ),
    )
    _write_table(
        tmp_path,
        "01_bronze/raw_articles",
        pl.DataFrame({"article_id": ["a1"], "date": [date(2024, 1, 2)], "title": ["t"]}),
    )
    return tmp_path


def _detector_on(lake: Path) -> Any:
    """Point auto_backfill's GapDetector() at the tmp lake (it takes no lake_path arg)."""
    return patch("equity_lake.ingestion.auto_backfill.GapDetector", side_effect=lambda: GapDetector(lake_path=lake))


def test_default_scans_only_price_market_dirs(lake: Path) -> None:
    """markets=None must target exactly the five required price markets — never enrichment dirs."""
    scanned: list[str] = []
    original = GapDetector.find_missing_dates

    def recording(self: GapDetector, market: str, **kwargs: Any) -> dict[str, list[date]]:
        scanned.append(market)
        return original(self, market, **kwargs)

    with (
        patch.object(GapDetector, "find_missing_dates", recording),
        _detector_on(lake),
        patch("equity_lake.ingestion.auto_backfill.run_daily_ingestion") as run_daily,
    ):
        results = find_and_fill_gaps(end_date=date(2024, 1, 5), days_back=5)

    assert sorted(scanned) == sorted(MARKET_DIR_MAP[m] for m in REQUIRED_PRICE_MARKETS)
    assert results == {"us": 1}
    run_daily.assert_called_once()


def test_tickerless_bronze_market_skipped_gracefully(lake: Path) -> None:
    """A market backed by a ticker-less bronze table (raw_articles) must be skipped, not crash the run."""
    with _detector_on(lake), patch("equity_lake.ingestion.auto_backfill.run_daily_ingestion") as run_daily:
        results = find_and_fill_gaps(end_date=date(2024, 1, 5), days_back=5, markets=["rss_news", "us"])

    assert "rss_news" not in results
    assert results == {"us": 1}
    assert all(call.kwargs["markets"] == ["us"] for call in run_daily.call_args_list)


def test_explicit_enrichment_market_skipped() -> None:
    """Every OPTIONAL_ENRICHMENT_MARKETS member is skipped with a warning — not just the old hard-coded trio."""
    with (
        patch("equity_lake.ingestion.auto_backfill.GapDetector") as detector_cls,
        patch("equity_lake.ingestion.auto_backfill.run_daily_ingestion") as run_daily,
    ):
        results = find_and_fill_gaps(end_date=date(2024, 1, 5), days_back=5, markets=["us_analyst_ratings"])

    detector_cls.return_value.__enter__.return_value.find_missing_dates.assert_not_called()
    run_daily.assert_not_called()
    assert results == {}


def test_missing_price_table_does_not_abort_run(tmp_path: Path) -> None:
    """A price market with no table yet (fresh lake) is logged and skipped, not fatal for the whole run."""
    with _detector_on(tmp_path), patch("equity_lake.ingestion.auto_backfill.run_daily_ingestion") as run_daily:
        results = find_and_fill_gaps(end_date=date(2024, 1, 5), days_back=5, markets=["us", "cn"])

    assert results == {}
    run_daily.assert_not_called()


def test_dry_run_reports_without_ingesting(lake: Path) -> None:
    with _detector_on(lake), patch("equity_lake.ingestion.auto_backfill.run_daily_ingestion") as run_daily:
        results = find_and_fill_gaps(end_date=date(2024, 1, 5), days_back=5, markets=["us"], dry_run=True)

    assert results == {"us": 1}
    run_daily.assert_not_called()
