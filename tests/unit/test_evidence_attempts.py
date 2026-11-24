"""E01b.1 write-side tests: attempt records, attempt directories, classification.

Scope: ADR-0025 Decisions 2-5 and 7-9 with the frozen E01-1..E01-5 decisions, as
implemented by ``backtesting/attempts.py`` and the attempt-publishing path in
``backtesting/report.py``. Every test uses a ``tmp_path`` findings root — nothing is
written under ``data/findings``.

What falsifies each test is stated in its docstring. The helpers here are shared
with ``test_evidence_readers.py`` (which reads the same evidence layout).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import polars as pl
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from equity_lake.backtesting.arena import (
    ArenaFailure,
    ArenaOutcome,
    ArenaRun,
    equal_weight_buyhold,
)
from equity_lake.backtesting.attempts import (
    ATTEMPT_FIELD_ORDER,
    ATTEMPT_MANIFEST_NAME,
    ATTEMPT_PROTOCOL,
    BENCHMARK_ATTEMPT_ARTIFACT,
    UNEVIDENCED_BENCHMARK_REF,
    AttemptRecord,
    AttemptScope,
    PairOutcome,
    PairRequest,
    RunWriteFailure,
    attempt_id_for,
    attempts_root_for,
    bind_cards_to_evidence,
    is_finalized_attempt,
    iter_attempt_directories,
    validate_attempt_directory,
    write_attempt_record,
)
from equity_lake.backtesting.evidence import (
    CodeIdentity,
    CostApplication,
    FinalisedRun,
    RunScope,
    finalise_run,
    validate_run_directory,
)
from equity_lake.backtesting.report import write_arena_artifacts, write_arena_artifacts_with_evidence
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard

IRREGULAR_DATES = [
    date(2022, 1, 3),
    date(2022, 1, 4),
    date(2022, 1, 10),
    date(2022, 2, 15),
    date(2022, 3, 1),
    date(2022, 3, 2),
]
IRREGULAR_VALUES = [100_000.0, 100_500.0, 99_250.0, 101_000.0, 102_750.0, 103_000.0]

_BUILD_TS = datetime(2026, 11, 6, 10, 15, 0, tzinfo=UTC)
#: A fixed, clearly-past insertion instant for publishes that let the writer stamp
#: ``finalized_at`` itself (a future ``started_at`` would be refused, correctly).
_LOGICAL_TS = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)

__all__ = [
    "IRREGULAR_DATES",
    "IRREGULAR_VALUES",
    "stub_outcome",
]


# ---------------------------------------------------------------------------
# Shared fixtures (imported by test_evidence_readers.py)
# ---------------------------------------------------------------------------


def stub_result(
    *,
    dates: list[date] | None | bool = IRREGULAR_DATES,
    values: list[float] | None = None,
    metrics: dict[str, float] | None = None,
    trades: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    """A dated (or explicitly undated) result; ``dates=False`` means *no* dates."""
    resolved_values = values if values is not None else IRREGULAR_VALUES
    resolved_dates = None if dates is False else cast("list[date] | None", dates)
    return BacktestResult(
        strategy_name="trend_following",
        tickers=["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        final_cash=resolved_values[-1],
        equity_curve=pl.Series("equity", resolved_values, dtype=pl.Float64),
        trades=trades if trades is not None else [],
        metrics=metrics if metrics is not None else {"total_return": 0.03, "sharpe_ratio": 1.1},
        equity_dates=resolved_dates,
    )


def _price_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {"ticker": ["AAPL"] * len(IRREGULAR_DATES), "date": IRREGULAR_DATES, "close": list(IRREGULAR_VALUES)},
        schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
    )


#: Sentinel meaning "this stub run recorded the default applied cost pair"; an
#: explicit ``None`` means the run recorded *no* cost application at all.
_RECORDED_COSTS = object()


def _resolve_stub_costs(applied_costs: Any) -> dict[str, float] | None:
    if applied_costs is _RECORDED_COSTS:
        return {"fee_ratio": 0.001425, "tax_ratio": 0.0}
    return dict(applied_costs) if applied_costs is not None else None


def stub_outcome(
    *,
    pairs: list[tuple[str, str]] | None = None,
    requested: list[tuple[str, str]] | None = None,
    failed: list[ArenaFailure] | None = None,
    applied_costs: Any = _RECORDED_COSTS,
    dates: list[date] | None | bool = IRREGULAR_DATES,
    metrics: dict[str, float] | None = None,
) -> ArenaOutcome:
    """A real :class:`ArenaOutcome` with stub results (no engine, no lake)."""
    resolved_pairs = pairs if pairs is not None else [("trend_following", "realistic")]
    frame = _price_frame()
    runs = [
        ArenaRun(
            strategy=strategy,
            cost_regime=regime,
            result=stub_result(dates=dates, metrics=metrics),
            strategy_params={"lookback": 20},
            applied_costs=_resolve_stub_costs(applied_costs),
        )
        for strategy, regime in resolved_pairs
    ]
    return ArenaOutcome(
        runs=runs,
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100_000.0),
        initial_cash=100_000.0,
        requested=resolved_pairs if requested is None else requested,
        failed=list(failed or []),
        markets=("us_equity",),
    )


def finalise_pair(
    root: Path,
    *,
    strategy: str = "trend_following",
    regime: str = "realistic",
    fee_ratio: float = 0.001425,
    tax_ratio: float = 0.0,
    parameters: dict[str, Any] | None = None,
    now: datetime = _BUILD_TS,
) -> FinalisedRun:
    """One finalized run for a single-pair scope (the run evidence an attempt names)."""
    scope = RunScope(
        universe=["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        strategies=[strategy],
        costs=[CostApplication(regime=regime, fee_ratio=fee_ratio, tax_ratio=tax_ratio)],
        initial_cash=100_000.0,
        parameters=parameters if parameters is not None else {"lookback": 20},
    )
    return finalise_run(
        stub_result(),
        scope=scope,
        code=CodeIdentity(commit="a" * 40),
        root=root,
        now=now,
    )


def completed_outcome(run: FinalisedRun, *, evaluation_status: str = "evaluated") -> PairOutcome:
    return PairOutcome(
        strategy=run.manifest.strategies[0],
        cost_regime=run.manifest.cost_regimes[0],
        outcome="completed",
        run_id=run.manifest.run_id,
        run_dir=run.directory.name,
        evaluation_status=cast("Any", evaluation_status),
    )


def build_record(
    *,
    requested: list[PairRequest],
    outcomes: list[PairOutcome],
    status: str,
    notes: list[str] | None = None,
    markets: list[str] | None = None,
    universe: list[str] | None = None,
    costs: list[CostApplication] | None = None,
    run_write_failures: list[RunWriteFailure] | None = None,
    attempt_dir: str = "20261106T101500Z-abcdef012345",
) -> AttemptRecord:
    """Build an :class:`AttemptRecord` whose ID is derived from its own scope."""
    scope = AttemptScope(
        requested=requested,
        universe=universe if universe is not None else ["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=markets if markets is not None else ["us_equity"],
        costs=list(costs or []),
    )
    return AttemptRecord(
        protocol=ATTEMPT_PROTOCOL,
        attempt_id=scope.attempt_id(),
        attempt_dir=attempt_dir,
        started_at=_BUILD_TS,
        finalized_at=_BUILD_TS + timedelta(seconds=5),
        status=cast("Any", status),
        requested=requested,
        outcomes=outcomes,
        run_write_failures=list(run_write_failures or []),
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=list(scope.universe),
        initial_cash=100_000.0,
        markets=list(scope.markets),
        costs=list(scope.costs),
        artifacts=[],
        notes=list(notes or []),
    )


def _failed(strategy: str = "trend_following", regime: str = "realistic") -> PairOutcome:
    return PairOutcome(strategy=strategy, cost_regime=regime, outcome="failed", error_type="RuntimeError", reason="boom")


def _write_failure(strategy: str = "trend_following", regime: str = "realistic") -> RunWriteFailure:
    return RunWriteFailure(strategy=strategy, cost_regime=regime, error_type="RuntimeError", reason="simulated finalise failure")


def _attempt_dirs(root: Path) -> list[Path]:
    attempts_root = attempts_root_for(root)
    return sorted(path for path in attempts_root.iterdir() if path.is_dir()) if attempts_root.is_dir() else []


def _card(card_id: str = "strategy-comparison", *, sharpe: float = 1.1) -> FindingCard:
    return FindingCard(
        id=card_id,
        axis="strategy",
        claim="trend following beats mean reversion",
        verdict="positive",
        conclusion="higher Sharpe at realistic cost",
        metrics={"sharpe": sharpe},
        run_date=date(2026, 11, 6),
    )


# ---------------------------------------------------------------------------
# 1. Attempt identity: documented field order, inputs only
# ---------------------------------------------------------------------------


def test_attempt_payload_order_matches_attempt_field_order() -> None:
    """``ATTEMPT_FIELD_ORDER`` is documentation *and* the payload builder's source.

    Falsified if :meth:`AttemptScope.scope_payload` ever hand-orders its keys again:
    the preimage would then be able to drift from the documented order, which is
    exactly how a run/attempt identity silently changes meaning.
    """
    scope = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["MSFT", "AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    payload = scope.scope_payload()
    assert tuple(payload) == ATTEMPT_FIELD_ORDER
    assert ATTEMPT_FIELD_ORDER == ("protocol", "requested", "universe", "start_date", "end_date", "initial_cash", "markets", "costs")

    # Canonicalisation: universe sorted/de-duplicated, so input order is not identity.
    assert payload["universe"] == ["AAPL", "MSFT"]
    assert json.loads(scope.canonical_scope_json()) == payload


def test_attempt_id_identifies_inputs_not_the_invocation_instant() -> None:
    """Equal inputs share an ID; a changed input moves it. Timestamps never do.

    Falsified if an attempt ID ever absorbed a timestamp, an artifact hash or a code
    identity: two identical invocations must coexist under one ID with distinct
    directories (E01-1).
    """
    base = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    twin = AttemptScope(
        # Same inputs, different request ordering: still the same identity.
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    assert base.attempt_id() == twin.attempt_id()

    changed_costs = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.0, tax_ratio=0.0)],
    )
    changed_markets = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["cn_ashare"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    changed_request = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic"), PairRequest(strategy="momentum", cost_regime="zero")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    changed_window = AttemptScope(
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0] + timedelta(days=1),
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
    )
    ids = {base.attempt_id(), changed_costs.attempt_id(), changed_markets.attempt_id(), changed_request.attempt_id(), changed_window.attempt_id()}
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# 2. Status consistency, coverage and duplicate refusal
# ---------------------------------------------------------------------------


def test_status_consistency_complete_partial_zero_completion_write_failed(tmp_path: Path) -> None:
    """Each status must be exactly the one the outcomes justify.

    Falsified if a status could be recorded that the outcomes contradict: ``complete``
    with a failed pair, ``partial`` with nothing completed, ``zero_completion`` with a
    completed outcome, or an empty-request ``zero_completion`` with no note. A
    ``write_failed`` record must also name at least one run-evidence write that raised,
    so an all-completed invocation can never claim it.
    """
    first = finalise_pair(tmp_path, strategy="trend_following")
    second = finalise_pair(tmp_path, strategy="momentum", regime="zero", fee_ratio=0.0, tax_ratio=0.0)
    both = [PairRequest(strategy="trend_following", cost_regime="realistic"), PairRequest(strategy="momentum", cost_regime="zero")]
    completed = [completed_outcome(first), completed_outcome(second)]

    complete = build_record(requested=both, outcomes=completed, status="complete")
    assert complete.status == "complete"

    partial = build_record(requested=both, outcomes=[completed[0], _failed("momentum", "zero")], status="partial")
    assert partial.status == "partial"

    zero = build_record(requested=both[:1], outcomes=[_failed()], status="zero_completion")
    assert zero.status == "zero_completion"

    # 'write_failed' may coexist with completed outcomes (one write raised, others held).
    write_failed = build_record(
        requested=both,
        outcomes=[completed[0], _failed("momentum", "zero")],
        status="write_failed",
        run_write_failures=[_write_failure("momentum", "zero")],
        notes=["one run-evidence write raised"],
    )
    assert write_failed.status == "write_failed"

    with pytest.raises(ValidationError, match="every requested pair completed"):
        build_record(requested=both, outcomes=[completed[0], _failed("momentum", "zero")], status="complete")
    with pytest.raises(ValidationError, match="at least one completed"):
        build_record(requested=both, outcomes=[_failed("trend_following", "realistic"), _failed("momentum", "zero")], status="partial")
    with pytest.raises(ValidationError, match="must not record a completed outcome"):
        build_record(requested=both, outcomes=completed, status="zero_completion")
    with pytest.raises(ValidationError, match="must carry notes naming why nothing ran"):
        build_record(requested=[], outcomes=[], status="zero_completion")
    # With a note it is accepted: the record names why nothing ran.
    assert build_record(requested=[], outcomes=[], status="zero_completion", notes=["no pair was requested"]).notes
    # ... and a 'write_failed' that names no failure is refused, because the status is
    # a claim about a write the record must own.
    with pytest.raises(ValidationError, match="must record at least one run_write_failures entry"):
        build_record(requested=both, outcomes=[completed[0], _failed("momentum", "zero")], status="write_failed")


def test_write_failed_without_a_recorded_failure_is_refused(tmp_path: Path) -> None:
    """``write_failed`` is refused unless the record names the write that raised.

    Falsified if an all-``completed`` record (every requested pair finalised, nothing
    raised) could claim ``write_failed``: the status would then be an unbacked label a
    reader could only trust, and a real failure would be indistinguishable from it.
    """
    run = finalise_pair(tmp_path)
    with pytest.raises(ValidationError, match="must record at least one run_write_failures entry"):
        build_record(
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[completed_outcome(run)],
            status="write_failed",
        )
    with pytest.raises(ValidationError, match="must record at least one run_write_failures entry"):
        write_attempt_record(
            tmp_path,
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[completed_outcome(run)],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            universe=["AAPL", "MSFT"],
            initial_cash=100_000.0,
            markets=["us_equity"],
            runs=(run,),
            status="write_failed",
            started_at=_LOGICAL_TS,
        )


def test_non_write_failed_status_with_recorded_failures_is_refused(tmp_path: Path) -> None:
    """Only ``write_failed`` may carry ``run_write_failures`` entries.

    Falsified if a ``complete``/``partial``/``zero_completion`` record could carry a
    recorded failure: the status and the failure evidence would then contradict each
    other, and a reader could not tell whether the invocation succeeded.
    """
    run = finalise_pair(tmp_path)
    second = finalise_pair(tmp_path, strategy="momentum", regime="zero", fee_ratio=0.0, tax_ratio=0.0)
    both = [PairRequest(strategy="trend_following", cost_regime="realistic"), PairRequest(strategy="momentum", cost_regime="zero")]
    completed = [completed_outcome(run), completed_outcome(second)]
    for status, outcomes in (
        ("complete", completed),
        ("partial", [completed[0], _failed("momentum", "zero")]),
        ("zero_completion", [_failed("trend_following", "realistic"), _failed("momentum", "zero")]),
    ):
        with pytest.raises(ValidationError, match="must not record run_write_failures entries"):
            build_record(requested=both, outcomes=outcomes, status=status, run_write_failures=[_write_failure("momentum", "zero")])


def test_write_failure_for_a_non_requested_pair_is_refused() -> None:
    """A recorded write failure must belong to a pair the invocation requested.

    Falsified if the record accepted a failure for an unrequested pair: the failure
    evidence would then describe work this invocation never attempted, and the
    requested set would stop being the boundary of what the record can claim.
    """
    with pytest.raises(ValidationError, match="run_write_failures name pairs that were not requested"):
        build_record(
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[_failed("trend_following", "realistic")],
            status="write_failed",
            run_write_failures=[_write_failure("momentum", "zero")],
        )
    with pytest.raises(ValidationError, match="a recorded run-evidence write failure needs a non-empty value"):
        RunWriteFailure(strategy="trend_following", cost_regime="realistic", error_type="RuntimeError", reason="  ")


def test_outcome_for_a_non_requested_pair_is_refused(tmp_path: Path) -> None:
    """An outcome may not name a pair the invocation never requested.

    Falsified if the record accepted an outcome outside ``requested``: the CLI's
    "requested set is the only published evidence" invariant would become
    unverifiable from the record alone.
    """
    run = finalise_pair(tmp_path)
    with pytest.raises(ValidationError, match="pairs that were not requested"):
        build_record(
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[completed_outcome(run), _failed("momentum", "zero")],
            status="partial",
        )


def test_duplicate_requested_pair_is_refused() -> None:
    """A pair requested twice is one attempt: the record refuses the ambiguity.

    Falsified if duplicates survived, because the coverage check could then accept
    two outcomes for one pair (or one for two) without noticing.
    """
    pair = PairRequest(strategy="trend_following", cost_regime="realistic")
    with pytest.raises(ValidationError, match="requested pairs must be unique"):
        build_record(requested=[pair, pair], outcomes=[_failed(), _failed()], status="zero_completion")


def test_outcome_fields_must_match_the_classification() -> None:
    """Each outcome kind requires and forbids its own fields, naming the violation."""
    with pytest.raises(ValidationError, match="must record the run evidence"):
        PairOutcome(strategy="s", cost_regime="r", outcome="completed")
    with pytest.raises(ValidationError, match="must not record run fields"):
        PairOutcome(
            strategy="s",
            cost_regime="r",
            outcome="missing",
            run_id="a" * 64,
            run_dir="20260101T000000Z-deadbeef0000",
            evaluation_status="evaluated",
        )
    with pytest.raises(ValidationError, match="non-empty error_type"):
        PairOutcome(strategy="s", cost_regime="r", outcome="failed", reason="boom")
    with pytest.raises(ValidationError, match="non-empty reason"):
        PairOutcome(strategy="s", cost_regime="r", outcome="failed", error_type="RuntimeError")
    with pytest.raises(ValidationError, match="one of"):
        PairOutcome(strategy="s", cost_regime="r", outcome="unevidenced", reason="because")
    # The fixed reasons are accepted, and are the skip vocabulary the writers emit.
    assert PairOutcome(strategy="s", cost_regime="r", outcome="unevidenced", reason="undated_curve").reason == "undated_curve"
    assert PairOutcome(strategy="s", cost_regime="r", outcome="unevidenced", reason="applied_costs_not_recorded").reason


def test_manifest_reverifies_the_attempt_id_against_its_inputs() -> None:
    """An edited ``attempt_id``/``markets`` pair is refused as a tampered record.

    Falsified if the ID were stored on trust: the manifest must re-derive it from the
    inputs it records (the same discipline as a run manifest).
    """
    record = build_record(requested=[PairRequest(strategy="s", cost_regime="r")], outcomes=[_failed("s", "r")], status="zero_completion")
    payload = json.loads(record.model_dump_json())
    payload["markets"] = ["cn_ashare"]  # recorded markets no longer match the stated ID
    with pytest.raises(ValidationError, match="attempt_id does not match"):
        AttemptRecord.model_validate(payload)


# ---------------------------------------------------------------------------
# 3. Publishing: atomic claim, discriminators, artifacts
# ---------------------------------------------------------------------------


def test_publish_claims_a_discriminator_and_never_overwrites(tmp_path: Path) -> None:
    """Two records with one name coexist as ``name`` and ``name-2``; nothing is replaced.

    Falsified if the second publish overwrote the first (or reused its directory),
    which would let one invocation's evidence masquerade as another's.
    """
    first = write_attempt_record(
        tmp_path,
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        benchmark=_price_frame().select("date", pl.col("close").alias("equity")),
        cards=[_card()],
        started_at=_LOGICAL_TS,
        finalized_at=_LOGICAL_TS + timedelta(seconds=1),
    )
    first_bytes = (first.directory / ATTEMPT_MANIFEST_NAME).read_bytes()

    second = write_attempt_record(
        tmp_path,
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        benchmark=_price_frame().select("date", pl.col("close").alias("equity")),
        cards=[_card()],
        started_at=_LOGICAL_TS,
        finalized_at=_LOGICAL_TS + timedelta(seconds=2),
    )

    assert first.directory.name != second.directory.name
    assert second.directory.name == f"{first.directory.name}-2"
    assert second.record.attempt_dir == second.directory.name
    assert (first.directory / ATTEMPT_MANIFEST_NAME).read_bytes() == first_bytes  # untouched
    assert sorted(path.name for path in _attempt_dirs(tmp_path)) == sorted([first.directory.name, second.directory.name])
    for attempt in (first, second):
        verdict = validate_attempt_directory(attempt.directory)
        assert verdict.finalized, verdict.reasons
        assert not attempt.directory.name.startswith(".tmp-")
    assert not [path for path in attempts_root_for(tmp_path).iterdir() if path.name.startswith(".tmp-")]


def test_artifact_checksums_validate_and_tampering_is_rejected_distinctly(tmp_path: Path) -> None:
    """Every recorded artifact is checksum-verified, and each failure has its own reason.

    Falsified if a tampered benchmark or a deleted artifact still validated (tampered
    evidence would read as intact), or if two different failures produced one
    catch-all reason (a reviewer could not tell them apart).
    """
    attempt = write_attempt_record(
        tmp_path,
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        benchmark=_price_frame().select("date", pl.col("close").alias("equity")),
        cards=[_card()],
        started_at=_LOGICAL_TS,
    )
    assert validate_attempt_directory(attempt.directory).finalized
    recorded = {artifact.path for artifact in attempt.record.artifacts}
    assert recorded == {BENCHMARK_ATTEMPT_ARTIFACT, "strategy-comparison.json"}

    absent = validate_attempt_directory(attempt.directory.with_name("no-such-attempt"))
    assert not absent.finalized and absent.record is None
    assert absent.reasons == ["attempt directory does not exist"]

    # Delete the benchmark: a missing artifact, not a silent pass.
    (attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).unlink()
    deleted = validate_attempt_directory(attempt.directory)
    assert not deleted.finalized
    assert deleted.reasons == [f"artifact missing: '{BENCHMARK_ATTEMPT_ARTIFACT}'"]
    assert not is_finalized_attempt(attempt.directory)

    # Rewrite it with different bytes: size and hash mismatch, two distinct reasons.
    (attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).write_bytes(b"not a parquet file")
    tampered = validate_attempt_directory(attempt.directory)
    assert not tampered.finalized
    assert any("size mismatch" in reason for reason in tampered.reasons), tampered.reasons
    assert any("hash mismatch" in reason for reason in tampered.reasons), tampered.reasons
    assert set(tampered.reasons) != set(deleted.reasons)

    # A card carrying a non-finite JSON token is a third, distinct failure mode.
    card_path = attempt.directory / "strategy-comparison.json"
    card_text = card_path.read_text(encoding="utf-8").replace('"sharpe": 1.1', '"sharpe": NaN')
    card_path.write_text(card_text, encoding="utf-8")
    non_finite = validate_attempt_directory(attempt.directory)
    assert not non_finite.finalized
    assert any("non-finite JSON token 'NaN'" in reason for reason in non_finite.reasons), non_finite.reasons
    assert set(non_finite.reasons) not in (set(deleted.reasons), set(tampered.reasons))


def test_attempt_dir_name_must_match_the_directory(tmp_path: Path) -> None:
    """A renamed attempt directory is not accepted under its new name.

    Falsified if the validator ignored ``attempt_dir``: a directory copied or renamed
    from another invocation would validate as itself.
    """
    attempt = write_attempt_record(
        tmp_path,
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        started_at=_LOGICAL_TS,
    )
    renamed = attempt.directory.with_name(attempt.directory.name + "-renamed")
    attempt.directory.rename(renamed)
    verdict = validate_attempt_directory(renamed)
    assert not verdict.finalized
    assert any("does not match directory" in reason for reason in verdict.reasons), verdict.reasons


def test_strict_card_writer_refuses_a_non_finite_card_value(tmp_path: Path) -> None:
    """A card metric carrying ``NaN`` refuses the attempt instead of becoming ``null``.

    Falsified if the strict card path ever sanitised the defect: Pydantic would encode
    ``NaN`` as JSON ``null``, i.e. a measured-looking document, and the attempt would
    publish a non-finite number as evidence.
    """
    with pytest.raises(ValueError, match=r"strategy-comparison\.json field 'metrics\.sharpe' is non-finite"):
        write_attempt_record(
            tmp_path,
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[_failed()],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            universe=["AAPL"],
            initial_cash=100_000.0,
            markets=["us_equity"],
            cards=[_card(sharpe=float("nan"))],
            started_at=_LOGICAL_TS,
        )
    assert _attempt_dirs(tmp_path) == []  # nothing was published
    assert not [path for path in attempts_root_for(tmp_path).iterdir() if path.name.startswith(".tmp-")]


def test_completed_outcome_requires_its_supplied_run(tmp_path: Path) -> None:
    """A ``completed`` outcome must name a supplied finalized run (never trust a claim).

    Falsified if a record could assert run evidence that was not supplied (or omit a
    supplied run from its outcomes): the record would then point at evidence it never
    checked.
    """
    run = finalise_pair(tmp_path)
    with pytest.raises(ValueError, match="requires the finalized run evidence"):
        write_attempt_record(
            tmp_path,
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[completed_outcome(run)],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            universe=["AAPL"],
            initial_cash=100_000.0,
            markets=["us_equity"],
            runs=(),
            started_at=_LOGICAL_TS,
        )
    with pytest.raises(ValueError, match="no completed outcome names"):
        write_attempt_record(
            tmp_path,
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[_failed()],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            universe=["AAPL"],
            initial_cash=100_000.0,
            markets=["us_equity"],
            runs=(run,),
            status="write_failed",
            started_at=_LOGICAL_TS,
        )


# ---------------------------------------------------------------------------
# 4. The arena writer publishes attempts (end to end, no engine)
# ---------------------------------------------------------------------------


def test_identical_invocations_coexist_with_equal_attempt_id_and_distinct_directories(tmp_path: Path) -> None:
    """Two identical invocations share an ``attempt_id`` and occupy distinct directories.

    Falsified if identity absorbed the timestamp (IDs would differ) or if the second
    publish overwrote the first (one attempt directory would not be able to hold both).
    """
    outcome = stub_outcome(pairs=[("trend_following", "realistic"), ("trend_following", "zero")])

    _, first = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))
    _, second = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))

    assert first.attempt is not None and second.attempt is not None
    assert first.attempt.record.attempt_id == second.attempt.record.attempt_id
    assert first.attempt.directory != second.attempt.directory
    assert len(_attempt_dirs(tmp_path)) == 2
    for attempt in (first.attempt, second.attempt):
        verdict = validate_attempt_directory(attempt.directory)
        assert verdict.finalized, verdict.reasons
        assert attempt.record.status == "complete"
        assert {outcome.outcome for outcome in attempt.record.outcomes} == {"completed"}


def test_run_evidence_failure_publishes_write_failed_and_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run-evidence write failure is durable evidence *and* reaches the caller.

    Falsified if the attempt were never published (the failure would only exist in the
    terminal) or if the exception were swallowed (the CLI would report success). The
    attempt must record ``write_failed`` with the runs that did finalise.
    """
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    original = evidence.finalise_run

    def _explode(result: BacktestResult, *, scope: RunScope, **kwargs: Any) -> FinalisedRun:
        if scope.strategies == ["trend_following"]:
            raise RuntimeError("simulated finalise failure")
        return original(result, scope=scope, **kwargs)

    monkeypatch.setattr(evidence, "finalise_run", _explode)
    outcome = stub_outcome(pairs=[("trend_following", "realistic"), ("mean_reversion", "realistic")])

    with pytest.raises(RuntimeError, match="simulated finalise failure") as excinfo:
        write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))

    assert any("published with status 'write_failed'" in note for note in getattr(excinfo.value, "__notes__", []))

    attempts = [path for path in _attempt_dirs(tmp_path) if not path.name.startswith(".tmp-")]
    assert len(attempts) == 1
    verdict = validate_attempt_directory(attempts[0])
    assert verdict.finalized, verdict.reasons
    assert verdict.record is not None
    assert verdict.record.status == "write_failed"
    statuses = {pair_outcome.slug: pair_outcome.outcome for pair_outcome in verdict.record.outcomes}
    assert statuses == {"trend_following__realistic": "failed", "mean_reversion__realistic": "completed"}
    failed = next(pair_outcome for pair_outcome in verdict.record.outcomes if pair_outcome.outcome == "failed")
    assert failed.error_type == "RuntimeError"
    assert "simulated finalise failure" in str(failed.reason)
    # The failure the status claims is recorded, not just emitted to the log.
    assert [failure.slug for failure in verdict.record.run_write_failures] == ["trend_following__realistic"]
    assert verdict.record.run_write_failures[0].error_type == "RuntimeError"
    assert "simulated finalise failure" in verdict.record.run_write_failures[0].reason
    # Exactly the run that did finalise exists, and no legacy path was written.
    runs = sorted(path for path in (tmp_path / "runs").iterdir() if path.is_dir())
    assert len(runs) == 1
    assert validate_run_directory(runs[0]).finalized
    assert not (tmp_path / "trend_following__realistic").exists()
    assert not (tmp_path / "benchmark__equity.parquet").exists()


