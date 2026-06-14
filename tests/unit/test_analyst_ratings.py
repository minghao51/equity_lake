"""Tests for the Finnhub analyst rating fetcher."""

from datetime import date
from unittest.mock import patch

import pytest

from equity_lake.core.schemas import ANALYST_RATING_COLUMNS
from equity_lake.sources.analyst_ratings import AnalystRatingFetcher


@pytest.fixture
def mock_recommendation():
    return [
        {
            "buy": 15,
            "hold": 8,
            "period": "2024-01-01",
            "sell": 2,
            "strongBuy": 10,
            "strongSell": 1,
            "symbol": "AAPL",
        }
    ]


@pytest.fixture
def mock_price_target():
    return {
        "lastUpdated": "2024-01-15",
        "symbol": "AAPL",
        "targetHigh": 250.0,
        "targetLow": 150.0,
        "targetMean": 195.5,
        "targetMedian": 195.0,
        "numberOfAnalysts": 30,
    }


class TestAnalystRatingFetcherInit:
    def test_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="FINNHUB_API_KEY"):
            AnalystRatingFetcher()

    def test_init_with_explicit_key(self):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL"])
        assert fetcher.api_key == "test-key"
        assert fetcher.tickers == ["AAPL"]
        assert fetcher.market == "us_analyst_ratings"


class TestAnalystRatingFetch:
    def test_fetch_returns_empty_when_no_tickers(self):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=[])
        df = fetcher.fetch(date(2024, 1, 15))
        assert df.is_empty()

    def test_fetch_returns_correct_schema(self, mock_recommendation, mock_price_target):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL"])

        with (
            patch.object(fetcher, "_get_recommendation", return_value=mock_recommendation),
            patch.object(fetcher, "_get_price_target", return_value=mock_price_target),
        ):
            df = fetcher.fetch(date(2024, 1, 15))

        assert not df.is_empty()
        assert set(ANALYST_RATING_COLUMNS).issubset(set(df.columns))

    def test_consensus_score_calculation(self, mock_recommendation, mock_price_target):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL"])

        with (
            patch.object(fetcher, "_get_recommendation", return_value=mock_recommendation),
            patch.object(fetcher, "_get_price_target", return_value=mock_price_target),
        ):
            df = fetcher.fetch(date(2024, 1, 15))

        row = df.row(0, named=True)
        assert row["strong_buy"] == 10
        assert row["buy"] == 15
        assert row["hold"] == 8
        assert row["sell"] == 2
        assert row["strong_sell"] == 1

        total = 10 + 15 + 8 + 2 + 1
        expected = (10 * 2 + 15 * 1 + 8 * 0 + 2 * -1 + 1 * -2) / total
        assert abs(row["consensus_score"] - round(expected, 4)) < 1e-6
        assert row["consensus_label"] == "buy"

    def test_price_target_fields_populated(self, mock_recommendation, mock_price_target):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL"])

        with (
            patch.object(fetcher, "_get_recommendation", return_value=mock_recommendation),
            patch.object(fetcher, "_get_price_target", return_value=mock_price_target),
        ):
            df = fetcher.fetch(date(2024, 1, 15))

        row = df.row(0, named=True)
        assert row["price_target_mean"] == 195.5
        assert row["price_target_median"] == 195.0
        assert row["price_target_high"] == 250.0
        assert row["price_target_low"] == 150.0
        assert row["price_target_count"] == 30

    def test_fetch_handles_api_error(self):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL", "MSFT"])

        with patch.object(fetcher, "_fetch_ticker", side_effect=Exception("API error")):
            df = fetcher.fetch(date(2024, 1, 15))

        assert df.is_empty()

    def test_fetch_skips_ticker_with_no_recommendation(self, mock_price_target):
        fetcher = AnalystRatingFetcher(api_key="test-key", tickers=["AAPL"])

        with (
            patch.object(fetcher, "_get_recommendation", return_value=[]),
            patch.object(fetcher, "_get_price_target", return_value=mock_price_target),
        ):
            df = fetcher.fetch(date(2024, 1, 15))

        assert df.is_empty()
