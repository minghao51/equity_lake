"""Tests for the bounded as-of refit preload (handoff R5 / decision D2).

The helper must (a) spread at most `MAX_REFITS` boundaries evenly over the
observed window and partition it without overlap, (b) refuse an out-of-bounds
refit count before anything is trained or written, (c) refuse at plan time a
window whose calendar span outlives the model age bound, (d) skip a boundary
whose training summary is not ``verified`` with the R3 message and no prediction
call, (e) fall back to the artifact sidecar when a fresh model is reused, (f)
fail when every boundary is unvalidated, (g) train each boundary's model before
predicting any session that could use it, (h) count only sessions that actually
persisted a row and report the rest with their status, and (i) re-run without
duplicating rows.

Training and inference are stubbed by point for most unit tests; two tests go
through the real forecaster/writer seams: the last one runs the real
``run_prediction_job`` + ``merge_delta`` path against a tmp lake, and the reuse
test drives the real ``PriceForecaster`` into its ``reused_fresh_model`` branch.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.core.availability import (  # type: ignore[import-untyped]
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    ActionCoverageProof,
    action_coverage_feature_fields,
    source_observable_session,
)
from equity_lake.core.instruments import (  # type: ignore[import-untyped]
    IDENTITY_SCHEMA_VERSION,
    InstrumentKey,
)
from equity_lake.core.schemas import (  # type: ignore[import-untyped]
    FEATURE_SCHEMA_VERSION,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)
from equity_lake.devtools.refit_preload import (  # type: ignore[import-untyped]
    MAX_REFITS,
    NO_VALIDATION_STATE_REASON,
    RefitPreloadError,
    boundary_windows,
    main,
    refit_boundaries,
    refit_preload,
)
from equity_lake.ml.forecasting import (  # type: ignore[import-untyped]
    DEFAULT_MAX_MODEL_AGE_DAYS,
    MISSING_VALUE_POLICY,
    MODEL_PROVENANCE_VERSION,
    PriceForecaster,
)
from equity_lake.storage.delta import read_delta  # type: ignore[import-untyped]

_DAYS = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(10)]
_ARTIFACT_SHA256 = "0123456789abcdef" * 4
_TICKER = "AAPL"


def _bronze_lake(tmp_path: Path, days: list[date]) -> Path:
    table = tmp_path / "01_bronze" / "market_data" / "us_equity"
    table.mkdir(parents=True)
    write_deltalake(str(table), pl.DataFrame({"ticker": [_TICKER] * len(days), "date": days}).to_arrow(), mode="append")
    return tmp_path


def _scope_lake(monkeypatch: pytest.MonkeyPatch, lake: Path) -> Path:
    """Point BOTH seams at *lake*: session reads and ``run_prediction_job`` writes.

    The module refuses a ``lake_dir`` that differs from ``storage.delta.LAKE_DIR``
    (a silent read/write split), so the lake must be scoped the way
    ``test_prediction_provenance_e2e`` does it.
    """
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", lake)
    return lake


def _install_forecaster(monkeypatch: pytest.MonkeyPatch, forecaster_class: type) -> None:
    """Install *forecaster_class* as ``PriceForecaster`` for the lazy imports.

    The fake keeps the real module's age-policy constant: the helper reads the
    ceiling from the same seam it imports the forecaster through.
    """
    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = forecaster_class
    fake_module.__dict__["DEFAULT_MAX_MODEL_AGE_DAYS"] = DEFAULT_MAX_MODEL_AGE_DAYS
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)


def _forecaster_class(
    *,
    events: list[tuple[str, date]],
    unvalidated: dict[date, str] | None = None,
    reused: Mapping[date, Mapping[str, Any] | None] | None = None,
) -> type:
    """Fake forecaster recording ``("train", boundary)`` and its validation verdict.

    ``reused`` simulates the real ``reused_fresh_model`` branch: the in-memory
    summary carries no ``validation_status`` and the verdict must come from the
    artifact sidecar returned by ``load_training_summary`` (``None`` = missing).
    """

    class _FakeForecaster:
        def __init__(self, model_dir: str | None = None, model_mode: str = "v1_direction", **_kwargs: Any) -> None:
            self.model_dir = model_dir
            self.model_mode = model_mode
            self._last: dict[str, Any] | None = None

        def train_model(self, ticker: str | InstrumentKey, start_date: date, end_date: date, **kwargs: Any) -> None:
            del ticker, start_date
            events.append(("train", end_date))
            self.training_kwargs = kwargs
            if reused is not None and end_date in reused:
                events[-1] = ("reuse", end_date)
                self._last = {"status": "reused_fresh_model", "trained_on": end_date.isoformat()}
                return
            reason = (unvalidated or {}).get(end_date)
            if reason is None:
                self._last = {"validation_status": "verified", "validation_unavailable_reason": None, "validation_fold_count": 3}
            else:
                self._last = {"validation_status": "unavailable", "validation_unavailable_reason": reason, "validation_fold_count": 0}

        def load_training_summary(self, ticker: str, as_of_date: date) -> dict[str, Any] | None:
            del ticker
            summary = (reused or {}).get(as_of_date)
            return dict(summary) if summary is not None else None

        def last_training_summary(self) -> dict[str, Any] | None:
            return self._last

        def close(self) -> None:
            return None

    return _FakeForecaster


def _record_predictions(
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, date]],
    *,
    expired: dict[date, str] | None = None,
) -> list[tuple[date, tuple[str, ...]]]:
    calls: list[tuple[date, tuple[str, ...]]] = []

    def fake_run_prediction_job(*, trading_date: date, tickers: Any, **_kwargs: Any) -> tuple[bool, dict[str, dict[str, Any]]]:
        events.append(("predict", trading_date))
        calls.append((trading_date, tuple(tickers)))
        status = (expired or {}).get(trading_date)
        if status is not None:
            failure = {"success": False, "persisted": False, "status": status, "error_type": "ModelExpiredError", "error": "stubbed"}
            return False, {ticker: dict(failure) for ticker in tickers}
        return True, {ticker: {"success": True, "persisted": True} for ticker in tickers}

    monkeypatch.setattr("equity_lake.devtools.refit_preload.run_prediction_job", fake_run_prediction_job)
    return calls


def _last_json(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output.strip().splitlines()[-1]))


def _keep_logging_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop ``main()`` from reconfiguring global structlog for the whole test process.

    ``setup_structured_logging()`` installs a new processor list; a test logger
    cached before that call keeps the old list, which makes ``capture_logs`` in
    other files silently see nothing. CLI tests therefore stub the setup.
    """
    monkeypatch.setattr("equity_lake.core.logging.setup_structured_logging", lambda: None)


