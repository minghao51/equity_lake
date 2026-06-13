"""Market-to-fetcher routing.

Maps market identifiers to their concrete fetcher classes and provides the
high-level ``fetch_market_data`` / ``fetch_market_data_with_config`` entry
points used by the ingestion pipeline.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import structlog

from equity_lake.core.config import TickerConfig, get_project_config
from equity_lake.core.polars_utils import ensure_polars, frame_is_empty
from equity_lake.ingestion.writers import validate_schema
from equity_lake.sources.base import MarketDataFetcher

logger = structlog.get_logger()


def _make_us_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.us import USEquityFetcher

    return USEquityFetcher(
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_cn_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
) -> MarketDataFetcher:
    from equity_lake.sources.cn_hybrid import CNHybridFetcher

    return CNHybridFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_hk_sg_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
) -> MarketDataFetcher:
    from equity_lake.sources.hk_sg import HKSGEquityFetcher

    return HKSGEquityFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_jpx_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
) -> MarketDataFetcher:
    from equity_lake.sources.jpx import JPXEquityFetcher

    return JPXEquityFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_krx_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
) -> MarketDataFetcher:
    from equity_lake.sources.krx import KRXEquityFetcher

    return KRXEquityFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_news_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    import os

    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.error("FINNHUB_API_KEY not set, cannot fetch news")
        raise OSError("FINNHUB_API_KEY not set")

    from equity_lake.sources.news import FinnhubNewsFetcher

    if not explicit_tickers and ticker_config:
        all_tickers = ticker_config.get_tickers_for_market("us", active_only=True)
        explicit_tickers = all_tickers[:100] if all_tickers else None

    return FinnhubNewsFetcher(
        api_key=api_key,
        tickers=explicit_tickers,
        max_articles_per_ticker=50,
        sentiment_method="vader",
    )


def _make_sentiment_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    import os

    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.error("FINNHUB_API_KEY not set, cannot fetch social sentiment")
        raise OSError("FINNHUB_API_KEY not set")

    from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

    if not explicit_tickers and ticker_config:
        all_tickers = ticker_config.get_tickers_for_market("us", active_only=True)
        explicit_tickers = all_tickers[:100] if all_tickers else None

    return FinnhubSocialSentimentFetcher(
        api_key=api_key,
        tickers=explicit_tickers,
    )


def fetch_market_data_with_config(
    market: str,
    trading_date: date,
    project_config: dict | None = None,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
) -> pl.DataFrame | None:
    """Fetch data for a specific market with config support.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        project_config: Runtime configuration dictionary
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    runtime_config = project_config or get_project_config()
    retry_attempts: int = int(runtime_config.get("retry_attempts", 3))
    retry_delay: float = float(runtime_config.get("retry_delay", 1.0))

    common_kwargs: dict[str, Any] = {
        "retry_attempts": retry_attempts,
        "retry_delay": retry_delay,
        "ticker_config": ticker_config,
        "filters": filters,
    }

    if market == "us":
        fetcher: Any = _make_us_fetcher(**common_kwargs, explicit_tickers=explicit_tickers)
    elif market == "cn":
        fetcher = _make_cn_fetcher(**common_kwargs)
    elif market == "hk_sg":
        fetcher = _make_hk_sg_fetcher(**common_kwargs)
    elif market == "jpx":
        fetcher = _make_jpx_fetcher(**common_kwargs)
    elif market == "krx":
        fetcher = _make_krx_fetcher(**common_kwargs)
    elif market == "us_news":
        fetcher = _make_news_fetcher(
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            explicit_tickers=explicit_tickers,
        )
    elif market == "us_social_sentiment":
        fetcher = _make_sentiment_fetcher(
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            explicit_tickers=explicit_tickers,
        )
    elif market == "macro":
        from equity_lake.sources.macro import MacroFetcher

        fetcher = MacroFetcher(
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
        )
    else:
        logger.error(f"Unknown market: {market}")
        return None

    try:
        df = ensure_polars(fetcher.fetch(trading_date))
        if not frame_is_empty(df) and validate_schema(df, market):
            return df
        return None
    except Exception as e:
        logger.error(f"Failed to fetch {market} data: {e}")
        return None


def fetch_market_data(
    market: str,
    trading_date: date,
    config: dict,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
) -> pl.DataFrame | None:
    """Fetch data for a specific market.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        config: Configuration dictionary
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    return fetch_market_data_with_config(
        market=market,
        trading_date=trading_date,
        project_config=config,
        ticker_config=ticker_config,
        filters=filters,
        explicit_tickers=explicit_tickers,
    )
