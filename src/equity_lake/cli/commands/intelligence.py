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
    from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY
    from equity_lake.ml.forecasting import PriceForecaster
    from equity_lake.signals.config import load_signal_config

    _init_logging(verbose)
    signal_config = load_signal_config()
    configured = signal_config.ml.get("candidate_strategies") or signal_config.backtest.get("strategies")
    candidate_strategies = (
        [strategy.model_dump() if hasattr(strategy, "model_dump") else dict(strategy) for strategy in configured]
        if configured
        else [dict(DEFAULT_BACKTEST_STRATEGY)]
    )
    forecaster = PriceForecaster(model_dir=model_dir, model_mode=model_mode, candidate_strategies=candidate_strategies)

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
    watchlist: Annotated[str | None, typer.Option("--watchlist", "-w", help="Explicit legacy ticker-only watchlist YAML; requires --market")] = None,
    market: Annotated[
        Literal["us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"] | None,
        typer.Option("--market", help="Canonical market for an explicit legacy watchlist; never inferred from ticker text"),
    ] = None,
    mic: Annotated[
        str | None,
        typer.Option("--mic", help="Exact MIC for legacy symbols; required when source configuration is ambiguous"),
    ] = None,
    ticker_config: Annotated[
        str | None,
        typer.Option("--ticker-config", help="Active source ticker configuration used to resolve legacy membership"),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Selected signal config; custom v2 candidates remain bound to this file"),
    ] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Save output to file")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Preview scope without scanning, inference, or writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Scan watchlist and generate signals; dry-run only previews the plan."""
    import yaml

    from equity_lake.signals.config import load_legacy_watchlist, load_signal_config
    from equity_lake.signals.models import Watchlist

    _init_logging(verbose)
    watchlist_path = Path(watchlist) if watchlist else None
    ticker_config_path = Path(ticker_config) if ticker_config else None
    config_path = Path(config) if config else None

    try:
        if watchlist_path is not None:
            if market is None:
                raise ValueError("--market is required with a legacy ticker-only --watchlist; bare ticker identity is never guessed")
            legacy = load_legacy_watchlist(watchlist_path)
            from equity_lake.signals.tracking import _resolve_configured_instrument

            instruments = [_resolve_configured_instrument(symbol, market, mic, ticker_config_path) for symbol in legacy.tickers]
            wl = legacy
        else:
            if market is not None or mic is not None:
                raise ValueError("--market/--mic selection applies only to an explicit legacy --watchlist")
            from equity_lake.signals.tracking import load_private_watchlist

            managed = load_private_watchlist(ticker_config_path=ticker_config_path)
            instruments = [item.instrument for item in managed.instruments]
            wl = Watchlist(name=managed.name, tickers=list(dict.fromkeys(item.instrument.symbol for item in managed.instruments)))
        sc = load_signal_config(config_path)
        if fmt not in {"json", "md", "table"}:
            raise ValueError(f"Unknown format: {fmt}. Use: json, md, table")
        target_date = _resolve_date(date_str)
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError) as exc:
        typer.secho(f"Invalid scan configuration: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    if dry_run:
        typer.echo(
            json.dumps(
                {
                    "status": "planned",
                    "reason": "dry_run",
                    "date": target_date.isoformat(),
                    "watchlist": wl.name,
                    "tickers": wl.tickers,
                    "instruments": [instrument.model_dump() for instrument in instruments],
                    "generators": [name for name in ("backtest", "sentiment", "ml") if sc.is_generator_enabled(name)],
                    "format": fmt,
                    "output": output,
                },
                indent=2,
            )
        )
        return

    from equity_lake.core.paths import ensure_dirs
    from equity_lake.signals.history import save_scan_report
    from equity_lake.signals.models import SignalScanReport
    from equity_lake.signals.scanner import SignalScanner

    ensure_dirs()
    scanner = SignalScanner(sc, wl, instruments=instruments)
    signals = scanner.scan(target_date)
    formatted = scanner.format_signals(signals, fmt)

    if output:
        Path(output).write_text(formatted, encoding="utf-8")
        typer.echo(f"Saved to {output}")
    else:
        typer.echo(formatted)

    report = getattr(scanner, "last_report", None)
    if isinstance(report, SignalScanReport):
        report_path = save_scan_report(report)
        counts = json.dumps(report.outcome_counts, sort_keys=True)
        typer.secho(f"Signal scan status: {report.status}; outcomes={counts}; report={report_path}", err=True)

    if signals:
        scanner.save_history(signals)

    if isinstance(report, SignalScanReport) and report.status == "failed":
        typer.secho("All required scan evaluations failed; see the saved scan report for per-generator outcomes.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
