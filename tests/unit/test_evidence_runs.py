"""E01a write-side tests: dated evidence, run manifests, immutable finalisation.

Scope: ADR-0025 Decisions 2-4, 6, 9 and the frozen E01-1/E01-4 decisions. Every
test uses a ``tmp_path`` findings root — nothing is written under ``data/findings``.
The last section drives the **production caller** end-to-end: ``run_arena`` →
``write_arena_artifacts`` leaves one immutable run per completed pair next to the
unchanged legacy directories. The reader/latest/legacy projection is E01b and is
not exercised here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import polars as pl
import pytest
from typer.testing import CliRunner

from equity_lake.backtesting.arena import (
    MARKET_COST_DEFAULTS,
    ArenaOutcome,
    ArenaRun,
    _resolve_regime_costs,
    equal_weight_buyhold,
)
from equity_lake.backtesting.engine import (
    POLARS_BACKTEST_AVAILABLE,
    UNDATED_EQUITY_WARNING,
    VectorBacktestEngine,
    _warn_if_undated,
)
from equity_lake.backtesting.evidence import (
    DRAWDOWN_ARTIFACT,
    EQUITY_ARTIFACT,
    MANIFEST_NAME,
    METRICS_ARTIFACT,
    REQUIRED_RUN_ARTIFACTS,
    SCOPE_FIELD_ORDER,
    TRADES_ARTIFACT,
    CodeIdentity,
    CostApplication,
    ExecutionTiming,
    FinalisedRun,
    RunManifest,
    RunScope,
    digest_non_secret_config,
    finalise_run,
    is_finalized_run,
    probe_code_identity,
    snapshot_from_file,
    validate_run_directory,
)
from equity_lake.backtesting.evidence_view import latest_attempt as latest_evidence_attempt
from equity_lake.backtesting.report import (
    APPLIED_COSTS_NOT_RECORDED_SKIP_REASON,
    UNDATED_RUN_EVIDENCE_SKIP_REASON,
    write_arena_artifacts,
    write_arena_artifacts_with_evidence,
    write_backtest_report,
)
from equity_lake.backtesting.result import BacktestResult
from equity_lake.core.instruments import IDENTITY_COLUMNS, InstrumentKey
from equity_lake.findings import FindingCard, write_finding_card

requires_engine = pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")

# Irregular sessions: weekend gaps, a holiday-sized gap, a month gap, and a
# non-5-day spacing — deliberately not a length-derived contiguous calendar.
IRREGULAR_DATES = [
    date(2022, 1, 3),
    date(2022, 1, 4),
    date(2022, 1, 10),
    date(2022, 2, 15),
    date(2022, 3, 1),
    date(2022, 3, 2),
]
IRREGULAR_VALUES = [100_000.0, 100_500.0, 99_250.0, 101_000.0, 102_750.0, 103_000.0]

_BUILD_TS = datetime(2026, 11, 4, 10, 15, 0, tzinfo=UTC)


def _result(
    *,
    dates: list[date] | None = IRREGULAR_DATES,
    values: list[float] = IRREGULAR_VALUES,
    metrics: dict[str, float] | None = None,
    warnings: list[str] | None = None,
    trades: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    return BacktestResult(
        strategy_name="trend_following",
        tickers=["AAPL", "MSFT"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        initial_cash=100_000.0,
        final_cash=values[-1] if values else 100_000.0,
        equity_curve=pl.Series("equity", values, dtype=pl.Float64),
        trades=trades if trades is not None else [],
        metrics=metrics if metrics is not None else {"total_return": 0.03, "sharpe_ratio": 1.1},
        warnings=warnings,
        equity_dates=dates,
    )


def _scope(**overrides: Any) -> RunScope:
    payload: dict[str, Any] = {
        "universe": ["us_equity/XNYS/AAPL", "us_equity/XNYS/MSFT"],
        "start_date": IRREGULAR_DATES[0],
        "end_date": IRREGULAR_DATES[-1],
        "strategies": ["trend_following"],
        "costs": [CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
        "initial_cash": 100_000.0,
        "parameters": {"lookback": 20, "fee_ratio": 0.001425},
    }
    payload.update(overrides)
    return RunScope(**payload)


def _finalise(
    tmp_path: Path,
    *,
    result: BacktestResult | None = None,
    scope: RunScope | None = None,
    **overrides: Any,
) -> FinalisedRun:
    kwargs: dict[str, Any] = {
        "execution": ExecutionTiming(trade_at_price="close", resample="1d", note="decision session close"),
        "seed": None,
        "code": CodeIdentity(commit="a" * 40),
        "root": tmp_path,
        "now": _BUILD_TS,
    }
    kwargs.update(overrides)
    return finalise_run(result if result is not None else _result(), scope=scope or _scope(), **kwargs)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_dirs(tmp_path: Path) -> list[Path]:
    runs_root = tmp_path / "runs"
    return sorted(path for path in runs_root.iterdir() if path.is_dir()) if runs_root.is_dir() else []


# ---------------------------------------------------------------------------
# 1. Dated evidence round-trip
# ---------------------------------------------------------------------------


def test_equity_dates_round_trip_irregular_session_dates(tmp_path: Path) -> None:
    """Irregular backend sessions survive to the dated parquet, provably not a range."""
    run = _finalise(tmp_path)

    equity = pl.read_parquet(run.directory / EQUITY_ARTIFACT)
    assert equity.columns == ["date", "equity"]
    assert equity.schema["date"] == pl.Date
    assert equity["date"].to_list() == IRREGULAR_DATES
    assert equity["equity"].to_list() == IRREGULAR_VALUES

    # Provably not a length-derived calendar: the window length disagrees with the
    # row count, and real multi-day gaps are present.
    span = IRREGULAR_DATES[-1] - IRREGULAR_DATES[0]
    assert span.days + 1 != len(IRREGULAR_DATES)
    gaps = {(b - a).days for a, b in zip(IRREGULAR_DATES[:-1], IRREGULAR_DATES[1:], strict=True)}
    assert gaps == {1, 6, 36, 14}
    assert gaps != {1}

    drawdown = pl.read_parquet(run.directory / DRAWDOWN_ARTIFACT)
    assert drawdown.columns == ["date", "drawdown"]
    assert drawdown["date"].to_list() == IRREGULAR_DATES

    assert run.manifest.artifacts  # manifest records the dated artifacts


# ---------------------------------------------------------------------------
# 2. Undated results stay undated
# ---------------------------------------------------------------------------


def test_result_without_backend_dates_stays_undated(tmp_path: Path) -> None:
    """No backend timestamps -> Series unchanged, dates None, warning, no dated claim."""
    undated_report = type("Report", (), {"creturn": pl.DataFrame({"creturn": [1.0, 1.02, 1.01]})})()
    equity = pl.Series("equity", [100_000.0, 102_000.0, 101_000.0], dtype=pl.Float64)

    assert VectorBacktestEngine._extract_equity_dates(undated_report, equity) is None

    warnings: list[str] = []
    _warn_if_undated(warnings, equity.len(), None)
    assert warnings == [UNDATED_EQUITY_WARNING]

    result = _result(dates=None, values=[100_000.0, 102_000.0, 101_000.0], metrics={"total_return": 0.01}, warnings=warnings)
    assert result.equity_curve.to_list() == [100_000.0, 102_000.0, 101_000.0]  # Series interface unchanged
    assert isinstance(result.equity_curve, pl.Series)
    assert result.equity_dates is None
    assert result.has_dated_evidence is False
    assert UNDATED_EQUITY_WARNING in result.warnings
    assert "equity_dates" not in result.to_dict()  # never claims dated evidence

    # A finalized run refuses to record an undated curve as dated evidence, and
    # leaves no run directory behind.
    with pytest.raises(ValueError, match="dated evidence unavailable"):
        finalise_run(result, scope=_scope(), root=tmp_path, code=CodeIdentity(commit="a" * 40))
    assert _run_dirs(tmp_path) == []


def test_engine_date_extraction_reads_backend_temporal_columns() -> None:
    """Session dates come from the backend's own column; timestamps normalise to date."""
    values = pl.Series("equity", [100_000.0, 101_000.0], dtype=pl.Float64)

    dated = type("Report", (), {"creturn": pl.DataFrame({"date": [date(2022, 1, 3), date(2022, 1, 10)], "creturn": [1.0, 1.01]})})()
    assert VectorBacktestEngine._extract_equity_dates(dated, values) == [date(2022, 1, 3), date(2022, 1, 10)]

    stamped = type(
        "Report",
        (),
        {
            "creturn": pl.DataFrame(
                {
                    "timestamp": [
                        datetime(2022, 1, 3, 21, 0, tzinfo=UTC),
                        datetime(2022, 1, 10, 21, 0, tzinfo=UTC),
                    ],
                    "creturn": [1.0, 1.01],
                }
            )
        },
    )()
    assert VectorBacktestEngine._extract_equity_dates(stamped, values) == [date(2022, 1, 3), date(2022, 1, 10)]

    # A short date column is never stretched: no reindex, no invented sessions.
    short = type("Report", (), {"creturn": pl.DataFrame({"date": [date(2022, 1, 3)], "creturn": [1.0]})})()
    assert VectorBacktestEngine._extract_equity_dates(short, values) is None

    # A string column is not a timestamp and is never parsed into one.
    text = type("Report", (), {"creturn": pl.DataFrame({"date": ["2022-01-03", "2022-01-10"], "creturn": [1.0, 1.01]})})()
    assert VectorBacktestEngine._extract_equity_dates(text, values) is None