def test_writer_records_unevidenced_and_missing_pairs_without_a_run(tmp_path: Path) -> None:
    """Undated runs and absent pairs are classified, never given invented evidence.

    Falsified if an undated run produced a run directory or manifest (no calendar is
    invented), if a pair with no run were silently dropped from the record, or if a
    skipped pair still advanced the run evidence.
    """
    outcome = stub_outcome(
        pairs=[("trend_following", "realistic")],
        requested=[("trend_following", "realistic"), ("mean_reversion", "zero")],
        dates=False,  # the completed run carries no backend dates
    )

    cards, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))

    assert cards  # the comparison cards are still written into the attempt directory
    assert summary.finalized == []
    assert list(summary.skipped_undated) == ["trend_following__realistic"]
    assert not (tmp_path / "runs").exists()  # no run directory, no manifest, no invented calendar
    assert not (tmp_path / "trend_following__realistic").exists()  # and no legacy path
    assert summary.attempt is not None
    assert summary.attempt.record.status == "zero_completion"
    statuses = {pair_outcome.slug: (pair_outcome.outcome, pair_outcome.reason) for pair_outcome in summary.attempt.record.outcomes}
    assert statuses == {
        "trend_following__realistic": ("unevidenced", "undated_curve"),
        "mean_reversion__zero": ("missing", None),
    }
    # The attempt still owns the benchmark copy and the cards it did produce.
    assert (summary.attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).is_file()
    assert validate_attempt_directory(summary.attempt.directory).finalized


