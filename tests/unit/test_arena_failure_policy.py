"""F04 regressions: arena completion/failure policy, exit codes, card admission.

Covers spec §3.4 / audit A04+A10:

- requested/completed/failed pairs are recorded on the in-memory outcome,
- zero completed requested runs exit 1 and never call the artifact writer,
- a partial invocation keeps its successful artifacts, is labelled PARTIAL and
  exits 1, while a full matrix exits 0,
- completed computation and evaluable evidence are distinct
  (``evaluated`` / ``no_trades`` / ``ineligible``), and only ``evaluated`` runs
  feed the strategy-comparison / cost-regime / vs-benchmark cards,
- a missing or non-finite ``sharpe_ratio`` never becomes a measured zero.

Old behaviour pinned here (``arena.py`` / ``cli/commands/arena.py`` before F04):
``run_arena`` skipped failed runs without recording them, and the CLI printed
``Arena complete`` and exited 0 with zero completed runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import polars as pl
import pytest
from typer.testing import CliRunner

import equity_lake.backtesting.arena as arena_module
from equity_lake.backtesting.arena import (
    STRATEGY_REGISTRY,
    ArenaFailure,
    ArenaOutcome,
    ArenaRun,
    equal_weight_buyhold,
    run_arena,
)
from equity_lake.backtesting.attempts import validate_attempt_directory
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE
from equity_lake.backtesting.report import ArenaEvidenceSummary, build_finding_cards
from equity_lake.backtesting.result import BacktestResult
from equity_lake.cli.__main__ import app
from equity_lake.devtools.seed_demo import seed_demo
from equity_lake.devtools.seeding import trailing_business_days

runner = CliRunner()

_D1, _D2, _D3, _D4 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)
requires_engine = pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")


# ---------------------------------------------------------------------------
# Fixtures / stubs (no engine, no network)
# ---------------------------------------------------------------------------


def _ticker_frame(tickers: tuple[str, ...] = ("AAA", "BBB")) -> pl.DataFrame:
    """Ticker-only price fixture (unique ticker/date rows)."""
    days = (_D1, _D2, _D3, _D4)
    rows = [
        {"ticker": ticker, "date": day, "close": 100.0 + 3.0 * index + offset}
        for offset, ticker in enumerate(tickers)
        for index, day in enumerate(days)
    ]
    return pl.DataFrame(rows, schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64})


def _benchmark_frame() -> pl.DataFrame:
    """A completed ``[date, equity]`` benchmark curve with a finite Sharpe."""
    return pl.DataFrame(
        {"date": [_D1, _D2, _D3, _D4], "equity": [100.0, 110.0, 100.0, 110.0]},
        schema={"date": pl.Date, "equity": pl.Float64},
    )


def _stub_result(*, sharpe: float | None = 0.5, trades: int = 2, metrics: dict[str, float] | None = None) -> BacktestResult:
    """A dated stub result; ``sharpe=None`` omits ``sharpe_ratio``, ``metrics`` wins outright.

    E01b.1 writes immutable per-run evidence only for dated runs, so a dated stub is
    what lets the CLI tests assert the *new* evidence location (``runs/`` +
    ``attempts/``) instead of the frozen legacy directories.
    """
    resolved: dict[str, float] = {"total_return": 0.01} if metrics is None else dict(metrics)
    if metrics is None and sharpe is not None:
        resolved["sharpe_ratio"] = sharpe
    return BacktestResult(
        strategy_name="stub",
        tickers=["AAA", "BBB"],
        start_date=_D1,
        end_date=_D4,
        initial_cash=100.0,
        final_cash=100.0,
        equity_curve=pl.Series("equity", [100.0, 110.0, 100.0, 110.0]),
        trades=[{"ticker": "AAA", "date": str(_D2)}] * trades,
        metrics=resolved,
        equity_dates=[_D1, _D2, _D3, _D4],
    )


def _outcome(
    *,
    runs: list[ArenaRun] | None = None,
    requested: list[tuple[str, str]] | None = None,
    failed: list[ArenaFailure] | None = None,
) -> ArenaOutcome:
    """An in-memory outcome whose completed runs carry what ``run_arena`` records.

    E01b.1's writer records the applied costs and the strategy parameters the run was
    built with; a stub without them is a *legacy* run and is skipped by design, so the
    fixture fills them exactly as ``run_arena`` does.
    """
    frame = _ticker_frame()
    prepared = runs if runs is not None else []
    for run in prepared:
        if run.applied_costs is None:
            run.applied_costs = {"fee_ratio": 0.001425, "tax_ratio": 0.0}
        if not run.strategy_params:
            run.strategy_params = {"lookback": 20}
    return ArenaOutcome(
        runs=prepared,
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100.0),
        initial_cash=100.0,
        requested=requested if requested is not None else [],
        failed=failed if failed is not None else [],
    )


def _attempt_dir(root: Path) -> Path:
    """The one published attempt directory under ``root`` (temporaries excluded)."""
    attempts_root = root / "attempts"
    assert attempts_root.is_dir(), sorted(path.name for path in root.iterdir())
    attempts = [path for path in attempts_root.iterdir() if path.is_dir() and not path.name.startswith(".tmp-")]
    assert len(attempts) == 1, [path.name for path in attempts]
    return attempts[0]


def _run_dirs(root: Path) -> list[Path]:
    """Published immutable run directories under ``root/runs``."""
    runs_root = root / "runs"
    if not runs_root.is_dir():
        return []
    return sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith(".partial-"))


def _assert_no_legacy_artifacts(root: Path, slugs: tuple[str, ...] = ()) -> None:
    """E01b.1: no pair-keyed directory, no flat benchmark and no flat card is written."""
    for slug in slugs:
        assert not (root / slug).exists(), slug
    assert not (root / "benchmark__equity.parquet").exists()
    assert sorted(root.glob("*.json")) == []


def _assert_zero_completion_evidence(root: Path, *, requested: int, failed: int) -> None:
    """A zero-completion invocation persists its classification and nothing else.

    The attempt record is the only artifact: no run directory, no card and no
    benchmark, because nothing of this invocation's request was measured.
    """
    assert sorted(path.name for path in root.iterdir()) == ["attempts"]
    attempt = _attempt_dir(root)
    assert sorted(path.name for path in attempt.iterdir()) == ["attempt.json"]
    verdict = validate_attempt_directory(attempt)
    assert verdict.finalized, verdict.reasons
    assert verdict.record is not None
    assert verdict.record.status == "zero_completion"
    assert len(verdict.record.outcomes) == requested
    assert sum(1 for outcome in verdict.record.outcomes if outcome.outcome == "failed") == failed
    _assert_no_legacy_artifacts(root)


def _strategy_class_name(registry_name: str) -> str:
    return str(STRATEGY_REGISTRY[registry_name].__name__)


def _engine_factory(
    *,
    failures: Mapping[str, BaseException] | None = None,
    trades_by_strategy: Mapping[str, int] | None = None,
    sharpe_by_strategy: Mapping[str, float] | None = None,
) -> Any:
    """A ``VectorBacktestEngine`` stand-in keyed by strategy class name.

    Returns a callable suitable for ``patch(..., side_effect=...)``: it raises the
    scripted exception for the named strategy and otherwise builds a stub result.
    """
    failures = failures or {}
    trades_by_strategy = trades_by_strategy or {}
    sharpe_by_strategy = sharpe_by_strategy or {}

    def _build(**kwargs: Any) -> Any:
        strategy = kwargs["strategy"]
        name = strategy.__class__.__name__
        if name in failures:
            raise failures[name]
        result = _stub_result(
            sharpe=sharpe_by_strategy.get(name, 0.5),
            trades=trades_by_strategy.get(name, 2),
        )
        return type("_StubEngine", (), {"run": lambda self: result})()

    return _build


def _waitless_cli_args(*extra: str) -> list[str]:
    return ["arena", "run", "--start-date", _D1.isoformat(), "--end-date", _D4.isoformat(), *extra]


def _raising_strategy_class(reason: str) -> type:
    """Registry-compatible strategy class whose ``__init__`` raises ``RuntimeError``."""
    base = STRATEGY_REGISTRY["momentum"]

    def _boom(self: Any) -> None:
        raise RuntimeError(reason)

    return type("ExplodingMomentumStrategy", (base,), {"__init__": _boom})


def _strategy_with_min_stocks(value: object) -> type:
    """Momentum subclass declaring ``min_stocks`` as an arbitrary (possibly non-int) value."""
    base = STRATEGY_REGISTRY["momentum"]

    def _init(self: Any) -> None:
        base.__init__(self, {"min_stocks": value})

    return type(f"MinStocks_{type(value).__name__}", (base,), {"__init__": _init})


# ---------------------------------------------------------------------------
# Sanitizer: paths and credentials never reach logs, cards or CLI output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected", "leaked"),
    [
        # Authorization headers: the header name survives, the scheme and credential do not.
        ("Authorization: Bearer abc", "authorization=<redacted>", ("Bearer", "abc")),
        ("authorization=Basic dXNlcjpwYXNz", "authorization=<redacted>", ("Basic", "dXNlcjpwYXNz")),
        ("authorization=Bearer abc", "authorization=<redacted>", ("Bearer", "abc")),
        # Fix round 5, reviewer reproduction 1: a scheme word with *no* header survived
        # unchanged, because the auth rule required the ``Authorization`` header name.
        ("Bearer abc", "<redacted>", ("Bearer", "abc")),
        ("bearer=abc", "<redacted>", ("bearer", "=abc")),
        ("Basic xyz", "<redacted>", ("Basic", "xyz")),
        # Other credential keys keep the key name and lose the value.
        ("api_key=xyz", "api_key=<redacted>", ("xyz",)),
        ("token: xyz", "token=<redacted>", ("xyz",)),
        ("secret=abc", "secret=<redacted>", ("=abc",)),
        ("password: abc", "password=<redacted>", (": abc",)),
        # Paths: absolute, split by a space, and a backslash-only relative Windows path
        # (fix round 5, reviewer reproduction 2: it survived because it held no ``/``).
        ("/tmp/private folder/secret", "<path>", ("/tmp", "private", "folder/secret")),
        (r"relative\private\secret.txt", "<path>", ("relative", "private", "secret.txt")),
        (r"C:\Users\x\secret", "<path>", ("C:", "Users", "secret")),
        ("~/private/file", "<path>", ("~", "private/file", "file")),
        ("/home/user/x/y.py", "<path>", ("/home", "y.py")),
        # A URL credential must not survive as a host or path fragment.
        ("https://user:pass@host/x", "<path>", ("user:pass", "host", "https:")),
        # A readable diagnostic is not over-redacted.
        ("engine raised: insufficient cash for the order", "engine raised: insufficient cash for the order", ()),
    ],
)
def test_sanitize_failure_reason_redacts_credentials_and_paths(message: str, expected: str, leaked: tuple[str, ...]) -> None:
    """Full sanitizer matrix: no token, no path tail, no credential, no over-redaction."""
    reason = arena_module._sanitize_failure_reason(RuntimeError(message))
    assert reason == expected, (message, reason)
    assert len(reason) <= 200
    assert reason.strip(), "a sanitized reason is never empty"
    for fragment in leaked:
        assert fragment not in reason, (fragment, reason)


def test_sanitize_failure_reason_keeps_error_type_intact() -> None:
    """Only the reason is rewritten; the recorded ``error_type`` stays the real class name."""
    frame = _ticker_frame()
    with patch.object(
        arena_module,
        "VectorBacktestEngine",
        side_effect=_engine_factory(failures={"CrossSectionalMomentumStrategy": RuntimeError("Bearer abc")}),
    ):
        outcome = run_arena(["AAA", "BBB"], _D1, _D4, preloaded_data=frame, strategies=["momentum"], cost_regimes=["zero"])

    assert [(failure.error_type, failure.reason) for failure in outcome.failed] == [("RuntimeError", "<redacted>")]


def test_zero_completion_names_missing_requested_pairs(tmp_path: Path) -> None:
    """Fix round 5: the zero-completion branch names every pair with neither outcome.

    A requested pair that neither completed nor failed is *missing evidence*; the
    guide promises such pairs are reported by name, but the zero-completion branch
    used to print only counts and recorded failures. Both missing pairs must be
    named (``<strategy>__<regime>``), the writer must stay untouched, and the
    existing "no run evidence or cards were written" line must remain, and the
    invocation must still publish its ``zero_completion`` attempt record.
    """
    outcome = _outcome(
        runs=[],
        requested=[("momentum", "zero"), ("mean_reversion", "realistic")],
        failed=[],
    )

    def _writer_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("write_arena_artifacts must not be called when no run completed")

    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=_writer_must_not_be_called),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "no run evidence or cards were written" in result.stdout
    assert "Attempt record:" in result.stdout
    assert "requested: 2 pair(s) | completed: 0 | failed: 0" in result.stdout
    # Every missing requested pair is named, not merely counted.
    assert "2 requested pair(s) neither completed nor recorded a failure" in result.stdout
    assert "momentum__zero" in result.stdout
    assert "mean_reversion__realistic" in result.stdout
    assert "missing evidence rather than a recorded failure" in result.stdout
    # Still no success banner, no cards, and nothing written.
    assert "Arena complete" not in result.stdout
    assert "Arena PARTIAL" not in result.stdout
    assert "FindingCards:" not in result.stdout
    assert "no run evidence or cards were written" in result.stdout
    _assert_zero_completion_evidence(tmp_path, requested=2, failed=0)


def test_sanitize_failure_reason_truncates_long_messages_to_200_chars() -> None:
    reason = arena_module._sanitize_failure_reason(RuntimeError("x" * 400))
    assert len(reason) <= 200
    assert reason.endswith("...")
    assert reason.startswith("x")


def test_sanitize_failure_reason_truncation_never_cuts_a_marker_in_half() -> None:
    """The cap must not emit a half marker such as ``<pat`` or ``<redac``."""
    # 195 readable chars followed by a token that sanitizes to exactly "<path>".
    reason = arena_module._sanitize_failure_reason(RuntimeError(("a " * 97) + "b " + "/tmp/private/secret"))
    assert len(reason) <= 200
    assert reason.endswith("...")
    assert "<path" not in reason.replace("<path>", "")
    assert "<redacted" not in reason.replace("<redacted>", "")


# ---------------------------------------------------------------------------
# Exit-code matrix: all-failed / partial / full
# ---------------------------------------------------------------------------


def test_all_failed_outcome_exits_one_and_never_calls_writer(tmp_path: Path) -> None:
    """Zero completed requested runs: exit 1, no cards, no banner, writer untouched."""
    outcome = _outcome(
        requested=[("momentum", "zero"), ("momentum", "realistic")],
        failed=[
            ArenaFailure(strategy="momentum", cost_regime="zero", error_type="RuntimeError", reason="boom"),
            ArenaFailure(strategy="momentum", cost_regime="realistic", error_type="RuntimeError", reason="boom"),
        ],
    )

    def _writer_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("write_arena_artifacts must not be called when no run completed")

    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=_writer_must_not_be_called),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "no completed runs" in result.stdout
    assert "requested: 2 pair(s) | completed: 0 | failed: 2" in result.stdout
    assert "momentum__zero: RuntimeError: boom" in result.stdout
    # No success banner and no success cards on a zero-completion invocation.
    assert "Arena complete" not in result.stdout
    assert "Arena PARTIAL" not in result.stdout
    assert "FindingCards:" not in result.stdout
    # No card/run evidence was written; only the classification record is durable.
    _assert_zero_completion_evidence(tmp_path, requested=2, failed=2)


def test_unrequested_completed_run_is_not_published(tmp_path: Path) -> None:
    """F04 fix round 4: a completed run outside the request is not this invocation's evidence.

    Reproduction from the review: ``requested=[("momentum", "zero")]`` with only an
    *unrequested* run ``("trend_following", "high")`` present used to call the
    artifact writer and banner ``0 of 1``, so evidence from a pair outside the
    request could be published. The zero-completion gate is now measured against
    completed *requested* pairs.
    """
    outcome = _outcome(
        runs=[ArenaRun("trend_following", "high", _stub_result())],
        requested=[("momentum", "zero")],
        failed=[],
    )

    def _writer_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("write_arena_artifacts must not be called when no requested run completed")

    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=_writer_must_not_be_called),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "no completed runs" in result.stdout
    assert "requested: 1 pair(s) | completed: 0 | failed: 0" in result.stdout
    # The unrequested completed pair is named and explicitly not published.
    assert "trend_following__high" in result.stdout
    assert "no requested pair completed" in result.stdout
    assert "not published because the invocation's requested set produced no completed evidence" in result.stdout
    # No success banner, no success cards, no write.
    assert "Arena complete" not in result.stdout
    assert "Arena PARTIAL" not in result.stdout
    assert "FindingCards:" not in result.stdout
    _assert_zero_completion_evidence(tmp_path, requested=1, failed=0)


def test_unrequested_pairs_are_never_counted_or_published(tmp_path: Path) -> None:
    """Only the requested set may be published or counted as success.

    An outcome that also carries a completed run and a recorded failure *outside*
    the request must satisfy the invocation (the request completed) while neither
    counting nor writing those pairs; they are named as out of scope instead.
    """
    requested_pair = ("mean_reversion", "zero")
    outcome = _outcome(
        runs=[ArenaRun(requested_pair[0], requested_pair[1], _stub_result()), ArenaRun("trend_following", "high", _stub_result())],
        requested=[requested_pair],
        failed=[ArenaFailure(strategy="momentum", cost_regime="high", error_type="RuntimeError", reason="boom")],
    )
    seen: list[ArenaOutcome] = []

    def _writer_spy(written: ArenaOutcome, **kwargs: Any) -> tuple[list[Any], ArenaEvidenceSummary]:
        seen.append(written)
        return [], ArenaEvidenceSummary()

    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=_writer_spy),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "Arena complete: 1 of 1 requested pair(s) completed" in result.stdout
    # Counts never include the unrequested pairs (the recorded failure included).
    assert "requested: 1 | completed: 1 | failed: 0" in result.stdout
    assert "trend_following__high (completed)" in result.stdout
    assert "momentum__high (failed)" in result.stdout
    assert "neither counted nor written" in result.stdout
    assert "PARTIAL" not in result.stdout
    # The writer received the requested pair only (and no unrequested failure).
    assert len(seen) == 1
    assert [(run.strategy, run.cost_regime) for run in seen[0].runs] == [requested_pair]
    assert seen[0].failed == []
    # The spy wrote nothing, so the findings dir stayed empty.
    assert list(tmp_path.iterdir()) == []


def test_write_failure_stays_visible_and_nonzero(tmp_path: Path) -> None:
    """An output-write failure is reported and exits 1 (no catch-all success fallback)."""
    outcome = _outcome(
        runs=[ArenaRun("mean_reversion", "zero", _stub_result())],
        requested=[("mean_reversion", "zero")],
    )
    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=OSError("disk full")),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "artifact write failed" in result.stdout
    assert "disk full" in result.stdout
    assert "Arena complete" not in result.stdout


def test_one_success_one_failure_is_partial_and_exits_one(tmp_path: Path) -> None:
    """Both pairs are attempted; the partial invocation keeps the successful artifacts."""
    outcome = _outcome(
        runs=[ArenaRun("mean_reversion", "realistic", _stub_result(sharpe=0.25))],
        requested=[("momentum", "realistic"), ("mean_reversion", "realistic")],
        failed=[ArenaFailure(strategy="momentum", cost_regime="realistic", error_type="ValueError", reason="no decisions")],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "requested: 2 | completed: 1 | failed: 1" in result.stdout
    assert "momentum__realistic: ValueError: no decisions" in result.stdout
    # The successful run's evidence is published as its own immutable run directory,
    # and the comparison artifacts are owned by this invocation's attempt directory.
    assert [run.strategy for run in outcome.runs] == ["mean_reversion"]
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    assert (runs[0] / "trades.json").exists()
    assert (runs[0] / "manifest.json").exists()
    attempt = _attempt_dir(tmp_path)
    assert (attempt / "benchmark__equity.parquet").exists()
    assert (attempt / "attempt.json").exists()
    # The frozen legacy layout is never recreated for the failed pair either.
    _assert_no_legacy_artifacts(tmp_path, ("mean_reversion__realistic", "momentum__realistic"))
    # Discloses that legacy directories are frozen and never written.
    assert "frozen and read-only" in result.stdout


def test_full_matrix_exits_zero_without_partial_label(tmp_path: Path) -> None:
    """All requested pairs completed: exit 0, no PARTIAL label, artifacts written."""
    outcome = _outcome(
        runs=[
            ArenaRun("mean_reversion", "zero", _stub_result()),
            ArenaRun("mean_reversion", "realistic", _stub_result(sharpe=0.4)),
        ],
        requested=[("mean_reversion", "zero"), ("mean_reversion", "realistic")],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "PARTIAL" not in result.stdout
    assert "Arena complete: 2 of 2 requested pair(s) completed" in result.stdout
    assert "requested: 2 | completed: 2 | failed: 0 | non-evaluable: 0" in result.stdout
    assert "neither completed nor recorded a failure" not in result.stdout
    # Temporary-output artifact test for the arena command: one immutable run per pair.
    runs = _run_dirs(tmp_path)
    assert len(runs) == 2
    for run_dir in runs:
        for name in ("equity.parquet", "drawdown.parquet", "metrics.json", "trades.json", "manifest.json"):
            assert (run_dir / name).exists(), (run_dir.name, name)
        # The redesigned layout stores real sessions, never the positional legacy column.
        assert pl.read_parquet(run_dir / "equity.parquet").columns == ["date", "equity"]
    attempt = _attempt_dir(tmp_path)
    assert (attempt / "benchmark__equity.parquet").exists()
    assert (attempt / "cost-regime.json").exists()
    assert (attempt / "strategy-comparison.json").exists()
    assert (attempt / "vs-benchmark.json").exists()
    _assert_no_legacy_artifacts(tmp_path, ("mean_reversion__zero", "mean_reversion__realistic"))


def test_fewer_completed_than_requested_without_failures_is_partial_and_exits_one(tmp_path: Path) -> None:
    """F04 fix round 2: success needs every requested pair, not an empty ``failed`` list.

    Reproduction from the review: one completed run, two requested pairs and no
    recorded failure used to print ``Arena complete: 1 of 2`` and exit 0.
    """
    outcome = _outcome(
        runs=[ArenaRun("momentum", "zero", _stub_result())],
        requested=[("momentum", "zero"), ("momentum", "realistic")],
        failed=[],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "Arena PARTIAL: 1 of 2 requested pair(s) completed" in result.stdout
    assert "requested: 2 | completed: 1 | failed: 0" in result.stdout
    # Missing evidence is reported as missing, never as a recorded error.
    assert "1 requested pair(s) neither completed nor recorded a failure" in result.stdout
    assert "momentum__realistic" in result.stdout
    assert "Arena complete" not in result.stdout
    assert "failed pairs (sanitized):" not in result.stdout
    # Writer behaviour: completed > 0, so the successful run's evidence was written.
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    assert (runs[0] / "metrics.json").exists()
    assert (runs[0] / "manifest.json").exists()
    assert (_attempt_dir(tmp_path) / "benchmark__equity.parquet").exists()
    _assert_no_legacy_artifacts(tmp_path, ("momentum__zero", "momentum__realistic"))


def test_duplicate_requested_pairs_are_deduplicated(tmp_path: Path) -> None:
    """F04 fix round 3: ``--strategies momentum,momentum`` is one attempt, not two.

    Reproduction from the review: ``requested=[p, p]`` with one completed run used
    to print ``1 of 2`` and still exit 0. ``run_arena`` now collapses duplicate
    strategy/regime selections (first-occurrence order), so the request itself is
    unique and the real outcome prints ``1 of 1``.
    """
    frame = _ticker_frame()
    calls: list[str] = []
    factory = _engine_factory()

    def _counting_engine(**kwargs: Any) -> Any:
        calls.append(kwargs["strategy"].__class__.__name__)
        return factory(**kwargs)

    with patch.object(arena_module, "VectorBacktestEngine", side_effect=_counting_engine):
        outcome = run_arena(
            ["AAA", "BBB"],
            _D1,
            _D4,
            preloaded_data=frame,
            strategies=["mean_reversion", "mean_reversion"],
            cost_regimes=["zero"],
        )

    pair = ("mean_reversion", "zero")
    assert outcome.requested == [pair], outcome.requested
    assert [(run.strategy, run.cost_regime) for run in outcome.runs] == [pair]
    # A duplicate selection runs the pair once, not twice.
    assert calls == ["BBMeanReversionStrategy"], calls

    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "Arena complete: 1 of 1 requested pair(s) completed" in result.stdout
    assert "requested: 1 | completed: 1 | failed: 0" in result.stdout
    assert "1 of 2" not in result.stdout
    assert "PARTIAL" not in result.stdout


def test_duplicate_requested_pairs_in_mocked_outcome_do_not_mislead(tmp_path: Path) -> None:
    """A legacy/mocked outcome carrying ``requested=[p, p]`` must not print ``1 of 2``.

    The CLI predicate deduplicates the request (first-occurrence order) before any
    count is computed or printed, so one completed run over a duplicated request
    reports ``1 of 1`` and exits 0.
    """
    pair = ("mean_reversion", "zero")
    outcome = _outcome(
        runs=[ArenaRun(pair[0], pair[1], _stub_result())],
        requested=[pair, pair],
        failed=[],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "Arena complete: 1 of 1 requested pair(s) completed" in result.stdout
    assert "requested: 1 | completed: 1 | failed: 0" in result.stdout
    assert "1 of 2" not in result.stdout
    assert "PARTIAL" not in result.stdout
    assert "neither completed nor recorded a failure" not in result.stdout
    # The single completed run's evidence is still published exactly once.
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    _assert_no_legacy_artifacts(tmp_path, ("mean_reversion__zero",))


def test_pair_recorded_both_completed_and_failed_is_diagnosed(tmp_path: Path) -> None:
    """A contradictory outcome (same pair completed *and* failed) exits 1 and says so.

    Silently preferring one side would let a self-contradicting outcome look
    complete; the PARTIAL diagnostics must name the contradiction.
    """
    pair = ("mean_reversion", "zero")
    outcome = _outcome(
        runs=[ArenaRun(pair[0], pair[1], _stub_result())],
        requested=[pair],
        failed=[ArenaFailure(strategy=pair[0], cost_regime=pair[1], error_type="RuntimeError", reason="boom")],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "Arena PARTIAL: 1 of 1 requested pair(s) completed" in result.stdout
    assert "requested: 1 | completed: 1 | failed: 1" in result.stdout
    assert "1 requested pair(s) are recorded both completed and failed" in result.stdout
    assert "mean_reversion__zero" in result.stdout
    # Neither side is silently dropped: the recorded failure is still listed.
    assert "mean_reversion__zero: RuntimeError: boom" in result.stdout
    assert "Arena complete" not in result.stdout
    # Not a missing pair: the pair has evidence, the problem is the contradiction.
    assert "neither completed nor recorded a failure" not in result.stdout


def test_completed_equals_requested_without_failures_exits_zero(tmp_path: Path) -> None:
    """One requested pair that completed is a complete invocation (exit 0, no PARTIAL)."""
    outcome = _outcome(
        runs=[ArenaRun("mean_reversion", "zero", _stub_result())],
        requested=[("mean_reversion", "zero")],
        failed=[],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "Arena complete: 1 of 1 requested pair(s) completed" in result.stdout
    assert "requested: 1 | completed: 1 | failed: 0" in result.stdout
    assert "PARTIAL" not in result.stdout
    assert "neither completed nor recorded a failure" not in result.stdout
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    _assert_no_legacy_artifacts(tmp_path, ("mean_reversion__zero",))


def test_zero_completed_without_failures_exits_one_and_never_calls_writer(tmp_path: Path) -> None:
    """No run completed and nothing failed: still exit 1 with the zero-completion guard."""
    outcome = _outcome(
        runs=[],
        requested=[("momentum", "zero"), ("momentum", "realistic")],
        failed=[],
    )

    def _writer_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("write_arena_artifacts must not be called when no run completed")

    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=_writer_must_not_be_called),
    ):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "no completed runs" in result.stdout
    assert "requested: 2 pair(s) | completed: 0 | failed: 0" in result.stdout
    assert "Arena complete" not in result.stdout
    assert "Arena PARTIAL" not in result.stdout
    _assert_zero_completion_evidence(tmp_path, requested=2, failed=0)


def test_legacy_outcome_without_requested_counts_completed_pairs(tmp_path: Path) -> None:
    """Backward compatibility: ``requested=[]`` falls back to the pairs that ran."""
    outcome = _outcome(
        runs=[
            ArenaRun("mean_reversion", "zero", _stub_result()),
            ArenaRun("mean_reversion", "realistic", _stub_result(sharpe=0.4)),
        ],
        requested=[],
        failed=[],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "Arena complete: 2 of 2 requested pair(s) completed" in result.stdout
    assert "requested: 2 | completed: 2 | failed: 0" in result.stdout
    assert "PARTIAL" not in result.stdout
    assert "neither completed nor recorded a failure" not in result.stdout


def test_legacy_outcome_without_requested_includes_failed_pairs(tmp_path: Path) -> None:
    """Backward compatibility: the legacy fallback derives requested from completed + failed."""
    outcome = _outcome(
        runs=[ArenaRun("mean_reversion", "zero", _stub_result())],
        requested=[],
        failed=[ArenaFailure(strategy="momentum", cost_regime="zero", error_type="RuntimeError", reason="boom")],
    )
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "Arena PARTIAL: 1 of 2 requested pair(s) completed" in result.stdout
    assert "requested: 2 | completed: 1 | failed: 1" in result.stdout
    assert "momentum__zero: RuntimeError: boom" in result.stdout
    # No pair is missing here: every derived requested pair is completed or failed.
    assert "neither completed nor recorded a failure" not in result.stdout
    assert len(_run_dirs(tmp_path)) == 1


# ---------------------------------------------------------------------------
# Evaluation status: evaluated / no_trades / ineligible
# ---------------------------------------------------------------------------


def test_evaluation_status_distinguishes_no_trades_failure_and_ineligible() -> None:
    """Three distinct statuses come from a real ``run_arena`` pass (fake engine only)."""
    frame = _ticker_frame(tickers=tuple(f"T{index}" for index in range(5)))
    start, end = _D1, _D4
    failures = {"SMACrossoverStrategy": RuntimeError("boom")}
    with patch.object(
        arena_module,
        "VectorBacktestEngine",
        side_effect=_engine_factory(
            failures=failures,
            trades_by_strategy={"BBMeanReversionStrategy": 0, "CrossSectionalMomentumStrategy": 2},
        ),
    ):
        outcome = run_arena(
            [f"T{index}" for index in range(5)],
            start,
            end,
            preloaded_data=frame,
            strategies=["momentum", "mean_reversion", "trend_following"],
            cost_regimes=["zero"],
        )

    statuses = {(run.strategy, run.cost_regime): run.evaluation_status for run in outcome.runs}
    assert statuses == {
        ("momentum", "zero"): "ineligible",
        ("mean_reversion", "zero"): "no_trades",
    }, statuses
    assert outcome.requested == [("momentum", "zero"), ("mean_reversion", "zero"), ("trend_following", "zero")]
    assert [(failure.strategy, failure.cost_regime, failure.error_type) for failure in outcome.failed] == [
        ("trend_following", "zero", "RuntimeError")
    ]

    momentum = next(run for run in outcome.runs if run.strategy == "momentum")
    assert any("universe 5 < min_stocks 10" in note for note in momentum.evaluation_notes), momentum.evaluation_notes
    mean_reversion = next(run for run in outcome.runs if run.strategy == "mean_reversion")
    assert mean_reversion.evaluation_notes and "no trades" in mean_reversion.evaluation_notes[0]
    # A completed, fully executed run is still ``evaluated`` by default.
    assert all(run.evaluation_status == "evaluated" for run in outcome.runs if run.strategy not in {"momentum", "mean_reversion"})


@pytest.mark.parametrize("min_stocks", [True, False, 2.0, None])
def test_non_integer_min_stocks_never_makes_a_run_ineligible(min_stocks: object) -> None:
    """Only a real ``int`` ``min_stocks`` gates eligibility; bool/float/None never do."""
    strategy_cls = _strategy_with_min_stocks(min_stocks)
    frame = _ticker_frame(tickers=("AAA", "BBB"))  # universe of 2
    with (
        patch.dict(arena_module.STRATEGY_REGISTRY, {"momentum": strategy_cls}),
        patch.object(arena_module, "VectorBacktestEngine", side_effect=_engine_factory()),
    ):
        outcome = run_arena(["AAA", "BBB"], _D1, _D4, preloaded_data=frame, strategies=["momentum"], cost_regimes=["zero"])

    assert outcome.failed == []
    assert len(outcome.runs) == 1
    assert outcome.runs[0].evaluation_status == "evaluated"
    assert outcome.runs[0].evaluation_notes == ()


@pytest.mark.parametrize(("min_stocks", "expected"), [(2, "evaluated"), (3, "ineligible")])
def test_universe_equal_to_min_stocks_is_eligible(min_stocks: int, expected: str) -> None:
    """The boundary is inclusive: a universe exactly at ``min_stocks`` is eligible."""
    strategy_cls = _strategy_with_min_stocks(min_stocks)
    frame = _ticker_frame(tickers=("AAA", "BBB"))  # universe of 2
    with (
        patch.dict(arena_module.STRATEGY_REGISTRY, {"momentum": strategy_cls}),
        patch.object(arena_module, "VectorBacktestEngine", side_effect=_engine_factory()),
    ):
        outcome = run_arena(["AAA", "BBB"], _D1, _D4, preloaded_data=frame, strategies=["momentum"], cost_regimes=["zero"])

    run = outcome.runs[0]
    assert run.evaluation_status == expected
    if expected == "ineligible":
        assert any("universe 2 < min_stocks 3" in note for note in run.evaluation_notes), run.evaluation_notes
    else:
        assert run.evaluation_notes == ()


@pytest.mark.parametrize(
    ("exc", "recorded"),
    [(SystemExit(3), False), (KeyboardInterrupt(), False), (RuntimeError("boom"), True)],
)
def test_per_pair_handler_records_exceptions_but_propagates_base_exceptions(exc: BaseException, recorded: bool) -> None:
    """``except Exception`` must record ordinary errors and never swallow BaseException."""
    frame = _ticker_frame()
    with patch.object(
        arena_module,
        "VectorBacktestEngine",
        side_effect=_engine_factory(failures={"CrossSectionalMomentumStrategy": exc}),
    ):
        if not recorded:
            with pytest.raises(type(exc)):
                run_arena(
                    ["AAA", "BBB"],
                    _D1,
                    _D4,
                    preloaded_data=frame,
                    strategies=["momentum", "mean_reversion"],
                    cost_regimes=["zero"],
                )
            return
        outcome = run_arena(
            ["AAA", "BBB"],
            _D1,
            _D4,
            preloaded_data=frame,
            strategies=["momentum", "mean_reversion"],
            cost_regimes=["zero"],
        )

    assert [run.strategy for run in outcome.runs] == ["mean_reversion"]
    assert [(failure.strategy, failure.error_type) for failure in outcome.failed] == [("momentum", "RuntimeError")]


def test_cli_prints_three_statuses_and_ineligible_does_not_fail(tmp_path: Path) -> None:
    """The five-ticker momentum case reports insufficient universe without failing the CLI."""
    outcome = _outcome(
        runs=[
            ArenaRun("momentum", "zero", _stub_result(), evaluation_status="ineligible", evaluation_notes=("universe 5 < min_stocks 10",)),
            ArenaRun("mean_reversion", "zero", _stub_result(trades=0), evaluation_status="no_trades", evaluation_notes=("no trades on this load",)),
            ArenaRun("trend_following", "zero", _stub_result()),
        ],
        requested=[("momentum", "zero"), ("mean_reversion", "zero"), ("trend_following", "zero")],
    )
    assert outcome.data.height > 0
    with patch("equity_lake.backtesting.arena.run_arena", return_value=outcome):
        result = runner.invoke(app, _waitless_cli_args("--output-dir", str(tmp_path)))

    assert result.exit_code == 0, result.stdout
    assert "momentum__zero: ineligible — universe 5 < min_stocks 10" in result.stdout
    assert "mean_reversion__zero: no_trades" in result.stdout
    assert "trend_following__zero: evaluated" in result.stdout
    assert "non-evaluable: 2" in result.stdout
    assert "not evaluable evidence and were excluded from the cards" in result.stdout
    assert "PARTIAL" not in result.stdout


# ---------------------------------------------------------------------------
# Real ``run_arena`` isolation and real CLI wiring
# ---------------------------------------------------------------------------


def test_run_arena_isolates_a_real_engine_exception() -> None:
    """One strategy raising must not stop the others; the pair is recorded sanitized."""
    frame = _ticker_frame()
    long_reason = "load failed for /home/howt/work/equity_lake/data/lake/03_gold/prices.parquet api_key=sk-live-SECRET " + "x" * 400
    with patch.object(
        arena_module,
        "VectorBacktestEngine",
        side_effect=_engine_factory(failures={"CrossSectionalMomentumStrategy": RuntimeError(long_reason)}),
    ):
        outcome = run_arena(
            ["AAA", "BBB"],
            _D1,
            _D4,
            preloaded_data=frame,
            strategies=["momentum", "mean_reversion"],
            cost_regimes=["zero"],
        )

    # The other run still executed and kept its evidence.
    assert [(run.strategy, run.cost_regime) for run in outcome.runs] == [("mean_reversion", "zero")]
    assert outcome.requested == [("momentum", "zero"), ("mean_reversion", "zero")]
    assert len(outcome.failed) == 1
    failure = outcome.failed[0]
    assert (failure.strategy, failure.cost_regime, failure.error_type) == ("momentum", "zero", "RuntimeError")
    # Sanitized: truncated, no absolute path, no credential.
    assert len(failure.reason) <= 200
    assert "<path>" in failure.reason
    assert "/home/howt" not in failure.reason
    assert "sk-live-SECRET" not in failure.reason
    assert "<redacted>" in failure.reason


def test_run_arena_isolates_strategy_constructor_failure() -> None:
    """A strategy ``__init__`` that raises is one failed pair, never an arena abort."""
    frame = _ticker_frame()
    exploding = _raising_strategy_class("strategy init failed for /tmp/private folder/secret")
    with (
        patch.dict(arena_module.STRATEGY_REGISTRY, {"momentum": exploding}),
        patch.object(arena_module, "VectorBacktestEngine", side_effect=_engine_factory()),
    ):
        outcome = run_arena(
            ["AAA", "BBB"],
            _D1,
            _D4,
            preloaded_data=frame,
            strategies=["momentum", "mean_reversion"],
            cost_regimes=["zero"],
        )

    # The other pair still completed; the constructor failure was recorded sanitized.
    assert [(run.strategy, run.cost_regime) for run in outcome.runs] == [("mean_reversion", "zero")]
    assert outcome.requested == [("momentum", "zero"), ("mean_reversion", "zero")]
    assert [(failure.strategy, failure.cost_regime, failure.error_type, failure.reason) for failure in outcome.failed] == [
        ("momentum", "zero", "RuntimeError", "strategy init failed for <path>")
    ]


def test_cli_strategy_constructor_failure_is_partial_and_exits_one(tmp_path: Path) -> None:
    """CLI level: the surviving pair keeps its artifacts and the invocation exits 1."""
    frame = _ticker_frame()
    exploding = _raising_strategy_class("strategy init failed for /tmp/private folder/secret")
    with (
        patch.dict(arena_module.STRATEGY_REGISTRY, {"momentum": exploding}),
        patch.object(arena_module, "VectorBacktestEngine", side_effect=_engine_factory()),
        patch.object(arena_module.BacktestDataLoader, "load", return_value=frame),
    ):
        result = runner.invoke(
            app,
            _waitless_cli_args(
                "--tickers",
                "AAA,BBB",
                "--strategies",
                "momentum,mean_reversion",
                "--cost-regimes",
                "zero",
                "--output-dir",
                str(tmp_path),
            ),
        )

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "requested: 2 | completed: 1 | failed: 1" in result.stdout
    assert "momentum__zero: RuntimeError: strategy init failed for <path>" in result.stdout
    assert "/tmp/private" not in result.stdout
    # Partial diagnostics keep the successful run's evidence on disk (its own run dir).
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    _assert_no_legacy_artifacts(tmp_path, ("mean_reversion__zero", "momentum__zero"))


def test_run_arena_nan_sharpe_metric_is_not_evaluated_evidence(tmp_path: Path) -> None:
    """A run whose metrics omit/NaN the Sharpe is admitted but never written as zero."""
    frame = _ticker_frame()
    outcome = _outcome(
        runs=[
            ArenaRun("mean_reversion", "realistic", _stub_result(metrics={})),
            ArenaRun("trend_following", "realistic", _stub_result(sharpe=float("nan"))),
        ],
        requested=[("mean_reversion", "realistic"), ("trend_following", "realistic")],
    )
    assert outcome.data.height == frame.height
    cards = build_finding_cards(outcome, run_date=_D4)
    comparison = next(card for card in cards if card.id == "strategy-comparison")
    all_cards = {card.id: card for card in cards}

    # Nothing derived from the unavailable metrics, and no positive verdict from them.
    assert "mean_reversion.sharpe" not in comparison.metrics
    assert "trend_following.sharpe" not in comparison.metrics
    assert comparison.verdict == "inconclusive"
    assert "no finite sharpe_ratio metric" in comparison.conclusion
    assert "mean_reversion.sharpe" not in all_cards["vs-benchmark"].metrics
    assert "trend_following.sharpe" not in all_cards["vs-benchmark"].metrics
    assert all_cards["vs-benchmark"].verdict != "positive"
    assert all(math.isfinite(value) for card in cards for value in card.metrics.values())


# ---------------------------------------------------------------------------
# Card admission: only ``evaluated`` runs are evidence
# ---------------------------------------------------------------------------


def _admission_outcome(*non_evaluable_status: Literal["no_trades", "ineligible"]) -> ArenaOutcome:
    runs = [ArenaRun("real_strategy", "realistic", _stub_result(sharpe=-1.0))]
    for index, status in enumerate(non_evaluable_status):
        runs.append(ArenaRun(f"excluded_strategy_{index}", "realistic", _stub_result(sharpe=50.0), evaluation_status=status))
    return _outcome(
        runs=runs,
        requested=[("real_strategy", "realistic")],
    )


@pytest.mark.parametrize("status", ["no_trades", "ineligible"])
def test_non_evaluable_runs_never_feed_card_evidence(status: Literal["no_trades", "ineligible"]) -> None:
    """A no-trade/ineligible run cannot make a strategy beat or match the benchmark."""
    outcome = _admission_outcome(status)
    cards = {card.id: card for card in build_finding_cards(outcome, run_date=_D4)}
    assert set(cards) == {"strategy-comparison", "vs-benchmark"}, cards.keys()

    for card_id, card in cards.items():
        assert not any(key.startswith("excluded_strategy") for key in card.metrics), (card_id, card.metrics)
        assert "excluded_strategy" not in card.conclusion, (card_id, card.conclusion)
        assert card.verdict != "positive", (card_id, card.conclusion)
        assert all(math.isfinite(value) for value in card.metrics.values())
    # The evaluated run's own measurement is still present.
    assert cards["strategy-comparison"].metrics["real_strategy.sharpe"] == -1.0


def test_card_scope_reports_completion_and_evaluation_counts() -> None:
    """Cards disclose requested/completed/failed/non-evaluable counts; callers cannot rewrite."""
    outcome = _outcome(
        runs=[
            ArenaRun("real_strategy", "realistic", _stub_result(sharpe=-1.0)),
            ArenaRun("stalled_strategy", "realistic", _stub_result(trades=0), evaluation_status="no_trades"),
        ],
        requested=[("real_strategy", "realistic"), ("stalled_strategy", "realistic"), ("lost_strategy", "realistic")],
        failed=[ArenaFailure(strategy="lost_strategy", cost_regime="realistic", error_type="RuntimeError", reason="boom")],
    )
    cards = build_finding_cards(outcome, run_date=_D4, scope={"completed_runs": 99, "failed_runs": 0})
    assert cards
    for card in cards:
        assert card.scope["requested_runs"] == 3
        assert card.scope["completed_runs"] == 2
        assert card.scope["failed_runs"] == 1
        assert card.scope["non_evaluable_runs"] == 1


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


def test_arena_dataclasses_stay_positionally_constructible() -> None:
    """Existing library fixtures/callers keep working (defaulted F04 fields)."""
    frame = _ticker_frame()
    result = _stub_result()
    run = ArenaRun("momentum", "zero", result)
    assert (run.strategy, run.cost_regime, run.result) == ("momentum", "zero", result)
    assert run.evaluation_status == "evaluated"
    assert run.evaluation_notes == ()

    outcome = ArenaOutcome([run], frame, equal_weight_buyhold(frame, initial_cash=100.0), 100.0)
    assert outcome.runs == [run]
    assert outcome.requested == []
    assert outcome.failed == []

    # Legacy outcomes (no ``requested``) still produce cards; counts fall back to runs.
    legacy = ArenaOutcome(
        [run, ArenaRun("momentum", "realistic", _stub_result(sharpe=0.25))],
        frame,
        equal_weight_buyhold(frame, initial_cash=100.0),
        100.0,
    )
    cards = build_finding_cards(legacy, run_date=_D4)
    assert cards
    for card in cards:
        assert card.scope["requested_runs"] == 2
        assert card.scope["completed_runs"] == 2
        assert card.scope["failed_runs"] == 0
        assert card.scope["non_evaluable_runs"] == 0


# ---------------------------------------------------------------------------
# Real loader + real engine CLI wiring (slow, engine group required)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@requires_engine
def test_cli_partial_diagnostics_with_real_run_arena_and_engine(tmp_path: Path) -> None:
    """Real loader + real ``run_arena`` + real engine: a momentum failure is PARTIAL, exit 1."""
    from equity_lake.backtesting.engine import VectorBacktestEngine as RealVectorBacktestEngine

    lake = tmp_path / "lake"
    seed_demo(years=1, tickers=["AAPL", "MSFT"], seed=13, lake_dir=lake)
    days = trailing_business_days(365)
    start_date, end_date = days[-120], days[-20]
    out_dir = tmp_path / "findings"

    def _engine(**kwargs: Any) -> Any:
        if kwargs["strategy"].__class__.__name__ == "CrossSectionalMomentumStrategy":
            raise RuntimeError("synthetic momentum failure at /home/howt/private/momentum.parquet")
        return RealVectorBacktestEngine(**kwargs)

    with patch("equity_lake.backtesting.arena.VectorBacktestEngine", side_effect=_engine):
        result = runner.invoke(
            app,
            [
                "arena",
                "run",
                "--tickers",
                "AAPL,MSFT",
                "--start-date",
                start_date.isoformat(),
                "--end-date",
                end_date.isoformat(),
                "--lake",
                str(lake),
                "--strategies",
                "momentum,mean_reversion",
                "--cost-regimes",
                "zero",
                "--output-dir",
                str(out_dir),
            ],
        )

    assert result.exit_code == 1, result.stdout
    assert "PARTIAL" in result.stdout
    assert "requested: 2 | completed: 1 | failed: 1" in result.stdout
    assert "momentum__zero: RuntimeError: synthetic momentum failure at <path>" in result.stdout
    assert "/home/howt/private" not in result.stdout
    # The successful (real engine) run wrote its immutable evidence and stays on disk.
    runs = _run_dirs(out_dir)
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    assert (_attempt_dir(out_dir) / "benchmark__equity.parquet").exists()
    assert not (out_dir / "mean_reversion__zero").exists()
    assert not (out_dir / "benchmark__equity.parquet").exists()
