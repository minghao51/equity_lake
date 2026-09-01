"""Market-to-fetcher routing.

Maps market identifiers to their concrete fetcher factories via a single
declarative ``MARKET_REGISTRY`` and provides the high-level
``fetch_market_data`` / ``fetch_market_data_with_config`` entry points used by
the ingestion pipeline.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date
from typing import Any

import polars as pl
import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.polars_utils import ensure_polars, frame_is_empty
from equity_lake.ingestion.writers import validate_schema
from equity_lake.sources.base import MarketDataFetcher

logger = structlog.get_logger()


def _require_finnhub_key(what: str) -> str:
    """Return the Finnhub API key or raise (loudly) if unset.

    Shared by the Finnhub-backed factories (news, social sentiment, transcripts,
    analyst ratings) so the "missing key" contract lives in one place.
    """
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.error("FINNHUB_API_KEY not set, cannot fetch %s", what)
        raise OSError("FINNHUB_API_KEY not set")
    return api_key


def _default_us_tickers(
    ticker_config: TickerConfig | None,
    *,
    max_tickers: int | None = None,
) -> list[str] | None:
    """Default US ticker list for Finnhub factories when none is passed explicitly.

    Mirrors the per-factory default: pull active US tickers from the config,
    optionally capped (news/social sentiment cap at 100 to stay within API
    limits), and return ``None`` when there is no config or no tickers.
    """
    if not ticker_config:
        return None
    tickers = ticker_config.get_tickers_for_market("us", active_only=True)
    if not tickers:
        return None
    if max_tickers is not None:
        tickers = tickers[:max_tickers]
    return tickers


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
    explicit_tickers: list[str] | None,
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
    explicit_tickers: list[str] | None,
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
    explicit_tickers: list[str] | None,
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
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.krx import KRXEquityFetcher

    return KRXEquityFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
    )


def _make_macro_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> Any:
    from equity_lake.sources.macro import MacroFetcher

    return MacroFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
    )


def _make_news_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    api_key = _require_finnhub_key("news")

    from equity_lake.sources.news import FinnhubNewsFetcher

    if not explicit_tickers:
        explicit_tickers = _default_us_tickers(ticker_config, max_tickers=100)

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
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    api_key = _require_finnhub_key("social sentiment")

    from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

    if not explicit_tickers:
        explicit_tickers = _default_us_tickers(ticker_config, max_tickers=100)

    return FinnhubSocialSentimentFetcher(
        api_key=api_key,
        tickers=explicit_tickers,
    )


def _make_rss_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.rss import RSSNewsFetcher

    return RSSNewsFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_reddit_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.reddit import RedditFetcher

    return RedditFetcher(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_stocktwits_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.stocktwits import StockTwitsFetcher

    if not explicit_tickers and ticker_config:
        explicit_tickers = ticker_config.get_tickers_for_market("us", active_only=True)

    return StockTwitsFetcher(
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_transcript_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    api_key = _require_finnhub_key("transcripts")

    from equity_lake.sources.transcripts import EarningsTranscriptFetcher

    if not explicit_tickers:
        explicit_tickers = _default_us_tickers(ticker_config)

    return EarningsTranscriptFetcher(
        api_key=api_key,
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_analyst_rating_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    api_key = _require_finnhub_key("analyst ratings")

    from equity_lake.sources.analyst_ratings import AnalystRatingFetcher

    if not explicit_tickers:
        explicit_tickers = _default_us_tickers(ticker_config)

    return AnalystRatingFetcher(
        api_key=api_key,
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_sec_filing_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.sec_fulltext import SECFilingFetcher

    if not explicit_tickers and ticker_config:
        explicit_tickers = ticker_config.get_tickers_for_market("us", active_only=True)

    return SECFilingFetcher(
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


def _make_sec_financials_fetcher(
    *,
    retry_attempts: int,
    retry_delay: float,
    ticker_config: TickerConfig | None,
    filters: dict | None,
    explicit_tickers: list[str] | None,
) -> MarketDataFetcher:
    from equity_lake.sources.sec_financials import SECFinancialsFetcher

    if not explicit_tickers and ticker_config:
        explicit_tickers = ticker_config.get_tickers_for_market("us", active_only=True)

    return SECFinancialsFetcher(
        tickers=explicit_tickers,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )


# Single source of truth: market identifier -> fetcher factory callable.
# Price markets use the canonical long keys (ADR-0010); normalize_markets()
# at the orchestrator boundary canonicalizes short aliases before routing.
# Referencing the factory functions directly (rather than by string name) makes
# a typo a loud import-time error instead of a silent runtime miss (the previous
# ``globals()`` lookup).
MARKET_REGISTRY: dict[str, Callable[..., Any]] = {
    "us_equity": _make_us_fetcher,
    "cn_ashare": _make_cn_fetcher,
    "hk_sg_equity": _make_hk_sg_fetcher,
    "jpx_equity": _make_jpx_fetcher,
    "krx_equity": _make_krx_fetcher,
    "macro": _make_macro_fetcher,
    "us_news": _make_news_fetcher,
    "us_social_sentiment": _make_sentiment_fetcher,
    "rss_news": _make_rss_fetcher,
    "reddit_posts": _make_reddit_fetcher,
    "stocktwits_messages": _make_stocktwits_fetcher,
    "us_earnings_transcripts": _make_transcript_fetcher,
    "us_analyst_ratings": _make_analyst_rating_fetcher,
    "sec_filings_fulltext": _make_sec_filing_fetcher,
    "us_sec_financials": _make_sec_financials_fetcher,
}


def fetch_market_data_with_config(
    market: str,
    trading_date: date,
    *,
    retry_attempts: int | None = None,
    retry_delay: float | None = None,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
) -> pl.DataFrame | None:
    """Fetch data for a specific market.

    Retry defaults are resolved from ``Settings.ingestion`` (env/YAML-configurable
    via ``EQUITY_INGESTION__RETRY_ATTEMPTS`` / ``EQUITY_INGESTION__RETRY_DELAY``)
    when not supplied explicitly.

    Args:
        market: Market identifier (e.g. 'us_equity', 'cn_ashare', 'macro', 'us_news')
        trading_date: Date to fetch
        retry_attempts: Override the configured retry attempt count
        retry_delay: Override the configured retry backoff base
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    ingestion = get_settings().ingestion
    if retry_attempts is None:
        retry_attempts = ingestion.retry_attempts
    if retry_delay is None:
        retry_delay = ingestion.retry_delay

    factory = MARKET_REGISTRY.get(market)
    if factory is None:
        logger.error("Unknown market: %s", market)
        return None

    # Factory construction is intentionally OUTSIDE the try/except so that
    # configuration errors (e.g. missing API keys) propagate to the caller.
    fetcher: Any = factory(
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
        explicit_tickers=explicit_tickers,
    )

    try:
        df = ensure_polars(fetcher.fetch(trading_date))
        if not frame_is_empty(df) and validate_schema(df, market):
            return df
        return None
    except Exception as e:
        logger.error("Failed to fetch %s data: %s", market, e)
        return None


def fetch_market_data(
    market: str,
    trading_date: date,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
    retry_attempts: int | None = None,
    retry_delay: float | None = None,
) -> pl.DataFrame | None:
    """Fetch data for a specific market (thin wrapper over the config-aware entry point)."""
    return fetch_market_data_with_config(
        market=market,
        trading_date=trading_date,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        ticker_config=ticker_config,
        filters=filters,
        explicit_tickers=explicit_tickers,
    )
