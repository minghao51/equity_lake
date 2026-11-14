"""Pure-read projections over the arena evidence under ``data/findings`` (E01b.1).

ADR-0025 Decision 8 makes legacy ``<strategy>__<regime>/`` directories,
``<id>.json`` cards and ``benchmark__equity.parquet`` a **read-only compatibility
view** over evidence that already exists, and Decision 5 keeps the *last attempt*
distinct from the *last successful comparable run*. This module is exactly that
view: it only reads, never writes, never creates a directory, and never repairs or
relabels an artifact.

Two evidence layouts coexist (E01b.1): the frozen legacy pair-keyed directories and
the immutable ``runs/`` + ``attempts/`` evidence. Readers resolve:

- the **latest successful run** per legacy slug from validated runs only, so a
  directory with no manifest, a truncated manifest, a corrupt artifact, a failed or
  partial write, or an undated/unrecorded-cost skip never advances the projection;
- the **latest attempt** per slug from validated attempt records, restricted to the
  attempts that actually requested that slug.

Provenance is *not* upgraded here: every reader surface reports what the manifest
recorded, and exactly one value is reported per run. Both protocols are read: a v3
manifest carries no price-origin summaries, so it reports its recorded ``unknown``
— or, for stored legacy evidence, the self-asserted claim v3 rules allowed — and is
never re-derived or upgraded on read. A v4 manifest (ADR-0027) carries one summary
per price snapshot and its claim was re-derived from them when it was written. Reader
support for v4 landed before any v4 writer, so nothing in this view needs a protocol
branch and no frozen payload shape changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from equity_lake.backtesting.attempts import (
    ATTEMPT_MANIFEST_NAME,
    FinalisedAttempt,
    iter_attempt_directories,
    validate_attempt_directory,
)
from equity_lake.backtesting.evidence import (
    MANIFEST_NAME,
    METRICS_ARTIFACT,
    RUNS_DIRNAME,
    FinalisedRun,
    RunManifest,
    RunValidation,
    iter_run_directories,
    validate_run_directory,
)
from equity_lake.core.paths import FINDINGS_DIR
from equity_lake.findings import FindingCard, load_finding_cards

logger = structlog.get_logger(__name__)

__all__ = [
    "attempt_history",
    "backtest_rows",
    "finding_cards",
    "latest_attempt",
    "latest_attempt_for_slug",
    "latest_success_by_slug",
    "legacy_slugs",
    "slug_for_manifest",
    "validated_attempts",
    "validated_runs",
]


def _root(root: Path | None) -> Path:
    return root if root is not None else FINDINGS_DIR


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _age_seconds(finalized_at: datetime, now: datetime) -> int:
    """Whole seconds between a recorded finalisation and ``now`` (never repaired)."""
    return int((now - finalized_at.astimezone(UTC)).total_seconds())


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def validated_runs(root: Path | None = None) -> list[RunValidation]:
    """Every **finalized** run under ``<root>/runs``, in directory-name order.

    A candidate directory is only evidence when :func:`validate_run_directory` says
    so: a missing/truncated manifest, a corrupt or tampered artifact, a renamed run
    or a failed/partial write is simply not a run, and never advances a projection.
    """
    verdicts: list[RunValidation] = []
    for directory in iter_run_directories(_root(root)):
        verdict = validate_run_directory(directory)
        if verdict.finalized:
            verdicts.append(verdict)
    return verdicts


def slug_for_manifest(manifest: RunManifest) -> str | None:
    """``<strategy>__<regime>`` for a single-pair scope, else ``None``.

    A multi-pair scope has no legacy slug: pairing several strategies or regimes
    into one legacy directory name would invent an identity the run never had.
    """
    if len(manifest.strategies) == 1 and len(manifest.cost_regimes) == 1:
        return f"{manifest.strategies[0]}__{manifest.cost_regimes[0]}"
    return None


def latest_success_by_slug(root: Path | None = None) -> dict[str, FinalisedRun]:
    """Newest validated run per legacy slug, by ``(finalized_at, directory name)``.

    Only validated runs are candidates, so a failed, partial, undated or corrupt
    write never becomes a "latest success" — the projection can only be advanced by
    evidence that validates.
    """
    latest: dict[str, FinalisedRun] = {}
    for verdict in validated_runs(root):
        manifest = verdict.manifest
        if manifest is None:  # defensive: a validated run always carries one
            continue
        slug = slug_for_manifest(manifest)
        if slug is None:
            continue
        directory = Path(verdict.directory)
        candidate = FinalisedRun(directory=directory, manifest=manifest)
        current = latest.get(slug)
        if current is None or (manifest.finalized_at, directory.name) > (current.manifest.finalized_at, current.directory.name):
            latest[slug] = candidate
    return latest


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


def validated_attempts(root: Path | None = None) -> list[FinalisedAttempt]:
    """Every **finalized** attempt under ``<root>/attempts``, newest first."""
    attempts: list[FinalisedAttempt] = []
    for directory in iter_attempt_directories(_root(root)):
        verdict = validate_attempt_directory(directory)
        if verdict.finalized and verdict.record is not None:
            attempts.append(FinalisedAttempt(directory=directory, record=verdict.record))
    attempts.sort(key=lambda attempt: (attempt.record.finalized_at, attempt.directory.name), reverse=True)
    return attempts


def attempt_history(root: Path | None = None) -> list[FinalisedAttempt]:
    """Every validated attempt, newest first (an undated/corrupt attempt is excluded)."""
    return validated_attempts(root)


def latest_attempt(root: Path | None = None) -> FinalisedAttempt | None:
    """The newest validated attempt, or ``None`` when no attempt evidence exists."""
    attempts = validated_attempts(root)
    return attempts[0] if attempts else None


def latest_attempt_for_slug(slug: str, root: Path | None = None) -> FinalisedAttempt | None:
    """The newest validated attempt whose *requested* set contains ``slug``.

    "Latest attempt for a slug" is deliberately about the request, not about which
    outcomes completed: a failed attempt for the pair must stay visible even though
    it produced no run evidence (Decision 5).
    """
    for attempt in validated_attempts(root):
        if slug in {pair.slug for pair in attempt.record.requested}:
            return attempt
    return None


# ---------------------------------------------------------------------------
# Legacy view
# ---------------------------------------------------------------------------


def legacy_slugs(root: Path | None = None) -> list[str]:
    """Frozen legacy pair directories: direct children of the root with ``metrics.json``."""
    base = _root(root)
    if not base.is_dir():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_dir() and path.name != RUNS_DIRNAME and (path / METRICS_ARTIFACT).is_file())


def _read_metrics_payload(path: Path) -> dict[str, Any] | None:
    """Read one ``metrics.json`` payload, or ``None`` when it is unreadable.

    An unreadable file is *skipped*, never reported as a success: a row that cannot
    be read is not evidence.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("backtest_metrics_unreadable", path=str(path))
        return None
    if not isinstance(payload, dict):
        logger.warning("backtest_metrics_not_an_object", path=str(path))
        return None
    return payload