@requires_engine
@pytest.mark.slow
def test_engine_populates_equity_dates_from_real_backend_report(tmp_path: Path) -> None:
    """One real backend run: the report's dates land in the finalized run artifacts."""
    ports = str(Path(__file__).parent)
    if ports not in sys.path:
        sys.path.insert(0, ports)
    from test_arena import _synthetic_data  # noqa: PLC0415 - test-only fixture import

    from equity_lake.backtesting.arena import STRATEGY_REGISTRY  # noqa: PLC0415

    data = _synthetic_data(n_days=320)
    start = cast(date, data["date"].min())
    end = cast(date, data["date"].max())
    engine = VectorBacktestEngine(
        STRATEGY_REGISTRY["mean_reversion"](),
        ["T00", "T01", "T02"],
        start,
        end,
        preloaded_data=data,
        markets=["us_equity"],
        config={"fee_ratio": 0.001425, "tax_ratio": 0.0},
    )
    result = engine.run()

    creturn = engine._report.creturn
    assert creturn.columns == ["date", "creturn"]  # probed backend shape
    assert result.equity_dates is not None
    assert result.equity_dates == creturn["date"].to_list()
    assert len(result.equity_dates) == result.equity_curve.len()
    assert all(isinstance(day, date) and not isinstance(day, datetime) for day in result.equity_dates)
    assert UNDATED_EQUITY_WARNING not in result.warnings

    scope = _scope(
        universe=["us_equity/XNYS/T00", "us_equity/XNYS/T01", "us_equity/XNYS/T02"],
        start_date=start,
        end_date=end,
        strategies=["mean_reversion"],
    )
    run = finalise_run(result, scope=scope, root=tmp_path, code=CodeIdentity(commit="a" * 40))
    assert pl.read_parquet(run.directory / EQUITY_ARTIFACT)["date"].to_list() == result.equity_dates
    assert is_finalized_run(run.directory)


# ---------------------------------------------------------------------------
# 3. Run identity is scope-only
# ---------------------------------------------------------------------------


def test_run_id_ignores_code_identity_and_changes_with_scope(tmp_path: Path) -> None:
    """Code identity never changes the ID; window/universe/parameters do."""
    base = _scope()
    assert base.run_id() == _scope().run_id()
    assert _scope(universe=["us_equity/XNYS/MSFT", "us_equity/XNYS/AAPL"]).run_id() == base.run_id()  # order-insensitive

    dirty = CodeIdentity(commit="b" * 40, dirty_digest="c" * 64)
    first = _finalise(tmp_path, code=CodeIdentity(commit="a" * 40))
    second = _finalise(tmp_path, code=dirty)

    assert first.manifest.run_id == second.manifest.run_id == base.run_id()
    assert first.manifest.code.commit == "a" * 40
    assert second.manifest.code.commit == "b" * 40
    assert second.manifest.code.dirty_digest == "c" * 64
    assert first.directory.name != second.directory.name
    # Same scope, so both names share the timestamp + scope-hash prefix and differ
    # only by the collision discriminator.
    assert second.directory.name.startswith(first.directory.name)

    changed: dict[str, dict[str, object]] = {
        "window": {"start_date": IRREGULAR_DATES[1]},
        "universe": {"universe": ["us_equity/XNYS/AAPL"]},
        "parameters": {"parameters": {"lookback": 21, "fee_ratio": 0.001425}},
        "cash": {"initial_cash": 50_000.0},
        "regimes": {"costs": [CostApplication(regime="high", fee_ratio=0.002, tax_ratio=0.001)]},
        "strategies": {"strategies": ["momentum"]},
    }
    run_ids = {base.run_id()}
    for label, overrides in changed.items():
        candidate = _scope(**overrides)
        assert candidate.run_id() != base.run_id(), f"{label} must change the run ID"
        run_ids.add(candidate.run_id())
    assert len(run_ids) == len(changed) + 1


# ---------------------------------------------------------------------------
# 4. Identical-scope invocations coexist
# ---------------------------------------------------------------------------


def test_scope_identical_invocations_coexist(tmp_path: Path) -> None:
    """Two runs of the same scope produce two finalized directories, neither overwritten."""
    first = _finalise(tmp_path)
    first_digests = {record.path: record.sha256 for record in first.manifest.artifacts}
    first_manifest_bytes = (first.directory / MANIFEST_NAME).read_bytes()

    second = _finalise(tmp_path)

    assert first.directory != second.directory
    assert second.directory.name.endswith("-2")  # documented discriminator, not an overwrite
    assert first.directory.is_dir() and second.directory.is_dir()

    validation_first = validate_run_directory(first.directory)
    validation_second = validate_run_directory(second.directory)
    assert validation_first.finalized, validation_first.reasons
    assert validation_second.finalized, validation_second.reasons

    # The first run's bytes were not touched by the second invocation.
    assert (first.directory / MANIFEST_NAME).read_bytes() == first_manifest_bytes
    first_validation = validate_run_directory(first.directory)
    assert first_validation.manifest is not None
    assert {record.path: record.sha256 for record in first_validation.manifest.artifacts} == first_digests

    # Same scope ID, distinct directory identity, and each manifest names its own dir.
    assert first.manifest.run_id == second.manifest.run_id
    assert first.manifest.run_dir == first.directory.name
    assert second.manifest.run_dir == second.directory.name
    assert len(_run_dirs(tmp_path)) == 2


# ---------------------------------------------------------------------------
# 5. Failed finalisation leaves nothing behind
# ---------------------------------------------------------------------------


def test_failed_finalisation_leaves_no_run_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-write failure removes the temporary directory and finalizes nothing."""
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    def _explode(result: BacktestResult, out_dir: Path, **kwargs: object) -> dict[str, Path]:
        (out_dir / "half-written.parquet").write_bytes(b"partial")
        raise RuntimeError("simulated artifact write failure")

    monkeypatch.setattr(evidence, "write_dated_backtest_report", _explode)

    with pytest.raises(RuntimeError, match="simulated artifact write failure"):
        _finalise(tmp_path)

    runs_root = tmp_path / "runs"
    leftovers = sorted(path.name for path in runs_root.iterdir()) if runs_root.is_dir() else []
    assert leftovers == []  # no finalized run and no `.partial-*` temporary


# ---------------------------------------------------------------------------
# 6. Incomplete or tampered runs are never finalized runs
# ---------------------------------------------------------------------------


def test_incomplete_or_tampered_run_is_not_a_finalized_run(tmp_path: Path) -> None:
    """Missing manifest, truncated artifact and edited artifact are each rejected with a reason."""
    missing_manifest = _finalise(tmp_path, scope=_scope(parameters={"lookback": 20}))
    (missing_manifest.directory / MANIFEST_NAME).unlink()
    verdict = validate_run_directory(missing_manifest.directory)
    assert verdict.finalized is False
    assert any("manifest.json missing" in reason for reason in verdict.reasons)

    truncated = _finalise(tmp_path, scope=_scope(parameters={"lookback": 21}))
    equity_path = truncated.directory / EQUITY_ARTIFACT
    equity_path.write_bytes(equity_path.read_bytes()[:12])
    verdict = validate_run_directory(truncated.directory)
    assert verdict.finalized is False
    assert any("artifact" in reason for reason in verdict.reasons)

    edited = _finalise(tmp_path, scope=_scope(parameters={"lookback": 22}))
    metrics_path = edited.directory / "metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    payload["total_return"] = 99.0
    metrics_path.write_text(json.dumps(payload), encoding="utf-8")
    verdict = validate_run_directory(edited.directory)
    assert verdict.finalized is False
    assert any("artifact hash mismatch: 'metrics.json'" in reason for reason in verdict.reasons)

    intact = _finalise(tmp_path, scope=_scope(parameters={"lookback": 23}))
    assert validate_run_directory(intact.directory).finalized is True
    assert validate_run_directory(tmp_path / "runs" / "does-not-exist").finalized is False


# ---------------------------------------------------------------------------
# 7. No secrets in a manifest
# ---------------------------------------------------------------------------


def test_manifest_contains_no_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret config keys and env values never reach the manifest; provenance stays unknown."""
    monkeypatch.setenv("EQUITY_ARENA_API_TOKEN", "tok-live-do-not-record-1234")
    secret_value = "sk-live-do-not-record-5678"

    run = _finalise(
        tmp_path,
        config={"fee_ratio": 0.001425, "api_key": secret_value, "nested": {"refresh_token": secret_value}},
    )
    text = (run.directory / MANIFEST_NAME).read_text(encoding="utf-8")

    assert secret_value not in text
    assert "tok-live-do-not-record-1234" not in text
    assert "api_key" not in text and "refresh_token" not in text
    assert "EQUITY_ARENA_API_TOKEN" not in text

    assert run.manifest.config_digest is not None
    assert len(run.manifest.config_digest) == 64
    int(run.manifest.config_digest, 16)
    # Same digest as hashing the non-secret subset alone: the secret never entered the preimage.
    assert run.manifest.config_digest == digest_non_secret_config({"fee_ratio": 0.001425, "nested": {}})
    assert run.manifest.data_provenance == "unknown"
    assert run.manifest.seed is None

    # A secret-named scope parameter is refused outright rather than silently hashed.
    with pytest.raises(ValueError, match="secret-named keys"):
        _scope(parameters={"lookback": 20, "api_key": secret_value})