def test_zero_completion_cli_publishes_an_attempt_and_no_run(tmp_path: Path) -> None:
    """A zero-completion CLI invocation publishes its classification and creates no run.

    Falsified if the invocation wrote run evidence (there is none), or if it exited 0,
    or if nothing durable recorded that every requested pair failed: the failure would
    then live only in the terminal output.
    """
    from equity_lake.cli.__main__ import app  # noqa: PLC0415 - CLI seam

    outcome = ArenaOutcome(
        runs=[],
        data=_price_frame(),
        benchmark=equal_weight_buyhold(_price_frame(), initial_cash=100_000.0),
        initial_cash=100_000.0,
        requested=[("trend_following", "realistic"), ("mean_reversion", "realistic")],
        failed=[
            ArenaFailure(strategy="trend_following", cost_regime="realistic", error_type="ValueError", reason="no decisions"),
            ArenaFailure(strategy="mean_reversion", cost_regime="realistic", error_type="RuntimeError", reason="boom"),
        ],
        markets=("us_equity",),
    )
    args = [
        "arena",
        "run",
        "--tickers",
        "AAPL,MSFT",
        "--start-date",
        IRREGULAR_DATES[0].isoformat(),
        "--end-date",
        IRREGULAR_DATES[-1].isoformat(),
        "--output-dir",
        str(tmp_path),
        "--strategies",
        "trend_following",
        "--cost-regimes",
        "realistic",
    ]
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 1, result.stdout
    assert "no run evidence or cards were written" in result.stdout
    assert "Attempt record:" in result.stdout
    assert not (tmp_path / "runs").exists()  # no run directory was created
    assert not (tmp_path / "trend_following__realistic").exists()  # and no legacy path
    attempts = [path for path in _attempt_dirs(tmp_path) if not path.name.startswith(".tmp-")]
    assert len(attempts) == 1
    verdict = validate_attempt_directory(attempts[0])
    assert verdict.finalized, verdict.reasons
    assert verdict.record is not None
    assert verdict.record.status == "zero_completion"
    assert {pair_outcome.outcome for pair_outcome in verdict.record.outcomes} == {"failed"}
    assert {pair_outcome.slug for pair_outcome in verdict.record.outcomes} == {"trend_following__realistic", "mean_reversion__realistic"}
    assert verdict.record.artifacts == []  # no benchmark, no cards: nothing was measured
    # The record owns the classification counts, so the terminal output is reproducible.
    assert "requested=2" in verdict.record.notes
    assert "failed=2" in verdict.record.notes
    assert "completed=0" in verdict.record.notes


