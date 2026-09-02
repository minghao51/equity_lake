"""Finnhub-backed ingestion commands (news, sentiment, transcripts, ratings)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, app


def _require_finnhub_api_key() -> str:
    """Return the Finnhub API key from the environment or abort with an error message."""
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)
    return key


def _run_finnhub_command(
    *,
    name: str,
    date_str: str | None,
    tickers: str | None,
    dry_run: bool,
    verbose: bool,
    fetcher_factory: Callable[..., Any],
    dataset_path: Path,
    schema_market: str,
    fetch_kwargs: dict[str, Any] | None = None,
    empty_msg: str | None = None,
) -> None:
    """Shared ingestion command body for all Finnhub-backed sources.

    The API key is read from ``FINNHUB_API_KEY`` (via dotenvx) — never accepted
    as a CLI option, which would leak it into shell history and process lists.
    """
    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset, validate_schema

    _init_logging(verbose)
    key = _require_finnhub_api_key()
    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)
    dataset_path.mkdir(parents=True, exist_ok=True)

    with timer(f"init_{name}"):
        fetcher = fetcher_factory(api_key=key, tickers=ticker_list, **(fetch_kwargs or {}))

    with timer(f"fetch_{name}"):
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo(empty_msg or f"No {name} data for {trading_date}")
        return

    if not validate_schema(df, schema_market):
        typer.secho("Schema validation failed", fg=typer.colors.RED)
        raise typer.Exit(1)

    with timer(f"write_{name}"):
        success = upsert_dataset(df, schema_market, trading_date, dry_run=dry_run)

    if success:
        typer.secho(f"{name.replace('_', ' ').title()} ingestion complete", fg=typer.colors.GREEN)
    else:
        typer.secho("Failed to write data", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("news")
def news(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    max_articles: Annotated[int, typer.Option("--max-articles", help="Max articles per ticker")] = 50,
    sentiment_method: Annotated[str, typer.Option("--sentiment-method", help="Sentiment method (vader)")] = "vader",
    min_relevance: Annotated[float, typer.Option("--min-relevance", help="Min relevance 0.0-1.0")] = 0.0,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Parallel workers")] = 1,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch market news with sentiment analysis (key from FINNHUB_API_KEY)."""
    from equity_lake.core.paths import US_NEWS_DIR
    from equity_lake.sources.news import FinnhubNewsFetcher

    _run_finnhub_command(
        name="news",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        fetcher_factory=FinnhubNewsFetcher,
        dataset_path=US_NEWS_DIR,
        schema_market="us_news",
        fetch_kwargs={
            "max_articles_per_ticker": max_articles,
            "sentiment_method": sentiment_method,
            "min_relevance": min_relevance,
            "max_workers": max_workers,
        },
    )


@app.command("sentiment")
def sentiment(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Parallel workers")] = 1,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Analyze market sentiment (key from FINNHUB_API_KEY)."""
    from equity_lake.core.paths import US_SOCIAL_SENTIMENT_DIR
    from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

    _run_finnhub_command(
        name="sentiment",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        fetcher_factory=FinnhubSocialSentimentFetcher,
        dataset_path=US_SOCIAL_SENTIMENT_DIR,
        schema_market="us_social_sentiment",
        fetch_kwargs={"max_workers": max_workers},
    )


@app.command("transcripts")
def transcripts(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch earnings call transcripts from Finnhub (key from FINNHUB_API_KEY)."""
    from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR
    from equity_lake.sources.transcripts import EarningsTranscriptFetcher

    _run_finnhub_command(
        name="transcripts",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        fetcher_factory=EarningsTranscriptFetcher,
        dataset_path=BRONZE_RAW_ARTICLES_DIR,
        schema_market="us_earnings_transcripts",
    )


@app.command("ratings")
def ratings(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch analyst ratings from Finnhub (structured data, no LLM needed; key from FINNHUB_API_KEY)."""
    from equity_lake.core.paths import SILVER_ANALYST_RATINGS_DIR
    from equity_lake.sources.analyst_ratings import AnalystRatingFetcher

    _run_finnhub_command(
        name="ratings",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        fetcher_factory=AnalystRatingFetcher,
        dataset_path=SILVER_ANALYST_RATINGS_DIR,
        schema_market="us_analyst_ratings",
    )


@app.command("sec")
def sec_filings(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    lookback_days: Annotated[int, typer.Option("--lookback", help="Filing lookback days")] = 120,
    process_silver: Annotated[bool, typer.Option("--process", help="Process bronze to silver via LLM")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
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
            bronze_ok = upsert_dataset(df, "01_bronze/raw_articles", trading_date)
        if not bronze_ok:
            typer.secho("Failed to write SEC bronze data", fg=typer.colors.RED)
            raise typer.Exit(1)

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


@app.command("corporate-actions")
def corporate_actions(
    market: Annotated[str, typer.Option("--market", help="Price market (ADR-0010 long key, yfinance-backed)")] = "us_equity",
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers (default: config)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Fetch but skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch dividends/splits (yfinance) into bronze+silver corporate actions (ADR-0011).

    Event-driven and incremental: fetches only events with ex_date after the
    max stored value, then upserts both layers partitioned by ex_date.
    """
    _init_logging(verbose)

    from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

    ticker_list = _parse_comma_list(tickers)
    outcome = ingest_corporate_actions(market, tickers=ticker_list, dry_run=dry_run)

    if not outcome["ok"]:
        typer.secho(f"Corporate-actions ingestion failed for {market}", fg=typer.colors.RED)
        raise typer.Exit(1)
    if outcome["fetched"] == 0:
        since = outcome["since"]
        typer.echo(f"No new corporate actions for {market} since {since or 'beginning of history'}")
        return
    scope = " [DRY RUN — nothing written]" if dry_run else ""
    typer.secho(f"Corporate actions: {outcome['fetched']} events ingested for {market}{scope}", fg=typer.colors.GREEN)


@app.command("financials")
def sec_financials(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    lookback_days: Annotated[int, typer.Option("--lookback", help="Filing lookback days")] = 120,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Skip writes")] = False,
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
            financials_ok = upsert_dataset(df, "us_sec_financials", trading_date)
        if not financials_ok:
            typer.secho("Failed to write SEC financials", fg=typer.colors.RED)
            raise typer.Exit(1)

    typer.secho("SEC financials ingestion complete", fg=typer.colors.GREEN)