def _last_success_payload(run: FinalisedRun, now: datetime) -> dict[str, Any]:
    return {
        "run_id": run.manifest.run_id,
        "run_dir": run.directory.name,
        "finalized_at": run.manifest.finalized_at.isoformat(),
        "age_seconds": _age_seconds(run.manifest.finalized_at, now),
    }


def _last_attempt_payload(attempt: FinalisedAttempt | None, now: datetime) -> dict[str, Any] | None:
    """The `/backtests` last-attempt projection: status, age and the failed pairs."""
    if attempt is None:
        return None
    record = attempt.record
    failed = [
        {
            "strategy": outcome.strategy,
            "cost_regime": outcome.cost_regime,
            "error_type": outcome.error_type,
            "reason": outcome.reason,
        }
        for outcome in record.outcomes
        if outcome.outcome == "failed"
    ]
    return {
        "attempt_id": record.attempt_id,
        "status": record.status,
        "finalized_at": record.finalized_at.isoformat(),
        "age_seconds": _age_seconds(record.finalized_at, now),
        "completed": sum(1 for outcome in record.outcomes if outcome.outcome == "completed"),
        "failed": failed,
    }


def backtest_rows(root: Path | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """The ``/backtests`` projection: one row per legacy slug, sorted by slug.

    The row payload is the latest validated success's ``metrics.json`` (same dated
    writer, identical key set) when one exists, else the frozen legacy
    ``metrics.json``; either way ``slug`` is added and no existing key is dropped or
    renamed. Two additive keys are appended:

    - ``last_success``: ``{run_id, run_dir, finalized_at, age_seconds}`` or ``None``;
    - ``last_attempt``: ``{attempt_id, status, finalized_at, age_seconds, completed,
      failed}`` or ``None``, restricted to attempts that requested this slug.

    An unreadable legacy ``metrics.json`` is skipped as before, so a corrupt file can
    never look like a healthy backtest.
    """
    base = _root(root)
    clock = _now(now)
    successes = latest_success_by_slug(base)
    rows: list[dict[str, Any]] = []
    for slug in sorted(set(legacy_slugs(base)) | set(successes)):
        success = successes.get(slug)
        payload = _read_metrics_payload(success.directory / METRICS_ARTIFACT) if success is not None else None
        if payload is None:
            # Either no validated run for this slug, or (defensively) its metrics did
            # not read: fall back to the frozen legacy artifact, and never invent one.
            payload = _read_metrics_payload(base / slug / METRICS_ARTIFACT)
        if payload is None:
            continue
        payload["slug"] = slug
        payload["last_success"] = _last_success_payload(success, clock) if success is not None else None
        payload["last_attempt"] = _last_attempt_payload(latest_attempt_for_slug(slug, base), clock)
        rows.append(payload)
    return rows


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def _load_attempt_card(path: Path) -> FindingCard | None:
    try:
        return FindingCard.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — never let one bad card break listing
        logger.warning("finding_card_load_failed", path=str(path), error=str(exc))
        return None


def finding_cards(root: Path | None = None) -> list[FindingCard]:
    """Every card, resolved **by id**, attempt-bound cards winning over legacy files.

    A new card lives in the attempt directory that produced it, so it can never
    silently inherit a later run's overwritten curve. The newest validated attempt's
    card for an id wins; a legacy flat ``<id>.json`` card fills the ids no attempt
    covers. Files are only ever read, sorted by id, and an unreadable file is skipped
    with a warning (D3, ADR-0025 Decision 8).
    """
    base = _root(root)
    cards: dict[str, FindingCard] = {}
    for attempt in validated_attempts(base):
        for artifact in attempt.record.artifacts:
            if not artifact.path.endswith(".json") or artifact.path == ATTEMPT_MANIFEST_NAME or artifact.path == MANIFEST_NAME:
                continue
            card = _load_attempt_card(attempt.directory / artifact.path)
            if card is not None:
                cards.setdefault(card.id, card)
    for card in load_finding_cards(base):
        cards.setdefault(card.id, card)
    return [cards[card_id] for card_id in sorted(cards)]
