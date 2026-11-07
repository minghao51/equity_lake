"""E01b.1 read-side tests: the latest-success/latest-attempt projections (pure reads).

Scope: ADR-0025 Decisions 5, 8 and 9 — the last attempt stays distinct from the last
successful comparable run, legacy artifacts are a read-only view, and corrupt or
incomplete evidence never looks successful. Every test uses a ``tmp_path`` findings
root; nothing under ``data/findings`` is read or written.

Each test states what falsifies it. The fixtures are shared with
``test_evidence_attempts.py`` so the readers are exercised over evidence the writers
actually produce.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from equity_lake.backtesting.arena import (  # type: ignore[import-untyped]
    ArenaOutcome,
    ArenaRun,
    equal_weight_buyhold,
)
from equity_lake.backtesting.attempts import (  # type: ignore[import-untyped]
    ATTEMPT_MANIFEST_NAME,
    BENCHMARK_ATTEMPT_ARTIFACT,
    PairOutcome,
    RunWriteFailure,
    attempts_root_for,
    write_attempt_record,
)
from equity_lake.backtesting.evidence import (  # type: ignore[import-untyped]  # type: ignore[import-untyped]
    MANIFEST_NAME,
    METRICS_ARTIFACT,
    CodeIdentity,
    CostApplication,
    FinalisedRun,
    RunScope,
    finalise_run,
    iter_run_directories,
    validate_run_directory,
)
from equity_lake.backtesting.evidence_view import (  # type: ignore[import-untyped]
    attempt_history,
    backtest_rows,
    finding_cards,
    latest_attempt,
    latest_attempt_for_slug,
    latest_success_by_slug,
    legacy_slugs,
    slug_for_manifest,
    validated_attempts,
    validated_runs,
)
from equity_lake.backtesting.report import (  # type: ignore[import-untyped]
    write_arena_artifacts_with_evidence,
    write_backtest_report,
)
from equity_lake.backtesting.result import BacktestResult  # type: ignore[import-untyped]
from equity_lake.findings import FindingCard, write_finding_card  # type: ignore[import-untyped]

#: Irregular backend sessions (weekend, holiday-sized and month gaps) so a stored
#: curve is provably not a length-derived calendar.
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


def stub_result(*, dates: list[date] | None | bool = IRREGULAR_DATES, metrics: dict[str, float] | None = None) -> BacktestResult:
    """A dated result (``dates=False`` means the backend supplied *no* dates)."""
    resolved_dates = None if dates is False else cast("list[date] | None", dates)
    return BacktestResult(
        strategy_name="trend_following",
        tickers=["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        final_cash=IRREGULAR_VALUES[-1],
        equity_curve=pl.Series("equity", IRREGULAR_VALUES, dtype=pl.Float64),
        trades=[],
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
    applied_costs: Any = _RECORDED_COSTS,
    dates: list[date] | None | bool = IRREGULAR_DATES,
) -> ArenaOutcome:
    """A real :class:`ArenaOutcome` with stub results (no engine, no lake)."""
    resolved_pairs = pairs if pairs is not None else [("trend_following", "realistic")]
    frame = _price_frame()
    runs = [
        ArenaRun(
            strategy=strategy,
            cost_regime=regime,
            result=stub_result(dates=dates),
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
        failed=[],
        markets=("us_equity",),
    )


def finalise_pair(
    root: Path,
    *,
    strategy: str = "trend_following",
    regime: str = "realistic",
    parameters: dict[str, Any] | None = None,
    now: datetime = _BUILD_TS,
) -> FinalisedRun:
    """One finalized run for a single-pair scope (the run evidence a reader projects)."""
    scope = RunScope(
        universe=["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        strategies=[strategy],
        costs=[CostApplication(regime=regime, fee_ratio=0.001425, tax_ratio=0.0)],
        initial_cash=100_000.0,
        parameters=parameters if parameters is not None else {"lookback": 20},
    )
    return finalise_run(stub_result(), scope=scope, code=CodeIdentity(commit="a" * 40), root=root, now=now)


_LATER = datetime(2026, 11, 6, 11, 0, 0, tzinfo=UTC)


def _card(conclusion: str, *, card_id: str = "strategy-comparison") -> FindingCard:
    return FindingCard(
        id=card_id,
        axis="strategy",
        claim="which strategy dominates at realistic cost?",
        verdict="inconclusive",
        conclusion=conclusion,
        metrics={"sharpe": 1.1},
        run_date=date(2026, 11, 6),
    )


def _run_dir_names(root: Path) -> list[str]:
    return [path.name for path in iter_run_directories(root)]


def _older_and_newer(root: Path) -> tuple[Any, Any]:
    """One older valid run and one newer valid run, both for ``trend_following__realistic``."""
    older = finalise_pair(root, parameters={"lookback": 20})
    newer = finalise_pair(root, parameters={"lookback": 21}, now=_LATER)
    assert older.directory.name != newer.directory.name
    assert validate_run_directory(older.directory).finalized
    assert validate_run_directory(newer.directory).finalized
    return older, newer


def _damage_missing_manifest(directory: Path) -> None:
    (directory / MANIFEST_NAME).unlink()


def _damage_corrupt_artifact(directory: Path) -> None:
    (directory / "equity.parquet").write_bytes(b"not a parquet file")


def _damage_truncated_manifest(directory: Path) -> None:
    payload = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    (directory / MANIFEST_NAME).write_text(json.dumps(payload)[: len(json.dumps(payload)) // 2], encoding="utf-8")


def _damage_invalid_metrics_token(directory: Path) -> None:
    metrics = directory / METRICS_ARTIFACT
    text = metrics.read_text(encoding="utf-8")
    assert '"total_return": 0.03' in text
    metrics.write_text(text.replace('"total_return": 0.03', '"total_return": NaN'), encoding="utf-8")


@pytest.mark.parametrize(
    "damage",
    [_damage_missing_manifest, _damage_corrupt_artifact, _damage_truncated_manifest, _damage_invalid_metrics_token],
    ids=["missing-manifest", "corrupt-artifact", "truncated-manifest", "invalid-metrics-token"],
)
def test_latest_success_ignores_a_damaged_newer_run(tmp_path: Path, damage: Callable[[Path], None]) -> None:
    """A newer but invalid run never shadows an older valid one.

    Falsified if "latest" were derived from the directory name alone: a truncated
    manifest, a corrupt artifact, a missing manifest or a persisted ``NaN`` metric
    would then be served as the newest success, and the age on ``/backtests`` would
    describe evidence that does not validate.
    """
    older, newer = _older_and_newer(tmp_path)
    # Before the damage the newer run *is* the latest success; that is the premise.
    assert latest_success_by_slug(tmp_path)["trend_following__realistic"].directory == newer.directory

    damage(newer.directory)

    latest = latest_success_by_slug(tmp_path)
    assert latest["trend_following__realistic"].directory == older.directory
    assert [Path(verdict.directory).name for verdict in validated_runs(tmp_path)] == [older.directory.name]
    # And the row served for the slug is the older run's, with its own identity.
    row = next(row for row in backtest_rows(tmp_path) if row["slug"] == "trend_following__realistic")
    assert row["last_success"]["run_id"] == older.manifest.run_id
    assert row["last_success"]["run_dir"] == older.directory.name


def test_non_run_directories_never_become_runs(tmp_path: Path) -> None:
    """A temporary/junk directory under ``runs/`` is not evidence and never advances.

    Falsified if candidate enumeration accepted a ``.partial-`` build directory or a
    directory with no manifest: half-written or unrelated directories would appear as
    the latest success.
    """
    older, newer = _older_and_newer(tmp_path)
    runs_root = tmp_path / "runs"
    (runs_root / ".partial-halfwritten").mkdir()
    (runs_root / ".partial-halfwritten" / "equity.parquet").write_bytes(b"junk")
    (runs_root / "20270101T000000Z-ffffffffffff").mkdir()

    latest = latest_success_by_slug(tmp_path)
    assert latest["trend_following__realistic"].directory == newer.directory
    valid = sorted(Path(verdict.directory).name for verdict in validated_runs(tmp_path))
    assert valid == sorted([older.directory.name, newer.directory.name])
    assert not any(name.startswith(".partial-") for name in _run_dir_names(tmp_path))


def test_skipped_runs_never_advance_the_latest_success(tmp_path: Path) -> None:
    """An undated or unrecorded-cost pair produces no run, so it cannot advance anything.

    Falsified if a skip published a manifest: a run with no backend dates (or no
    recorded cost application) would become the "latest success" and hide the real
    last success's age.
    """
    for outcome in (
        stub_outcome(pairs=[("trend_following", "realistic")], dates=False),
        stub_outcome(pairs=[("trend_following", "realistic")], applied_costs=None),
    ):
        _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))
        assert summary.finalized == []
        assert summary.attempt is not None
        assert summary.attempt.record.status == "zero_completion"

    assert latest_success_by_slug(tmp_path) == {}
    assert validated_runs(tmp_path) == []
    assert not (tmp_path / "runs").exists()
    # The skips are still visible as a last attempt, which is exactly the point.
    assert latest_attempt(tmp_path) is not None
    assert latest_attempt_for_slug("trend_following__realistic", tmp_path) is not None
    assert backtest_rows(tmp_path) == []  # no legacy slug, no validated run: no row


def test_a_failed_attempt_does_not_advance_the_last_success_and_both_ages_are_visible(tmp_path: Path) -> None:
    """A failed attempt is visible next to the age of the last real success (Decision 5).

    Falsified if the projection collapsed "latest" into one pointer: a failed attempt
    would either erase the last success or be hidden behind it, and the age of the last
    success would not be readable on the same surface as the failure.
    """
    run = finalise_pair(tmp_path)
    slug = "trend_following__realistic"
    write_attempt_record(
        tmp_path,
        requested=[(slug.split("__")[0], slug.split("__")[1])],
        outcomes=[
            # The same writer vocabulary report.py emits for a failed pair.
            PairOutcome(
                strategy="trend_following",
                cost_regime="realistic",
                outcome="failed",
                error_type="RuntimeError",
                reason="simulated failure",
            )
        ],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL", "MSFT"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        status="write_failed",
        run_write_failures=[
            RunWriteFailure(strategy="trend_following", cost_regime="realistic", error_type="RuntimeError", reason="simulated failure")
        ],
    )

    clock = datetime.now(UTC)
    rows = backtest_rows(tmp_path, now=clock)
    row = next(candidate for candidate in rows if candidate["slug"] == slug)

    assert row["last_success"]["run_id"] == run.manifest.run_id  # the failure did not erase it
    assert row["last_success"]["age_seconds"] >= 0
    assert row["last_success"]["finalized_at"] == run.manifest.finalized_at.isoformat()
    assert row["last_attempt"]["status"] == "write_failed"
    assert row["last_attempt"]["age_seconds"] >= 0
    assert row["last_attempt"]["completed"] == 0
    assert row["last_attempt"]["failed"] == [
        {"strategy": "trend_following", "cost_regime": "realistic", "error_type": "RuntimeError", "reason": "simulated failure"}
    ]
    # The attempt is the newest attempt for that slug, and it did request the slug.
    assert latest_attempt_for_slug(slug, tmp_path) is not None


def test_legacy_slug_row_is_served_when_no_validated_run_exists(tmp_path: Path) -> None:
    """The frozen legacy ``<slug>/metrics.json`` is still served, with no invented success.

    Falsified if the reader dropped legacy rows (existing consumers would lose every
    pre-E01 backtest) or if it reported a ``last_success`` for evidence that has no run
    identity.
    """
    legacy = tmp_path / "momentum__zero"
    write_backtest_report(stub_result(), legacy, strategy="momentum", cost_regime="zero")
    legacy_payload = json.loads((legacy / METRICS_ARTIFACT).read_text(encoding="utf-8"))

    assert legacy_slugs(tmp_path) == ["momentum__zero"]
    rows = backtest_rows(tmp_path)
    assert [row["slug"] for row in rows] == ["momentum__zero"]
    row = rows[0]
    assert set(row) == set(legacy_payload) | {"slug", "last_success", "last_attempt"}
    assert row["slug"] == "momentum__zero"
    assert row["last_success"] is None  # no run identity exists for frozen legacy evidence
    assert row["last_attempt"] is None
    # Every legacy metric key survives verbatim.
    for key, value in legacy_payload.items():
        assert row[key] == value

    # An unreadable legacy file is skipped as before (never a false success).
    (legacy / METRICS_ARTIFACT).write_text("{not json", encoding="utf-8")
    assert backtest_rows(tmp_path) == []


def test_latest_success_row_shape_is_the_legacy_payload_plus_slug(tmp_path: Path) -> None:
    """A validated run's row keeps the legacy key set (same dated writer) and adds ``slug``.

    Falsified if the projection renamed or dropped a key: the ``/backtests`` payload is
    a pinned public shape, so the value source may change but the shape may not.
    """
    legacy = tmp_path / "trend_following__realistic"
    write_backtest_report(stub_result(), legacy, strategy="trend_following", cost_regime="realistic")
    legacy_payload = json.loads((legacy / METRICS_ARTIFACT).read_text(encoding="utf-8"))

    run = finalise_pair(tmp_path)
    dated_payload = json.loads((run.directory / METRICS_ARTIFACT).read_text(encoding="utf-8"))
    assert set(dated_payload) == set(legacy_payload)  # the same dated writer produces both

    rows = backtest_rows(tmp_path)
    assert len(rows) == 1  # one row per slug, the validated success winning over the frozen file
    row = rows[0]
    assert set(row) == set(legacy_payload) | {"slug", "last_success", "last_attempt"}
    assert row["slug"] == "trend_following__realistic"
    for key, value in dated_payload.items():
        assert row[key] == value


def test_last_attempt_is_attached_only_to_the_slugs_it_requested(tmp_path: Path) -> None:
    """``last_attempt`` appears only on rows whose slug the newest attempt requested.

    Falsified if an attempt leaked onto an unrelated slug: a failure in one pair would
    be reported against a pair that was never attempted.
    """
    write_backtest_report(stub_result(), tmp_path / "momentum__zero", strategy="momentum", cost_regime="zero")
    write_backtest_report(stub_result(), tmp_path / "trend_following__realistic", strategy="trend_following", cost_regime="realistic")
    attempt = write_attempt_record(
        tmp_path,
        requested=[("trend_following", "realistic")],
        outcomes=[
            PairOutcome(
                strategy="trend_following",
                cost_regime="realistic",
                outcome="missing",
            )
        ],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        status="zero_completion",
        notes=["the pair neither completed nor failed"],
    )

    rows = {row["slug"]: row for row in backtest_rows(tmp_path)}
    assert set(rows) == {"momentum__zero", "trend_following__realistic"}
    assert rows["trend_following__realistic"]["last_attempt"]["attempt_id"] == attempt.record.attempt_id
    assert rows["trend_following__realistic"]["last_attempt"]["status"] == "zero_completion"
    assert rows["momentum__zero"]["last_attempt"] is None


def test_findings_prefer_the_newest_attempt_card_and_leave_the_legacy_file_untouched(tmp_path: Path) -> None:
    """A card id resolves to the newest attempt-bound card; the legacy file stays as-is.

    Falsified if the legacy flat card won (a reader would show evidence the attempt
    superseded) or if reading rewrote the legacy file (frozen evidence must stay
    byte-identical).
    """
    legacy_path = write_finding_card(_card("legacy conclusion"), base=tmp_path)
    legacy_bytes = legacy_path.read_bytes()

    first = write_attempt_record(
        tmp_path,
        requested=[("trend_following", "realistic")],
        outcomes=[PairOutcome(strategy="trend_following", cost_regime="realistic", outcome="missing")],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        cards=[_card("older attempt conclusion")],
        status="zero_completion",
        notes=["nothing ran"],
    )
    newer = write_attempt_record(
        tmp_path,
        requested=[("trend_following", "realistic")],
        outcomes=[PairOutcome(strategy="trend_following", cost_regime="realistic", outcome="missing")],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        cards=[_card("newest attempt conclusion")],
        status="zero_completion",
        notes=["nothing ran"],
    )

    cards = {card.id: card for card in finding_cards(tmp_path)}
    assert cards["strategy-comparison"].conclusion == "newest attempt conclusion"
    assert [attempt.directory.name for attempt in attempt_history(tmp_path)][0] == newer.directory.name
    assert first.record.attempt_id == newer.record.attempt_id  # same inputs, distinct directories

    assert legacy_path.read_bytes() == legacy_bytes  # frozen legacy card, never rewritten
    assert json.loads(legacy_bytes)["conclusion"] == "legacy conclusion"


def test_unknown_provenance_stays_unknown_on_the_reader_surface(tmp_path: Path) -> None:
    """Provenance is reported as recorded: nothing upgrades a run to ``real``.

    Falsified if a reader inferred ``real`` from anything (a path, a lake presence, a
    successful validation): the E01c provenance upgrade is not implemented here, and a
    fabricated label would be exactly the failure ADR-0025 Decision 3 forbids.
    """
    run = finalise_pair(tmp_path)
    assert run.manifest.data_provenance == "unknown"

    latest = latest_success_by_slug(tmp_path)["trend_following__realistic"]
    assert latest.manifest.data_provenance == "unknown"
    assert latest.manifest.input_snapshots == []
    row = backtest_rows(tmp_path)[0]
    assert "data_provenance" not in row  # the row shape is the metrics payload, not a claim
    assert row["last_success"]["run_id"] == run.manifest.run_id


def test_an_arena_run_never_rewrites_legacy_metrics_or_the_benchmark(tmp_path: Path) -> None:
    """Legacy ``<slug>/metrics.json`` and the flat benchmark stay byte-identical.

    Falsified if the new write path refreshed a legacy artifact: a card or reader could
    then silently inherit a later run's curve, the exact defect E01 exists to remove.
    """
    legacy = tmp_path / "trend_following__realistic"
    write_backtest_report(stub_result(), legacy, strategy="trend_following", cost_regime="realistic")
    benchmark = tmp_path / "benchmark__equity.parquet"
    benchmark.write_bytes(b"legacy benchmark bytes")
    legacy_before = {path.name: path.read_bytes() for path in sorted(legacy.iterdir())}
    benchmark_before = benchmark.read_bytes()

    outcome = stub_outcome(pairs=[("trend_following", "realistic")])
    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 11, 6))

    assert summary.attempt is not None
    assert summary.finalized  # the invocation did publish its own evidence
    assert {path.name: path.read_bytes() for path in sorted(legacy.iterdir())} == legacy_before
    assert benchmark.read_bytes() == benchmark_before
    # The invocation's own benchmark copy lives in its attempt directory instead.
    assert (summary.attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).is_file()
    assert hashlib.sha256(benchmark.read_bytes()).hexdigest() == hashlib.sha256(benchmark_before).hexdigest()


def test_attempt_history_and_latest_attempt_order_newest_first(tmp_path: Path) -> None:
    """Attempt history is newest-first, and only validated attempts appear.

    Falsified if an invalid attempt (no ``attempt.json``) appeared in history or if the
    ordering were oldest-first: "latest attempt" would then be the oldest one.
    """
    first = write_attempt_record(
        tmp_path,
        requested=[("trend_following", "realistic")],
        outcomes=[PairOutcome(strategy="trend_following", cost_regime="realistic", outcome="missing")],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        status="zero_completion",
        notes=["nothing ran"],
        started_at=datetime(2026, 1, 2, 8, 0, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 1, 2, 8, 0, 1, tzinfo=UTC),
    )
    second = write_attempt_record(
        tmp_path,
        requested=[("trend_following", "realistic")],
        outcomes=[PairOutcome(strategy="trend_following", cost_regime="realistic", outcome="missing")],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        universe=["AAPL"],
        initial_cash=100_000.0,
        markets=["us_equity"],
        status="zero_completion",
        notes=["nothing ran"],
        started_at=datetime(2026, 1, 3, 8, 0, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 1, 3, 8, 0, 1, tzinfo=UTC),
    )
    junk = attempts_root_for(tmp_path) / "20270101T000000Z-ffffffffffff"
    junk.mkdir()
    (junk / ATTEMPT_MANIFEST_NAME).write_text("{not json", encoding="utf-8")

    history = attempt_history(tmp_path)
    assert [attempt.directory.name for attempt in history] == [second.directory.name, first.directory.name]
    assert validated_attempts(tmp_path) == history
    assert latest_attempt(tmp_path) is not None
    latest = latest_attempt(tmp_path)
    assert latest is not None
    assert latest.directory == second.directory


def test_slug_for_manifest_only_names_single_pair_scopes() -> None:
    """A multi-pair scope has no legacy slug: pairing several identities would invent one.

    Falsified if a multi-strategy/multi-regime manifest produced a slug: two
    different runs would be addressable under one legacy directory name.
    """
    from equity_lake.backtesting.evidence import CostApplication, RunManifest, RunScope  # noqa: PLC0415 - local import

    def _manifest(*, strategies: list[str], cost_regimes: list[str]) -> RunManifest:
        scope = RunScope(
            universe=["AAPL"],
            start_date=IRREGULAR_DATES[0],
            end_date=IRREGULAR_DATES[-1],
            strategies=strategies,
            costs=[CostApplication(regime=regime, fee_ratio=0.001425, tax_ratio=0.0) for regime in cost_regimes],
            initial_cash=100_000.0,
        )
        return RunManifest(
            run_id=scope.run_id(),
            run_dir="20261106T101500Z-abcdef012345",
            created_at=_LATER,
            finalized_at=_LATER,
            protocol=scope.protocol,
            start_date=scope.start_date,
            end_date=scope.end_date,
            universe=list(scope.universe),
            # These fixtures are ticker-only universes (E01c: parsed, never guessed).
            universe_basis="ticker_only",
            strategies=list(scope.strategies),
            cost_regimes=scope.cost_regimes,
            initial_cash=scope.initial_cash,
            costs=list(scope.costs),
        )

    assert slug_for_manifest(_manifest(strategies=["momentum"], cost_regimes=["zero"])) == "momentum__zero"
    assert slug_for_manifest(_manifest(strategies=["momentum", "mean_reversion"], cost_regimes=["zero"])) is None
    assert slug_for_manifest(_manifest(strategies=["momentum"], cost_regimes=["zero", "high"])) is None
