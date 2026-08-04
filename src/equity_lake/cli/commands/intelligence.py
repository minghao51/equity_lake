from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import polars as pl
import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _resolve_date, app, signal_app


def _format_training_summary(summary: dict[str, object]) -> str:
    """Render a concise operator-facing training summary."""
    lines = [
        f"Ticker: {summary['ticker']}",
        f"Trained on: {summary['trained_on']}",
        f"Mode: {summary['model_mode']}",
        f"Status: {summary['status']}",
    ]

    if summary.get("status") == "trained":
        lines.extend(
            [
                f"Train rows: {summary['train_rows']}",
                f"Validation rows: {summary['validation_rows']}",
                f"Validation folds: {summary['validation_fold_count']}",
                f"Mean accuracy: {float(summary['mean_accuracy']):.3f}",  # type: ignore[arg-type]
                f"Mean precision: {float(summary['mean_precision']):.3f}",  # type: ignore[arg-type]
                f"Mean recall: {float(summary['mean_recall']):.3f}",  # type: ignore[arg-type]
            ]
        )
        barrier_settings = summary.get("barrier_settings")
        if isinstance(barrier_settings, dict):
            lines.extend(
                [
                    f"Barrier days: {barrier_settings['vertical_barrier_days']}",
                    f"PT multiplier: {float(barrier_settings['pt_mult']):.2f}",
                    f"SL multiplier: {float(barrier_settings['sl_mult']):.2f}",
                    f"Meta-label threshold: {float(barrier_settings['meta_label_threshold']):.2f}",
                ]
            )

    return "\n".join(lines)


