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
``equity pipeline --markets us --tickers <demo subset> --allow-history-backfill``
rather than auto-backfilling.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

import typer

from equity_lake.cli._app import _init_logging, ml_app

_DEFAULT_LOOKBACK_DAYS = 730


def _resolve_ticker(universe: str, ticker_override: str | None) -> tuple[str, list[str]]:
    """Return ``(selected_ticker, all_universe_tickers)`` for a config group."""
    from equity_lake.core.config import get_default_config

    tickers = get_default_config().get_tickers_by_group(universe)
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
        f"  dotenvx run -- uv run equity pipeline --markets us --tickers {ticker} --allow-history-backfill",
        fg=typer.colors.RED,
    )
    raise typer.Exit(1)


def _default_window() -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    return start, end


@ml_app.command("compare")
def ml_compare(
    universe: Annotated[str, typer.Option("--universe", help="Config ticker group (default: demo)")] = "demo",
    ticker: Annotated[str | None, typer.Option("--ticker", "-t", help="Run on a specific ticker (default: first of universe)")] = None,
    start: Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD (default: ~2y ago)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD (default: today)")] = None,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Findings dir (default: data/findings)")] = None,
    train_window: Annotated[int, typer.Option("--train-window", help="Walk-forward train window (rows)")] = 252,
    test_window: Annotated[int, typer.Option("--test-window", help="Walk-forward test window (rows)")] = 21,
    embargo_window: Annotated[int, typer.Option("--embargo-window", help="Post-test embargo (rows)")] = 1,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Compare v1 vs v2 labeling and XGBoost vs LightGBM, emit 2 FindingCards."""
    from pathlib import Path

    from equity_lake.findings.writer import write_finding_card
    from equity_lake.ml.comparison import run_comparison
    from equity_lake.ml.feature_loader import FeatureLoader

    _init_logging(verbose)
    selected, _ = _resolve_ticker(universe, ticker)
    start_date = date.fromisoformat(start) if start else _default_window()[0]
    end_date = date.fromisoformat(end) if end else _default_window()[1]
    base = Path(output_dir) if output_dir else None

    loader = FeatureLoader()
    try:
        features = loader.load_features(selected, start_date, end_date)
    finally:
        loader.close()
    if features.is_empty():
        _missing_features_exit(selected)

    try:
        cards = run_comparison(
            features=features,
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
        # ``run_comparison`` already wrote each card; echo the resolved path.
        path = write_finding_card(card, base=base)
        typer.echo(f"  [{card.axis}] {card.id}: {card.verdict} — {card.conclusion}")
        typer.echo(f"    -> {path}")


@ml_app.command("ablate")
def ml_ablate(
    universe: Annotated[str, typer.Option("--universe", help="Config ticker group (default: demo)")] = "demo",
    ticker: Annotated[str | None, typer.Option("--ticker", "-t", help="Run on a specific ticker (default: first of universe)")] = None,
    start: Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD (default: ~2y ago)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD (default: today)")] = None,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Findings dir (default: data/findings)")] = None,
    train_window: Annotated[int, typer.Option("--train-window", help="Walk-forward train window (rows)")] = 252,
    test_window: Annotated[int, typer.Option("--test-window", help="Walk-forward test window (rows)")] = 21,
    embargo_window: Annotated[int, typer.Option("--embargo-window", help="Post-test embargo (rows)")] = 1,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Ablate enriched vs technical-only features, emit the enrichment-ablation card."""
    from pathlib import Path

    from equity_lake.features import _load_feature_engineer
    from equity_lake.findings.writer import write_finding_card
    from equity_lake.ml.ablation import run_ablation

    _init_logging(verbose)
    selected, _ = _resolve_ticker(universe, ticker)
    start_date = date.fromisoformat(start) if start else _default_window()[0]
    end_date = date.fromisoformat(end) if end else _default_window()[1]
    base = Path(output_dir) if output_dir else None

    engineer = _load_feature_engineer()()
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
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            base=base,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"ablation failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.secho(f"Ablation complete for {selected} ({universe} universe).", fg=typer.colors.GREEN)
    path = write_finding_card(card, base=base)
    typer.echo(f"  [{card.axis}] {card.id}: {card.verdict} — {card.conclusion}")
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
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Train one backend classifier (mirrors ``intelligence forecast --mode train``)."""
    from equity_lake.ml.forecasting import PriceForecaster

    _init_logging(verbose)
    start_date = date.fromisoformat(start) if start else date.today() - timedelta(days=365)
    end_date = date.fromisoformat(end) if end else date.today()

    forecaster = PriceForecaster(model_mode=model_mode, backend=backend)
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
