from __future__ import annotations

import os
from typing import Annotated, Literal

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, app


@app.command("news")
def news(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    max_articles: Annotated[int, typer.Option("--max-articles", help="Max articles per ticker")] = 50,
    sentiment_method: Annotated[Literal["vader", "finbert"], typer.Option("--sentiment-method", help="vader or finbert")] = "vader",
    min_relevance: Annotated[float, typer.Option("--min-relevance", help="Min relevance 0.0-1.0")] = 0.0,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Parallel workers")] = 1,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch market news with sentiment analysis."""
    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.core.paths import US_NEWS_DIR
    from equity_lake.ingestion.writers import upsert_dataset, validate_schema
    from equity_lake.sources.news import FinnhubNewsFetcher

    _init_logging(verbose)

    if not api_key and not os.getenv("FINNHUB_API_KEY"):
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)
    US_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    with timer("init_fetcher"):
        fetcher = FinnhubNewsFetcher(
            api_key=api_key,
            tickers=ticker_list,
            max_articles_per_ticker=max_articles,
            sentiment_method=sentiment_method,
            min_relevance=min_relevance,
            max_workers=max_workers,
        )

    with timer("fetch_news"):
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No news articles fetched")
        return

    if not validate_schema(df, "us_news"):
        typer.secho("Schema validation failed", fg=typer.colors.RED)
        raise typer.Exit(1)

    with timer("write_parquet"):
        success = upsert_dataset(df, "us_news", trading_date, dry_run=dry_run)

    if success:
        typer.secho("News ingestion complete", fg=typer.colors.GREEN)
    else:
        typer.secho("Failed to write Parquet", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("sentiment")
def sentiment(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Parallel workers")] = 1,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Analyze market sentiment."""
    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.core.paths import US_SOCIAL_SENTIMENT_DIR
    from equity_lake.ingestion.writers import upsert_dataset, validate_schema
    from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

    _init_logging(verbose)

    if not api_key and not os.getenv("FINNHUB_API_KEY"):
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)
    US_SOCIAL_SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)

    with timer("init_fetcher"):
        fetcher = FinnhubSocialSentimentFetcher(
            api_key=api_key,
            tickers=ticker_list,
            max_workers=max_workers,
        )

    with timer("fetch_sentiment"):
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No sentiment data fetched")
        return

    if not validate_schema(df, "us_social_sentiment"):
        typer.secho("Schema validation failed", fg=typer.colors.RED)
        raise typer.Exit(1)

    with timer("write_parquet"):
        success = upsert_dataset(df, "us_social_sentiment", trading_date, dry_run=dry_run)

    if success:
        typer.secho("Sentiment ingestion complete", fg=typer.colors.GREEN)
    else:
        typer.secho("Failed to write Parquet", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("sec")
def sec_filings(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    lookback_days: Annotated[int, typer.Option("--lookback", help="Filing lookback days")] = 120,
    process_silver: Annotated[bool, typer.Option("--process", help="Process bronze to silver via LLM")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch SEC 10-K/10-Q filings from EDGAR and optionally process to silver."""
    _init_logging(verbose)

    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset
    from equity_lake.sources.sec_fulltext import SECFilingFetcher

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)

    with timer("fetch_sec"):
        fetcher = SECFilingFetcher(tickers=ticker_list, lookback_days=lookback_days)
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No SEC filings found")
        return

    typer.echo(f"Fetched {df.height} SEC filing sections")

    if not dry_run:
        with timer("write_bronze"):
            upsert_dataset(df, "01_bronze/raw_articles", trading_date)

    if process_silver and not dry_run:
        typer.echo("Processing SEC bronze to silver...")
        from equity_lake.ingestion.sec_processor import process_sec_bronze_to_silver

        with timer("sec_to_silver"):
            success = process_sec_bronze_to_silver(trading_date)

        if success:
            typer.secho("SEC processing complete", fg=typer.colors.GREEN)
        else:
            typer.secho("SEC processing failed or no new sections", fg=typer.colors.YELLOW)
    else:
        typer.secho("SEC filing ingestion complete", fg=typer.colors.GREEN)


@app.command("transcripts")
def transcripts(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch earnings call transcripts from Finnhub."""
    _init_logging(verbose)

    if not api_key and not os.getenv("FINNHUB_API_KEY"):
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)

    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset
    from equity_lake.sources.transcripts import EarningsTranscriptFetcher

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)

    with timer("fetch_transcripts"):
        fetcher = EarningsTranscriptFetcher(api_key=api_key, tickers=ticker_list)
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No transcripts fetched")
        return

    typer.echo(f"Fetched {df.height} transcript articles")

    if not dry_run:
        with timer("write_bronze"):
            upsert_dataset(df, "01_bronze/raw_articles", trading_date)

    typer.secho("Transcript ingestion complete", fg=typer.colors.GREEN)


@app.command("ratings")
def ratings(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch analyst ratings from Finnhub (structured data, no LLM needed)."""
    _init_logging(verbose)

    if not api_key and not os.getenv("FINNHUB_API_KEY"):
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)

    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset
    from equity_lake.sources.analyst_ratings import AnalystRatingFetcher

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)

    with timer("fetch_ratings"):
        fetcher = AnalystRatingFetcher(api_key=api_key, tickers=ticker_list)
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No analyst ratings fetched")
        return

    typer.echo(f"Fetched {df.height} rating rows")

    if not dry_run:
        with timer("write_ratings"):
            upsert_dataset(df, "us_analyst_ratings", trading_date)

    typer.secho("Analyst ratings ingestion complete", fg=typer.colors.GREEN)


@app.command("financials")
def sec_financials(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    lookback_days: Annotated[int, typer.Option("--lookback", help="Filing lookback days")] = 120,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch SEC XBRL structured financials (balance sheet, income statement, ratios)."""
    _init_logging(verbose)

    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset
    from equity_lake.sources.sec_financials import SECFinancialsFetcher

    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)

    with timer("fetch_financials"):
        fetcher = SECFinancialsFetcher(tickers=ticker_list, lookback_days=lookback_days)
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo("No SEC financials found")
        return

    typer.echo(f"Fetched {df.height} financial records")

    if not dry_run:
        with timer("write_financials"):
            upsert_dataset(df, "us_sec_financials", trading_date)

    typer.secho("SEC financials ingestion complete", fg=typer.colors.GREEN)