# --- boundary planning ------------------------------------------------------


def test_boundary_selection_spreads_evenly_and_partitions_the_window() -> None:
    sessions = _DAYS

    boundaries = refit_boundaries(sessions, 3)

    # m = 3 over n = 10 -> indices floor(i * 9 / 2) = 0, 4, 9.
    assert boundaries == [sessions[0], sessions[4], sessions[9]]
    assert boundaries == sorted(boundaries)
    assert boundaries[-1] == sessions[-1]  # the tail is always covered
    windows = boundary_windows(boundaries, sessions)
    assert [window[0] for _, window in windows] == boundaries
    assert [session for _, window in windows for session in window] == sessions  # covers every session exactly once
    for (_, older), (_, newer) in itertools.pairwise(windows):
        assert older[-1] < newer[0]  # non-overlapping partition


def test_a_single_refit_is_the_window_s_first_session_and_owns_the_whole_window() -> None:
    assert refit_boundaries(_DAYS, 1) == [_DAYS[0]]

    windows = boundary_windows([_DAYS[0]], _DAYS)

    assert windows == [(_DAYS[0], tuple(_DAYS))]
    # A last-session boundary would leave every earlier session unpredicted.
    assert windows[0][1][0] == _DAYS[0]
    assert len(windows[0][1]) == len(_DAYS)


