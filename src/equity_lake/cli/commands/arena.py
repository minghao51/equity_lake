"""CLI commands for the strategy arena and backtest reports.

Wires two sub-apps (declared in :mod:`equity_lake.cli._app`, registered in
:mod:`equity_lake.cli.__main__`):

- ``equity arena run`` — run strategies x cost regimes on lake data, emit the
  Phase 1 FindingCards (strategy / cost / benchmark) + per-run artifacts.
- ``equity report backtest`` — run a single backtest under one cost regime and
  write its report artifacts (equity/drawdown/metrics/trades). ``backtest`` stays
  a flat top-level command; report sub-commands live here (B1 decision).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _parse_markets, arena_app, report_app

_DEFAULT_TICKERS = "AAPL,MSFT,GOOGL,AMZN,NVDA"


@arena_app.command("run")
def arena_run(
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = _DEFAULT_TICKERS,
    start_date: Annotated[str, typer.Option("--start-date", help="Start date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    end_date: Annotated[str, typer.Option("--end-date", help="End date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    markets: Annotated[
        str,
        typer.Option("--markets", help="Comma-separated market keys (long keys like us_equity; short aliases like us accepted)"),
    ] = "us_equity",
    initial_cash: Annotated[float, typer.Option("--initial-cash", help="Initial capital")] = 100_000,
    strategies: Annotated[str | None, typer.Option("--strategies", help="Comma-separated strategy names (default: all)")] = None,
    cost_regimes: Annotated[str | None, typer.Option("--cost-regimes", help="Comma-separated regimes (default: all)")] = None,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Findings dir (default: data/findings)")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Run the strategy arena and emit FindingCards + per-run artifacts."""
    from equity_lake.backtesting.arena import COST_REGIMES, STRATEGY_REGISTRY, run_arena
    from equity_lake.backtesting.report import write_arena_artifacts

    _init_logging(verbose)
    base = Path(output_dir) if output_dir else None

    try:
        outcome = run_arena(
            _parse_comma_list(tickers) or [],
            date.fromisoformat(start_date),
            date.fromisoformat(end_date),
            markets=tuple(_parse_markets(markets) or ["us_equity"]),
            initial_cash=initial_cash,
            strategies=_parse_comma_list(strategies),
            cost_regimes=_parse_comma_list(cost_regimes),
        )
    except ValueError as exc:
        typer.secho(f"arena failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    cards = write_arena_artifacts(
        outcome,
        base=base,
        run_date=date.today(),
        scope={
            "universe": (
                f"default mega-cap universe ({_DEFAULT_TICKERS}) — survivorship-biased: today's winners picked with hindsight"
                if tickers == _DEFAULT_TICKERS
                else f"user-specified tickers ({tickers}) — not a random sample; selection bias possible"
            )
        },
    )
    strategies_run = {run.strategy for run in outcome.runs}
    regimes_run = {run.cost_regime for run in outcome.runs}
    typer.echo(f"\nArena complete: {len(outcome.runs)} runs ({len(strategies_run)} strategies x {len(regimes_run)} regimes).")
    typer.echo(f"strategies: {', '.join(STRATEGY_REGISTRY)} | regimes: {', '.join(COST_REGIMES)}")
    if not cards:
        typer.secho("No FindingCards produced (insufficient completed runs).", fg=typer.colors.YELLOW)
    else:
        typer.echo("\nFindingCards:")
        for card in cards:
            typer.echo(f"  [{card.axis}] {card.id}: {card.verdict} — {card.conclusion}")
    typer.echo(f"\nArtifacts + cards written to: {base or 'data/findings'}")


@report_app.command("backtest")
def report_backtest(
    strategy: Annotated[str, typer.Option("--strategy", "-s", help="Strategy name")] = "momentum",
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = _DEFAULT_TICKERS,
    start_date: Annotated[str, typer.Option("--start-date", help="Start date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    end_date: Annotated[str, typer.Option("--end-date", help="End date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    markets: Annotated[
        str,
        typer.Option("--markets", help="Comma-separated market keys (long keys like us_equity; short aliases like us accepted)"),
    ] = "us_equity",
    cost_regime: Annotated[str, typer.Option("--cost-regime", help="Cost regime: zero|realistic|high")] = "realistic",
    initial_cash: Annotated[float, typer.Option("--initial-cash", help="Initial capital")] = 100_000,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Findings dir (default: data/findings)")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Run a single backtest and write its report artifacts (no FindingCard)."""
    from equity_lake.backtesting.factory import build_backtest_engine
    from equity_lake.backtesting.report import write_backtest_report
    from equity_lake.core.paths import FINDINGS_DIR

    _init_logging(verbose)
    try:
        engine = build_backtest_engine(
            strategy=strategy,
            tickers=_parse_comma_list(tickers) or [],
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            initial_cash=initial_cash,
            markets=_parse_markets(markets) or ["us_equity"],
            cost_regime=cost_regime,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    try:
        result = engine.run()
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"backtest failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    out_dir = (Path(output_dir) if output_dir else FINDINGS_DIR) / f"{strategy}__{cost_regime}"
    write_backtest_report(result, out_dir, strategy=strategy, cost_regime=cost_regime)
    typer.echo(result.summary())
    typer.echo(f"\nReport written to: {out_dir}")