def test_iter_attempts_excludes_temporaries_and_orders_by_name(tmp_path: Path) -> None:
    """Candidate enumeration is name-ordered and never treats a temp dir as evidence.

    Falsified if a ``.tmp-`` build directory appeared as an attempt (a half-written
    invocation would be indistinguishable from a published one).
    """
    attempt = write_attempt_record(
        tmp_path,
        requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        started_at=_LOGICAL_TS,
    )
    attempts_root = attempts_root_for(tmp_path)
    (attempts_root / ".tmp-halfwritten").mkdir()
    (attempts_root / "zzz-no-manifest").mkdir()

    directories = list(iter_attempt_directories(tmp_path))
    assert attempt.directory in directories
    assert all(not path.name.startswith(".tmp-") for path in directories)
    assert directories == sorted(directories)
    assert not is_finalized_attempt(attempts_root / "zzz-no-manifest")


# ---------------------------------------------------------------------------
# 5. Evidence refs are bound to this invocation's immutable evidence
# ---------------------------------------------------------------------------


def test_bind_cards_resolves_a_requested_legacy_slug_ref_to_the_finalised_run_id(tmp_path: Path) -> None:
    """A requested pair's ``<strategy>__<regime>/`` ref becomes ``run:<run_id>``.

    Falsified if a newly written card kept the legacy slug path: it would point at a
    frozen directory a *different* run wrote, which is exactly the wrong-evidence bug
    ADR-0025 Decision 8 closes.
    """
    run = finalise_pair(tmp_path)
    card = _card().model_copy(update={"evidence_refs": ["trend_following__realistic/", BENCHMARK_ATTEMPT_ARTIFACT]})

    bound = bind_cards_to_evidence(
        [card],
        runs_by_slug={"trend_following__realistic": run.manifest.run_id},
        requested_slugs={"trend_following__realistic"},
        attempt_id="a" * 64,
        benchmark_written=True,
    )

    assert bound[0].evidence_refs == [f"run:{run.manifest.run_id}", f"attempt:{'a' * 64}/{BENCHMARK_ATTEMPT_ARTIFACT}"]
    assert bound[0].id == card.id and bound[0].verdict == card.verdict and bound[0].metrics == card.metrics
    # The input is never mutated: the writer's return value is a new list of new cards.
    assert card.evidence_refs == ["trend_following__realistic/", BENCHMARK_ATTEMPT_ARTIFACT]