def test_more_requested_refits_than_sessions_yields_one_per_session() -> None:
    short_window = _DAYS[-5:]
    assert refit_boundaries(short_window, MAX_REFITS) == short_window


def test_refits_are_placed_before_any_window_span_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A requested spread that fits the age bound is never refused."""
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    _record_predictions(monkeypatch, events)

    report = refit_preload(days=4, refits=2, end=_DAYS[3], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    assert report.max_model_age_days == DEFAULT_MAX_MODEL_AGE_DAYS
    assert [boundary.boundary for boundary in report.boundaries] == [_DAYS[0], _DAYS[3]]
    assert report.complete is True


def test_plan_time_refusal_names_the_boundary_span_and_required_refits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A window that outlives the age bound must be refused before any work."""
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    calls = _record_predictions(monkeypatch, events)
    models = tmp_path / "models"

    # days=10, refits=2 -> boundaries s0 and s9; the first window spans 8 days
    # (s0..s8), and only a 3-boundary spread keeps every window within 7 days.
    with pytest.raises(RefitPreloadError) as excinfo:
        refit_preload(days=10, refits=2, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=models)

    message = str(excinfo.value)
    assert f"boundary {_DAYS[0].isoformat()}..{_DAYS[8].isoformat()} spans 8 days" in message
    assert f"above --max-model-age-days {DEFAULT_MAX_MODEL_AGE_DAYS}" in message
    assert "use at least 3 refits for this window or raise --max-model-age-days" in message
    # Nothing was trained, predicted, or written.
    assert events == []
    assert calls == []
    assert not models.exists()


def test_the_documented_days_120_refits_6_example_is_refused_up_front(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(120)]
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, sessions))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    calls = _record_predictions(monkeypatch, events)

    with pytest.raises(RefitPreloadError) as excinfo:
        refit_preload(days=120, refits=6, end=sessions[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    message = str(excinfo.value)
    assert "spans 22 days" in message  # boundaries land on indices 0, 23, 47, 71, 95, 119
    # 120 consecutive sessions cannot be covered by 12 evenly spread boundaries at a 7-day bound.
    assert f"no evenly spread plan of at most {MAX_REFITS} refits covers this window" in message
    assert "one boundary can own at most 7 calendar days" in message
    assert events == [] and calls == []
    assert not (tmp_path / "models").exists()


def test_refit_bound_over_the_maximum_or_below_the_minimum_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    models = tmp_path / "models"

    def _forbidden(**_kwargs: Any) -> Any:
        raise AssertionError("an out-of-bounds refit request must not predict")

    monkeypatch.setattr("equity_lake.devtools.refit_preload.run_prediction_job", _forbidden)

    with pytest.raises(RefitPreloadError, match=str(MAX_REFITS)):
        refit_preload(days=3, refits=MAX_REFITS + 1, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=models)
    for refits in (0, -1):
        with pytest.raises(RefitPreloadError, match="at least 1"):
            refit_preload(days=3, refits=refits, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=models)

    assert not models.exists()
    assert not (lake / "04_platinum").exists()


def test_the_age_bound_cannot_be_widened_past_the_inference_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))

    with pytest.raises(RefitPreloadError, match="max-model-age-days must be an integer in"):
        refit_preload(
            days=2,
            refits=1,
            end=_DAYS[1],
            tickers=[_TICKER],
            max_model_age_days=DEFAULT_MAX_MODEL_AGE_DAYS + 1,
            lake_dir=lake,
            model_dir=tmp_path / "models",
        )
    assert events == []
    assert not (tmp_path / "models").exists()


def test_a_lake_dir_that_differs_from_the_storage_write_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path / "canonical", _DAYS))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    calls = _record_predictions(monkeypatch, events)

    with pytest.raises(RefitPreloadError, match="differs from storage.delta.LAKE_DIR"):
        refit_preload(days=2, refits=1, end=_DAYS[1], tickers=[_TICKER], lake_dir=elsewhere, model_dir=tmp_path / "models")

    assert events == [] and calls == []
    assert lake.exists()


