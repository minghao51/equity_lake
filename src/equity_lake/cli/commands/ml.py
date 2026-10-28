"""``equity ml`` commands — comparison, ablation, and training (Phase 2A Step 4).

Wires the ``ml_app`` sub-app (declared in :mod:`equity_lake.cli._app`, registered
in :mod:`equity_lake.cli.__main__`):

- ``equity ml compare --universe demo`` — run the labeling + backend comparison
  (``ml/comparison.run_comparison``) and write the ``meta-label-vs-direction``
  and ``xgb-vs-lgbm`` FindingCards.
- ``equity ml ablate --universe demo`` — run the feature-enrichment ablation
  (``ml/ablation.run_ablation``) and write the ``enrichment-ablation`` FindingCard.
- ``equity ml train --ticker ... --backend xgboost|lightgbm`` — train one
  backend model (mirrors ``intelligence forecast --mode train``).

Both comparison harnesses operate on a single per-ticker time series
(walk-forward folds are derived from the row count). The ``--universe`` flag
resolves the candidate ticker set from ``config/tickers.yaml``; the first ticker
with sufficient feature history is used (override with ``--ticker``).

Backfill guardrail: feature history must already exist under
``03_gold/features``. If it is missing, these commands exit non-zero and point at
``equity pipeline --markets us_equity --tickers <demo subset> --allow-history-backfill``
rather than auto-backfilling.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import typer

from equity_lake.cli._app import _default_window as _shared_default_window
from equity_lake.cli._app import _init_logging, ml_app
from equity_lake.cli._display import format_finding_card

# Per-command rolling lookbacks. Verified against pre-change behaviour: compare/
# ablate rolled ~2y, train rolled ~1y (the handoff prose had these inverted).
_COMPARE_LOOKBACK_DAYS = 730
_TRAIN_LOOKBACK_DAYS = 365

# ``ml compare`` and ``ml ablate`` share one option surface — the aliases below
# are the single definition of those flags (names + help text) for both commands.
UniverseOption = Annotated[str, typer.Option("--universe", help="Config ticker group (default: demo)")]
TickerOption = Annotated[str | None, typer.Option("--ticker", "-t", help="Run on a specific ticker (default: first of universe)")]
StartOption = Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD (default: ~2y ago)")]
EndOption = Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD (default: today)")]
OutputDirOption = Annotated[str | None, typer.Option("--output-dir", "-o", help="Findings dir (default: data/findings)")]
TrainWindowOption = Annotated[int, typer.Option("--train-window", help="Walk-forward train window (rows)")]
TestWindowOption = Annotated[int, typer.Option("--test-window", help="Walk-forward test window (rows)")]
EmbargoWindowOption = Annotated[int, typer.Option("--embargo-window", help="Post-test embargo (rows)")]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")]


def _resolve_ticker(universe: str, ticker_override: str | None) -> tuple[str, list[str]]:
    """Return ``(selected_ticker, all_universe_tickers)`` for a config group."""
    from equity_lake.core.config import TickerConfig

    tickers = TickerConfig().get_tickers_by_group(universe)
    if not tickers:
        typer.secho(f"No tickers found in universe '{universe}'.", fg=typer.colors.RED)
        raise typer.Exit(1)
    selected = ticker_override if ticker_override else tickers[0]
    if ticker_override and ticker_override not in tickers:
        typer.secho(
            f"Ticker {ticker_override!r} is not in universe '{universe}'; using it anyway.",
            fg=typer.colors.YELLOW,
        )
    return selected, tickers


def _missing_features_exit(ticker: str) -> None:
    typer.secho(
        f"No feature history found for {ticker} under 03_gold/features.\n"
        "Generate it first, e.g.:\n"
        f"  dotenvx run -- uv run equity pipeline --markets us_equity --tickers {ticker} --allow-history-backfill",
        fg=typer.colors.RED,
    )
    raise typer.Exit(1)


def _compare_default_window() -> tuple[date, date]:
    """``compare``/``ablate`` default window: ~2y rolling (``_COMPARE_LOOKBACK_DAYS``)."""
    return _shared_default_window(_COMPARE_LOOKBACK_DAYS)


def _prepare_run(
    universe: str,
    ticker: str | None,
    start: str | None,
    end: str | None,
    output_dir: str | None,
    verbose: bool,
) -> tuple[str, date, date, Path | None]:
    """Shared ``ml compare``/``ml ablate`` preamble.

    Returns ``(selected_ticker, start_date, end_date, findings_base)``.
    """
    _init_logging(verbose)
    selected, _ = _resolve_ticker(universe, ticker)
    default_start, default_end = _compare_default_window()
    start_date = date.fromisoformat(start) if start else default_start
    end_date = date.fromisoformat(end) if end else default_end
    base = Path(output_dir) if output_dir else None
    return selected, start_date, end_date, base


@ml_app.command("compare")
def ml_compare(
    universe: UniverseOption = "demo",
    ticker: TickerOption = None,
    start: StartOption = None,
    end: EndOption = None,
    output_dir: OutputDirOption = None,
    train_window: TrainWindowOption = 252,
    test_window: TestWindowOption = 21,
    embargo_window: EmbargoWindowOption = 1,
    verbose: VerboseOption = False,
) -> None:
    """Compare v1 vs v2 labeling and XGBoost vs LightGBM, emit 2 FindingCards."""
    from equity_lake.findings.writer import card_path
    from equity_lake.ml.comparison import run_comparison
    from equity_lake.ml.feature_loader import FeatureLoader

    selected, start_date, end_date, base = _prepare_run(universe, ticker, start, end, output_dir, verbose)

    loader = FeatureLoader()
    try:
        # ADR-0014 §5: comparison/ablation harnesses train models, so labels
        # finalized after the requested window (or legacy unknown-availability
        # rows) are excluded here just as in train_model/backtest (review
        # warning 1).
        features = loader.load_features(selected, start_date, end_date, require_available_by=end_date)
    finally:
        loader.close()
    if features.is_empty():
        _missing_features_exit(selected)

    try:
        cards = run_comparison(
            features=features,
            ticker=selected,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            base=base,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"comparison failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.secho(f"Comparison complete for {selected} ({universe} universe).", fg=typer.colors.GREEN)
    for card in cards:
        # ``run_comparison`` already wrote each card; resolve the path without re-writing.
        path = card_path(card.id, base=base)
        typer.echo(format_finding_card(card))
        typer.echo(f"    -> {path}")


@ml_app.command("ablate")
def ml_ablate(
    universe: UniverseOption = "demo",
    ticker: TickerOption = None,
    start: StartOption = None,
    end: EndOption = None,
    output_dir: OutputDirOption = None,
    train_window: TrainWindowOption = 252,
    test_window: TestWindowOption = 21,
    embargo_window: EmbargoWindowOption = 1,
    verbose: VerboseOption = False,
) -> None:
    """Ablate enriched vs technical-only features, emit the enrichment-ablation card."""
    from equity_lake.features import load_feature_engineer
    from equity_lake.findings.writer import card_path
    from equity_lake.ml.ablation import run_ablation

    selected, start_date, end_date, base = _prepare_run(universe, ticker, start, end, output_dir, verbose)

    engineer = load_feature_engineer()
    try:
        enriched = engineer.generate_features([selected], start_date, end_date, include_macro=True)
        technical = engineer.generate_features([selected], start_date, end_date, include_macro=False)
    finally:
        engineer.close()
    if enriched.is_empty() or technical.is_empty():
        _missing_features_exit(selected)

    try:
        card = run_ablation(
            enriched_features=enriched,
            technical_features=technical,
            ticker=selected,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            base=base,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"ablation failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.secho(f"Ablation complete for {selected} ({universe} universe).", fg=typer.colors.GREEN)
    path = card_path(card.id, base=base)
    typer.echo(format_finding_card(card))
    typer.echo(f"    -> {path}")


@ml_app.command("train")
def ml_train(
    ticker: Annotated[str, typer.Option("--ticker", "-t", help="Ticker symbol")] = "AAPL",
    backend: Annotated[Literal["xgboost", "lightgbm"], typer.Option("--backend", help="Model backend: xgboost|lightgbm")] = "xgboost",
    start: Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD (default: ~1y ago)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD (default: today)")] = None,
    model_mode: Annotated[
        Literal["v1_direction", "v2_meta_label"],
        typer.Option("--model-mode", help="v1_direction or v2_meta_label"),
    ] = "v1_direction",
    tune: Annotated[bool, typer.Option("--tune", help="Hyperparameter tuning")] = False,
    verbose: VerboseOption = False,
) -> None:
    """Train one backend classifier (canonical entrypoint; ``intelligence forecast --mode train`` is the legacy alias)."""
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
    # ``train`` rolls ~1y, matching its pre-change inline default (not the compare window).
    default_start, default_end = _shared_default_window(_TRAIN_LOOKBACK_DAYS)
    start_date = date.fromisoformat(start) if start else default_start
    end_date = date.fromisoformat(end) if end else default_end

    forecaster = PriceForecaster(model_mode=model_mode, backend=backend, candidate_strategies=candidate_strategies)
    try:
        forecaster.train_model(ticker, start_date, end_date, tune_hyperparams=tune, validate=True)
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"training failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    finally:
        forecaster.close()

    typer.secho(f"Training complete for {ticker} ({backend}/{model_mode}).", fg=typer.colors.GREEN)
    summary = forecaster.last_training_summary()
    if summary:
        typer.echo(f"  status: {summary.get('status')} | model: {summary.get('model_file')}")
        if summary.get("status") == "trained":
            typer.echo(
                f"  folds={summary.get('validation_fold_count')} "
                f"acc={float(summary.get('mean_accuracy', 0.0)):.3f} "
                f"prec={float(summary.get('mean_precision', 0.0)):.3f}",
            )
