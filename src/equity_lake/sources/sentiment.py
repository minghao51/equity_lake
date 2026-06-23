"""Finnhub social sentiment fetcher for US equities."""

import os
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
import requests
import structlog

from equity_lake.core.schemas import SOCIAL_COLUMNS
from equity_lake.ingestion.parallel import fetch_items_parallel
from equity_lake.sources.base import MarketDataFetcher

logger = structlog.get_logger()


# Finnhub API endpoint
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubSocialSentimentFetcher(MarketDataFetcher):
    """
    Fetch social sentiment from Finnhub API for US equities.

    Finnhub provides free access to Reddit and Twitter sentiment metrics
    for US stocks. This fetcher retrieves social mentions and sentiment scores.

    Attributes:
        api_key: Finnhub API key (from FINNHUB_API_KEY env var)
        tickers: List of ticker symbols to fetch
        retry_attempts: Number of retry attempts for API calls
        retry_delay: Base delay between retries
        max_workers: Maximum parallel workers (default: 1, sequential)
    """

    def __init__(
        self,
        api_key: str | None = None,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        max_workers: int = 1,
    ):
        """
        Initialize the Finnhub social sentiment fetcher.

        Args:
            api_key: Finnhub API key (default: from FINNHUB_API_KEY env var)
            tickers: List of ticker symbols
            retry_attempts: Number of retry attempts
            retry_delay: Base delay between retries
            max_workers: Maximum parallel workers (default: 1, sequential)
        """
        super().__init__(retry_attempts, retry_delay)

        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("Finnhub API key not found. Set FINNHUB_API_KEY environment variable or pass api_key parameter.")

        self.tickers = tickers or []
        self.max_workers = max_workers

        logger.info(
            "Initialized FinnhubSocialSentimentFetcher",
            tickers_count=len(self.tickers),
            max_workers=max_workers,
        )

    def fetch(self, trading_date: date) -> pl.DataFrame:
        """
        Fetch social sentiment for all tickers on the given date.

        Args:
            trading_date: Date to fetch sentiment for

        Returns:
            DataFrame with columns: SOCIAL_COLUMNS
        """
        if not self.tickers:
            logger.warning("No tickers configured for social sentiment fetching")
            return pl.DataFrame()

        logger.info(
            "Fetching social sentiment for %s tickers on %s (workers=%s)",
            len(self.tickers),
            trading_date,
            self.max_workers,
        )

        all_metrics = fetch_items_parallel(
            self.tickers,
            self._fetch_sentiment_for_ticker,
            trading_date,
            max_workers=self.max_workers,
            rate_limit_seconds=1.0,
        )

        if not all_metrics:
            logger.warning("No social sentiment metrics fetched for any ticker")
            return pl.DataFrame()

        logger.info("Fetched %s total social sentiment records", len(all_metrics))

        # Convert to DataFrame
        df = pl.DataFrame(all_metrics)

        # Ensure all columns present
        for col in SOCIAL_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit("mention_count").alias(col)) if col == "social_metric" else df.with_columns(pl.lit(None).alias(col))

        df = df.select(SOCIAL_COLUMNS)

        logger.info(
            "Returning %s social sentiment records for %s",
            len(df),
            trading_date,
        )

        return df

    def _fetch_sentiment_for_ticker(
        self,
        ticker: str,
        trading_date: date,
    ) -> list[dict[str, Any]]:
        """
        Fetch social sentiment metrics for a single ticker.

        Args:
            ticker: Ticker symbol
            trading_date: Date to fetch

        Returns:
            List of sentiment metric dictionaries
        """
        # Finnhub news-sentiment API provides social sentiment
        url = f"{FINNHUB_BASE_URL}/news-sentiment"
        params = {
            "symbol": ticker,
            "token": self.api_key,
        }

        try:
            response = self._retry_on_failure(
                requests.get,
                url,
                params=params,
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error("API request failed for %s: %s", ticker, exc)
            return []

        # Parse JSON response
        try:
            data = response.json()
        except Exception as exc:
            logger.error("Failed to parse JSON response for %s: %s", ticker, exc)
            return []

        if not isinstance(data, dict):
            logger.warning("Unexpected response format for %s: %s", ticker, type(data))
            return []

        # Extract sentiment data
        sentiment_data = data.get("sentiment", {})
        if not sentiment_data:
            logger.debug("No sentiment data for %s", ticker)
            return []

        # Parse metrics for Reddit and Twitter
        parsed_metrics = []

        # Process Reddit sentiment
        reddit_data = sentiment_data.get("reddit", {})
        if reddit_data:
            reddit_metric = self._parse_sentiment_metric(reddit_data, ticker, trading_date, "reddit")
            if reddit_metric:
                parsed_metrics.append(reddit_metric)

        # Process Twitter sentiment
        twitter_data = sentiment_data.get("twitter", {})
        if twitter_data:
            twitter_metric = self._parse_sentiment_metric(twitter_data, ticker, trading_date, "twitter")
            if twitter_metric:
                parsed_metrics.append(twitter_metric)

        logger.debug("Fetched %s sentiment metrics for %s", len(parsed_metrics), ticker)
        return parsed_metrics

    def _parse_sentiment_metric(
        self,
        source_data: dict,
        ticker: str,
        trading_date: date,
        source: str,
    ) -> dict[str, Any] | None:
        """
        Parse a single sentiment metric from Finnhub API response.

        Args:
            source_data: Raw sentiment data from API (reddit/twitter)
            ticker: Ticker symbol
            trading_date: Date of measurement
            source: Source type ('reddit' or 'twitter')

        Returns:
            Parsed sentiment metric dictionary or None if invalid
        """
        try:
            # Extract mention count
            mention_count = source_data.get("mention", 0)
            if not isinstance(mention_count, int | float):
                mention_count = 0

            # Extract positive and negative scores
            positive_score = source_data.get("positive", 0)
            negative_score = source_data.get("negative", 0)

            # Normalize scores to -1 to 1 range
            # Finnhub provides scores as raw counts, normalize them
            total_score = positive_score + negative_score
            normalized_score = (positive_score - negative_score) / total_score if total_score > 0 else 0.0

            # Use current time as datetime (API doesn't provide timestamp)
            dt = datetime.now(UTC)

            return {
                "ticker": ticker,
                "date": trading_date,
                "datetime": dt,
                "source": source,
                "mention_count": int(mention_count),
                "positive_score": float(positive_score),
                "negative_score": float(negative_score),
                "score": float(normalized_score),
                "social_metric": "mention_count",
            }

        except Exception as exc:
            logger.warning(
                "Failed to parse sentiment metric for %s from %s: %s",
                ticker,
                source,
                exc,
            )
            return None


__all__ = ["FinnhubSocialSentimentFetcher"]