# --- validation honesty -----------------------------------------------------


def test_unvalidated_boundary_skips_its_sessions_and_names_the_r3_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    reason = "no purged walk-forward fold fits in 3 labelled rows"
    # The window is the last five stored sessions; days=5, refits=2 -> its first
    # and last session. The last one is the unvalidated boundary.
    window = _DAYS[-5:]
    _install_forecaster(monkeypatch, _forecaster_class(events=events, unvalidated={window[-1]: reason}))
    calls = _record_predictions(monkeypatch, events)

    report = refit_preload(days=5, refits=2, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    assert [call[0] for call in calls] == list(window[:-1])  # the unvalidated boundary never predicts
    assert [boundary.boundary for boundary in report.boundaries] == [window[0], window[-1]]
    skipped = report.boundaries[1]
    assert skipped.reason == f"validation: unavailable ({reason})"
    assert skipped.validation_status == "unavailable"
    assert skipped.sessions == (window[-1],)
    assert skipped.predictions_persisted == 0
    assert [failure.session for failure in skipped.missing] == [window[-1]]  # skipped sessions are reported missing
    assert report.applied == 1
    assert report.skipped == 1
    assert report.sessions_predicted == 4  # only the four sessions of the applied boundary
    assert report.sessions_missing == 1
    assert report.complete is False
    payload = report.to_payload()
    assert payload["boundaries"][1]["reason"] == f"validation: unavailable ({reason})"
    assert payload["boundaries"][1]["missing"] == [{"session": window[-1].isoformat(), "reason": f"validation: unavailable ({reason})"}]
    assert payload["refits_skipped"] == 1


def test_a_reused_model_verdict_comes_from_the_artifact_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``reused_fresh_model`` has no verdict of its own; the sidecar supplies it."""
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    sidecars = {_DAYS[-2]: {"validation_status": "verified", "validation_unavailable_reason": None, "validation_fold_count": 2}}
    _install_forecaster(monkeypatch, _forecaster_class(events=events, reused=sidecars))
    _record_predictions(monkeypatch, events)

    report = refit_preload(days=2, refits=1, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    boundary = report.boundaries[0]
    assert ("reuse", _DAYS[-2]) in events  # a fresh model was reused, not retrained
    assert ("train", _DAYS[-2]) not in events
    assert boundary.validation_status == "verified"  # only the sidecar can supply this
    assert boundary.reason is None
    assert report.applied == 1
    assert report.complete is True


def test_a_reused_model_without_a_sidecar_says_so_instead_of_none_validation_folds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events, reused={_DAYS[-2]: None}))
    _record_predictions(monkeypatch, events)

    with pytest.raises(RefitPreloadError) as excinfo:
        refit_preload(days=2, refits=1, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    message = str(excinfo.value)
    assert "None validation folds" not in message
    assert f"validation: unavailable ({NO_VALIDATION_STATE_REASON})" in message


def test_every_boundary_unvalidated_fails_with_the_r3_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    reason = "no purged walk-forward fold fits in 2 labelled rows"
    _install_forecaster(monkeypatch, _forecaster_class(events=events, unvalidated={day: reason for day in _DAYS}))
    calls = _record_predictions(monkeypatch, events)

    with pytest.raises(RefitPreloadError) as excinfo:
        refit_preload(days=4, refits=2, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    message = str(excinfo.value)
    assert "every refit boundary was skipped" in message
    assert f"validation: unavailable ({reason})" in message
    assert calls == []  # never predict from an unvalidated model


def test_training_for_a_boundary_precedes_the_predictions_that_use_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    _record_predictions(monkeypatch, events)

    report = refit_preload(days=6, refits=3, end=_DAYS[-1], tickers=[_TICKER], lake_dir=lake, model_dir=tmp_path / "models")

    window = _DAYS[-6:]
    boundaries = [boundary.boundary for boundary in report.boundaries]
    assert boundaries == [window[0], window[2], window[5]]
    train_index = {event_date: index for index, (kind, event_date) in enumerate(events) if kind == "train"}
    predict_index = {event_date: index for index, (kind, event_date) in enumerate(events) if kind == "predict"}
    assert set(predict_index) == set(window)
    for boundary in boundaries:
        for session in window:
            if session >= boundary:
                # A session may only be predicted after its as-of model exists.
                assert train_index[boundary] < predict_index[session]
            else:
                # An earlier session is never predicted with a later boundary's model.
                assert predict_index[session] < train_index[boundary]


def test_predictions_are_persisted_only_through_run_prediction_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    calls = _record_predictions(monkeypatch, events)

    refit_preload(days=3, refits=1, end=_DAYS[-1], tickers=[_TICKER, "MSFT"], lake_dir=lake, model_dir=tmp_path / "models")

    # days=3, refits=1 -> the first session is the boundary and owns all three.
    assert calls == [(_DAYS[-3], (_TICKER, "MSFT")), (_DAYS[-2], (_TICKER, "MSFT")), (_DAYS[-1], (_TICKER, "MSFT"))]
    # The module owns no second write path: the only writer it names is the stage.
    source = Path(__file__).resolve().parents[2] / "src" / "equity_lake" / "devtools" / "refit_preload.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in ("merge_delta", "write_delta", "write_deltalake"):
        assert forbidden not in text, f"{forbidden} must not appear: persistence goes through run_prediction_job"


def test_help_text_reports_the_bounds_and_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for fragment in (
        "--days",
        "--refits",
        "--lookback-days",
        "--train-window",
        "--test-window",
        "--embargo-window",
        "--max-model-age-days",
        f"1..{MAX_REFITS}",
    ):
        assert fragment in help_text
    assert "~15s" in help_text
    assert str(DEFAULT_MAX_MODEL_AGE_DAYS) in help_text


def test_make_refit_preload_target_is_bounded_and_offline() -> None:
    root = Path(__file__).resolve().parents[2]
    output = subprocess.run(
        ["make", "--no-print-directory", "-n", "refit-preload"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout

    assert "uv run python -m equity_lake.devtools.refit_preload" in output
    assert "--days 60" in output  # shared bounded default, overridable with DAYS=
    assert "--refits 12" in output  # bounded default, overridable with REFITS=
    assert "dotenvkit" not in output and "dotenvx" not in output
    assert "equity ingest" not in output


# --- truthful per-session accounting ---------------------------------------


def test_a_refused_plan_exits_2_before_training_or_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented exit-2 contract for a refused plan is pinned at `main()`."""
    _keep_logging_config(monkeypatch)
    sessions = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(12)]
    _scope_lake(monkeypatch, _bronze_lake(tmp_path, sessions))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    calls = _record_predictions(monkeypatch, events)
    model_dir = tmp_path / "models"

    # 12 consecutive calendar days over 2 evenly spread boundaries leaves a 10-day window.
    exit_code = main(["--days", "12", "--refits", "2", "--end", sessions[-1].isoformat(), "--tickers", _TICKER, "--model-dir", str(model_dir)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "spans" in captured.err and "--max-model-age-days" in captured.err
    assert events == []  # nothing was trained
    assert calls == []  # nothing was predicted
    assert not model_dir.exists()  # nothing was written


def test_sessions_without_a_persisted_row_are_missing_and_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session lost to model expiry must never be counted as predicted."""
    _keep_logging_config(monkeypatch)
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS[:4]))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    expired = {_DAYS[1]: "model_expired", _DAYS[2]: "model_expired"}
    calls = _record_predictions(monkeypatch, events, expired=expired)

    exit_code = main(
        ["--days", "4", "--refits", "1", "--end", _DAYS[3].isoformat(), "--tickers", _TICKER, "--model-dir", str(tmp_path / "models")],
    )

    captured = capsys.readouterr()
    payload = _last_json(captured.out)
    assert exit_code == 1  # partial coverage is a failure, not a success
    assert len(calls) == 4  # every requested session was attempted
    assert payload["sessions_requested"] == 4
    assert payload["sessions_predicted"] == 2  # only the sessions that persisted a row
    assert payload["sessions_missing"] == 2
    assert payload["coverage"] == {"covered": 2, "total": 4, "complete": False}
    assert payload["boundaries"][0]["missing"] == [
        {"session": _DAYS[1].isoformat(), "reason": "model_expired"},
        {"session": _DAYS[2].isoformat(), "reason": "model_expired"},
    ]
    assert payload["boundaries"][0]["sessions_covered"] == 2
    assert payload["boundaries"][0]["predictions_persisted"] == 2
    assert "coverage 2/4 sessions" in captured.out  # the human line states covered/total
    assert (lake / "04_platinum").exists() is False  # the stub never wrote a row


def test_a_fully_covered_run_exits_0_and_reports_complete_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_logging_config(monkeypatch)
    _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS[:3]))
    events: list[tuple[str, date]] = []
    _install_forecaster(monkeypatch, _forecaster_class(events=events))
    _record_predictions(monkeypatch, events)

    exit_code = main(
        ["--days", "3", "--refits", "1", "--end", _DAYS[2].isoformat(), "--tickers", _TICKER, "--model-dir", str(tmp_path / "models")],
    )

    payload = _last_json(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["coverage"] == {"covered": 3, "total": 3, "complete": True}
    assert payload["sessions_missing"] == 0
    assert payload["boundaries"][0]["missing"] == []


# --- real forecaster: the reuse branch must not poison the verdict ----------


class _FixedModel:
    """Picklable stand-in backend: no fitting, deterministic probability."""

    def __init__(self, probability: float = 0.7) -> None:
        self.probability = probability

    def fit(self, X: Any, y: Any, **fit_kwargs: Any) -> _FixedModel:
        del X, y, fit_kwargs
        return self

    def predict_proba(self, X: Any) -> Any:
        import numpy as np

        return np.tile(np.array([[1.0 - self.probability, self.probability]]), (X.height, 1))

    def predict(self, X: Any) -> Any:
        import numpy as np

        return np.zeros(X.height, dtype=np.int8)

    def get_params(self, deep: bool = True) -> dict[str, Any]:
        del deep
        return {}


def _action_proof_fields(dates: list[date]) -> dict[str, Any]:
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol=_TICKER)
    source_time = datetime.combine(min(dates) - timedelta(days=400), time(12), tzinfo=UTC)
    source_session = source_observable_session(instrument, source_time)
    proof = ActionCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source="synthetic-source",
        coverage_start=min(dates) - timedelta(days=1),
        coverage_end=max(dates) + timedelta(days=1),
        source_available_at=source_time,
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )
    return {
        "action_coverage": pl.Series([1] * len(dates), dtype=pl.Int8),
        "action_source_age_days": pl.Series([(day - source_session).days for day in dates], dtype=pl.Int32),
        **{column: [value] * len(dates) for column, value in action_coverage_feature_fields(proof).items()},
    }


def _features_frame(last_session: date, rows: int = 200) -> pl.DataFrame:
    dates = [last_session - timedelta(days=offset) for offset in reversed(range(rows))]
    return pl.DataFrame(
        {
            "market": ["us_equity"] * rows,
            "mic": ["XNAS"] * rows,
            "symbol": [_TICKER] * rows,
            "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * rows,
            "ticker": [_TICKER] * rows,
            "date": dates,
            "next_day_return": [0.02 if index % 2 == 0 else -0.01 for index in range(rows - 1)] + [None],
            "label_available_date": [dates[index + 1] if index + 1 < rows else None for index in range(rows)],
            "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * rows, dtype=pl.Int8),
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * rows,
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * rows,
            **_action_proof_fields(dates),
            "rsi_14": pl.Series([float(index % 17) for index in range(rows)], dtype=pl.Float64),
            "macd": pl.Series([float(index % 5) for index in range(rows)], dtype=pl.Float64),
        }
    )


def _install_real_forecaster(monkeypatch: pytest.MonkeyPatch, frame: pl.DataFrame, fresh_fits: list[bool]) -> None:
    """Let the real ``PriceForecaster`` train, but replace the estimator and IO seams.

    ``fresh_fits`` records one entry per fresh (non-reused) fit of the boundary
    model, so a re-run that takes the reuse branch appends nothing.
    """
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    # The validation folds and the final boundary fit share these seams; only the
    # final fit is counted (validation keeps its own build/fit pair below).
    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: _FixedModel())
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *_args, **_kwargs: fresh_fits.append(True))
    monkeypatch.setattr("equity_lake.ml.validation.build_estimator", lambda *_args, **_kwargs: _FixedModel())
    monkeypatch.setattr("equity_lake.ml.validation.fit_estimator", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_class_weights", lambda _y: {"scale_pos_weight": 1.0})
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_shap_importance", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", lambda *_args, **_kwargs: 0.6)
    monkeypatch.setattr("equity_lake.ml.forecasting.backend_of", lambda _model: "xgboost")


def test_rerun_hits_the_reuse_branch_reads_the_sidecar_and_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second identical run reuses fresh artifacts and must still exit 0."""
    _keep_logging_config(monkeypatch)
    sessions = _DAYS[:4]
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, sessions))
    events: list[tuple[str, date]] = []
    fresh_fits: list[bool] = []
    _record_predictions(monkeypatch, events)  # inference stays stubbed; training is real
    _install_real_forecaster(monkeypatch, _features_frame(sessions[-1]), fresh_fits)
    models = tmp_path / "models"
    argv = [
        "--days",
        "4",
        "--refits",
        "1",
        "--end",
        sessions[-1].isoformat(),
        "--tickers",
        _TICKER,
        "--model-dir",
        str(models),
        "--train-window",
        "30",
        "--test-window",
        "10",
    ]

    first_exit = main(argv)
    first_payload = _last_json(capsys.readouterr().out)
    fits_after_first = len(fresh_fits)
    sidecars = sorted(models.glob("*.training_summary.json"))
    second_exit = main(argv)
    second_output = capsys.readouterr().out
    second_payload = _last_json(second_output)

    assert first_exit == 0 and second_exit == 0
    assert first_payload["coverage"] == second_payload["coverage"] == {"covered": 4, "total": 4, "complete": True}
    assert second_payload["refits_skipped"] == 0
    # The in-memory reuse summary has no validation_status; a "verified" verdict
    # can only have come from the sidecar written by the first run.
    assert first_payload["boundaries"][0]["validation_status"] == "verified"
    assert second_payload["boundaries"][0]["validation_status"] == "verified"
    assert second_payload["boundaries"][0]["reason"] is None
    # The R3 reason must never be synthesised from a missing fold count.
    assert "None validation folds" not in second_output
    reasons = [boundary["reason"] for boundary in second_payload["boundaries"] if boundary["reason"] is not None]
    assert all("None" not in reason for reason in reasons)
    assert all("None" not in boundary["validation_status"] for boundary in second_payload["boundaries"])
    # The reuse branch really ran: no second fit, sidecar present.
    assert len(fresh_fits) == fits_after_first == 1  # the boundary was trained once, then reused
    assert sidecars and all(Path(path).exists() for path in sidecars)
    assert lake.exists()


