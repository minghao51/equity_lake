"""Bounded per-date as-of refit preload for historical Platinum predictions.

Why this is a module and not a shell loop: a historical prediction date has to
be produced by a model trained **before** that date, and
:class:`~equity_lake.ml.forecasting.PriceForecaster` refuses future-trained
artifacts (``has_future_model`` selection). Producing a window of prediction
dates therefore means training once per *refit boundary*, oldest -> newest, and
then running the pipeline ML stage for the sessions that follow each boundary.
Training is the expensive half (~15 s per boundary), so the boundary count is
validated here instead of in Make. Sessions come from the stored Bronze prices
via :func:`equity_lake.devtools.preload.observed_sessions` (the ADR-0014
"observed session"), never from a calendar.

Two bounds decide whether a plan is executable, both checked **before** anything
is trained or written:

* ``--refits`` (1..``MAX_REFITS``) — one training per boundary.
* ``--max-model-age-days`` (0..``ml.forecasting.DEFAULT_MAX_MODEL_AGE_DAYS``) —
  the inclusive calendar-day age bound ``predict()`` applies to a boundary's
  model. A boundary can therefore only own sessions whose calendar distance from
  it is within the bound, so every planned window's span is checked at plan time
  and an over-long window is refused with the concrete refit count or age bound
  it would need.

Boundary rule (explicit, so the spread is auditable): let ``n`` be the number of
sessions in the window and ``m = min(refits, n)``. With ``m == 1`` the only
boundary is the window's **first** session, so that single model owns the whole
window. With ``m > 1`` boundary ``i`` is session index
``floor(i * (n - 1) / (m - 1))`` for ``i = 0 .. m - 1``. That spreads the refits
evenly over the window, always includes the first and the last session, and
yields strictly increasing, distinct indices. Boundary ``i`` owns every session
from its own date up to (excluding) boundary ``i + 1`` — a non-overlapping
partition of the window, oldest -> newest.

Predictions are only ever written by :func:`equity_lake.ml.run_prediction_job`
(the pipeline ML stage), and only to ``storage.delta.LAKE_DIR``: a ``lake_dir``
that differs from that resolved root is refused rather than silently splitting
session reads from prediction writes. Nothing is hand-written, and a boundary
whose training summary is not ``validation_status == "verified"`` is skipped with
the R3 message (``validation: unavailable (<reason>)``) instead of producing rows
from an unvalidated model. A reused artifact reports only
``status: reused_fresh_model``, so its verdict is read back from the artifact's
``*.training_summary.json`` sidecar.

Accounting is per session, not per boundary: a session counts as covered only
when its :func:`~equity_lake.ml.run_prediction_job` call persisted every ticker's
row. Sessions that did not are listed under ``missing`` with the product-readable
reason (``model_expired``, ``model_from_future``, ...). Exit codes: ``0`` only on
full coverage, ``1`` when any requested session is missing, ``2`` for a refused
plan or when every boundary was skipped.

    uv run python -m equity_lake.devtools.refit_preload --days 60 --refits 12
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog

from equity_lake.devtools.preload import (
    DEFAULT_PRELOAD_SESSIONS,
    DEFAULT_PRELOAD_TICKERS,
    PRELOAD_MARKET,
    observed_sessions,
)
from equity_lake.ml import run_prediction_job

logger = structlog.get_logger(__name__)

#: Only the direction model has a persisted prediction row-bound proof contract
#: (ADR-0021); meta-label semantics stay unknown by design.
MODEL_MODE = "v1_direction"
#: Upper bound on one refit preload: one training per boundary (~15 s measured
#: for the five default tickers) plus one inference call per session.
MAX_REFITS = 12
DEFAULT_REFITS = 12
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_TRAIN_WINDOW = 252
DEFAULT_TEST_WINDOW = 21
DEFAULT_EMBARGO_WINDOW = 1
VALIDATION_MODE = "purged_walk_forward"
#: R3 wording; a boundary skipped for lack of validation must say this.
VALIDATION_SKIP_PREFIX = "validation: unavailable"
#: ``train_model`` returns this status instead of a validation verdict when it
#: reuses an artifact that was already trained for the same boundary.
REUSED_MODEL_STATUS = "reused_fresh_model"
#: Used when neither the reuse summary nor the artifact sidecar records a verdict.
NO_VALIDATION_STATE_REASON = "the artifact's training-summary sidecar carries no validation state"


class RefitPreloadError(ValueError):
    """The requested refit scope is empty, out of bounds, or wholly unvalidated."""


def _max_model_age_ceiling() -> int:
    """The forecaster's trusted ``max_model_age_days`` ceiling (imports ML lazily)."""
    from equity_lake.ml.forecasting import DEFAULT_MAX_MODEL_AGE_DAYS

    return DEFAULT_MAX_MODEL_AGE_DAYS


