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

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _parse_markets, arena_app, report_app
from equity_lake.cli._display import print_finding_cards

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
    lake: Annotated[str | None, typer.Option("--lake", help="Lake root to read prices/action coverage from (default: canonical lake)")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Run the strategy arena and emit FindingCards + per-run artifacts.

    Per-run isolation: one failed strategy/regime never stops the other runs. The
    summary reports requested/completed/failed/non-evaluable counts, every failed
    pair (sanitized error type + reason) and each run's evaluation status
    (evaluated | no_trades | ineligible). Only evaluated runs feed the cards; a
    cash-only/no-condition run does not fail the CLI but its status is printed.

    Exit codes: 0 only when every requested pair completed and none failed; 1 when
    no *requested* pair completed (nothing is written, even if a run outside the
    request completed), when any requested pair failed, or when a requested pair
    is missing without a recorded failure — such an invocation is labelled PARTIAL
    and keeps the successful runs' artifacts. Requested pairs are deduplicated
    (first-occurrence order), so a duplicated ``--strategies momentum,momentum``
    selection is one attempt and prints ``1 of 1``, never ``1 of 2``. An outcome
    that records one pair as both completed and failed is contradictory: it is
    diagnosed as such and exits 1. Write failures are reported and exit 1 too.

    Invariant: the requested set is the only thing that may be published or counted
    as success — a run recorded outside ``requested`` is named in the diagnostics but
    never counted, never written and never substitutes for a requested pair.
    """
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
            lake_dir=Path(lake) if lake else None,
        )
    except ValueError as exc:
        typer.secho(f"arena failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    # F04 completion accounting.
    #
    # Invariant: **the requested set is the only thing that may be published or
    # counted as success**. Every decision below — the zero-completion gate, the
    # artifact-write gate, the banner numerator, the PARTIAL predicate, the counts
    # and the runs handed to the writer/cards — is derived from requested pairs,
    # never from ``outcome.runs`` as a whole. A completed (or failed) pair that is
    # outside ``requested`` is named in the diagnostics but never counted, never
    # written, and can never stand in for a requested pair.
    #
    # ``requested`` is recorded by ``run_arena``; a legacy outcome without it falls
    # back to the pairs that actually ran (completed plus failed) so existing library
    # fixtures keep their behaviour.
    completed_pairs = [(run.strategy, run.cost_regime) for run in outcome.runs]
    all_failures = list(outcome.failed)
    failed_pairs = [(failure.strategy, failure.cost_regime) for failure in all_failures]
    # Deduplicate the request itself (first-occurrence order) before any count is
    # computed or printed: a pair requested twice is one attempt, so a single run
    # can never be reported as "1 of 2". ``run_arena`` already returns unique
    # pairs; this also covers a legacy/mocked outcome that still carries duplicates.
    requested_pairs = list(dict.fromkeys(list(outcome.requested) or [*completed_pairs, *failed_pairs]))
    requested = len(requested_pairs)
    requested_set = set(requested_pairs)
    completed_set = set(completed_pairs)
    failed_set = set(failed_pairs)
    # Only pairs that appeared in the request count towards completion or failure.
    completed_requested = len(requested_set & completed_set)
    failed = [failure for failure in all_failures if (failure.strategy, failure.cost_regime) in requested_set]
    # Recorded pairs outside the request: reported by name, never counted/published.
    unrequested_completed_pairs = [pair for pair in dict.fromkeys(completed_pairs) if pair not in requested_set]
    unrequested_failed_pairs = [pair for pair in dict.fromkeys(failed_pairs) if pair not in requested_set]
    # The writer and the cards only ever see requested pairs (and requested failures).
    publishable_runs = [run for run in outcome.runs if (run.strategy, run.cost_regime) in requested_set]
    # A requested pair that is neither completed nor failed is *missing* evidence.
    # It must be reported as missing instead of implying a recorded error.
    missing_pairs = [pair for pair in requested_pairs if pair not in completed_set and pair not in failed_set]
    # A pair cannot be both a completed run and a recorded failure. When an outcome
    # contradicts itself, name the contradiction instead of silently picking a side.
    contradictory_pairs = [pair for pair in requested_pairs if pair in completed_set and pair in failed_set]
    non_evaluable = [run for run in publishable_runs if run.evaluation_status != "evaluated"]
    target = base or "data/findings"

    if completed_requested == 0:
        # Zero *requested* completion: nothing of this invocation's request is
        # evidence, so refuse before a writer could replace latest artifacts — a run
        # that completed outside the request must not publish anything either.
        typer.secho(
            "arena produced no completed runs for the requested pairs — no artifacts or cards were written.",
            fg=typer.colors.RED,
        )
        typer.echo(f"requested: {requested} pair(s) | completed: 0 | failed: {len(failed)}")
        for failure in failed:
            typer.echo(f"  failed {failure.strategy}__{failure.cost_regime}: {failure.error_type}: {failure.reason}")
        # A requested pair that neither completed nor failed must be named *here* too,
        # not only in the PARTIAL branch: the guide promises missing evidence is
        # reported by name, and this branch is where all of it is missing. Same
        # ``<strategy>__<regime>`` formatting as every other diagnostic.
        if missing_pairs:
            typer.secho(
                f"{len(missing_pairs)} requested pair(s) neither completed nor recorded a failure: "
                + ", ".join(f"{strategy}__{regime}" for strategy, regime in missing_pairs)
                + " — no error was recorded for them, so treat them as missing evidence rather than a recorded failure.",
                fg=typer.colors.YELLOW,
            )
        if unrequested_completed_pairs:
            typer.secho(
                "no requested pair completed, and "
                f"{len(unrequested_completed_pairs)} completed run(s) exist outside this invocation's request: "
                + ", ".join(f"{strategy}__{regime}" for strategy, regime in unrequested_completed_pairs)
                + " — they are not published because the invocation's requested set produced no completed evidence.",
                fg=typer.colors.YELLOW,
            )
        elif requested:
            typer.secho(
                f"no requested pair completed ({requested} requested, {len(failed)} failed): this invocation has no completed evidence to publish.",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                "no pair was requested and no run completed: this invocation has no evidence to publish.",
                fg=typer.colors.YELLOW,
            )
        if unrequested_failed_pairs:
            typer.secho(
                f"{len(unrequested_failed_pairs)} recorded failure(s) are also outside the request: "
                + ", ".join(f"{strategy}__{regime}" for strategy, regime in unrequested_failed_pairs)
                + ".",
                fg=typer.colors.YELLOW,
            )
        typer.echo(
            f"Fix the failures above (window, universe or coverage) and re-run; existing artifacts under {target} were left untouched, not deleted."
        )
        raise typer.Exit(1)

    try:
        # Publish only what was requested: the writer and the cards see the requested
        # completed runs (and requested failures) exclusively.
        cards = write_arena_artifacts(
            replace(outcome, runs=publishable_runs, requested=requested_pairs, failed=failed),
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
    except Exception as exc:  # noqa: BLE001 — a write failure must stay visible and nonzero
        typer.secho(f"arena artifact write failed: {exc}", fg=typer.colors.RED)
        typer.echo("Completed runs are unchanged; no catch-all success fallback is applied.")
        raise typer.Exit(1) from exc

    # Success requires every requested pair, not merely an empty failure list, and an
    # outcome that records one pair as both completed and failed is never a success.
    partial = bool(failed) or bool(missing_pairs) or bool(contradictory_pairs)
    if partial:
        typer.secho(
            "PARTIAL: not every requested pair completed — the counts below are the truth of this run "
            "(a missing pair is reported as missing, never as a recorded error; a pair recorded both "
            "completed and failed is reported as a contradiction).",
            fg=typer.colors.YELLOW,
        )
    strategies_requested = {strategy for strategy, _ in requested_pairs}
    regimes_requested = {regime for _, regime in requested_pairs}
    typer.echo(
        f"\nArena {'PARTIAL' if partial else 'complete'}: {completed_requested} of {requested} requested pair(s) completed "
        f"({len(strategies_requested)} strategies x {len(regimes_requested)} regimes)."
    )
    typer.echo(f"requested: {requested} | completed: {completed_requested} | failed: {len(failed)} | non-evaluable: {len(non_evaluable)}")
    typer.echo(f"strategies: {', '.join(STRATEGY_REGISTRY)} | regimes: {', '.join(COST_REGIMES)}")
    if failed:
        typer.secho("failed pairs (sanitized):", fg=typer.colors.RED)
        for failure in failed:
            typer.echo(f"  {failure.strategy}__{failure.cost_regime}: {failure.error_type}: {failure.reason}")
    if missing_pairs:
        typer.secho(
            f"{len(missing_pairs)} requested pair(s) neither completed nor recorded a failure: "
            + ", ".join(f"{strategy}__{regime}" for strategy, regime in missing_pairs)
            + " — no error was recorded for them, so treat them as missing evidence rather than a recorded failure.",
            fg=typer.colors.YELLOW,
        )
    if contradictory_pairs:
        typer.secho(
            f"{len(contradictory_pairs)} requested pair(s) are recorded both completed and failed: "
            + ", ".join(f"{strategy}__{regime}" for strategy, regime in contradictory_pairs)
            + " — the outcome contradicts itself, so this invocation is PARTIAL and exits nonzero; re-run to get a single verdict per pair.",
            fg=typer.colors.RED,
        )
    if unrequested_completed_pairs or unrequested_failed_pairs:
        outside_request = [f"{strategy}__{regime} (completed)" for strategy, regime in unrequested_completed_pairs] + [
            f"{strategy}__{regime} (failed)" for strategy, regime in unrequested_failed_pairs
        ]
        typer.secho(
            f"{len(outside_request)} recorded pair(s) are outside this invocation's request: "
            + ", ".join(outside_request)
            + " — the requested set is the only evidence published or counted here, so these are neither counted nor written.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("evaluation status (only 'evaluated' runs feed FindingCards):")
    for run in publishable_runs:
        notes = "; ".join(run.evaluation_notes)
        typer.echo(f"  {run.strategy}__{run.cost_regime}: {run.evaluation_status}" + (f" — {notes}" if notes else ""))
    if non_evaluable:
        typer.secho(
            f"{len(non_evaluable)} completed run(s) are not evaluable evidence and were excluded from the cards.",
            fg=typer.colors.YELLOW,
        )
    if not cards:
        typer.secho("No FindingCards produced (no evaluated runs to compare).", fg=typer.colors.YELLOW)
    else:
        typer.echo("\nFindingCards:")
        print_finding_cards(cards)
    typer.echo(f"\nArtifacts + cards written to: {target}")
    typer.echo(
        "Note: artifacts are keyed by <strategy>__<regime>, so directories from earlier runs can remain "
        "(per-run immutable layout is E01); nothing was deleted, and failed pairs did not refresh any "
        "previously published evidence."
    )
    if partial:
        raise typer.Exit(1)


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