# ---------------------------------------------------------------------------
# 8. Artifact checksums
# ---------------------------------------------------------------------------


def test_dated_artifacts_match_manifest_checksums(tmp_path: Path) -> None:
    """Every recorded artifact matches its size and sha256, and the manifest is excluded."""
    run = _finalise(tmp_path)

    recorded = {record.path: record for record in run.manifest.artifacts}
    assert set(REQUIRED_RUN_ARTIFACTS) <= set(recorded)
    assert MANIFEST_NAME not in recorded  # the manifest never hashes itself
    for path, record in recorded.items():
        artifact = run.directory / path
        assert artifact.is_file()
        assert record.bytes == artifact.stat().st_size
        assert record.sha256 == _sha256(artifact)


# ---------------------------------------------------------------------------
# 9. Snapshot binding (E01-4)
# ---------------------------------------------------------------------------


def test_manifest_records_input_snapshot_hashes_of_what_was_read(tmp_path: Path) -> None:
    """Supplied read-seam hashes appear verbatim; none are invented."""
    fixture = tmp_path / "fixture_ohlcv.parquet"
    pl.DataFrame({"date": IRREGULAR_DATES, "close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}).write_parquet(fixture)
    snapshot = snapshot_from_file(fixture, name="fixture:ohlcv", source="fixture:file")
    assert snapshot.sha256 == hashlib.sha256(fixture.read_bytes()).hexdigest()

    other = tmp_path / "synthetic_context.json"
    other.write_text('{"actions": []}', encoding="utf-8")
    second = snapshot_from_file(other, source="synthetic:arena")

    run = _finalise(tmp_path, input_snapshots=[snapshot, second])

    recorded = {entry.name: entry for entry in run.manifest.input_snapshots}
    assert set(recorded) == {"fixture:ohlcv", "synthetic_context.json"}  # nothing invented, nothing dropped
    assert recorded["fixture:ohlcv"].sha256 == snapshot.sha256
    assert recorded["fixture:ohlcv"].source == "fixture:file"
    assert recorded["synthetic_context.json"].sha256 == second.sha256
    assert recorded["synthetic_context.json"].source == "synthetic:arena"
    assert run.manifest.data_provenance == "unknown"

    # Claims are refused, not downgraded: snapshots alone are no longer sufficient.
    with pytest.raises(ValueError, match="requires complete, snapshot-bound"):
        _finalise(tmp_path, data_provenance="real", input_snapshots=[snapshot, second])

    # An empty snapshot list keeps the honest default.
    undated_claim = _finalise(tmp_path, input_snapshots=[])
    assert undated_claim.manifest.data_provenance == "unknown"
    assert undated_claim.manifest.input_snapshots == []


# ---------------------------------------------------------------------------
# Supporting helpers behave as documented
# ---------------------------------------------------------------------------


def test_code_identity_probe_never_fails_a_run(tmp_path: Path) -> None:
    """A non-repository directory yields an empty identity instead of raising."""
    identity = probe_code_identity(tmp_path)
    assert identity.commit is None or identity.commit
    assert identity.dirty_digest is None or len(identity.dirty_digest) == 64


def test_manifest_rejects_inconsistent_scope_and_naive_timestamps(tmp_path: Path) -> None:
    """The manifest refuses a run ID that does not match its scope, and naive times."""
    run = _finalise(tmp_path)
    payload = json.loads((run.directory / MANIFEST_NAME).read_text(encoding="utf-8"))

    payload["initial_cash"] = 123.0

    with pytest.raises(ValueError, match="run_id does not match"):
        RunManifest.model_validate(payload)

    payload = json.loads((run.directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["created_at"] = datetime(2026, 11, 4, 10, 15, 0).isoformat()
    with pytest.raises(ValueError, match="timezone-aware"):
        RunManifest.model_validate(payload)

    # Metric availability distinguishes measured from missing, never a measured zero.
    availability = {entry.metric: entry for entry in run.manifest.metric_availability}
    assert availability["total_return"].status == "measured"
    assert availability["sharpe_ratio"].status == "measured"
    assert availability["cagr"].status == "missing"
    assert availability["cagr"].value is None


def test_undated_legacy_writer_shape_is_unchanged(tmp_path: Path) -> None:
    """The legacy positional writer keeps its (t, equity) shape even with backend dates."""
    result = _result(dates=IRREGULAR_DATES + [], values=IRREGULAR_VALUES)
    result.equity_dates = list(IRREGULAR_DATES)  # dated result fed into the legacy writer
    out = tmp_path / "legacy"
    write_backtest_report(result, out, strategy="trend_following", cost_regime="realistic")

    equity = pl.read_parquet(out / "equity.parquet")
    assert equity.columns == ["t", "equity"]  # no date column in the legacy layout
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert "equity_dates" not in metrics  # legacy metrics.json shape untouched


def test_run_id_is_stable_across_process_state(tmp_path: Path) -> None:
    """The ID depends only on scope: rebuild order and code state do not move it."""
    first = _scope().run_id()
    reordered = RunScope(
        universe=["us_equity/XNYS/MSFT", "us_equity/XNYS/AAPL"],
        start_date=IRREGULAR_DATES[0],
        end_date=IRREGULAR_DATES[-1],
        strategies=["trend_following"],
        costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0)],
        initial_cash=100_000,
        parameters={"fee_ratio": 0.001425, "lookback": 20},
    )
    assert reordered.run_id() == first
    assert _scope(initial_cash=100_000).run_id() == first  # int vs float cash is the same scope
    assert _scope(start_date=IRREGULAR_DATES[0] - timedelta(days=1)).run_id() != first


def test_scope_payload_order_matches_scope_field_order() -> None:
    """``SCOPE_FIELD_ORDER`` is documentation *and* the payload builder's source.

    Falsified if :meth:`RunScope.scope_payload` ever hand-orders its keys again: the
    documented order and the run-ID preimage could drift apart, silently changing what
    ``run_id`` means (the hazard the attempt payload's equivalent test also pins).
    """
    scope = _scope()
    payload = scope.scope_payload()
    assert tuple(payload) == SCOPE_FIELD_ORDER
    assert SCOPE_FIELD_ORDER == ("protocol", "universe", "start_date", "end_date", "strategies", "costs", "initial_cash", "parameters")
    # Canonicalisation is unchanged: the preimage still hashes the same fields.
    assert json.loads(scope.canonical_scope_json()) == payload
    assert scope.run_id() == hashlib.sha256(scope.canonical_scope_json().encode("utf-8")).hexdigest()


def _evidence_outcome(strategy: str = "trend_following", regime: str = "realistic") -> ArenaOutcome:
    """A dated stub outcome (no engine, no lake) that the attempt writer can publish."""
    frame = pl.DataFrame(
        {"ticker": ["AAPL"] * len(IRREGULAR_DATES), "date": IRREGULAR_DATES, "close": list(IRREGULAR_VALUES)},
        schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
    )
    run = ArenaRun(
        strategy=strategy,
        cost_regime=regime,
        result=_result(),
        strategy_params={"lookback": 20},
        applied_costs={"fee_ratio": 0.001425, "tax_ratio": 0.0},
    )
    return ArenaOutcome(
        runs=[run],
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100_000.0),
        initial_cash=100_000.0,
        requested=[(strategy, regime)],
        markets=("us_equity",),
    )


def test_arena_writer_writes_no_legacy_paths(tmp_path: Path) -> None:
    """E01b.1: the arena writer creates no legacy slug dir, flat benchmark or flat card.

    Falsified if any legacy path reappears: the frozen layout is read-only evidence, and
    an invocation's comparison artifacts must be owned by its own attempt directory.
    """
    outcome = _evidence_outcome()

    cards, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert cards
    assert len(summary.finalized) == 1
    assert summary.attempt is not None
    assert summary.attempt.record.status == "complete"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["attempts", "runs"]
    _assert_no_legacy_writes(tmp_path, outcome)


def test_arena_writer_leaves_a_pre_existing_legacy_directory_byte_identical(tmp_path: Path) -> None:
    """Frozen legacy evidence is never rewritten, migrated or relabelled by a new run.

    Falsified if the writer refreshed a legacy directory, the flat benchmark or a flat
    card: an earlier card could then silently inherit this run's curve (the exact defect
    ADR-0025 Decision 8 closes).
    """
    legacy = tmp_path / "trend_following__realistic"
    write_backtest_report(_result(), legacy, strategy="trend_following", cost_regime="realistic")
    benchmark = tmp_path / "benchmark__equity.parquet"
    benchmark.write_bytes(b"legacy benchmark bytes")
    flat_card_path = write_finding_card(_card_for_legacy(), base=tmp_path)

    legacy_before = {path.name: path.read_bytes() for path in sorted(legacy.iterdir())}
    benchmark_before = benchmark.read_bytes()
    flat_card_before = flat_card_path.read_bytes()

    _, summary = write_arena_artifacts_with_evidence(_evidence_outcome(), base=tmp_path, run_date=date(2026, 8, 4))

    assert summary.attempt is not None and summary.finalized  # the invocation published its own evidence
    assert {path.name: path.read_bytes() for path in sorted(legacy.iterdir())} == legacy_before
    assert benchmark.read_bytes() == benchmark_before
    assert flat_card_path.read_bytes() == flat_card_before
    assert (summary.attempt.directory / "benchmark__equity.parquet").is_file()


def _card_for_legacy() -> FindingCard:
    return FindingCard(
        id="strategy-comparison",
        axis="strategy",
        claim="legacy claim",
        verdict="inconclusive",
        conclusion="legacy conclusion",
        metrics={"sharpe": 0.5},
        run_date=date(2026, 8, 4),
    )


# ---------------------------------------------------------------------------
# Production caller: run_arena -> write_arena_artifacts finalises run evidence
# ---------------------------------------------------------------------------


def _synthetic_panel(*, n_tickers: int = 12, n_days: int = 320, seed: int = 42) -> pl.DataFrame:
    """Deterministic long OHLCV panel from the arena test fixture (local, no lake)."""
    ports = str(Path(__file__).parent)
    if ports not in sys.path:
        sys.path.insert(0, ports)
    from test_arena import _synthetic_data  # noqa: PLC0415 - test-only fixture import

    return _synthetic_data(n_tickers=n_tickers, n_days=n_days, seed=seed)


def _arena_outcome(
    *,
    panel: pl.DataFrame | None = None,
    n_tickers: int = 12,
    n_days: int = 320,
    seed: int = 42,
    strategies: list[str] | None = None,
    cost_regimes: list[str] | None = None,
    market_costs: dict[str, dict[str, float]] | None = None,
) -> Any:
    """Real ``run_arena`` over a synthetic panel: the production caller's input."""
    from equity_lake.backtesting.arena import run_arena  # noqa: PLC0415 - engine group only

    data = panel if panel is not None else _synthetic_panel(n_tickers=n_tickers, n_days=n_days, seed=seed)
    tickers = sorted(str(ticker) for ticker in data["ticker"].unique().to_list())
    start = cast(date, data["date"].min())
    end = cast(date, data["date"].max())
    return run_arena(
        tickers,
        start,
        end,
        preloaded_data=data,
        strategies=strategies,
        cost_regimes=cost_regimes,
        market_costs=market_costs,
    )


def _assert_no_legacy_writes(root: Path, outcome: Any) -> None:
    """E01b.1: the arena writer creates **no** legacy path.

    Legacy ``<strategy>__<regime>/`` directories, the flat
    ``benchmark__equity.parquet`` and the flat ``<card-id>.json`` cards are frozen
    read-only evidence: an invocation writes its own immutable ``runs/`` and
    ``attempts/`` layout instead, so asserting a legacy path here would pin the defect
    E01 removes.
    """
    for run in outcome.runs:
        assert not (root / f"{run.strategy}__{run.cost_regime}").exists()
    assert not (root / "benchmark__equity.parquet").exists()
    assert sorted(root.glob("*.json")) == []
    # The evidence that used to live at those paths is owned by the attempt directory.
    attempts_root = root / "attempts"
    assert attempts_root.is_dir()
    attempts = [path for path in attempts_root.iterdir() if path.is_dir() and not path.name.startswith(".tmp-")]
    assert len(attempts) == 1
    attempt_dir = attempts[0]
    assert (attempt_dir / "attempt.json").is_file()
    if not outcome.benchmark.is_empty():
        assert (attempt_dir / "benchmark__equity.parquet").is_file()
    for card_id in ("strategy-comparison", "cost-regime", "vs-benchmark"):
        if (attempt_dir / f"{card_id}.json").is_file():
            assert json.loads((attempt_dir / f"{card_id}.json").read_text(encoding="utf-8"))["id"] == card_id


def _pair_of(manifest: Any) -> tuple[str, str]:
    return (manifest.strategies[0], manifest.cost_regimes[0])


@requires_engine
@pytest.mark.slow
def test_arena_writer_finalises_one_run_per_completed_pair(tmp_path: Path) -> None:
    """The real arena writer leaves one finalized, validator-accepted run per pair."""
    outcome = _arena_outcome(strategies=["mean_reversion", "trend_following"], cost_regimes=["zero", "realistic"])
    completed = {(run.strategy, run.cost_regime) for run in outcome.runs}
    assert len(completed) == 4  # the fixture is long enough for both strategies to trade

    cards, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert cards  # cards unchanged by the extra evidence write
    assert summary.skipped_undated == {}  # every run carried backend dates
    assert summary.skipped_unrecorded_costs == {}  # every run recorded its applied costs
    assert len(summary.finalized) == len(completed)

    run_dirs = _run_dirs(tmp_path)
    assert len(run_dirs) == len(completed)
    assert not [path for path in (tmp_path / "runs").iterdir() if path.name.startswith(".partial-")]

    pairs: list[tuple[str, str]] = []
    for finalised in summary.finalized:
        verdict = validate_run_directory(finalised.directory)
        assert verdict.finalized, verdict.reasons
        manifest = finalised.manifest
        assert manifest.run_dir == finalised.directory.name
        pair = _pair_of(manifest)
        pairs.append(pair)

        run = next(candidate for candidate in outcome.runs if (candidate.strategy, candidate.cost_regime) == pair)
        # The scope is the run's own: one strategy, one regime, this run's window,
        # universe, cash and registry parameters.
        assert manifest.start_date == run.result.start_date
        assert manifest.end_date == run.result.end_date
        # E01c: the arena loaded one frame with full identity, so the universe is its
        # canonical display keys and the basis is parsed as 'canonical' — not the
        # requested bare tickers.
        expected_universe = {InstrumentKey(**row).display_key for row in outcome.data.select(*IDENTITY_COLUMNS).unique().to_dicts()}
        assert set(manifest.universe) == expected_universe
        assert manifest.universe_basis == "canonical"
        assert manifest.initial_cash == run.result.initial_cash
        assert manifest.parameters  # strategy parameters are recorded, never empty

        # That regime's fees/taxes, recorded by the arena and never re-derived here.
        expected_costs = _resolve_regime_costs(run.cost_regime, ("us_equity",), MARKET_COST_DEFAULTS)
        assert run.applied_costs == expected_costs  # the arena records what it configured
        assert [cost.regime for cost in manifest.costs] == [run.cost_regime]
        assert manifest.costs[0].fee_ratio == expected_costs["fee_ratio"]
        assert manifest.costs[0].tax_ratio == expected_costs["tax_ratio"]

        # The dated artifact reproduces the backend's own sessions for this run.
        equity = pl.read_parquet(finalised.directory / EQUITY_ARTIFACT)
        assert equity.columns == ["date", "equity"]
        assert equity["date"].to_list() == run.result.equity_dates

    assert sorted(pairs) == sorted(completed)  # exactly one run per completed pair, none invented

    # The legacy layout is still there, unchanged in shape.
    _assert_no_legacy_writes(tmp_path, outcome)


@requires_engine
@pytest.mark.slow
def test_scope_identical_invocations_coexist_through_the_writer(tmp_path: Path) -> None:
    """Two writer invocations of one scope publish two distinct, intact run directories."""
    outcome = _arena_outcome(strategies=["mean_reversion"], cost_regimes=["realistic"])

    _, first = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))
    first_bytes = {finalised.directory.name: (finalised.directory / MANIFEST_NAME).read_bytes() for finalised in first.finalized}
    second_cards, second = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert second_cards  # the second invocation still writes its cards

    first_dir = first.finalized[0].directory
    second_dir = second.finalized[0].directory
    assert first_dir != second_dir  # never overwritten, never reused
    assert {first_dir, second_dir} == set(_run_dirs(tmp_path))

    for directory in (first_dir, second_dir):
        verdict = validate_run_directory(directory)
        assert verdict.finalized, verdict.reasons
    # Same scope -> same run ID; distinct directory identity.
    assert first.finalized[0].manifest.run_id == second.finalized[0].manifest.run_id
    # The first invocation's published bytes were not touched.
    assert (first_dir / MANIFEST_NAME).read_bytes() == first_bytes[first_dir.name]


