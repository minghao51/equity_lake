"""CLI commands for the strategy arena and backtest reports.

Wires two sub-apps (declared in :mod:`equity_lake.cli._app`, registered in
:mod:`equity_lake.cli.__main__`):

- ``equity arena run`` — run strategies x cost regimes on lake data, emit the
  Phase 1 FindingCards (strategy / cost / benchmark) + per-run artifacts.
- ``equity report backtest`` — run a single backtest under one cost regime and
  publish its immutable run + attempt evidence. ``backtest`` stays a flat top-level
  command; report sub-commands live here (B1 decision).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer

from equity_lake.cli._app import (
    _init_logging,
    _parse_comma_list,
    _parse_markets,
    _require_real_root_scope,
    _resolve_lake_dir,
    arena_app,
    report_app,
)
from equity_lake.cli._display import print_finding_cards

_DEFAULT_TICKERS = "AAPL,MSFT,GOOGL,AMZN,NVDA"

#: Bound on the read-only "previous evidence" block: one line per requested pair,
#: capped so a wide sweep cannot flood the terminal.
_PREVIOUS_EVIDENCE_PAIR_LIMIT = 12


def _format_age(seconds: float) -> str:
    """Compact human age for the previous-evidence block (seconds are exact)."""
    total = max(int(seconds), 0)
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _previous_evidence_lines(root: Path, pairs: Sequence[tuple[str, str]], *, now: datetime | None = None) -> list[str]:
    """Bounded read-only "previous evidence" lines for the *requested* pairs only.

    Prints the age of the last **successful** comparable run (or ``none``) next to
    the last **attempt** status/age for each requested pair, so a failed attempt is
    visible without hiding the age of the last real success (ADR-0025 Decision 5).
    Read-only: no writes, no new CLI option, and a reader failure never fails the run.
    """
    from equity_lake.backtesting import evidence_view  # noqa: PLC0415 - keep the CLI import seam light

    clock = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        successes = evidence_view.latest_success_by_slug(root)
    except Exception as exc:  # noqa: BLE001 - a preview must never break the invocation
        return [f"  previous evidence unavailable: {type(exc).__name__}"]

    lines: list[str] = []
    for strategy, cost_regime in pairs[:_PREVIOUS_EVIDENCE_PAIR_LIMIT]:
        slug = f"{strategy}__{cost_regime}"
        success = successes.get(slug)
        success_text = (
            "last successful run: none"
            if success is None
            else f"last successful run: {_format_age((clock - success.manifest.finalized_at).total_seconds())} ago"
        )
        try:
            attempt = evidence_view.latest_attempt_for_slug(slug, root)
        except Exception:  # noqa: BLE001 - same rule: a preview never breaks the invocation
            attempt = None
        attempt_text = (
            "last attempt: none"
            if attempt is None
            else f"last attempt: {attempt.record.status} {_format_age((clock - attempt.record.finalized_at).total_seconds())} ago"
        )
        lines.append(f"  {slug}: {success_text}; {attempt_text}")
    hidden = len(pairs) - _PREVIOUS_EVIDENCE_PAIR_LIMIT
    if hidden > 0:
        lines.append(f"  ... {hidden} further requested pair(s) not shown")
    return lines


def _publish_zero_completion_attempt(
    *,
    root: Path,
    started_at: datetime,
    requested_pairs: Sequence[tuple[str, str]],
    failed: Sequence[Any],
    missing_pairs: Sequence[tuple[str, str]],
    contradictory_pairs: Sequence[tuple[str, str]],
    tickers: Sequence[str],
    markets: Sequence[str],
    start_date: date,
    end_date: date,
    initial_cash: float,
) -> Path | None:
    """Publish the durable ``zero_completion`` attempt record for this invocation.

    The invocation produced no completed requested run, so there is no run directory
    and no card — but the *attempt* is still evidence: which pairs were requested and
    how each was classified (failed / missing / contradictory) must survive the
    terminal (ADR-0025 Decision 5). Returns the published directory, or ``None`` when
    the attempt record itself could not be written (reported, never fatal to the
    documented exit code).
    """
    from equity_lake.backtesting.attempts import PairOutcome, PairRequest, write_attempt_record  # noqa: PLC0415

    failures_by_pair = {(failure.strategy, failure.cost_regime): failure for failure in failed}
    missing = set(missing_pairs)
    contradictory = set(contradictory_pairs)
    outcomes: list[PairOutcome] = []
    for strategy, cost_regime in requested_pairs:
        pair = (strategy, cost_regime)
        if pair in contradictory:
            outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="contradictory"))
            continue
        failure = failures_by_pair.get(pair)
        if failure is not None:
            outcomes.append(
                PairOutcome(
                    strategy=strategy,
                    cost_regime=cost_regime,
                    outcome="failed",
                    error_type=failure.error_type,
                    reason=failure.reason,
                )
            )
            continue
        # Neither completed, failed nor contradictory: missing evidence, never a
        # recorded error (the CLI wording owns that distinction).
        outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="missing"))

    notes: list[str] = []
    if not requested_pairs:
        notes.append("no pair was requested, so this invocation attempted no run and has no evidence to publish")
    elif missing:
        notes.append(f"{len(missing)} requested pair(s) neither completed nor recorded a failure: missing evidence, not a recorded error")
    try:
        finalised = write_attempt_record(
            root,
            requested=[PairRequest(strategy=strategy, cost_regime=cost_regime) for strategy, cost_regime in requested_pairs],
            outcomes=outcomes,
            start_date=start_date,
            end_date=end_date,
            universe=sorted({str(ticker).strip() for ticker in tickers if str(ticker).strip()}) or ["unspecified"],
            initial_cash=float(initial_cash),
            markets=list(markets) or ["us_equity"],
            costs=(),
            benchmark=None,
            cards=(),
            runs=(),
            summary_facts={
                "requested": len(requested_pairs),
                "completed": 0,
                "failed": len(failures_by_pair),
                "missing": len(missing),
                "contradictory": len(contradictory),
            },
            status="zero_completion",
            notes=notes,
            started_at=started_at,
        )
    except Exception as exc:  # noqa: BLE001 - the documented exit code must survive this
        typer.secho(f"attempt record could not be written: {exc}", fg=typer.colors.RED)
        return None
    return finalised.directory


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

    Evidence: every invocation publishes one immutable **attempt** directory under
    ``<findings root>/attempts/`` that owns the three comparison cards and the
    benchmark copy, plus one immutable **run** directory per requested completed pair
    under ``<findings root>/runs/``. Legacy ``<strategy>__<regime>/`` directories and
    flat cards are frozen read-only evidence from earlier runs: they are read, never
    rewritten. A zero-completion invocation still publishes its attempt record (no
    run directory), so its failure is durable rather than only terminal output.
    """
    from equity_lake.backtesting.arena import COST_REGIMES, STRATEGY_REGISTRY, run_arena
    from equity_lake.backtesting.report import write_arena_artifacts_with_evidence
    from equity_lake.core.paths import FINDINGS_DIR  # noqa: PLC0415 - the documented findings root

    _init_logging(verbose)
    base = Path(output_dir) if output_dir else None
    evidence_root = base or FINDINGS_DIR
    attempt_started_at = datetime.now(UTC)

    # Read-only preview **before** the run: the age of the last successful comparable
    # run next to the last attempt's status/age, for the requested pairs only.
    requested_hint = list(
        dict.fromkeys(
            (strategy, cost_regime)
            for strategy in (_parse_comma_list(strategies) or list(STRATEGY_REGISTRY))
            for cost_regime in (_parse_comma_list(cost_regimes) or list(COST_REGIMES))
        )
    )
    typer.echo("previous evidence for the requested pairs (read-only):")
    for line in _previous_evidence_lines(evidence_root, requested_hint):
        typer.echo(line)

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
            "arena produced no completed runs for the requested pairs — no run evidence or cards were written.",
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
        # The attempt is still evidence: publish the classification durably (no run
        # directory, no card) before exiting nonzero (ADR-0025 Decision 5).
        attempt_dir = _publish_zero_completion_attempt(
            root=evidence_root,
            started_at=attempt_started_at,
            requested_pairs=requested_pairs,
            failed=failed,
            missing_pairs=missing_pairs,
            contradictory_pairs=contradictory_pairs,
            tickers=_parse_comma_list(tickers) or [],
            markets=_parse_markets(markets) or ["us_equity"],
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            initial_cash=initial_cash,
        )
        if attempt_dir is not None:
            typer.echo(f"Attempt record: {attempt_dir} (status zero_completion; no run directory was created)")
        raise typer.Exit(1)

    try:
        # Publish only what was requested: the writer and the cards see the requested
        # completed runs (and requested failures) exclusively.
        cards, summary = write_arena_artifacts_with_evidence(
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
    if summary.attempt is not None:
        typer.echo(
            f"Attempt evidence: {summary.attempt.directory} (status {summary.attempt.record.status}; "
            f"{len(summary.finalized)} immutable run(s) finalized)"
        )
    for slug in sorted(summary.skipped_undated):
        typer.secho(f"  {slug}: no immutable run evidence (undated curve; no calendar is invented)", fg=typer.colors.YELLOW)
    for slug in sorted(summary.skipped_unrecorded_costs):
        typer.secho(f"  {slug}: no immutable run evidence (applied costs not recorded; no default is guessed)", fg=typer.colors.YELLOW)
    typer.echo(
        "Note: each invocation writes its own immutable attempt directory (which owns the three cards and the "
        "benchmark copy) plus one run directory per completed pair under runs/; legacy <strategy>__<regime>/ "
        "directories and flat cards from earlier runs are frozen and read-only, so nothing was overwritten or deleted."
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
    coverage_basis: Annotated[
        str,
        typer.Option(
            "--coverage-basis",
            help="Action-coverage basis: source_clock (strict point-in-time default) or vendor_events_no_clock (opt-in weak basis, ADR-0032)",
        ),
    ] = "source_clock",
    lake_dir: Annotated[
        str | None,
        typer.Option("--lake-dir", help="Explicit lake root such as data/lake_real (ADR-0030); default is the canonical lake"),
    ] = None,
    protocol_experiment: Annotated[
        str | None,
        typer.Option(
            "--protocol-experiment",
            help="Bind this run to a frozen R00 protocol: the experiment whose frozen manifest must admit the run (R01)",
        ),
    ] = None,
    protocol_digest: Annotated[
        str | None,
        typer.Option("--protocol-digest", help="Protocol digest this run expects; refused unless a frozen manifest carries it"),
    ] = None,
    protocol_root: Annotated[
        str | None,
        typer.Option("--protocol-root", help="Directory holding frozen protocol manifests (default: data/research/protocols)"),
    ] = None,
    output_dir: Annotated[
        str | None,
        typer.Option("--output-dir", "-o", help="Findings root for the run and attempt evidence (default: data/findings)"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Run one backtest and publish immutable run + attempt evidence.

    Writes one run directory under ``<findings root>/runs/`` and one attempt
    record under ``<findings root>/attempts/``; ``--output-dir`` is that
    findings root. No legacy path is written: no ``<strategy>__<regime>/``
    directory, no flat ``benchmark__equity.parquet`` and no flat card, so this
    invocation becomes latest-success evidence for its slug instead of
    overwriting a frozen pre-E01 directory.

    The run records what actually ran: the fee/tax pair the engine was
    configured with (the static ``COST_REGIMES`` pair for ``--cost-regime``,
    not the arena's per-market ``realistic`` defaults), the strategy
    parameters in force, the resample the engine used, and a bounded
    non-secret ``config_digest``. Its attempt outcome carries the shared
    evaluation status (evaluated | no_trades | ineligible).

    An undated result (``BacktestResult.equity_dates is None``) is refused:
    no calendar is invented, nothing is written, and the exit code is 1.

    The run reads one lake root at one action-coverage basis: ``--lake-dir`` selects
    the root (ADR-0030) and ``--coverage-basis`` the ADR-0032 basis; the defaults keep
    the canonical root and the strict ``source_clock`` basis, so a pre-existing
    invocation is unchanged. A basis is never inferred from the data, and two bases are
    never mixed in one run.

    ``--protocol-experiment`` binds the run to a frozen R00 protocol under
    ``--protocol-root``: the freeze must exist (absent refuses), ``--protocol-digest``
    must match a frozen record when given, the freeze must be unambiguous, and the
    run's own recorded universe, window, holdout and cost regimes must conform before
    anything is written. The frozen digest and experiment are recorded in the run
    scope ``parameters``, so the binding is in the run ID. No protocol flag keeps the
    run unbound, exactly as before.

    Exit codes: 0 when the run and its attempt record are published; 1 when
    the backtest raises, the result carries no dated evidence, the frozen
    protocol refuses the run, or the evidence cannot be written.
    """
    from equity_lake.backtesting.arena import evaluation_status
    from equity_lake.backtesting.attempts import PairOutcome, PairRequest, write_attempt_record
    from equity_lake.backtesting.evidence import CostApplication, ExecutionTiming, InputSnapshot, PriceOriginSummary, RunScope, finalise_run
    from equity_lake.backtesting.factory import build_backtest_engine
    from equity_lake.backtesting.protocol import (
        ProtocolError,
        cost_disclosures,
        protocol_binding,
        require_run_conformance,
        select_frozen_protocol,
    )
    from equity_lake.core.availability import COVERAGE_BASES, CoverageBasis
    from equity_lake.core.paths import FINDINGS_DIR

    _init_logging(verbose)
    # ``--output-dir`` is the findings root: the run and its attempt record both live
    # under it, so one invocation can never split its evidence across two trees.
    root = Path(output_dir) if output_dir else FINDINGS_DIR
    started_at = datetime.now(UTC)

    # One lake root, at exactly one action-coverage basis (ADR-0030 / ADR-0032). The
    # defaults keep the canonical root and the strict basis, so every pre-existing
    # invocation behaves exactly as before; a basis is never inferred from the data.
    if coverage_basis not in COVERAGE_BASES:
        typer.secho(f"Unknown --coverage-basis {coverage_basis!r}; expected one of {', '.join(COVERAGE_BASES)}.", fg=typer.colors.RED)
        raise typer.Exit(1)
    basis = cast("CoverageBasis", coverage_basis)
    market_list = _parse_markets(markets) or ["us_equity"]
    selected_lake = _resolve_lake_dir(lake_dir)
    _require_real_root_scope(market_list, selected_lake)

    # R01: a protocol-bound run selects its frozen manifest **before** the engine is
    # built, so an absent freeze, an unmatched digest claim or an ambiguous freeze
    # refuses the run before it spends anything. No protocol flag keeps the run
    # unbound, exactly as before.
    frozen = None
    if protocol_digest is not None and protocol_experiment is None:
        typer.secho("--protocol-digest requires --protocol-experiment: a digest claim binds one frozen experiment.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if protocol_experiment is not None:
        try:
            frozen = select_frozen_protocol(
                protocol_experiment,
                root=Path(protocol_root) if protocol_root is not None else None,
                digest=protocol_digest,
            )
        except ProtocolError as exc:
            typer.secho(f"refusing to run: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        typer.echo(f"Frozen protocol: {frozen.protocol.experiment} (digest {frozen.protocol_digest})")

    try:
        engine = build_backtest_engine(
            strategy=strategy,
            tickers=_parse_comma_list(tickers) or [],
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            initial_cash=initial_cash,
            markets=market_list,
            cost_regime=cost_regime,
            lake_dir=selected_lake,
            coverage_basis=basis,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    try:
        result = engine.run()
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"backtest failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    if result.equity_dates is None:
        # Decision 6: a dated run never gains a calendar inferred from the series
        # length, and the frozen legacy layout is not written by any command — so an
        # undated result publishes nothing at all.
        typer.secho(
            "refusing to publish this run: the result carries no dated evidence "
            "(BacktestResult.equity_dates is None). Undated curves are never given an invented "
            "calendar, so no run directory, attempt record or legacy "
            f"{strategy}__{cost_regime}/ directory was written under {root}.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    # The costs recorded are the pair the engine was actually configured with (the
    # static COST_REGIMES entry for this regime), never a re-resolution of
    # COST_REGIMES/MARKET_COST_DEFAULTS here: the arena's per-market ``realistic``
    # defaults do not apply to a single report.
    costs = [
        CostApplication(
            regime=cost_regime,
            fee_ratio=float(engine.fee_ratio),
            tax_ratio=float(engine.tax_ratio),
        )
    ]

    # The cohort the evaluation classifier is given: the size of the frame the engine
    # itself loaded. A backend that never loaded through the engine's loader has no
    # recorded size, so the requested tickers are used *and* the substitution is
    # disclosed in the attempt notes rather than assumed silently.
    notes: list[str] = ["published by the `equity report backtest` command (single strategy under one cost regime)"]
    loaded_universe_size = getattr(engine, "loaded_universe_size", None)
    if loaded_universe_size is None:
        loaded_universe_size = len({str(ticker).strip() for ticker in result.tickers if str(ticker).strip()})
        notes.append(
            f"evaluation cohort: the requested tickers ({loaded_universe_size} distinct), because this engine recorded no loaded-universe size"
        )
    run_evaluation_status, evaluation_notes = evaluation_status(engine.strategy, result, loaded_universe_size)
    notes.extend(f"evaluation: {note}" for note in evaluation_notes)

    try:
        # The universe this run actually consulted. When the engine loaded its own
        # frame it recorded the canonical display keys it read (identity_model-derived,
        # no MIC filled); otherwise the requested tickers are recorded with the
        # ``ticker_only`` basis, which claims no venue at all (E01c item 1).
        loaded_identities = getattr(engine, "loaded_identities", None)
        universe_basis: Literal["canonical", "ticker_only"] = "canonical" if loaded_identities else "ticker_only"
        if loaded_identities:
            universe = [str(identity) for identity in loaded_identities]
        else:
            universe = [str(ticker) for ticker in result.tickers if str(ticker).strip()]
            notes.append("universe basis: ticker_only, because this engine recorded no loaded canonical identity")
        # Read-seam snapshots: whatever the engine's own loader read (empty when it
        # had none, e.g. a preloaded frame). Provenance is never passed: the writer
        # derives it from the origin summaries below (ADR-0027 §4.5).
        engine_loader = getattr(engine, "data_loader", None)
        read_snapshots = getattr(engine_loader, "snapshots", None)
        read_origin_summaries = getattr(engine_loader, "price_origin_summaries", None)
        loader_snapshots: list[dict[str, str | None]] = list(read_snapshots()) if read_snapshots is not None else []
        loader_origin_summaries = list(read_origin_summaries()) if read_origin_summaries is not None else []
        input_snapshots = [
            InputSnapshot(
                name=str(snapshot["name"]),
                sha256=str(snapshot["sha256"]),
                source=str(snapshot["source"]),
                version=snapshot.get("version"),
                # The ADR-0032 basis an action_coverage: read admitted; absent elsewhere.
                coverage_basis=cast("CoverageBasis | None", snapshot.get("coverage_basis")),
            )
            for snapshot in loader_snapshots
        ]
        # R01: a protocol-bound run is checked against the frozen assumptions from its
        # *own* recorded facts. A violation refuses the run here — before the scope is
        # built and before any run directory exists, so nothing is published.
        parameters = dict(engine.strategy.params)
        if frozen is not None:
            require_run_conformance(
                frozen,
                universe=universe,
                start_date=result.start_date,
                end_date=result.end_date,
                cost_regimes=[cost_regime],
            )
            binding = protocol_binding(frozen)
            collision = sorted(set(binding) & set(parameters))
            if collision:
                raise ProtocolError(
                    f"strategy parameters already carry {collision}: the frozen protocol binding cannot be recorded without overwriting them"
                )
            parameters.update(binding)
            notes.extend(f"protocol: {note}" for note in cost_disclosures(frozen, costs))
        scope = RunScope(
            universe=universe,
            start_date=result.start_date,
            end_date=result.end_date,
            strategies=[strategy],
            costs=costs,
            initial_cash=float(result.initial_cash),
            # The parameters of the instance that actually ran, never a registry default.
            # A protocol-bound run adds the frozen digest and experiment here, so the
            # binding is part of the run-ID preimage and readable from the manifest
            # without a new manifest field (R01; no schema change).
            parameters=parameters,
        )
        finalised = finalise_run(
            result,
            scope=scope,
            universe_basis=universe_basis,
            execution=ExecutionTiming(
                trade_at_price="close",
                resample=engine.resample,
                note=f"decision at the session close of each session; the engine resampled the curve with {engine.resample!r}",
            ),
            # The bounded non-secret configuration actually in force on this path: it is
            # digested into the manifest and never stored (ADR-0025 Decision 3).
            config={"markets": list(engine.markets), "adjust": engine.adjust, "cost_regime": cost_regime},
            input_snapshots=input_snapshots,
            # One summary per price snapshot the engine's loader consumed; the writer
            # re-derives `data_provenance` from them instead of taking a literal.
            price_origin_summaries=[PriceOriginSummary.model_validate(entry) for entry in loader_origin_summaries],
            seed=None,
            cards=(),
            root=root,
        )
    except ProtocolError as exc:
        # A frozen-assumption refusal is not an evidence-write failure: it publishes
        # nothing and says so (R01).
        typer.secho(f"refusing to publish this run: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001 — an evidence write failure must stay visible and nonzero
        typer.secho(f"run evidence could not be written: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    try:
        attempt = write_attempt_record(
            root,
            requested=[PairRequest(strategy=strategy, cost_regime=cost_regime)],
            outcomes=[
                PairOutcome(
                    strategy=strategy,
                    cost_regime=cost_regime,
                    outcome="completed",
                    run_id=finalised.manifest.run_id,
                    run_dir=finalised.directory.name,
                    evaluation_status=run_evaluation_status,
                )
            ],
            start_date=result.start_date,
            end_date=result.end_date,
            universe=list(scope.universe),
            initial_cash=float(result.initial_cash),
            markets=list(engine.markets),
            costs=costs,
            benchmark=None,
            cards=(),
            runs=[finalised],
            summary_facts={"command": "equity report backtest", "evaluation_status": run_evaluation_status},
            notes=notes,
            started_at=started_at,
        )
    except Exception as exc:  # noqa: BLE001 — the run evidence is already durable; report both facts
        typer.secho(f"attempt record could not be written: {exc}", fg=typer.colors.RED)
        typer.echo(f"Run evidence was finalized at: {finalised.directory}")
        raise typer.Exit(1) from exc

    typer.echo(result.summary())
    typer.echo(f"\nRun evidence: {finalised.directory}")
    typer.echo(f"Attempt evidence: {attempt.directory} (status {attempt.record.status})")
    typer.echo(f"evaluation status: {run_evaluation_status}" + (f" — {'; '.join(evaluation_notes)}" if evaluation_notes else ""))
    if frozen is not None:
        # The binding and every disclosed asymmetry are printed as well as recorded: a
        # protocol-bound run that applied costs differently from the frozen ratios, or
        # that executed a different convention, says so where the operator can see it.
        typer.echo(f"protocol binding: {frozen.protocol.experiment} (digest {frozen.protocol_digest}) recorded in the run scope")
        for note in cost_disclosures(frozen, costs):
            typer.secho(f"  {note}", fg=typer.colors.YELLOW)