def test_bind_cards_marks_a_requested_slug_without_a_finalised_run_unevidenced() -> None:
    """A requested slug with no finalised run becomes ``unevidenced:<slug>``.

    Falsified if a skip were bound to some other run's ID (a fabricated pointer) or
    dropped from the refs (the missing evidence would become invisible).
    """
    card = _card().model_copy(update={"evidence_refs": ["trend_following__realistic/"]})
    bound = bind_cards_to_evidence(
        [card], runs_by_slug={}, requested_slugs={"trend_following__realistic"}, attempt_id="b" * 64, benchmark_written=True
    )
    assert bound[0].evidence_refs == ["unevidenced:trend_following__realistic"]


def test_bind_cards_leaves_shape_similar_tokens_verbatim() -> None:
    """Only an exactly-requested ``<slug>/`` is rewritten; string shape never decides.

    Falsified if any of these adversarial tokens were rewritten: none of them is one of
    this invocation's pair refs, so rewriting one would make the card claim evidence
    the invocation never wrote (``C:\\reports\\foo__bar/`` is a Windows path,
    ``run:abc__def/`` is an already-bound run token, ``custom__token/`` is an unknown
    name, and ``momentum__zero/extra.json`` is a path *below* a pair directory, not the
    pair directory itself).
    """
    refs = ["C:\\reports\\foo__bar/", "run:abc__def/", "custom__token/", "momentum__zero/extra.json"]
    card = _card().model_copy(update={"evidence_refs": list(refs)})
    bound = bind_cards_to_evidence(
        [card],
        runs_by_slug={"momentum__zero": "f" * 64, "foo__bar": "a" * 64},
        requested_slugs={"momentum__zero", "foo__bar"},
        attempt_id="f" * 64,
        benchmark_written=True,
    )
    assert bound[0].evidence_refs == refs