@requires_engine
@pytest.mark.slow
def test_writer_records_irregular_session_dates_end_to_end(tmp_path: Path) -> None:
    """Gapped sessions survive to the dated artifact and are provably not a length-derived range."""
    panel = _synthetic_panel(n_days=320)
    sessions = panel["date"].unique().sort().to_list()
    # A two-session mid-week holiday block (Thu+Fri) near the end of the panel, inside
    # the window the strategies actually trade: the evidence must show that 5-day gap.
    dropped = [sessions[-12], sessions[-11]]
    keep = {day for day in sessions if day not in dropped}
    gapped = panel.filter(pl.col("date").is_in(list(keep)))
    assert gapped["date"].n_unique() == len(sessions) - len(dropped)

    outcome = _arena_outcome(panel=gapped, strategies=["mean_reversion", "trend_following"], cost_regimes=["realistic"])
    assert {run.evaluation_status for run in outcome.runs} == {"evaluated"}
    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))
    assert len(summary.finalized) == len(outcome.runs) == 2

    for finalised in summary.finalized:
        verdict = validate_run_directory(finalised.directory)
        assert verdict.finalized, verdict.reasons
        run = next(candidate for candidate in outcome.runs if (candidate.strategy, candidate.cost_regime) == _pair_of(finalised.manifest))
        dates = pl.read_parquet(finalised.directory / EQUITY_ARTIFACT)["date"].to_list()
        assert dates == run.result.equity_dates  # the backend's sessions, verbatim
        assert len(dates) > 1
        # Never the panel calendar: the holiday block is absent from the evidence.
        assert not set(dates) & set(dropped)
        assert set(dates) <= set(sessions)
        # Not a length-derived range: the window spans more calendar days than it has
        # rows, and the mid-week holiday shows up as a 5-day gap (weekends are 3).
        assert (dates[-1] - dates[0]).days + 1 > len(dates)
        gaps = {(later - earlier).days for earlier, later in zip(dates[:-1], dates[1:], strict=True)}
        assert 1 in gaps  # consecutive sessions exist
        assert 5 in gaps  # the dropped Thu+Fri block: a real 5-day session gap
        assert max(gaps) > 3  # not merely the weekend gap a length-derived calendar would fake


