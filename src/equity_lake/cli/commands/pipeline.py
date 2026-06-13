from __future__ import annotations

from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _resolve_date, app


def _pipeline_succeeded(results: dict[str, object]) -> bool:
    """Return True when every returned stage reports success."""
    for stage_result in results.values():
        if isinstance(stage_result, dict) and stage_result.get("success") is False:
            return False
        if isinstance(stage_result, dict) and "success" not in stage_result and any(value is False for value in stage_result.values()):
            return False
    return True


@app.command("pipeline")
def pipeline(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date YYYY-MM-DD")] = None,
    days_back: Annotated[int, typer.Option("--days-back", help="Days back")] = 1,
    markets: Annotated[str | None, typer.Option("--markets", "-m", help="Comma-separated markets")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    skip_ingestion: Annotated[bool, typer.Option("--skip-ingestion", help="Skip Stage 1")] = False,
    skip_features: Annotated[bool, typer.Option("--skip-features", help="Skip Stage 2")] = False,
    skip_ml: Annotated[bool, typer.Option("--skip-ml", help="Skip Stage 3")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
    save_results: Annotated[bool, typer.Option("--save-results", help="Save JSON results")] = False,
) -> None:
    """Run the full EOD pipeline (ingest -> features -> ML)."""
    import json
    from pathlib import Path

    from equity_lake.pipeline import execute_eod_pipeline

    _init_logging(verbose)
    trading_date = _resolve_date(date_str, days_back)
    market_list = _parse_comma_list(markets)
    ticker_list = _parse_comma_list(tickers)

    results = execute_eod_pipeline(
        trading_date=trading_date,
        markets=market_list,
        tickers=ticker_list,
        dry_run=dry_run,
        skip_ingestion=skip_ingestion,
        skip_features=skip_features,
        skip_ml=skip_ml,
        explicit_tickers=ticker_list,
    )

    if save_results:
        output_path = Path(f"pipeline_results_{trading_date}.json")
        output_path.write_text(json.dumps(results, indent=2, default=str))
        typer.echo(f"Results saved to {output_path}")

    if not _pipeline_succeeded(results):
        raise typer.Exit(1)