def _persisted_max_model_age_days() -> int:
    """The age bound ``predict()`` stamps on each row (the persisted provenance value).

    Reported next to the plan bound so the JSON says both what the plan verified
    against and what the rows actually carry for the read API.
    """
    return _max_model_age_ceiling()


def _resolve_max_model_age_days(max_model_age_days: int | None) -> int:
    """Resolve and bound the age flag before any lake access.

    ``predict()`` (and therefore the persisted ``model_max_age_days`` provenance
    column the read API checks) refuses a wider bound than the forecaster's
    trusted policy allows, so widening it here would make the report lie about
    what was actually verified. The flag can only narrow the bound.
    """
    ceiling = _max_model_age_ceiling()
    if max_model_age_days is None:
        return ceiling
    if isinstance(max_model_age_days, bool) or not isinstance(max_model_age_days, int) or not 0 <= max_model_age_days <= ceiling:
        raise RefitPreloadError(
            f"--max-model-age-days must be an integer in [0, {ceiling}], got {max_model_age_days!r}; "
            f"the forecaster's trusted age policy (ml.forecasting.DEFAULT_MAX_MODEL_AGE_DAYS={ceiling}) "
            "refuses a wider inference bound, so a wider value would not be persisted as verified provenance",
        )
    return max_model_age_days


def _canonical_lake_dir() -> Path:
    """The lake root ``run_prediction_job`` writes to (``storage.delta.LAKE_DIR``)."""
    from equity_lake.storage import delta as delta_storage

    return Path(delta_storage.LAKE_DIR).resolve()


def _guard_lake_dir(lake_dir: Path | None) -> None:
    """Refuse a lake that differs from the storage layer's write root.

    Session reads and prediction writes must observe one lake; redirecting only
    the reads would report coverage for a lake nothing was written to.
    """
    if lake_dir is None:
        return
    requested = Path(lake_dir).resolve()
    canonical = _canonical_lake_dir()
    if requested != canonical:
        raise RefitPreloadError(
            f"lake_dir {requested} differs from storage.delta.LAKE_DIR {canonical}: "
            "session reads and prediction writes must share one lake. Point storage.delta.LAKE_DIR "
            "(what run_prediction_job writes through) at the lake instead of passing lake_dir.",
        )


def _validate_refit_count(refits: int) -> None:
    """Refuse a refit count outside ``1 .. MAX_REFITS`` before any work happens."""
    if refits < 1:
        raise RefitPreloadError(f"--refits must be at least 1, got {refits}")
    if refits > MAX_REFITS:
        raise RefitPreloadError(
            f"--refits={refits} exceeds the {MAX_REFITS}-boundary bound; "
            "each boundary costs one training (~15s) — run several bounded preloads instead",
        )


