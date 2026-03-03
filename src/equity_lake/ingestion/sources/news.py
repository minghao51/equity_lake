"""Finnhub news data fetcher for US equities."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import requests
import structlog

from equity_lake.core.runtime import NEWS_COLUMNS
from equity_lake.ingestion.sources.base import MarketDataFetcher
from equity_lake.sentiment import SentimentAnalyzer

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

    def __init__(
        self,
        api_key: str | None = None,
        tickers: list[str] | None = None,
        max_articles_per_ticker: int = 50,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        sentiment_method: str = "vader",
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
            raise ValueError(
                "Finnhub API key not found. "
                "Set FINNHUB_API_KEY environment variable or pass api_key parameter."
            )

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

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """
        Fetch news for all tickers on the given date.

        Args:
            trading_date: Date to fetch news for

        Returns:
            DataFrame with columns: NEWS_COLUMNS
        """
        if not self.tickers:
            logger.warning("No tickers configured for news fetching")
            return pd.DataFrame()

        logger.info(
            "Fetching news for %s tickers on %s (workers=%s)",
            len(self.tickers),
            trading_date,
            self.max_workers,
        )

        all_articles = []

        if self.max_workers > 1:
            # Parallel fetching
            all_articles = self._fetch_parallel(trading_date)
        else:
            # Sequential fetching
            all_articles = self._fetch_sequential(trading_date)

        if not all_articles:
            logger.warning("No articles fetched for any ticker")
            return pd.DataFrame()

        logger.info("Fetched %s total articles", len(all_articles))

        # Convert to DataFrame
        df = pd.DataFrame(all_articles)

        # Analyze sentiment if analyzer available
        if self.sentiment_analyzer and not df.empty:
            df = self._add_sentiment_analysis(df)

        # Ensure all columns present
        for col in NEWS_COLUMNS:
            if col not in df.columns:
                if col == "category":
                    df[col] = "general"
                elif col == "relevance_score":
                    df[col] = 1.0
                else:
                    df[col] = None

        # Filter by minimum relevance
        if self.min_relevance > 0:
            df = df[df["relevance_score"] >= self.min_relevance]
            logger.info(
                "Filtered to %s articles with relevance >= %.2f",
                len(df),
                self.min_relevance,
            )

        # Select only NEWS_COLUMNS
        df = df[NEWS_COLUMNS]

        logger.info(
            "Returning %s articles for %s",
            len(df),
            trading_date,
        )

        return df

    def _fetch_sequential(self, trading_date: date) -> list[dict[str, Any]]:
        """
        Fetch news sequentially (one ticker at a time).

        Args:
            trading_date: Date to fetch news for

        Returns:
            List of article dictionaries
        """
        all_articles = []

        for i, ticker in enumerate(self.tickers):
            try:
                ticker_num = i + 1
                total = len(self.tickers)
                logger.debug(
                    "Fetching news for %s (%s/%s)",
                    ticker,
                    ticker_num,
                    total,
                )

                # Rate limiting: Finnhub allows 60 calls/minute
                # Add 1 second delay between tickers to be safe
                if i > 0:
                    time.sleep(1.0)

                articles = self._fetch_news_for_ticker(ticker, trading_date)
                all_articles.extend(articles)

            except Exception as exc:
                logger.error("Failed to fetch news for %s: %s", ticker, exc)
                continue

        return all_articles

    def _fetch_parallel(self, trading_date: date) -> list[dict[str, Any]]:
        """
        Fetch news in parallel using ThreadPoolExecutor.

        Args:
            trading_date: Date to fetch news for

        Returns:
            List of article dictionaries
        """
        all_articles = []

        # Limit workers to number of tickers
        workers = min(self.max_workers, len(self.tickers))

        logger.info(
            "Using parallel fetching with %s workers",
            workers,
        )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Submit all tasks
            future_to_ticker = {
                executor.submit(
                    self._fetch_news_for_ticker,
                    ticker,
                    trading_date,
                ): ticker
                for ticker in self.tickers
            }

            # Process completed tasks
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                    logger.debug(
                        "Fetched %s articles for %s",
                        len(articles),
                        ticker,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to fetch news for %s: %s",
                        ticker,
                        exc,
                    )

        return all_articles

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

        if not isinstance(response, pd.DataFrame):
            # Response is the actual requests.Response object
            data = response.json() if hasattr(response, "json") else []
        else:
            data = []

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
            if isinstance(datetime_str, int):
                dt = datetime.fromtimestamp(datetime_str)
            else:
                dt = pd.to_datetime(datetime_str)
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

    def _add_sentiment_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add sentiment analysis to news DataFrame.

        Args:
            df: DataFrame with headline column

        Returns:
            DataFrame with sentiment columns added
        """
        if df.empty or "headline" not in df.columns:
            return df

        logger.info("Analyzing sentiment for %s articles...", len(df))

        # Analyze headlines (more reliable than summaries)
        headlines = df["headline"].fillna("").tolist()

        # Batch analyze
        sentiment_results = []
        for headline in headlines:
            result = self.sentiment_analyzer.analyze(headline)
            sentiment_results.append(
                {
                    "sentiment_score": result.get("compound", 0.0),
                    "sentiment_label": result.get("label", "neutral"),
                }
            )

        # Add to DataFrame
        sentiment_df = pd.DataFrame(sentiment_results)
        df["sentiment_score"] = sentiment_df["sentiment_score"].values
        df["sentiment_label"] = sentiment_df["sentiment_label"].values

        # Log distribution
        label_counts = df["sentiment_label"].value_counts()
        logger.info(
            "Sentiment distribution: %s",
            dict(label_counts),
        )

        return df


__all__ = ["FinnhubNewsFetcher"]