@requires_engine
@pytest.mark.slow
def test_undated_run_is_skipped_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An undated pair gets no run directory, is reported, and never blocks the others."""
    original = VectorBacktestEngine._extract_equity_dates
    calls = {"count": 0}

    def _undate_second_pair(report: Any, equity_curve: pl.Series) -> list[date] | None:
        calls["count"] += 1
        if calls["count"] == 2:  # the second requested pair only
            return None
        return cast("list[date] | None", original(report, equity_curve))

    monkeypatch.setattr(VectorBacktestEngine, "_extract_equity_dates", staticmethod(_undate_second_pair))

    outcome = _arena_outcome(strategies=["mean_reversion", "trend_following"], cost_regimes=["realistic"])
    assert calls["count"] == len(outcome.runs)
    undated = outcome.runs[1]
    assert undated.result.equity_dates is None
    assert UNDATED_EQUITY_WARNING in undated.result.warnings
    undated_slug = f"{undated.strategy}__{undated.cost_regime}"

    cards, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert cards  # the write itself still succeeds
    assert list(summary.skipped_undated) == [undated_slug]
    assert summary.skipped_undated[undated_slug] == UNDATED_RUN_EVIDENCE_SKIP_REASON
    assert "undated" in summary.skipped_undated[undated_slug]
    assert "no calendar is invented" in summary.skipped_undated[undated_slug]
    assert len(summary.finalized) == len(outcome.runs) - 1

    run_dirs = _run_dirs(tmp_path)
    assert len(run_dirs) == len(outcome.runs) - 1
    for directory in run_dirs:
        verdict = validate_run_directory(directory)
        assert verdict.finalized, verdict.reasons
        assert verdict.manifest is not None
        assert _pair_of(verdict.manifest) != (undated.strategy, undated.cost_regime)  # no manifest claims it
    # The undated pair still has its legacy (undated) artifacts.
    assert not (tmp_path / "runs" / undated_slug).exists()
    _assert_no_legacy_writes(tmp_path, outcome)


@requires_engine
@pytest.mark.slow
def test_finalise_failure_publishes_write_failed_and_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing finalisation keeps the other runs, publishes ``write_failed``, and raises.

    E01b.1 moves the guarantee from "legacy artifacts survive" to "the failure is
    durable evidence": the attempt record is published with ``status='write_failed'``
    (naming the pair that failed and the run that did finalise) *before* the exception
    reaches the caller, and no partial run directory is left behind.
    """
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    original = evidence.finalise_run

    def _explode(result: BacktestResult, *, scope: RunScope, **kwargs: Any) -> FinalisedRun:
        if scope.strategies == ["trend_following"]:
            raise RuntimeError("simulated finalise failure")
        return original(result, scope=scope, **kwargs)

    monkeypatch.setattr(evidence, "finalise_run", _explode)

    outcome = _arena_outcome(strategies=["mean_reversion", "trend_following"], cost_regimes=["realistic"])

    # The entry point the CLI calls surfaces the failure, so the CLI's
    # writer-exception path still turns it into exit 1.
    with pytest.raises(RuntimeError, match="simulated finalise failure") as excinfo:
        write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    notes = getattr(excinfo.value, "__notes__", [])
    assert any("write_failed" in note for note in notes), notes

    # The other run's evidence survived; the failing run left no directory at all.
    run_dirs = _run_dirs(tmp_path)
    assert len(run_dirs) == len(outcome.runs) - 1
    assert not [path for path in (tmp_path / "runs").iterdir() if path.name.startswith(".partial-")]
    manifests = []
    for directory in run_dirs:
        verdict = validate_run_directory(directory)
        assert verdict.finalized, verdict.reasons
        assert verdict.manifest is not None
        manifests.append(verdict.manifest)
    assert {tuple(manifest.strategies) for manifest in manifests} == {("mean_reversion",)}

    # The failed write is durable: one attempt record, status write_failed.
    attempt = latest_evidence_attempt(tmp_path)
    assert attempt is not None
    assert attempt.record.status == "write_failed"
    statuses = {pair_outcome.slug: pair_outcome.outcome for pair_outcome in attempt.record.outcomes}
    assert statuses == {"mean_reversion__realistic": "completed", "trend_following__realistic": "failed"}
    # And no legacy path was created for either pair.
    _assert_no_legacy_writes(tmp_path, outcome)


# ---------------------------------------------------------------------------
# Fix round 2: cost identity, value verification, atomic claim, validator reasons
# ---------------------------------------------------------------------------


def test_run_id_covers_the_applied_costs_and_ignores_their_order(tmp_path: Path) -> None:
    """Scope fields identical apart from applied fees/taxes are different run IDs.

    Regression (E01-1): the cost-regime identity is the regime name **plus** the
    resolved fee and tax ratio, so two materially different cost applications never
    look scope-identical. The documented cost order is by regime name, so the same
    scope serialized with a different costs ordering keeps one ID.
    """
    base = _scope()
    free = _scope(costs=[CostApplication(regime="realistic", fee_ratio=0.0, tax_ratio=0.0)])
    dear = _scope(costs=[CostApplication(regime="realistic", fee_ratio=0.0025, tax_ratio=0.001)])
    dearer_tax = _scope(costs=[CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0005)])

    # Everything else is equal: only the applied costs move the ID.
    for scope in (base, free, dear):
        assert (scope.universe, scope.strategies, scope.start_date) == (base.universe, base.strategies, base.start_date)
    assert len({base.run_id(), free.run_id(), dear.run_id(), dearer_tax.run_id()}) == 4

    # The cost-regime identity is name + fees + tax; costs ordering is not identity.
    two_regimes = [
        CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.0),
        CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0),
    ]
    left = _scope(costs=two_regimes)
    right = _scope(costs=list(reversed(two_regimes)))
    assert left.run_id() == right.run_id()
    assert left.cost_regimes == right.cost_regimes == ["realistic", "zero"]
    assert left.canonical_scope_json() == right.canonical_scope_json()

    # A regime may be cost-identified once per scope; a duplicate is refused rather
    # than resolved by picking one of two different cost applications.
    with pytest.raises(ValueError, match="at most once"):
        _scope(costs=[two_regimes[0], two_regimes[0]])

    # Published runs of the same scope but different costs are distinct IDs *and*
    # distinct scope hashes in the directory name, not merely a collision suffix.
    free_run = _finalise(tmp_path, scope=free)
    dear_run = _finalise(tmp_path, scope=dear)
    assert free_run.manifest.run_id != dear_run.manifest.run_id
    assert free_run.directory.name.split("-")[-1] != dear_run.directory.name.split("-")[-1]
    for run in (free_run, dear_run):
        assert validate_run_directory(run.directory).finalized
    assert free_run.manifest.costs[0].fee_ratio == 0.0
    assert dear_run.manifest.costs[0].tax_ratio == 0.001