def _run_fetch_command(
    *,
    label: str,
    dataset: str,
    empty_message: str,
    fetched_message: str,
    complete_message: str,
    date_str: str | None,
    tickers: str | None,
    dry_run: bool,
    verbose: bool,
    factory: Any,
    requires_finnhub: bool = False,
    api_key: str | None = None,
    validate: str | None = None,
) -> None:
    """Shared skeleton for the uniform fetch-and-write intelligence commands.

    Wraps the resolve-date / init-fetcher / fetch / validate / upsert flow that
    news, sentiment, transcripts, ratings, and financials all follow. ``sec``
    is intentionally NOT routed through here — it has a distinct silver-processing
    step and conditional write path.
    """
    _init_logging(verbose)

    if requires_finnhub and not api_key and not os.getenv("FINNHUB_API_KEY"):
        typer.secho("FINNHUB_API_KEY not set. Get one at https://finnhub.io/", fg=typer.colors.RED)
        raise typer.Exit(1)

    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.core.logging import timer
    from equity_lake.ingestion.writers import upsert_dataset, validate_schema

    trading_date = resolve_trading_date(date_str)
    _parse_comma_list(tickers)

    with timer(f"fetch_{label}"):
        fetcher = factory()
        df = fetcher.fetch(trading_date)

    if df.is_empty():
        typer.echo(empty_message)
        return

    typer.echo(fetched_message.format(count=df.height))

    if validate and not validate_schema(df, validate):
        typer.secho("Schema validation failed", fg=typer.colors.RED)
        raise typer.Exit(1)

    if dry_run:
        typer.secho(complete_message, fg=typer.colors.GREEN)
        return

    with timer(f"write_{label}"):
        success = upsert_dataset(df, dataset, trading_date)

    if success:
        typer.secho(complete_message, fg=typer.colors.GREEN)
    else:
        typer.secho("Failed to write Parquet", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("forecast")
def forecast(
    mode: Annotated[str, typer.Option("--mode", help="train, predict, or backtest")] = "predict",
    ticker: Annotated[str, typer.Option("--ticker", help="Ticker symbol")] = "AAPL",
    start: Annotated[str | None, typer.Option("--start", help="Start date")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date")] = None,
    date_str: Annotated[str | None, typer.Option("--date", help="Single prediction date")] = None,
    model_dir: Annotated[str | None, typer.Option("--model-dir", help="Model directory")] = None,
    model_mode: Annotated[
        Literal["v1_direction", "v2_meta_label"],
        typer.Option("--model-mode", help="v1_direction or v2_meta_label"),
    ] = "v1_direction",
    tune: Annotated[bool, typer.Option("--tune", help="Hyperparameter tuning")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Price forecasting."""
    from equity_lake.ml.forecasting import PriceForecaster

    _init_logging(verbose)
    forecaster = PriceForecaster(model_dir=model_dir, model_mode=model_mode)

    if mode == "train":
        start_date = date.fromisoformat(start) if start else date.today() - timedelta(days=365)
        end_date = date.fromisoformat(end) if end else date.today()
        forecaster.train_model(ticker, start_date, end_date, tune_hyperparams=tune, validate=True)
        summary = forecaster.last_training_summary()
        typer.secho(f"Forecast training complete for {ticker}", fg=typer.colors.GREEN)
        if summary:
            typer.echo(_format_training_summary(summary))

    elif mode == "predict":
        prediction_date = date.fromisoformat(date_str) if date_str else date.today()
        result = forecaster.predict(ticker, prediction_date)
        typer.echo(json.dumps(result, indent=2, default=str))

    elif mode == "backtest":
        start_date = date.fromisoformat(start) if start else date.today() - timedelta(days=365)
        end_date = date.fromisoformat(end) if end else date.today()
        results_df = forecaster.backtest(ticker, start_date, end_date)
        if not results_df.is_empty():
            accuracy = float(results_df.select((pl.col("prediction") == pl.col("actual")).mean()).item())
            typer.echo(f"Backtest accuracy: {accuracy:.2%} over {len(results_df)} predictions")
            typer.echo(str(results_df))
        else:
            typer.secho("No backtest results", fg=typer.colors.YELLOW)

    else:
        typer.secho(f"Unknown mode: {mode}. Use train, predict, or backtest.", fg=typer.colors.RED)
        raise typer.Exit(1)

    forecaster.close()


@signal_app.command("scan")
def signal_scan(
    fmt: Annotated[str, typer.Option("--format", "-f", help="json, md, or table")] = "table",
    date_str: Annotated[str | None, typer.Option("--date", "-d", help="Target date YYYY-MM-DD")] = None,
    watchlist: Annotated[str | None, typer.Option("--watchlist", "-w", help="Watchlist config path")] = None,
    config: Annotated[str | None, typer.Option("--config", "-c", help="Signal config path")] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Save output to file")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Don't save history")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Scan watchlist and generate signals."""
    from equity_lake.signals.config import load_signal_config, load_watchlist
    from equity_lake.signals.scanner import SignalScanner

    _init_logging(verbose)
    watchlist_path = Path(watchlist) if watchlist else None
    config_path = Path(config) if config else None

    wl = load_watchlist(watchlist_path)
    sc = load_signal_config(config_path)
    scanner = SignalScanner(sc, wl)
    target_date = _resolve_date(date_str)
    signals = scanner.scan(target_date)
    formatted = scanner.format_signals(signals, fmt)

    if output:
        Path(output).write_text(formatted)
        typer.echo(f"Saved to {output}")
    else:
        typer.echo(formatted)

    if not dry_run and signals:
        scanner.save_history(signals)


@app.command("news")
def news(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    max_articles: Annotated[int, typer.Option("--max-articles", help="Max articles per ticker")] = 50,
    min_relevance: Annotated[float, typer.Option("--min-relevance", help="Min relevance 0.0-1.0")] = 0.0,
    max_workers: Annotated[int, typer.Option("--max-workers", help="Parallel workers")] = 1,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch market news with sentiment analysis."""
    from equity_lake.core.paths import US_NEWS_DIR
    from equity_lake.sources.news import FinnhubNewsFetcher

    US_NEWS_DIR.mkdir(parents=True, exist_ok=True)
    _run_fetch_command(
        label="news",
        dataset="us_news",
        empty_message="No news articles fetched",
        fetched_message="Fetched {count} news articles",
        complete_message="News ingestion complete",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        api_key=api_key,
        requires_finnhub=True,
        validate="us_news",
        factory=lambda: FinnhubNewsFetcher(
            api_key=api_key,
            tickers=_parse_comma_list(tickers),
            max_articles_per_ticker=max_articles,
            min_relevance=min_relevance,
            max_workers=max_workers,
        ),
    )


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
    from equity_lake.core.paths import US_SOCIAL_SENTIMENT_DIR
    from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

    US_SOCIAL_SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)
    _run_fetch_command(
        label="sentiment",
        dataset="us_social_sentiment",
        empty_message="No sentiment data fetched",
        fetched_message="Fetched {count} sentiment rows",
        complete_message="Sentiment ingestion complete",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        api_key=api_key,
        requires_finnhub=True,
        validate="us_social_sentiment",
        factory=lambda: FinnhubSocialSentimentFetcher(
            api_key=api_key,
            tickers=_parse_comma_list(tickers),
            max_workers=max_workers,
        ),
    )


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
            upsert_dataset(df, "bronze/raw_articles", trading_date)

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
    from equity_lake.sources.transcripts import EarningsTranscriptFetcher

    _run_fetch_command(
        label="transcripts",
        dataset="bronze/raw_articles",
        empty_message="No transcripts fetched",
        fetched_message="Fetched {count} transcript articles",
        complete_message="Transcript ingestion complete",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        api_key=api_key,
        requires_finnhub=True,
        factory=lambda: EarningsTranscriptFetcher(api_key=api_key, tickers=_parse_comma_list(tickers)),
    )


@app.command("ratings")
def ratings(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", help="Finnhub API key")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch analyst ratings from Finnhub (structured data, no LLM needed)."""
    from equity_lake.sources.analyst_ratings import AnalystRatingFetcher

    _run_fetch_command(
        label="ratings",
        dataset="us_analyst_ratings",
        empty_message="No analyst ratings fetched",
        fetched_message="Fetched {count} rating rows",
        complete_message="Analyst ratings ingestion complete",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        api_key=api_key,
        requires_finnhub=True,
        factory=lambda: AnalystRatingFetcher(api_key=api_key, tickers=_parse_comma_list(tickers)),
    )


@app.command("financials")
def sec_financials(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    lookback_days: Annotated[int, typer.Option("--lookback", help="Filing lookback days")] = 120,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Skip writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch SEC XBRL structured financials (balance sheet, income statement, ratios)."""
    from equity_lake.sources.sec_financials import SECFinancialsFetcher

    _run_fetch_command(
        label="financials",
        dataset="us_sec_financials",
        empty_message="No SEC financials found",
        fetched_message="Fetched {count} financial records",
        complete_message="SEC financials ingestion complete",
        date_str=date_str,
        tickers=tickers,
        dry_run=dry_run,
        verbose=verbose,
        factory=lambda: SECFinancialsFetcher(tickers=_parse_comma_list(tickers), lookback_days=lookback_days),
    )