def test_bind_cards_leaves_a_pair_ref_the_invocation_did_not_request_verbatim() -> None:
    """A ``<slug>/`` ref for a slug outside the request is preserved, even if it ran.

    Falsified if the explicit candidate set were replaced by a shape test: a card would
    then be bound to a run the invocation never requested (and therefore never
    published under this attempt), silently widening the card's claimed evidence.
    """
    card = _card().model_copy(update={"evidence_refs": ["value__high/"]})
    bound = bind_cards_to_evidence(
        [card],
        runs_by_slug={"trend_following__realistic": "c" * 64},
        requested_slugs={"trend_following__realistic"},
        attempt_id="c" * 64,
        benchmark_written=True,
    )
    assert bound[0].evidence_refs == ["value__high/"]


def test_bind_cards_rewrites_the_benchmark_ref_to_the_attempt_directory() -> None:
    """``benchmark__equity.parquet`` becomes the attempt-owned copy, never the legacy file.

    Falsified if the bare name survived: the flat legacy ``benchmark__equity.parquet``
    is frozen evidence from another run, so a new card naming it would describe a
    curve this attempt never measured.
    """
    card = _card().model_copy(update={"evidence_refs": [BENCHMARK_ATTEMPT_ARTIFACT]})
    bound = bind_cards_to_evidence([card], runs_by_slug={}, requested_slugs=set(), attempt_id="c" * 64, benchmark_written=True)
    assert bound[0].evidence_refs == [f"attempt:{'c' * 64}/{BENCHMARK_ATTEMPT_ARTIFACT}"]