def refit_boundaries(sessions: Sequence[date], refits: int) -> list[date]:
    """Return up to *refits* boundary sessions, oldest first.

    ``m = min(refits, len(sessions))`` boundaries are picked at session indices
    ``floor(i * (n - 1) / (m - 1))`` (``i = 0 .. m - 1``), so the spread is
    even, the window's first and last session are always included, and the
    indices are strictly increasing. A single boundary is the window's **first**
    session: its model must own every session of the window (a last-session
    boundary would leave the rest of the window unpredicted).
    """
    _validate_refit_count(refits)
    if not sessions:
        raise RefitPreloadError("the refit window is empty")
    count = min(refits, len(sessions))
    last = len(sessions) - 1
    if count == 1:
        return [sessions[0]]
    return [sessions[(index * last) // (count - 1)] for index in range(count)]


def boundary_windows(boundaries: Sequence[date], sessions: Sequence[date]) -> list[tuple[date, tuple[date, ...]]]:
    """Partition *sessions* into ``[boundary, next_boundary)`` windows.

    The result is non-overlapping, oldest -> newest, and covers every session
    exactly once — including the whole window when there is a single boundary.
    Predictions for a window may only use that boundary's model.
    """
    positions = {session: index for index, session in enumerate(sessions)}
    windows: list[tuple[date, tuple[date, ...]]] = []
    for index, boundary in enumerate(boundaries):
        start = positions[boundary]
        stop = positions[boundaries[index + 1]] if index + 1 < len(boundaries) else len(sessions)
        windows.append((boundary, tuple(sessions[start:stop])))
    return windows


def minimum_coverable_refits(sessions: Sequence[date], max_model_age_days: int) -> int | None:
    """Smallest ``--refits`` whose even-spread plan keeps every window in the bound.

    The boundaries are placed by :func:`refit_boundaries`, so the answer has to
    respect that placement rather than an ideal hand-picked one. ``None`` means
    no run inside the ``MAX_REFITS`` bound can cover the window.
    """
    if not sessions:
        return 0
    for refits in range(1, MAX_REFITS + 1):
        windows = boundary_windows(refit_boundaries(sessions, refits), sessions)
        if all((window[-1] - window[0]).days <= max_model_age_days for _, window in windows if window):
            return refits
    return None


def _guard_window_spans(
    windows: Sequence[tuple[date, tuple[date, ...]]],
    *,
    sessions: Sequence[date],
    max_model_age_days: int,
) -> None:
    """Refuse a plan whose windows outlive the model age bound, before any work.

    ``predict()`` rejects a model whose ``refit_boundary`` is more than
    ``max_model_age_days`` calendar days before the prediction date, so a window
    that spans further than that can never be covered by its boundary. Catching
    it here turns a half-written run (some sessions persisted, the rest silently
    expired) into a refusal that names the requirement.
    """
    ceiling = _max_model_age_ceiling()
    for boundary, window in windows:
        if not window:
            continue
        span = (window[-1] - window[0]).days
        if span <= max_model_age_days:
            continue
        needed = minimum_coverable_refits(sessions, max_model_age_days)
        message = f"boundary {window[0].isoformat()}..{window[-1].isoformat()} spans {span} days, above --max-model-age-days {max_model_age_days}; "
        if needed is None:
            message += (
                f"no evenly spread plan of at most {MAX_REFITS} refits covers this window "
                f"(at this spread one boundary can own at most {max_model_age_days} calendar days, and the even-spread "
                "placement is the only one this run makes), so cover the window with several smaller --days preloads "
                f"or raise --max-model-age-days (capped at {ceiling} by the forecaster's trusted age policy)"
            )
        else:
            message += (
                f"use at least {needed} refits for this window or raise --max-model-age-days "
                f"(capped at {ceiling} by the forecaster's trusted age policy); "
                f"boundary {boundary.isoformat()} owns {len(window)} of {len(sessions)} planned sessions"
            )
        raise RefitPreloadError(message)


@dataclass(frozen=True)
class SessionFailure:
    """One requested session that produced no persisted prediction row."""

    session: date
    reason: str

    def to_payload(self) -> dict[str, str]:
        return {"session": self.session.isoformat(), "reason": self.reason}


@dataclass(frozen=True)
class BoundaryReport:
    """Outcome of one refit boundary (training + the sessions it owns)."""

    boundary: date
    tickers_trained: tuple[str, ...]
    validation_status: str
    reason: str | None
    sessions: tuple[date, ...]
    predictions_persisted: int = 0
    missing: tuple[SessionFailure, ...] = ()

    @property
    def covered(self) -> int:
        """Sessions of this window that persisted a prediction row."""
        return len(self.sessions) - len(self.missing)

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.boundary.isoformat(),
            "tickers_trained": list(self.tickers_trained),
            "validation_status": self.validation_status,
            "reason": self.reason,
            "sessions": [session.isoformat() for session in self.sessions],
            "sessions_covered": self.covered,
            "predictions_persisted": self.predictions_persisted,
            "missing": [failure.to_payload() for failure in self.missing],
        }


@dataclass(frozen=True)
class RefitPreloadReport:
    """Outcome of one bounded refit preload run (summary only, no artifact written)."""

    days_requested: int
    sessions: tuple[date, ...]
    tickers: tuple[str, ...]
    refits_requested: int
    boundaries: tuple[BoundaryReport, ...]
    lookback_days: int
    train_window: int
    test_window: int
    embargo_window: int
    max_model_age_days: int

    @property
    def applied(self) -> int:
        """Boundaries whose model validated (they attempted to predict their window)."""
        return sum(1 for boundary in self.boundaries if boundary.reason is None)

    @property
    def skipped(self) -> int:
        """Boundaries refused because no validated model could be produced."""
        return len(self.boundaries) - self.applied

    @property
    def sessions_requested(self) -> int:
        return len(self.sessions)

    @property
    def sessions_predicted(self) -> int:
        """Sessions that actually persisted a prediction row (never the plan)."""
        return sum(boundary.covered for boundary in self.boundaries)

    @property
    def sessions_missing(self) -> int:
        return sum(len(boundary.missing) for boundary in self.boundaries)

    @property
    def predictions_persisted(self) -> int:
        return sum(boundary.predictions_persisted for boundary in self.boundaries)

    @property
    def complete(self) -> bool:
        """True only when every requested session persisted a row."""
        return self.sessions_missing == 0

    def to_payload(self) -> dict[str, Any]:
        covered = self.sessions_predicted
        requested = self.sessions_requested
        return {
            "market": PRELOAD_MARKET,
            "days_requested": self.days_requested,
            "sessions_requested": requested,
            "sessions_covered": covered,
            "sessions_missing": self.sessions_missing,
            "coverage": {"covered": covered, "total": requested, "complete": self.complete},
            "window": {
                "start": self.sessions[0].isoformat() if self.sessions else None,
                "end": self.sessions[-1].isoformat() if self.sessions else None,
            },
            "tickers": list(self.tickers),
            "model_mode": MODEL_MODE,
            "plan_max_model_age_days": self.max_model_age_days,
            "prediction_max_model_age_days": _persisted_max_model_age_days(),
            "lookback_days": self.lookback_days,
            "validation": {
                "mode": VALIDATION_MODE,
                "train_window": self.train_window,
                "test_window": self.test_window,
                "embargo_window": self.embargo_window,
            },
            "refits_requested": self.refits_requested,
            "refits_applied": self.applied,
            "refits_skipped": self.skipped,
            "sessions_predicted": covered,
            "predictions_persisted": self.predictions_persisted,
            "boundaries": [boundary.to_payload() for boundary in self.boundaries],
        }


def _resolve_model_dir(model_dir: Path | None) -> str | None:
    return str(model_dir) if model_dir is not None else None


def _summary_verdict(summary: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """Return ``(validation_status, skip_reason)`` from a training summary.

    ``None`` means the summary carries no validation state at all (the reuse
    path's summary, or a missing sidecar) — never "unvalidated with a made-up
    fold count".
    """
    status = summary.get("validation_status")
    if status is None:
        return None
    if status == "verified":
        return "verified", None
    unavailable_reason = summary.get("validation_unavailable_reason")
    if not unavailable_reason:
        folds = summary.get("validation_fold_count")
        unavailable_reason = f"{folds} validation folds" if isinstance(folds, int) else NO_VALIDATION_STATE_REASON
    return str(status), f"{VALIDATION_SKIP_PREFIX} ({unavailable_reason})"


def _load_sidecar_summary(forecaster: Any, ticker: str, boundary: date) -> dict[str, Any] | None:
    """Read the artifact's own ``*.training_summary.json`` sidecar."""
    try:
        summary = forecaster.load_training_summary(ticker, boundary)
    except Exception as exc:  # noqa: BLE001 — a broken sidecar is a skip reason, not a crash
        logger.warning("refit_training_summary_unreadable", ticker=ticker, date=boundary.isoformat(), error=str(exc))
        return None
    return dict(summary) if isinstance(summary, Mapping) else None


def _train_boundary(
    *,
    boundary: date,
    tickers: Sequence[str],
    lookback_days: int,
    train_window: int,
    test_window: int,
    embargo_window: int,
    max_model_age_days: int,
    model_dir: Path | None,
) -> tuple[tuple[str, ...], str, str | None]:
    """Train one as-of model per ticker at *boundary*, honestly.

    Returns ``(tickers_trained, validation_status, reason)``. A boundary is only
    usable when every ticker's training summary reports
    ``validation_status == "verified"``; training stops at the first ticker that
    cannot prove it, and the returned reason carries the R3 wording.

    A reused artifact (``status: reused_fresh_model``) carries no verdict of its
    own, so the verdict is read back from the artifact's training-summary
    sidecar. Only a missing sidecar — or one that itself records no validation
    state — may leave the boundary unvalidated, and the reason then says exactly
    that.
    """
    try:
        # Lazy so the module (and its argparse help) stays importable without the
        # optional ML stack; the pipeline imports its forecaster the same way.
        from equity_lake.ml.forecasting import PriceForecaster
    except ImportError as exc:  # pragma: no cover - ML extra is installed in CI
        raise RefitPreloadError(f"ML training requires the optional 'ml' dependencies: {exc}") from exc

    forecaster = PriceForecaster(model_dir=_resolve_model_dir(model_dir), model_mode=MODEL_MODE)
    trained: list[str] = []
    try:
        for ticker in tickers:
            try:
                forecaster.train_model(
                    ticker,
                    boundary - timedelta(days=lookback_days),
                    boundary,
                    validate=True,
                    validation_mode=VALIDATION_MODE,
                    train_window=train_window,
                    test_window=test_window,
                    embargo_window=embargo_window,
                    max_model_age_days=max_model_age_days,
                )
            except Exception as exc:  # noqa: BLE001 — reported, never silently ignored
                return tuple(trained), "training_failed", f"training: unavailable ({exc})"
            trained.append(ticker)
            summary = forecaster.last_training_summary() or {}
            verdict = _summary_verdict(summary)
            if verdict is None:
                # Reused artifacts report only ``status: reused_fresh_model``;
                # their verdict belongs to the sidecar written at training time.
                sidecar = _load_sidecar_summary(forecaster, ticker, boundary)
                verdict = _summary_verdict(sidecar) if sidecar is not None else None
            if verdict is None:
                return tuple(trained), "unavailable", f"{VALIDATION_SKIP_PREFIX} ({NO_VALIDATION_STATE_REASON})"
            status, reason = verdict
            if reason is not None:
                return tuple(trained), status, reason
    finally:
        forecaster.close()
    return tuple(trained), "verified", None


def _session_failure_reason(results: Mapping[str, Mapping[str, Any]]) -> str:
    """Product-readable reason a session persisted nothing (``model_expired``, ...)."""
    reasons = sorted(
        {
            str(result.get("status") or result.get("error_type") or "prediction write or validation failed")
            for result in results.values()
            if result.get("persisted") is not True
        },
    )
    if not reasons:
        return "no prediction row was persisted"
    return ", ".join(reasons)


def _session_persisted(success: bool, results: Mapping[str, Mapping[str, Any]]) -> bool:
    """A session is covered only when every requested ticker's row actually landed."""
    return success and bool(results) and all(result.get("persisted") is True for result in results.values())


def refit_preload(
    *,
    days: int = DEFAULT_PRELOAD_SESSIONS,
    refits: int = DEFAULT_REFITS,
    end: date | None = None,
    tickers: Sequence[str] = DEFAULT_PRELOAD_TICKERS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    train_window: int = DEFAULT_TRAIN_WINDOW,
    test_window: int = DEFAULT_TEST_WINDOW,
    embargo_window: int = DEFAULT_EMBARGO_WINDOW,
    max_model_age_days: int | None = None,
    lake_dir: Path | None = None,
    model_dir: Path | None = None,
) -> RefitPreloadReport:
    """Train as-of models at up to *refits* boundaries and predict their sessions.

    Boundaries are walked oldest -> newest: train as of the boundary, then run
    :func:`equity_lake.ml.run_prediction_job` for every session from the boundary
    up to (excluding) the next one. Sessions before a boundary never see its
    model because the forecaster selects ``refit_boundary <= as_of_date`` and
    rejects future-trained artifacts.

    The whole plan is checked before anything is trained or written: every
    window's calendar span must fit ``max_model_age_days`` (the inclusive bound
    ``predict()`` applies), otherwise the call refuses with the boundary, its
    span, and the refit count or age bound it would need.

    A boundary whose training summary is not ``verified`` is skipped (with the
    R3 reason recorded) and its sessions are reported missing. When no boundary
    is usable the call raises :class:`RefitPreloadError` carrying those reasons.
    Sessions that did not persist a row are reported in ``missing`` — never
    counted as predicted — and ``report.complete`` is True only on full coverage.

    Re-running the same command is idempotent: model artifacts are keyed by refit
    date (a fresh one is reused, and its verdict is read from its sidecar) and the
    prediction upsert keys on ``(market, mic, symbol, date)``.
    """
    if lookback_days < 1:
        raise RefitPreloadError(f"--lookback-days must be at least 1, got {lookback_days}")
    for flag, value in (("--train-window", train_window), ("--test-window", test_window)):
        if value < 1:
            raise RefitPreloadError(f"{flag} must be at least 1, got {value}")
    if embargo_window < 0:
        raise RefitPreloadError(f"--embargo-window must be 0 or more, got {embargo_window}")
    # Validate bounds and the write root before touching the lake so nothing is
    # trained or written for an out-of-bounds or split-lake request.
    age_bound = _resolve_max_model_age_days(max_model_age_days)
    _validate_refit_count(refits)
    _guard_lake_dir(lake_dir)

    sessions = observed_sessions(days, end=end, lake_dir=lake_dir)
    symbols = [symbol.strip().upper() for symbol in tickers if symbol.strip()]
    if not symbols:
        raise RefitPreloadError("at least one ticker is required")

    windows = boundary_windows(refit_boundaries(sessions, refits), sessions)
    # Plan-time refusal: an over-long window would otherwise half-write the run
    # and lose the remaining sessions inside ``predict()``'s age check.
    _guard_window_spans(windows, sessions=sessions, max_model_age_days=age_bound)

    reports: list[BoundaryReport] = []
    for boundary, window in windows:
        tickers_trained, validation_status, reason = _train_boundary(
            boundary=boundary,
            tickers=symbols,
            lookback_days=lookback_days,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            max_model_age_days=age_bound,
            model_dir=model_dir,
        )
        if reason is not None:
            logger.warning("refit_boundary_skipped", date=boundary.isoformat(), reason=reason, tickers=list(tickers_trained))
            reports.append(
                BoundaryReport(
                    boundary=boundary,
                    tickers_trained=tickers_trained,
                    validation_status=validation_status,
                    reason=reason,
                    sessions=window,
                    missing=tuple(SessionFailure(session=session, reason=reason) for session in window),
                ),
            )
            continue
        persisted = 0
        missing: list[SessionFailure] = []
        for session in window:
            try:
                success, results = run_prediction_job(
                    trading_date=session,
                    tickers=symbols,
                    model_dir=_resolve_model_dir(model_dir),
                )
            except Exception as exc:  # noqa: BLE001 — one failed session must not hide the rest
                failure_reason = str(getattr(exc, "status", None) or type(exc).__name__)
                missing.append(SessionFailure(session=session, reason=failure_reason))
                logger.warning(
                    "refit_session_prediction_failed",
                    date=session.isoformat(),
                    boundary=boundary.isoformat(),
                    reason=failure_reason,
                    error=str(exc),
                )
                continue
            persisted += sum(1 for result in results.values() if result.get("persisted") is True)
            if not _session_persisted(success, results):
                failure_reason = _session_failure_reason(results)
                missing.append(SessionFailure(session=session, reason=failure_reason))
                logger.warning(
                    "refit_session_prediction_missing",
                    date=session.isoformat(),
                    boundary=boundary.isoformat(),
                    reason=failure_reason,
                    results={key: value.get("status", "persisted" if value.get("persisted") else "unknown") for key, value in results.items()},
                )
        logger.info(
            "refit_boundary_complete",
            date=boundary.isoformat(),
            sessions=len(window),
            sessions_covered=len(window) - len(missing),
            predictions_persisted=persisted,
        )
        reports.append(
            BoundaryReport(
                boundary=boundary,
                tickers_trained=tickers_trained,
                validation_status=validation_status,
                reason=None,
                sessions=window,
                predictions_persisted=persisted,
                missing=tuple(missing),
            ),
        )

    report = RefitPreloadReport(
        days_requested=days,
        sessions=tuple(sessions),
        tickers=tuple(symbols),
        refits_requested=refits,
        boundaries=tuple(reports),
        lookback_days=lookback_days,
        train_window=train_window,
        test_window=test_window,
        embargo_window=embargo_window,
        max_model_age_days=age_bound,
    )
    if report.applied == 0:
        reasons = "; ".join(f"{boundary.boundary.isoformat()}: {boundary.reason}" for boundary in report.boundaries)
        raise RefitPreloadError(f"every refit boundary was skipped, no predictions were produced — {reasons}")
    logger.info("refit_preload_complete", **report.to_payload())
    return report


def _print_summary(report: RefitPreloadReport) -> None:
    """Short human summary; the machine-readable JSON follows on the last line."""
    payload = report.to_payload()
    window = payload["window"]
    print(
        f"refit preload: coverage {payload['sessions_covered']}/{payload['sessions_requested']} sessions "
        f"({window['start']} -> {window['end']}), {payload['refits_applied']}/{payload['refits_requested']} boundaries applied, "
        f"{payload['refits_skipped']} skipped, max model age {report.max_model_age_days}d",
    )
    print(
        f"  windows: lookback={report.lookback_days}d train={report.train_window} "
        f"test={report.test_window} embargo={report.embargo_window} ({VALIDATION_MODE})",
    )
    for boundary in report.boundaries:
        if boundary.reason is not None:
            print(f"  {boundary.boundary.isoformat()} skipped ({boundary.reason}) sessions={len(boundary.sessions)} not predicted")
            continue
        line = (
            f"  {boundary.boundary.isoformat()} {boundary.validation_status} "
            f"sessions={len(boundary.sessions)} covered={boundary.covered} predictions={boundary.predictions_persisted}"
        )
        if boundary.missing:
            grouped = Counter(failure.reason for failure in boundary.missing)
            line += f" missing={len(boundary.missing)} ({', '.join(f'{reason} x{count}' for reason, count in sorted(grouped.items()))})"
        print(line)
    if not report.complete:
        print(
            f"  incomplete: {report.sessions_missing} of {report.sessions_requested} requested session(s) persisted no row "
            "(exit 1; see missing[] in the JSON report)",
        )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by ``make refit-preload``."""
    age_ceiling = _max_model_age_ceiling()
    parser = argparse.ArgumentParser(
        prog="python -m equity_lake.devtools.refit_preload",
        description=(
            "Bounded per-date as-of refit preload: train at up to "
            f"{MAX_REFITS} boundaries, then write historical Platinum predictions through the pipeline ML stage. "
            "Exits 0 only when every requested session persisted a row, 1 on partial coverage, 2 on refusal."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_PRELOAD_SESSIONS,
        help="Observed sessions to cover (max 500, same bound as `make preload`; default 60)",
    )
    parser.add_argument(
        "--refits",
        type=int,
        default=DEFAULT_REFITS,
        help=f"As-of refit boundaries, oldest -> newest (1..{MAX_REFITS}; each costs one training, ~15s)",
    )
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last session YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--tickers", default=",".join(DEFAULT_PRELOAD_TICKERS), help="Comma-separated configured US tickers")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Training lookback before each boundary, in calendar days (default 365)",
    )
    parser.add_argument("--train-window", type=int, default=DEFAULT_TRAIN_WINDOW, help="Purged walk-forward train window (default 252)")
    parser.add_argument("--test-window", type=int, default=DEFAULT_TEST_WINDOW, help="Purged walk-forward test window (default 21)")
    parser.add_argument("--embargo-window", type=int, default=DEFAULT_EMBARGO_WINDOW, help="Purged walk-forward embargo window (default 1)")
    parser.add_argument(
        "--max-model-age-days",
        type=int,
        default=None,
        help=(
            "Inclusive calendar-day age bound one boundary may cover "
            f"(default and ceiling: {age_ceiling}, ml.forecasting.DEFAULT_MAX_MODEL_AGE_DAYS). "
            "Every prediction row persists the bound that was applied and the read API's freshness verdict "
            "rejects a row whose age exceeds it, so this flag can only narrow coverage: a window that spans "
            "further is refused at plan time with the refit count it needs"
        ),
    )
    parser.add_argument("--model-dir", type=Path, default=None, help="Model artifact directory (default: core.paths.MODELS_DIR)")
    args = parser.parse_args(argv)

    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging()
    try:
        report = refit_preload(
            days=args.days,
            refits=args.refits,
            end=args.end,
            tickers=args.tickers.split(","),
            lookback_days=args.lookback_days,
            train_window=args.train_window,
            test_window=args.test_window,
            embargo_window=args.embargo_window,
            max_model_age_days=args.max_model_age_days,
            model_dir=args.model_dir,
        )
    except ValueError as exc:
        # Includes PreloadScopeError (the shared --days bound), the plan-time span
        # refusal, the model-age refusal, and every unvalidated-boundary refusal:
        # all must fail loudly, never half-write.
        logger.error("refit_preload_refused", error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_summary(report)
    print(json.dumps(report.to_payload()))
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