def test_manifest_reverifies_the_run_id_against_the_applied_costs(tmp_path: Path) -> None:
    """The manifest re-derives its ID from its own recorded costs and refuses drift."""
    run = _finalise(tmp_path)
    payload = json.loads((run.directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["costs"] == [{"regime": "realistic", "fee_ratio": 0.001425, "tax_ratio": 0.0}]
    assert payload["cost_regimes"] == ["realistic"]

    cheaper = json.loads(json.dumps(payload))
    cheaper["costs"][0]["fee_ratio"] = 0.0  # a different cost application, same stated ID
    with pytest.raises(ValueError, match="run_id does not match"):
        RunManifest.model_validate(cheaper)

    disagreeing = json.loads(json.dumps(payload))
    disagreeing["cost_regimes"] = ["high"]  # regime names that the costs do not describe
    with pytest.raises(ValueError, match="cost_regimes must name exactly"):
        RunManifest.model_validate(disagreeing)

    # On disk, the edited manifest makes the validator reject the directory outright.
    (run.directory / MANIFEST_NAME).write_text(json.dumps(cheaper), encoding="utf-8")
    verdict = validate_run_directory(run.directory)
    assert verdict.finalized is False
    assert any("manifest.json invalid" in reason for reason in verdict.reasons)


def test_finalisation_refuses_a_writer_that_mutates_curve_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A value-mutating writer is caught: dates, counts and shape all still look right."""
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    original = evidence.write_dated_backtest_report

    def _mutate_equity(result: BacktestResult, out_dir: Path, **kwargs: Any) -> dict[str, Path]:
        paths = original(result, out_dir, **kwargs)
        frame = pl.read_parquet(paths["equity"])
        values = frame["equity"].to_list()
        values[1] = values[1] + 1.0  # one altered point; date column and row count untouched
        pl.DataFrame({"date": frame["date"], "equity": pl.Series(values, dtype=pl.Float64)}).write_parquet(paths["equity"])
        return cast("dict[str, Path]", paths)

    monkeypatch.setattr(evidence, "write_dated_backtest_report", _mutate_equity)
    with pytest.raises(ValueError, match=r"equity\.parquet value at row 1 does not reproduce"):
        _finalise(tmp_path)
    assert _run_dirs(tmp_path) == []  # refused before a manifest existed: no run directory

    def _mutate_drawdown(result: BacktestResult, out_dir: Path, **kwargs: Any) -> dict[str, Path]:
        paths = original(result, out_dir, **kwargs)
        frame = pl.read_parquet(paths["drawdown"])
        values = [value * 0.5 for value in frame["drawdown"].to_list()]  # still non-positive, still wrong
        pl.DataFrame({"date": frame["date"], "drawdown": pl.Series(values, dtype=pl.Float64)}).write_parquet(paths["drawdown"])
        return cast("dict[str, Path]", paths)

    monkeypatch.setattr(evidence, "write_dated_backtest_report", _mutate_drawdown)
    with pytest.raises(ValueError, match=r"drawdown\.parquet value at row .* does not reproduce"):
        _finalise(tmp_path)
    assert _run_dirs(tmp_path) == []


def test_publish_never_replaces_a_directory_created_before_the_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The publish claim is atomic: a directory that appears first is never replaced."""
    import equity_lake.backtesting.evidence as evidence  # noqa: PLC0415 - patch target

    scope = _scope()
    base_name = f"{_BUILD_TS.strftime('%Y%m%dT%H%M%SZ')}-{scope.run_id()[:12]}"
    target = tmp_path / "runs" / base_name
    real_mkdir = evidence.os.mkdir
    raced: list[str] = []

    def _racing_mkdir(path: Any, *args: Any, **kwargs: Any) -> None:
        """Simulate an outside actor winning the create race for the base name."""
        if Path(path).name == base_name and not raced:
            raced.append(base_name)
            real_mkdir(path)
            (Path(path) / "occupant.txt").write_text("pre-existing run", encoding="utf-8")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(evidence.os, "mkdir", _racing_mkdir)
    run = _finalise(tmp_path, scope=scope)

    assert raced == [base_name]  # the race really happened
    assert run.directory.name == f"{base_name}-2"  # the new run took the discriminator
    assert run.manifest.run_dir == run.directory.name
    assert validate_run_directory(run.directory).finalized

    # The pre-existing directory and its contents are untouched, and it was never
    # turned into a finalized run by the loser of the race.
    assert sorted(path.name for path in target.iterdir()) == ["occupant.txt"]
    assert (target / "occupant.txt").read_text(encoding="utf-8") == "pre-existing run"
    verdict = validate_run_directory(target)
    assert verdict.finalized is False
    assert verdict.reasons == ["manifest.json missing: the directory was never finalized"]


def test_validator_rejects_each_invalid_run_with_a_distinct_reason(tmp_path: Path) -> None:
    """Unparsable manifest, run_dir mismatch, size mismatch and a renamed run each fail distinctly."""
    unparsable = _finalise(tmp_path, scope=_scope(parameters={"lookback": 20}))
    (unparsable.directory / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    unparsable_verdict = validate_run_directory(unparsable.directory)

    wrong_name = _finalise(tmp_path, scope=_scope(parameters={"lookback": 21}))
    payload = json.loads((wrong_name.directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["run_dir"] = "20260101T000000Z-deadbeef0000"
    (wrong_name.directory / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
    wrong_name_verdict = validate_run_directory(wrong_name.directory)

    size_only = _finalise(tmp_path, scope=_scope(parameters={"lookback": 22}))
    manifest_path = size_only.directory / MANIFEST_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = next(entry for entry in payload["artifacts"] if entry["path"] == EQUITY_ARTIFACT)
    record["bytes"] = record["bytes"] + 1  # recorded size disagrees; the file's hash still matches
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    size_verdict = validate_run_directory(size_only.directory)

    renamed = _finalise(tmp_path, scope=_scope(parameters={"lookback": 23}))
    renamed_target = renamed.directory.with_name(renamed.directory.name + "-renamed")
    renamed.directory.rename(renamed_target)
    renamed_verdict = validate_run_directory(renamed_target)

    verdicts = [unparsable_verdict, wrong_name_verdict, size_verdict, renamed_verdict]
    assert [verdict.finalized for verdict in verdicts] == [False, False, False, False]
    assert all(verdict.reasons for verdict in verdicts)
    # Four failures, four distinct explanations (no generic "invalid" catch-all).
    assert len({frozenset(verdict.reasons) for verdict in verdicts}) == 4

    assert unparsable_verdict.manifest is None
    assert len(unparsable_verdict.reasons) == 1
    assert unparsable_verdict.reasons[0].startswith("manifest.json invalid:")

    assert any(f"artifact size mismatch: '{EQUITY_ARTIFACT}'" in reason for reason in size_verdict.reasons)
    assert not any("hash mismatch" in reason for reason in size_verdict.reasons)  # the size check stands alone

    for verdict in (wrong_name_verdict, renamed_verdict):
        assert len(verdict.reasons) == 1
        assert "does not match directory" in verdict.reasons[0]
    assert not renamed.directory.exists()  # a renamed run is not silently accepted under its new name


def test_cli_arena_run_exits_one_when_the_evidence_writer_raises(tmp_path: Path) -> None:
    """CLI exit mapping only: a raising artifact/evidence writer makes `arena run` exit 1.

    The run is stubbed, not computed: ``run_arena`` returns a small valid outcome with
    one dated run (so the writer *would* publish evidence) and ``write_arena_artifacts``
    raises. This test asserts the documented CLI mapping (writer exception -> exit 1,
    visible message, no success banner), not what the writer does internally.
    """
    from equity_lake.backtesting.arena import ArenaOutcome, ArenaRun  # noqa: PLC0415 - CLI seam
    from equity_lake.cli.__main__ import app  # noqa: PLC0415 - CLI seam

    outcome = ArenaOutcome(
        runs=[ArenaRun(strategy="trend_following", cost_regime="realistic", result=_result())],
        data=pl.DataFrame(
            {
                "ticker": ["AAPL"] * len(IRREGULAR_DATES),
                "date": IRREGULAR_DATES,
                "close": list(IRREGULAR_VALUES),
            }
        ),
        benchmark=pl.DataFrame({"date": IRREGULAR_DATES, "equity": list(IRREGULAR_VALUES)}),
        initial_cash=100_000.0,
        requested=[("trend_following", "realistic")],
    )
    args = [
        "arena",
        "run",
        "--start-date",
        IRREGULAR_DATES[0].isoformat(),
        "--end-date",
        IRREGULAR_DATES[-1].isoformat(),
        "--tickers",
        "AAPL,MSFT",
        "--output-dir",
        str(tmp_path),
    ]
    with (
        patch("equity_lake.backtesting.arena.run_arena", return_value=outcome),
        patch("equity_lake.backtesting.report.write_arena_artifacts_with_evidence", side_effect=OSError("evidence publish failed")),
    ):
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 1, result.stdout
    assert "artifact write failed" in result.stdout
    assert "evidence publish failed" in result.stdout
    assert "Arena complete" not in result.stdout
    assert list(tmp_path.iterdir()) == []  # the writer never ran, so nothing was published


# ---------------------------------------------------------------------------
# Fix round 3: recorded (not guessed) applied costs, and the curve refusal gate
# ---------------------------------------------------------------------------


@requires_engine
@pytest.mark.slow
def test_writer_records_the_costs_the_arena_actually_applied(tmp_path: Path) -> None:
    """A ``market_costs`` override reaches the manifest verbatim, never as a default.

    Regression (fix round 3, finding 1): the writer used to re-resolve
    ``MARKET_COST_DEFAULTS`` instead of recording what the engine was configured
    with, so a non-default cost application was published under the default
    fee/tax pair. The arena now records the resolved pair on each completed run and
    the writer records exactly that.
    """
    override = {"us_equity": {"fee_ratio": 0.0123, "tax_ratio": 0.0456}}
    outcome = _arena_outcome(strategies=["mean_reversion"], cost_regimes=["realistic"], market_costs=override)

    assert [run.applied_costs for run in outcome.runs] == [{"fee_ratio": 0.0123, "tax_ratio": 0.0456}]

    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert summary.skipped_undated == {}
    assert summary.skipped_unrecorded_costs == {}
    assert len(summary.finalized) == 1
    manifest = summary.finalized[0].manifest
    assert [(cost.regime, cost.fee_ratio, cost.tax_ratio) for cost in manifest.costs] == [("realistic", 0.0123, 0.0456)]
    # Not the venue default this regime would otherwise resolve to.
    default = MARKET_COST_DEFAULTS["us_equity"]
    assert (manifest.costs[0].fee_ratio, manifest.costs[0].tax_ratio) != (default["fee_ratio"], default["tax_ratio"])
    # The recorded application is part of the scope, so the manifest re-verifies it.
    assert validate_run_directory(summary.finalized[0].directory).finalized
    # Legacy artifacts are unchanged by the recorded costs.
    _assert_no_legacy_writes(tmp_path, outcome)


@requires_engine
@pytest.mark.slow
def test_writer_records_the_resolved_default_costs(tmp_path: Path) -> None:
    """A default arena run records the resolved venue defaults, not a bare regime name."""
    outcome = _arena_outcome(strategies=["mean_reversion"], cost_regimes=["realistic"])
    expected = _resolve_regime_costs("realistic", ("us_equity",), MARKET_COST_DEFAULTS)

    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert len(summary.finalized) == 1
    manifest = summary.finalized[0].manifest
    assert [(cost.regime, cost.fee_ratio, cost.tax_ratio) for cost in manifest.costs] == [("realistic", expected["fee_ratio"], expected["tax_ratio"])]
    assert manifest.costs[0].fee_ratio == expected["fee_ratio"] > 0.0  # a real fee, not 0.0 by accident
    assert validate_run_directory(summary.finalized[0].directory).finalized


def test_writer_skips_evidence_when_the_applied_costs_were_not_recorded(tmp_path: Path) -> None:
    """A run without recorded costs is skipped and reported — the writer never guesses.

    Fix round 3, finding 1: a legacy/hand-built ``ArenaRun`` (``applied_costs is
    None``) has no record of the fee/tax pair its engine was configured with, so the
    writer must not fall back to ``MARKET_COST_DEFAULTS``. The run is skipped through
    the same reported-skip mechanism as an undated run: its legacy artifacts and the
    cards are still written, no run directory or manifest claims a cost application,
    and the reason is in the evidence summary.
    """
    data = pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(IRREGULAR_DATES),
            "date": IRREGULAR_DATES,
            "close": list(IRREGULAR_VALUES),
        }
    )
    benchmark = equal_weight_buyhold(data, initial_cash=100_000.0)
    runs = [
        ArenaRun("trend_following", "realistic", _result()),
        ArenaRun("trend_following", "zero", _result()),
    ]
    assert [run.applied_costs for run in runs] == [None, None]  # nothing recorded: no cost application to claim
    outcome = ArenaOutcome(
        runs=runs,
        data=data,
        benchmark=benchmark,
        initial_cash=100_000.0,
        requested=[("trend_following", "realistic"), ("trend_following", "zero")],
    )

    cards, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=date(2026, 8, 4))

    assert cards  # the write itself still succeeds
    assert summary.finalized == []  # no manifest may claim a guessed cost application
    assert summary.skipped_undated == {}  # both runs carried backend dates
    assert set(summary.skipped_unrecorded_costs) == {"trend_following__realistic", "trend_following__zero"}
    for reason in summary.skipped_unrecorded_costs.values():
        assert reason == APPLIED_COSTS_NOT_RECORDED_SKIP_REASON
        assert "applied costs not recorded" in reason
        assert "never guesses a market default" in reason
    assert _run_dirs(tmp_path) == []  # no run directory for either pair
    assert not (tmp_path / "runs").exists()  # the evidence root was never even created
    # The legacy (pair-keyed) artifacts for both pairs are still written.
    _assert_no_legacy_writes(tmp_path, outcome)


@pytest.mark.parametrize(("bad_value", "reason"), [(float("nan"), "non-finite"), (float("inf"), "non-finite"), (float("-inf"), "non-finite")])
def test_finalisation_refuses_a_non_finite_curve_value(tmp_path: Path, bad_value: float, reason: str) -> None:
    """A NaN/±inf equity point is refused before comparison, leaving no run directory.

    The refusal is **deliberate**: a finalized run is a checksum-certified claim that
    the curve was measured, and NaN/inf is not a measurement (never persist NaN as
    measured evidence). The comparator alone would accept ``NaN == NaN`` and certify
    it, so the gate runs first and names the artifact and row.
    """
    values = [100_000.0, bad_value, 99_250.0, 101_000.0, 102_750.0, 103_000.0]
    result = _result(values=values)

    with pytest.raises(ValueError, match=rf"equity\.parquet value at row 1 is {reason}") as excinfo:
        _finalise(tmp_path, result=result)

    assert "measured evidence" in str(excinfo.value)
    assert _run_dirs(tmp_path) == []  # no manifest, no partial run directory


def test_finalisation_refuses_a_null_curve_value(tmp_path: Path) -> None:
    """A null equity point is refused as a null (not as a drawdown artefact).

    The refusal is **deliberate**: a missing point is not a measurement, and it is
    never zero-filled or dropped so the run can be published. Naming the null keeps
    the reason honest — the previous path reported it as a positive drawdown.
    """
    values: list[Any] = [100_000.0, None, 99_250.0, 101_000.0, 102_750.0, 103_000.0]
    result = _result(values=cast("list[float]", values))
    assert result.equity_curve.null_count() == 1

    with pytest.raises(ValueError, match=r"equity\.parquet value at row 1 is null") as excinfo:
        _finalise(tmp_path, result=result)

    assert "measured evidence" in str(excinfo.value)
    assert _run_dirs(tmp_path) == []


def test_a_finite_curve_still_finalises(tmp_path: Path) -> None:
    """The refusal gate is not a blanket rejection: a measured curve still finalises."""
    run = _finalise(tmp_path)

    assert validate_run_directory(run.directory).finalized
    assert _run_dirs(tmp_path) == [run.directory]
    assert pl.read_parquet(run.directory / EQUITY_ARTIFACT)["equity"].to_list() == IRREGULAR_VALUES


# ---------------------------------------------------------------------------
# Fix round 4: an unavailable metric is recorded, never persisted
# ---------------------------------------------------------------------------


def test_non_finite_metric_is_recorded_as_unavailable_and_not_persisted(tmp_path: Path) -> None:
    """A finite curve with NaN/inf metrics finalises with ``null`` metrics, not ``NaN``.

    Regression (fix round 4, finding 1): the dated writer used a permissive
    ``json.dumps`` and finalisation only checked that ``metrics.json`` parsed, so a
    finite curve carrying ``sharpe_ratio=NaN`` published a literal ``NaN`` even though
    the manifest labelled that metric ``non_finite``. An unavailable number is now
    recorded as unavailable: the key stays, the value is ``null``, and the manifest
    records ``non_finite``. A finite metric in the same run stays ``measured`` with
    its value, and a metric the run never measured stays ``missing``.
    """
    result = _result(metrics={"total_return": float("inf"), "sharpe_ratio": float("nan"), "cagr": 0.05})

    run = _finalise(tmp_path, result=result)

    assert validate_run_directory(run.directory).finalized  # the run is measurable evidence
    raw = (run.directory / METRICS_ARTIFACT).read_text(encoding="utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    payload = json.loads(raw)
    assert payload["metrics"]["sharpe_ratio"] is None
    assert payload["metrics"]["total_return"] is None
    assert payload["metrics"]["cagr"] == 0.05

    availability = {entry.metric: entry for entry in run.manifest.metric_availability}
    assert availability["sharpe_ratio"].status == "non_finite"
    assert availability["total_return"].status == "non_finite"
    assert availability["sharpe_ratio"].value is None  # omitted, never a coerced 0
    assert availability["total_return"].value is None
    assert availability["cagr"].status == "measured"
    assert availability["cagr"].value == 0.05
    assert availability["volatility"].status == "missing"
    assert availability["volatility"].value is None
    assert _run_dirs(tmp_path) == [run.directory]


def test_tampered_metrics_with_a_nan_token_is_refused(tmp_path: Path) -> None:
    """A persisted ``NaN`` token, or a null metric without availability, is not a run.

    The strict finalisation validator parses ``metrics.json`` with the non-finite
    literals rejected and cross-checks every metric against the recorded
    availability, so evidence edited after the fact can never be read back as a
    finalized run — and each refusal names its own reason.
    """
    run = _finalise(tmp_path, result=_result(metrics={"total_return": 0.03, "sharpe_ratio": float("nan")}))
    metrics_path = run.directory / METRICS_ARTIFACT
    original_metrics = metrics_path.read_text(encoding="utf-8")
    assert '"sharpe_ratio": null' in original_metrics  # written as unavailable, not as NaN
    assert validate_run_directory(run.directory).finalized

    metrics_path.write_text(original_metrics.replace('"sharpe_ratio": null', '"sharpe_ratio": NaN'), encoding="utf-8")
    verdict = validate_run_directory(run.directory)
    assert not verdict.finalized
    assert verdict.manifest is None
    assert any("non-finite JSON token" in reason for reason in verdict.reasons), verdict.reasons
    assert not is_finalized_run(run.directory)

    metrics_path.write_text(original_metrics, encoding="utf-8")  # restored byte-for-byte
    manifest_path = run.directory / MANIFEST_NAME
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["metric_availability"] = [entry for entry in manifest_payload["metric_availability"] if entry["metric"] != "sharpe_ratio"]
    manifest_path.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")
    verdict = validate_run_directory(run.directory)
    assert not verdict.finalized
    assert verdict.manifest is None
    assert any("no availability entry" in reason for reason in verdict.reasons), verdict.reasons
    assert not is_finalized_run(run.directory)


# ---------------------------------------------------------------------------
# Fix round 5: a non-finite trade value refuses the run, it is never published
# ---------------------------------------------------------------------------


def test_finalisation_refuses_a_non_finite_trade_value(tmp_path: Path) -> None:
    """A ``NaN`` trade price refuses the run instead of publishing a JSON ``NaN``.

    Regression (fix round 5, finding 1): the dated writer used a permissive
    ``json.dumps`` for ``trades.json``, so a finite curve carrying
    ``trades=[{"price": NaN}]`` published a literal ``NaN`` and the run validator
    accepted it. A non-finite price/fee/quantity is a data defect, not an
    unavailable measurement, so the run is **refused**: the refusal names the
    artifact, entry and field, no run directory is published, and the legacy
    artifacts that were already written stay byte-for-byte untouched.
    """
    legacy = tmp_path / "trend_following__realistic"
    write_backtest_report(_result(), legacy)  # the pre-existing undated pair
    legacy_bytes = {path.name: path.read_bytes() for path in sorted(legacy.iterdir())}
    assert "trades.json" in legacy_bytes  # legacy artifacts exist before the attempt

    result = _result(
        trades=[
            {"ticker": "AAPL", "side": "buy", "quantity": 10.0, "price": 101.5, "fee": 1.42},
            {"ticker": "MSFT", "side": "buy", "quantity": 5.0, "price": float("nan"), "fee": 0.71},
        ]
    )

    with pytest.raises(ValueError, match=r"trades\.json entry 1 field 'price' is non-finite") as excinfo:
        _finalise(tmp_path, result=result)

    assert "data defect" in str(excinfo.value)
    assert _run_dirs(tmp_path) == []  # no manifest, no published run directory
    assert not [path for path in (tmp_path / "runs").iterdir()]  # no leftover temporary run
    # The legacy artifacts written before the refusal are untouched.
    assert {path.name: path.read_bytes() for path in sorted(legacy.iterdir())} == legacy_bytes


def test_valid_trades_with_nulls_still_finalise(tmp_path: Path) -> None:
    """An open position's ``null`` pnl/exit price finalises and stays ``null``.

    A missing realised pnl is not a non-finite number: it is an honestly unavailable
    measurement, encoded as JSON ``null``. Only non-finite values refuse the run, so
    the refusal gate must never turn a legitimate open position into a rejection.
    """
    trades: list[dict[str, Any]] = [
        {"ticker": "AAPL", "side": "buy", "quantity": 10.0, "price": 101.5, "fee": 1.42, "pnl": None, "exit_price": None},
        {"ticker": "MSFT", "side": "sell", "quantity": 5.0, "price": 103.0, "fee": 0.71, "pnl": 7.5, "exit_price": 103.0},
    ]

    run = _finalise(tmp_path, result=_result(trades=trades))

    assert validate_run_directory(run.directory).finalized
    raw = (run.directory / TRADES_ARTIFACT).read_text(encoding="utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    written = json.loads(raw)
    assert written == trades  # nulls stay nulls, measured values are unchanged
    assert written[0]["pnl"] is None
    assert written[0]["exit_price"] is None
    assert _run_dirs(tmp_path) == [run.directory]


def test_open_position_record_writes_unmeasured_fields_as_null(tmp_path: Path) -> None:
    """The backend's still-open record is written with ``null``, not ``NaN``.

    ``VectorBacktestEngine`` encodes a position still open at the end of the window as
    a row whose ``entry_date``/``exit_date`` are null and whose ``entry_price``/``pnl``
    are ``NaN``. An open position has no realised pnl or exit price, so those three
    outcome fields are unavailable and are written as ``null`` (the same encoding an
    explicit ``None`` gets). The exemption is deliberately narrow: the same row's
    finite fields are unchanged, and a non-finite value in any other field would still
    refuse the run.
    """
    trades: list[dict[str, Any]] = [
        {
            "ticker": "T04",
            "entry_date": None,
            "exit_date": None,
            "entry_price": float("nan"),
            "exit_price": 0,
            "shares": 0.95,
            "pnl": float("nan"),
            "action": "SELL",
        }
    ]

    run = _finalise(tmp_path, result=_result(trades=trades))

    assert validate_run_directory(run.directory).finalized
    raw = (run.directory / TRADES_ARTIFACT).read_text(encoding="utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    written = json.loads(raw)[0]
    assert written["entry_price"] is None
    assert written["pnl"] is None
    assert written["entry_date"] is None and written["exit_date"] is None
    assert written["shares"] == 0.95  # finite fields untouched
    assert written["exit_price"] == 0


def test_tampered_trades_with_a_nan_token_is_refused(tmp_path: Path) -> None:
    """A ``NaN`` token in ``trades.json`` makes the directory not-a-run.

    The validator parses trades strictly, so a trade file edited after the fact is
    reported with its own distinct reason (a non-finite JSON token naming the
    artifact) instead of being read back as finalized evidence.
    """
    trades: list[dict[str, Any]] = [{"ticker": "AAPL", "side": "buy", "quantity": 10.0, "price": 101.5, "fee": 1.42, "pnl": None, "exit_price": None}]
    run = _finalise(tmp_path, result=_result(trades=trades))
    trades_path = run.directory / TRADES_ARTIFACT
    original_trades = trades_path.read_text(encoding="utf-8")
    assert validate_run_directory(run.directory).finalized

    trades_path.write_text(original_trades.replace('"price": 101.5', '"price": NaN'), encoding="utf-8")
    verdict = validate_run_directory(run.directory)

    assert not verdict.finalized
    assert verdict.manifest is None
    assert any("trades.json carries a non-finite JSON token 'NaN'" in reason for reason in verdict.reasons), verdict.reasons
    assert not is_finalized_run(run.directory)


def test_finalisation_refuses_a_non_finite_card_value(tmp_path: Path) -> None:
    """A card metric carrying ``NaN`` refuses the run instead of silently becoming ``null``.

    Pydantic's JSON mode encodes a non-finite float as ``null`` by default, i.e. it
    would *sanitise* a data defect into a measured-looking card. The card payload is
    therefore refused before it is written, naming the artifact and field, so no run
    directory is published.
    """
    card = FindingCard(
        id="strategy-comparison",
        axis="strategy",
        claim="trend following beats mean reversion",
        verdict="positive",
        conclusion="higher Sharpe at realistic cost",
        metrics={"sharpe": float("nan")},
        run_date=date(2026, 8, 4),
    )

    with pytest.raises(ValueError, match=r"strategy-comparison\.json field 'metrics\.sharpe' is non-finite") as excinfo:
        _finalise(tmp_path, cards=[card])

    assert "data defect" in str(excinfo.value)
    assert _run_dirs(tmp_path) == []
    assert not [path for path in (tmp_path / "runs").iterdir()]


def test_tampered_card_with_a_nan_token_is_refused(tmp_path: Path) -> None:
    """A ``NaN`` token in a recorded card JSON makes the directory not-a-run.

    Cards are recorded as artifacts, so the validator parses them strictly too: a
    card edited after the fact is reported with its own distinct reason naming the
    card artifact, instead of being read back as finalized evidence.
    """
    card = FindingCard(
        id="strategy-comparison",
        axis="strategy",
        claim="trend following beats mean reversion",
        verdict="positive",
        conclusion="higher Sharpe at realistic cost",
        metrics={"sharpe": 1.1},
        run_date=date(2026, 8, 4),
    )
    run = _finalise(tmp_path, cards=[card])
    card_artifact = run.directory / "strategy-comparison.json"
    original_card = card_artifact.read_text(encoding="utf-8")
    assert '"sharpe": 1.1' in original_card
    assert validate_run_directory(run.directory).finalized

    card_artifact.write_text(original_card.replace('"sharpe": 1.1', '"sharpe": NaN'), encoding="utf-8")
    verdict = validate_run_directory(run.directory)

    assert not verdict.finalized
    assert verdict.manifest is None
    assert any("strategy-comparison.json carries a non-finite JSON token 'NaN'" in reason for reason in verdict.reasons), verdict.reasons
    assert not is_finalized_run(run.directory)