def test_bind_cards_maps_the_benchmark_ref_unevidenced_when_no_copy_is_written() -> None:
    """Without a benchmark copy the ref is ``unevidenced:benchmark``, never an attempt path.

    Falsified if the binder kept composing ``attempt:<id>/benchmark__equity.parquet``
    when the caller states no copy is written: the card would name an artifact the
    attempt directory does not hold, which is a pointer that resolves to nothing.
    """
    card = _card().model_copy(update={"evidence_refs": [BENCHMARK_ATTEMPT_ARTIFACT]})
    bound = bind_cards_to_evidence([card], runs_by_slug={}, requested_slugs=set(), attempt_id="c" * 64, benchmark_written=False)
    assert bound[0].evidence_refs == [UNEVIDENCED_BENCHMARK_REF]
    assert UNEVIDENCED_BENCHMARK_REF == "unevidenced:benchmark"


def test_bind_cards_preserves_an_unknown_ref_verbatim() -> None:
    """Any other ref is kept as-is: an unknown pointer is reported, never guessed.

    Falsified if an unrecognised ref were rewritten or dropped: a reader would lose the
    only honest statement the card makes about evidence the binder does not know.
    """
    refs = ["data/raw/prices.parquet", "https://example.invalid/note", "custom__token"]
    card = _card().model_copy(update={"evidence_refs": list(refs)})
    bound = bind_cards_to_evidence(
        [card],
        runs_by_slug={"trend_following__realistic": "d" * 64},
        requested_slugs={"trend_following__realistic"},
        attempt_id="d" * 64,
        benchmark_written=True,
    )
    assert bound[0].evidence_refs == refs


def test_bind_cards_dedupes_preserving_first_seen_order(tmp_path: Path) -> None:
    """Repeated refs collapse to one, and the surviving order is first-seen.

    Falsified if binding produced duplicates (two refs for one artifact) or sorted them
    (the card's evidence order would stop reflecting the writer's construction order).
    """
    run = finalise_pair(tmp_path)
    card = _card().model_copy(
        update={
            "evidence_refs": [
                "trend_following__realistic/",
                BENCHMARK_ATTEMPT_ARTIFACT,
                "trend_following__realistic/",
                "unknown/ref.json",
                BENCHMARK_ATTEMPT_ARTIFACT,
            ]
        }
    )
    bound = bind_cards_to_evidence(
        [card],
        runs_by_slug={"trend_following__realistic": run.manifest.run_id},
        requested_slugs={"trend_following__realistic"},
        attempt_id="e" * 64,
        benchmark_written=True,
    )
    assert bound[0].evidence_refs == [
        f"run:{run.manifest.run_id}",
        f"attempt:{'e' * 64}/{BENCHMARK_ATTEMPT_ARTIFACT}",
        "unknown/ref.json",
    ]


def _persisted_cards(attempt_dir: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in sorted(attempt_dir.glob("*.json")):
        if path.name == ATTEMPT_MANIFEST_NAME:
            continue
        payloads.append(json.loads(path.read_text(encoding="utf-8")))
    return payloads


def test_writer_persists_only_bound_evidence_refs(tmp_path: Path) -> None:
    """Every card an attempt directory holds names immutable evidence, never a legacy path.

    Falsified if any persisted card carries ``<strategy>__<regime>/`` (a frozen legacy
    path from another run) or the bare legacy ``benchmark__equity.parquet``: the card
    would claim evidence this invocation may not have written, and the whole point of
    an attempt-bound card is that its refs resolve inside this invocation.
    """
    dated = tmp_path / "dated"
    outcome = stub_outcome(pairs=[("trend_following", "realistic"), ("momentum", "realistic")])
    _, summary = write_arena_artifacts_with_evidence(outcome, base=dated, run_date=date(2026, 11, 6))
    assert summary.attempt is not None
    attempt = summary.attempt
    run_ids = {run.manifest.run_id for run in summary.finalized}
    assert run_ids

    payloads = _persisted_cards(attempt.directory)
    assert {payload["id"] for payload in payloads} == {"strategy-comparison", "vs-benchmark"}
    seen_run_refs: set[str] = set()
    for payload in payloads:
        for ref in payload["evidence_refs"]:
            assert not re.fullmatch(r"[^/\s]+__[^/\s]+/", ref), f"legacy slug ref persisted: {ref}"
            assert ref != BENCHMARK_ATTEMPT_ARTIFACT, f"legacy benchmark ref persisted: {ref}"
            assert ref.startswith(("run:", "attempt:", "unevidenced:")), ref
            if ref.startswith("run:"):
                seen_run_refs.add(ref.removeprefix("run:"))
            elif ref.startswith("attempt:"):
                assert ref == f"attempt:{attempt.record.attempt_id}/{BENCHMARK_ATTEMPT_ARTIFACT}"
    assert seen_run_refs == run_ids  # the refs name exactly this invocation's runs

    # An invocation whose runs produced no immutable evidence binds the skipped pair
    # honestly instead of pointing at a legacy directory or inventing a run ID.
    undated = tmp_path / "undated"
    skipped = stub_outcome(pairs=[("trend_following", "realistic")], dates=False)
    _, skipped_summary = write_arena_artifacts_with_evidence(skipped, base=undated, run_date=date(2026, 11, 6))
    assert skipped_summary.attempt is not None
    skipped_refs = [ref for payload in _persisted_cards(skipped_summary.attempt.directory) for ref in payload["evidence_refs"]]
    assert "unevidenced:trend_following__realistic" in skipped_refs
    assert all(ref.startswith(("attempt:", "unevidenced:")) for ref in skipped_refs), skipped_refs


def test_writer_returns_the_cards_it_persisted(tmp_path: Path) -> None:
    """The caller's cards are the persisted cards, field for field.

    Falsified if the writer returned the pre-binding cards (the caller would then hold a
    card the attempt directory does not contain, and a round-trip check comparing only
    verdict/metrics could pass for a stale card with the same numbers) or if a returned
    ref were still in the unbound legacy vocabulary.
    """
    outcome = stub_outcome(pairs=[("trend_following", "realistic"), ("momentum", "realistic")])
    cards = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 11, 6))
    attempt_dirs = [path for path in _attempt_dirs(tmp_path) if not path.name.startswith(".tmp-")]
    assert len(attempt_dirs) == 1
    persisted = [
        FindingCard.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(attempt_dirs[0].glob("*.json"))
        if path.name != ATTEMPT_MANIFEST_NAME
    ]
    assert {card.id for card in cards} == {card.id for card in persisted}
    assert sorted(persisted, key=lambda card: card.id) == sorted(cards, key=lambda card: card.id)
    for card in cards:
        assert card.evidence_refs
        assert all(ref.startswith(("run:", "attempt:")) for ref in card.evidence_refs), card.evidence_refs


