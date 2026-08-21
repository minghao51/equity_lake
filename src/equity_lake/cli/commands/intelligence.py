from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal

import polars as pl
import typer

from equity_lake.cli._app import _init_logging, _resolve_date, app, signal_app


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
    """Price forecasting (train/predict/backtest).

    For model training prefer ``equity ml train`` (surfaces ``--backend``).
    """
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