def test_make_refit_preload_echo_is_not_a_completeness_claim() -> None:
    root = Path(__file__).resolve().parents[2]
    output = subprocess.run(
        ["make", "--no-print-directory", "-n", "refit-preload"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout

    assert "✅ Historical predictions written" not in output
    assert "coverage covered/total" in output


# --- real writer -> Delta -> re-run proof (R5 acceptance) -------------------


def _v1_payload(ticker: str, trading_date: date, trained_at: date) -> dict[str, Any]:
    """Inference payload shaped like a verified ``v1_direction`` ``predict()``."""
    identity = InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker)
    return {
        **identity.as_columns(),
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "date": trading_date,
        "prediction": 1,
        "probability": 0.62,
        "model_mode": "v1_direction",
        "model_version": f"instrument-v1-xgboost_v1_direction_{trained_at.isoformat()}",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_provenance_version": MODEL_PROVENANCE_VERSION,
        "model_artifact_sha256": _ARTIFACT_SHA256,
        "model_trained_at": trained_at,
        "model_age_days": (trading_date - trained_at).days,
        "model_max_age_days": DEFAULT_MAX_MODEL_AGE_DAYS,
        "training_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "inference_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "missing_value_policy": MISSING_VALUE_POLICY,
        "candidate_strategy_fingerprint": None,
        "horizon_days": 1,
        "threshold_used": 0.55,
        "threshold_source": "stored_optimized",
    }


class _TrainedAsOfForecasterFactory:
    """Build a stub exposing each boundary's model as the newest trained on/before the date."""

    @staticmethod
    def build(trained: list[date]) -> type:
        class _TrainedAsOfForecaster:
            def __init__(self, model_dir: str | None = None, model_mode: str = "v1_direction", **_kwargs: Any) -> None:
                self.model_dir = model_dir
                self.model_mode = model_mode

            def train_model(self, ticker: str | InstrumentKey, start_date: date, end_date: date, **_kwargs: Any) -> None:
                del ticker, start_date
                trained.append(end_date)

            def last_training_summary(self) -> dict[str, Any]:
                return {"validation_status": "verified", "validation_unavailable_reason": None, "validation_fold_count": 3}

            def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
                symbol = ticker if isinstance(ticker, str) else ticker.symbol
                trained_at = max(boundary for boundary in trained if boundary <= date)
                return _v1_payload(symbol, date, trained_at)

            def close(self) -> None:
                return None

        return _TrainedAsOfForecaster


def test_the_full_flow_is_idempotent_and_never_trains_a_future_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running twice must overwrite (not duplicate) and keep ``model_trained_at <= date``."""
    lake = _scope_lake(monkeypatch, _bronze_lake(tmp_path, _DAYS[:4]))
    trained: list[date] = []
    _install_forecaster(monkeypatch, _TrainedAsOfForecasterFactory.build(trained))
    # The real writer resolves the lake root from this module global; storage is
    # NOT stubbed, so the real merge/Delta path runs.

    scope: dict[str, Any] = {
        "days": 4,
        "refits": 2,
        "end": _DAYS[3],
        "tickers": [_TICKER],
        "lake_dir": lake,
        "model_dir": tmp_path / "models",
    }
    first = refit_preload(**scope)
    rows_after_first = read_delta("04_platinum/predictions", lake_dir=lake)
    second = refit_preload(**scope)
    rows_after_second = read_delta("04_platinum/predictions", lake_dir=lake)

    assert first.predictions_persisted == second.predictions_persisted == 4  # 4 sessions, 1 ticker
    assert first.complete is True and second.complete is True
    assert rows_after_first.height == rows_after_second.height == 4  # overwrite, not duplicate
    assert rows_after_second["date"].n_unique() == 4
    assert rows_after_second.select((pl.col("model_trained_at") <= pl.col("date")).all()).item() is True
    # Each date is served by the newest boundary at or before it (R5 as-of rule):
    # 4 sessions, 2 refits -> boundaries s0 and s3, windows [s0, s1, s2] and [s3].
    assert rows_after_second.sort("date")["model_trained_at"].to_list() == [
        _DAYS[0],
        _DAYS[0],
        _DAYS[0],
        _DAYS[3],
    ]
    assert rows_after_second["forecast_provenance_version"].unique().to_list() == [FORECAST_PROVENANCE_VERSION]
    assert rows_after_second["prediction_semantics_version"].unique().to_list() == [PREDICTION_SEMANTICS_VERSION]
    assert rows_after_second["prediction_target"].unique().to_list() == [PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE]
