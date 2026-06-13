"""Finnhub news data fetcher for US equities."""

import os
from datetime import date, datetime, timedelta
from typing import Any, Literal

import polars as pl
import requests
import structlog

from equity_lake.core.schemas import NEWS_COLUMNS
from equity_lake.ingestion.parallel import fetch_items_parallel
from equity_lake.sentiment import SentimentAnalyzer
from equity_lake.sources.base import MarketDataFetcher, _empty_frame, standardize_columns

logger = structlog.get_logger()


# Finnhub API endpoint
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubNewsFetcher(MarketDataFetcher):
    """
    Fetch company news from Finnhub API with sentiment analysis.

    Finnhub provides free access to company news for US equities.
    This fetcher retrieves news articles and analyzes sentiment using VADER.

    Attributes:
        api_key: Finnhub API key (from FINNHUB_API_KEY env var)
        tickers: List of ticker symbols to fetch
        max_articles_per_ticker: Maximum articles to fetch per ticker (default: 50)
        sentiment_method: Sentiment analysis method ("vader" or "finbert")
        min_relevance: Minimum relevance score (0.0 to 1.0)
    """

    sentiment_analyzer: SentimentAnalyzer | None = None

    def __init__(
        self,
        api_key: str | None = None,
        tickers: list[str] | None = None,
        max_articles_per_ticker: int = 50,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        sentiment_method: Literal["vader", "finbert"] = "vader",
        min_relevance: float = 0.0,
        max_workers: int = 1,
    ):
        """
        Initialize the Finnhub news fetcher.

        Args:
            api_key: Finnhub API key (default: from FINNHUB_API_KEY env var)
            tickers: List of ticker symbols
            max_articles_per_ticker: Maximum articles per ticker
            retry_attempts: Number of retry attempts
            retry_delay: Base delay between retries
            sentiment_method: Sentiment analysis method
            min_relevance: Minimum relevance threshold
            max_workers: Maximum parallel workers (default: 1, sequential)
        """
        super().__init__(retry_attempts, retry_delay)

        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("Finnhub API key not found. Set FINNHUB_API_KEY environment variable or pass api_key parameter.")

        self.tickers = tickers or []
        self.max_articles_per_ticker = max_articles_per_ticker
        self.sentiment_method = sentiment_method
        self.min_relevance = min_relevance
        self.max_workers = max_workers

        # Initialize sentiment analyzer
        try:
            self.sentiment_analyzer = SentimentAnalyzer(method=sentiment_method)
        except ImportError as e:
            logger.warning(
                "Sentiment analyzer not available: %s. Proceeding without sentiment.",
                e,
            )
            self.sentiment_analyzer = None

        logger.info(
            "Initialized FinnhubNewsFetcher",
            tickers_count=len(self.tickers),
            max_articles=max_articles_per_ticker,
            sentiment_method=sentiment_method,
            max_workers=max_workers,
        )

    def fetch(self, trading_date: date) -> pl.DataFrame:
        """
        Fetch news for all tickers on the given date.

        Args:
            trading_date: Date to fetch news for

        Returns:
            DataFrame with columns: NEWS_COLUMNS
        """
        if not self.tickers:
            logger.warning("No tickers configured for news fetching")
            return _empty_frame()

        logger.info(
            "Fetching news for %s tickers on %s (workers=%s)",
            len(self.tickers),
            trading_date,
            self.max_workers,
        )

        all_articles = fetch_items_parallel(
            self.tickers,
            self._fetch_news_for_ticker,
            trading_date,
            max_workers=self.max_workers,
            rate_limit_seconds=1.0,
        )

        if not all_articles:
            logger.warning("No articles fetched for any ticker")
            return _empty_frame()

        logger.info("Fetched %s total articles", len(all_articles))

        df = pl.DataFrame(all_articles, orient="row")

        if self.sentiment_analyzer and not df.is_empty():
            df = self._add_sentiment_analysis(df)

        for col in NEWS_COLUMNS:
            if col not in df.columns:
                if col == "category":
                    df = df.with_columns(pl.lit("general").alias(col))
                elif col == "relevance_score":
                    df = df.with_columns(pl.lit(1.0).alias(col))
                else:
                    df = df.with_columns(pl.lit(None).alias(col))

        if self.min_relevance > 0:
            df = df.filter(pl.col("relevance_score") >= self.min_relevance)
            logger.info(
                "Filtered to %s articles with relevance >= %.2f",
                df.height,
                self.min_relevance,
            )

        df = df.select([col for col in NEWS_COLUMNS if col in df.columns])

        logger.info(
            "Returning %s articles for %s",
            len(df),
            trading_date,
        )

        return standardize_columns(df, columns=NEWS_COLUMNS, date_columns=("date",), datetime_columns=("datetime",))

    def _fetch_news_for_ticker(
        self,
        ticker: str,
        trading_date: date,
    ) -> list[dict[str, Any]]:
        """
        Fetch news articles for a single ticker.

        Args:
            ticker: Ticker symbol
            trading_date: Date to fetch

        Returns:
            List of article dictionaries
        """
        # Finnhub company-news API requires date range
        # Use the trading date and the next day
        start_date = trading_date.strftime("%Y-%m-%d")
        end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

        url = f"{FINNHUB_BASE_URL}/company-news"
        params = {
            "symbol": ticker,
            "from": start_date,
            "to": end_date,
            "token": self.api_key,
        }

        try:
            response = self._retry_on_failure(
                requests.get,
                url,
                params=params,
                timeout=10,
            )
        except Exception as exc:
            logger.error("API request failed for %s: %s", ticker, exc)
            return []

        data = response.json() if hasattr(response, "json") else []

        if not isinstance(data, list):
            logger.warning("Unexpected response format for %s: %s", ticker, type(data))
            return []

        # Limit articles per ticker
        articles = data[: self.max_articles_per_ticker]

        # Parse articles
        parsed_articles = []
        for article in articles:
            try:
                parsed = self._parse_article(article, ticker)
                if parsed:
                    parsed_articles.append(parsed)
            except Exception as exc:
                logger.warning("Failed to parse article for %s: %s", ticker, exc)
                continue

        logger.debug("Fetched %s articles for %s", len(parsed_articles), ticker)
        return parsed_articles

    def _parse_article(self, article: dict, ticker: str) -> dict[str, Any] | None:
        """
        Parse a single article from Finnhub API response.

        Args:
            article: Raw article dictionary from API
            ticker: Ticker symbol

        Returns:
            Parsed article dictionary or None if invalid
        """
        # Extract datetime
        datetime_str = article.get("datetime", 0)
        try:
            dt = datetime.fromtimestamp(datetime_str) if isinstance(datetime_str, int) else datetime.fromisoformat(str(datetime_str))
        except Exception:
            dt = datetime.now()

        return {
            "ticker": ticker,
            "date": dt.date(),
            "datetime": dt,
            "source": article.get("source", "Unknown"),
            "headline": article.get("headline", ""),
            "summary": article.get("summary", ""),
            "url": article.get("url", ""),
            "category": article.get("category", "general"),
            "sentiment_score": 0.0,  # Will be filled by sentiment analyzer
            "sentiment_label": "neutral",  # Will be filled by sentiment analyzer
            "relevance_score": 1.0,  # Finnhub doesn't provide relevance
        }

    def _add_sentiment_analysis(self, df: pl.DataFrame) -> pl.DataFrame:
        """Add sentiment analysis to news DataFrame."""
        if df.is_empty() or "headline" not in df.columns:
            return df

        logger.info("Analyzing sentiment for %s articles...", df.height)

        headlines = df.get_column("headline").fill_null("").to_list()

        sentiment_results = []
        for headline in headlines:
            result = {"compound": 0.0, "label": "neutral"} if self.sentiment_analyzer is None else self.sentiment_analyzer.analyze(headline)
            sentiment_results.append(
                {
                    "sentiment_score": result.get("compound", 0.0),
                    "sentiment_label": result.get("label", "neutral"),
                }
            )

        sentiment_df = pl.DataFrame(sentiment_results)
        df = df.with_columns(
            sentiment_df.get_column("sentiment_score").alias("sentiment_score"),
            sentiment_df.get_column("sentiment_label").alias("sentiment_label"),
        )

        label_counts = df.group_by("sentiment_label").agg(pl.len().alias("count")).rows(named=True)
        logger.info(
            "Sentiment distribution: %s",
            {row["sentiment_label"]: row["count"] for row in label_counts},
        )

        return df


__all__ = ["FinnhubNewsFetcher"]