def test_writer_binds_the_benchmark_ref_to_the_copy_it_wrote(tmp_path: Path) -> None:
    """With a non-empty benchmark the ref names the attempt copy **and the file exists**.

    Falsified if the bound ref named a path the attempt directory does not hold: the
    token would be an address a reader follows to nothing. (Asserts the artifact on
    disk, not only the string.)
    """
    outcome = stub_outcome(pairs=[("trend_following", "realistic")])
    assert not outcome.benchmark.is_empty()
    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))
    assert summary.attempt is not None
    attempt = summary.attempt

    expected = f"attempt:{attempt.record.attempt_id}/{BENCHMARK_ATTEMPT_ARTIFACT}"
    refs = [ref for payload in _persisted_cards(attempt.directory) for ref in payload["evidence_refs"]]
    benchmark_refs = [ref for ref in refs if ref.startswith("attempt:")]
    assert benchmark_refs, refs
    assert set(benchmark_refs) == {expected}
    assert UNEVIDENCED_BENCHMARK_REF not in refs

    # The token resolves inside this attempt directory: the artifact is really there.
    artifact = attempt.directory / benchmark_refs[0].split("/", 1)[1]
    assert artifact == attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT
    assert artifact.is_file()
    assert artifact.stat().st_size > 0
    assert {record.path for record in attempt.record.artifacts} >= {BENCHMARK_ATTEMPT_ARTIFACT}


def test_writer_maps_the_benchmark_ref_unevidenced_when_no_copy_is_written(tmp_path: Path) -> None:
    """No benchmark copy ⇒ no benchmark path on the card, and no file on disk.

    The writer is invoked for the same attempt ID the cards are bound to, with
    ``benchmark=None`` (the arena passes exactly that when the curve is empty).
    Falsified if the persisted card carried the attempt path
    ``attempt:<attempt_id>/benchmark__equity.parquet`` — asserted by the refs **and** by
    the absence of the file, the temp directories and any flat legacy copy on disk.
    """
    pairs = [PairRequest(strategy="trend_following", cost_regime="realistic")]
    attempt_id = attempt_id_for(
        requested=pairs,
        universe=["AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
    )
    card = _card().model_copy(update={"evidence_refs": [BENCHMARK_ATTEMPT_ARTIFACT]})
    bound = bind_cards_to_evidence([card], runs_by_slug={}, requested_slugs=set(), attempt_id=attempt_id, benchmark_written=False)
    assert bound[0].evidence_refs == [UNEVIDENCED_BENCHMARK_REF]

    attempt = write_attempt_record(
        tmp_path,
        requested=pairs,
        outcomes=[_failed()],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        benchmark=None,
        cards=bound,
        started_at=_LOGICAL_TS,
    )
    assert attempt.record.attempt_id == attempt_id  # the cards named this attempt

    payloads = _persisted_cards(attempt.directory)
    assert payloads and payloads[0]["evidence_refs"] == [UNEVIDENCED_BENCHMARK_REF]
    # Absence on disk, not only the string: no attempt copy, no flat legacy copy, no
    # temp directory left behind, and no manifest entry claiming the artifact.
    assert not (attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).exists()
    assert list(tmp_path.rglob(BENCHMARK_ATTEMPT_ARTIFACT)) == []
    assert not [path for path in attempts_root_for(tmp_path).iterdir() if path.name.startswith(".tmp-")]
    assert BENCHMARK_ATTEMPT_ARTIFACT not in {record.path for record in attempt.record.artifacts}


def test_writer_refuses_an_unusable_benchmark_and_publishes_no_attempt(tmp_path: Path) -> None:
    """An unusable (all-null) benchmark writes no artifact and publishes nothing.

    The refusal happens before any name is claimed, so there is no directory — and
    therefore no card — that could name a benchmark artifact. Falsified if a refused
    curve left an attempt directory, a temp directory or a benchmark file on disk.
    """
    unusable = pl.DataFrame({"date": [IRREGULAR_DATES[0]], "equity": [None]}, schema={"date": pl.Date, "equity": pl.Float64})
    with pytest.raises(ValueError, match="is null"):
        write_attempt_record(
            tmp_path,
            requested=[PairRequest(strategy="trend_following", cost_regime="realistic")],
            outcomes=[_failed()],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            universe=["AAPL"],
            initial_cash=100_000.0,
            markets=["us_equity"],
            benchmark=unusable,
            cards=[_card().model_copy(update={"evidence_refs": [UNEVIDENCED_BENCHMARK_REF]})],
            started_at=_LOGICAL_TS,
        )
    assert _attempt_dirs(tmp_path) == []  # nothing was published, so no card exists
    assert list(tmp_path.rglob(BENCHMARK_ATTEMPT_ARTIFACT)) == []


def test_writer_records_the_run_write_failure_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real write-failure path records the failing pair, error type and reason.

    Falsified if ``run_write_failures`` were empty on a ``write_failed`` attempt, or if
    it named a different pair/error than the write that raised: the record would then
    claim a failure it cannot describe, which is what the validator now refuses.
    """
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    original = evidence.finalise_run

    def _explode(result: BacktestResult, *, scope: RunScope, **kwargs: Any) -> FinalisedRun:
        if scope.strategies == ["trend_following"]:
            raise ValueError("run evidence write refused: simulated")
        return original(result, scope=scope, **kwargs)

    monkeypatch.setattr(evidence, "finalise_run", _explode)
    outcome = stub_outcome(pairs=[("trend_following", "realistic"), ("mean_reversion", "realistic")])

    with pytest.raises(ValueError, match="run evidence write refused"):
        write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))

    attempt = next(path for path in _attempt_dirs(tmp_path) if not path.name.startswith(".tmp-"))
    record = validate_attempt_directory(attempt).record
    assert record is not None
    assert record.status == "write_failed"
    assert [(failure.slug, failure.error_type) for failure in record.run_write_failures] == [("trend_following__realistic", "ValueError")]
    assert "run evidence write refused" in record.run_write_failures[0].reason
    # The recorded failure is exactly the pair the classification marked failed.
    failed = [pair_outcome.slug for pair_outcome in record.outcomes if pair_outcome.outcome == "failed"]
    assert failed == [failure.slug for failure in record.run_write_failures]
