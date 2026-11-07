"""Immutable per-attempt arena evidence: one attempt directory per invocation (E01b.1).

ADR-0025 (Decisions 2-5, 7-9 and the frozen E01-1) makes a research run an
addressable record. E01a built the **run** record; this module builds the
**attempt** record: the account of one invocation of the arena, which pairs it
requested, what happened to each of them, and the comparison artifacts (the three
arena FindingCards and the benchmark curve) that describe the comparison rather
than one run.

Layout::

    <root>/attempts/<UTC-timestamp>Z-<short-attempt-hash>/   # e.g. attempts/20261106T101500Z-1f2e3d4c5b6a/
        benchmark__equity.parquet   # immutable copy, only when the curve is non-empty
        <card-id>.json              # the arena's comparison cards, written strictly
        attempt.json                # written last, inside the claimed directory

An attempt directory is **finalized** only when ``attempt.json`` parses and every
artifact it lists exists with the recorded size and SHA-256 (see
:func:`validate_attempt_directory`). Attempt evidence is auxiliary storage under
``data/findings/`` — not cataloged, not pointblank-validated (ADR-0025 Decision 1,
ADR-0006).

Attempt identity (E01b.1): ``attempt_id`` is the SHA-256 of the **inputs** only —
protocol, the requested pairs, the canonical universe, the exact date window, the
initial cash, the markets and the applied costs — serialized in the documented
field order of :data:`ATTEMPT_FIELD_ORDER`. It identifies inputs, not an
invocation instant: two identical invocations share an ``attempt_id`` and get
**distinct** directories (the timestamp and a collision discriminator live in the
directory name, never in the ID). Timestamps are recorded in the record but are
not identity.

This stage writes **no** legacy path: no ``<strategy>__<regime>/`` directory, no
flat ``benchmark__equity.parquet`` and no flat ``<card-id>.json`` card. Legacy
files written before E01 stay frozen, readable and are never rewritten, migrated
or relabelled.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from equity_lake.backtesting.evidence import (
    ArtifactRecord,
    CostApplication,
    FinalisedRun,
    artifact_records,
    discard_directory,
    file_digest,
    publish_evidence_directory,
    read_json_artifact_strictly,
    write_card_strictly,
)
from equity_lake.core.paths import FINDINGS_DIR
from equity_lake.findings import FindingCard

logger = structlog.get_logger(__name__)

#: Attempt-format protocol identifier. Part of the attempt scope: a protocol bump is
#: a different set of inputs and therefore a different attempt ID.
ATTEMPT_PROTOCOL = "equity-arena-attempt/1"

#: Subdirectory of the findings root that holds immutable attempt directories.
ATTEMPTS_DIRNAME = "attempts"

ATTEMPT_MANIFEST_NAME = "attempt.json"
BENCHMARK_ATTEMPT_ARTIFACT = "benchmark__equity.parquet"

#: Documented canonical attempt field order (E01b.1) and the source of truth for the
#: attempt-ID preimage: :meth:`AttemptScope.scope_payload` derives its key order from
#: this tuple, so the preimage can never silently drift from the documented order
#: (the hazard the E01a ``SCOPE_FIELD_ORDER`` test now also pins). ``requested`` is
#: serialized in sorted ``(strategy, cost_regime)`` order, ``universe``/``markets``
#: sorted and de-duplicated, and ``costs`` in regime-name order, so two invocations
#: that describe the same inputs hash identically regardless of input ordering.
ATTEMPT_FIELD_ORDER: tuple[str, ...] = (
    "protocol",
    "requested",
    "universe",
    "start_date",
    "end_date",
    "initial_cash",
    "markets",
    "costs",
)

#: Evidence-ref token naming the attempt's own benchmark artifact when that artifact is
#: **not** written: an empty curve means the attempt directory holds no benchmark copy,
#: so a card must report the absent immutable artifact instead of naming a path.
UNEVIDENCED_BENCHMARK_REF = "unevidenced:benchmark"

#: Reasons an outcome can be recorded as ``unevidenced`` (no run directory exists).
UNEVIDENCED_REASONS: tuple[str, ...] = ("undated_curve", "applied_costs_not_recorded")

_TEMP_PREFIX = ".tmp-"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_NOTE_LENGTH = 200

PairKind = Literal["completed", "failed", "missing", "contradictory", "unevidenced"]
AttemptStatus = Literal["complete", "partial", "zero_completion", "write_failed"]
EvaluationStatus = Literal["evaluated", "no_trades", "ineligible"]
UnevidencedReason = Literal["undated_curve", "applied_costs_not_recorded"]

__all__ = [
    "ATTEMPTS_DIRNAME",
    "ATTEMPT_FIELD_ORDER",
    "ATTEMPT_MANIFEST_NAME",
    "ATTEMPT_PROTOCOL",
    "BENCHMARK_ATTEMPT_ARTIFACT",
    "UNEVIDENCED_BENCHMARK_REF",
    "UNEVIDENCED_REASONS",
    "AttemptRecord",
    "AttemptScope",
    "AttemptStatus",
    "AttemptValidation",
    "FinalisedAttempt",
    "PairOutcome",
    "PairRequest",
    "RunWriteFailure",
    "attempt_id_for",
    "attempts_root_for",
    "bind_cards_to_evidence",
    "is_finalized_attempt",
    "iter_attempt_directories",
    "validate_attempt_directory",
    "write_attempt_record",
]


# ---------------------------------------------------------------------------
# Pair models (Pydantic v2 write boundary, ADR-0006 / ADR-0025)
# ---------------------------------------------------------------------------


class PairRequest(BaseModel):
    """One requested ``(strategy, cost_regime)`` pair, in request order."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(..., min_length=1, description="Arena strategy identity")
    cost_regime: str = Field(..., min_length=1, description="Cost-regime identity")

    @field_validator("strategy", "cost_regime")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a requested pair identity must be non-empty")
        return stripped

    @property
    def slug(self) -> str:
        """The legacy ``<strategy>__<regime>`` slug this pair projects onto."""
        return f"{self.strategy}__{self.cost_regime}"


class PairOutcome(BaseModel):
    """What happened to one requested pair (ADR-0025 Decision 5).

    The record owns the *classification*; the CLI owns the wording, so a ``missing``
    or ``contradictory`` outcome carries no free-text requirement. The fields each
    kind requires and forbids are enforced here, so a caller can never record a run
    identity on a pair that produced no evidence, or a completed outcome without the
    run directory it names.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(..., min_length=1)
    cost_regime: str = Field(..., min_length=1)
    outcome: PairKind
    run_id: str | None = Field(default=None, description="Scope-only run ID, completed outcomes only")
    run_dir: str | None = Field(default=None, description="Finalized run directory name, completed outcomes only")
    evaluation_status: EvaluationStatus | None = Field(default=None, description="The run's evaluability verdict")
    error_type: str | None = Field(default=None, description="Failure kind, failed outcomes only")
    reason: str | None = Field(default=None, description="Failure detail (failed) or fixed reason (unevidenced)")

    @field_validator("strategy", "cost_regime")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("an outcome pair identity must be non-empty")
        return stripped

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("run_id must be a lowercase hex sha256 digest")
        return value

    @model_validator(mode="after")
    def _fields_match_outcome(self) -> PairOutcome:
        run_fields = {"run_id": self.run_id, "run_dir": self.run_dir, "evaluation_status": self.evaluation_status}
        if self.outcome == "completed":
            missing = [name for name, value in run_fields.items() if value is None or (isinstance(value, str) and not value.strip())]
            if missing:
                raise ValueError(f"a completed outcome must record the run evidence it names: missing {missing}")
            if self.reason is not None or self.error_type is not None:
                raise ValueError("a completed outcome must not record a reason or error_type: it names measured run evidence")
            return self
        present = [name for name, value in run_fields.items() if value is not None]
        if present:
            raise ValueError(f"a '{self.outcome}' outcome must not record run fields {present}: no run evidence exists for it")
        if self.outcome == "failed":
            if self.error_type is None or not self.error_type.strip():
                raise ValueError("a failed outcome must record a non-empty error_type")
            if self.reason is None or not self.reason.strip():
                raise ValueError("a failed outcome must record a non-empty reason")
            return self
        if self.outcome == "unevidenced":
            if self.reason not in UNEVIDENCED_REASONS:
                raise ValueError(f"an unevidenced outcome must record one of {list(UNEVIDENCED_REASONS)}, got {self.reason!r}")
            if self.error_type is not None:
                raise ValueError("an unevidenced outcome is a skip, not a failure: it must not record an error_type")
            return self
        # missing / contradictory: classified, no run fields, no error_type, wording owned by the caller.
        if self.error_type is not None:
            raise ValueError(f"a '{self.outcome}' outcome must not record an error_type")
        return self

    @property
    def slug(self) -> str:
        """The legacy ``<strategy>__<regime>`` slug this outcome projects onto."""
        return f"{self.strategy}__{self.cost_regime}"


class RunWriteFailure(BaseModel):
    """One run-evidence write that raised during the invocation (Decision 5).

    ``status='write_failed'`` is a claim that at least one write raised, so the record
    must name that write instead of asking a reader to trust the label: the pair it
    belonged to, the exception type, and the bounded reason the writer recorded. This
    is what makes an all-``completed`` record unable to claim ``write_failed``.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(..., min_length=1, description="Strategy of the pair whose run evidence write raised")
    cost_regime: str = Field(..., min_length=1, description="Cost regime of the pair whose run evidence write raised")
    error_type: str = Field(..., min_length=1, description="Exception type the failed run-evidence write raised")
    reason: str = Field(..., min_length=1, description="Bounded, non-secret failure reason as recorded by the writer")

    @field_validator("strategy", "cost_regime", "error_type", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a recorded run-evidence write failure needs a non-empty value for every field")
        return stripped

    @property
    def slug(self) -> str:
        """The legacy ``<strategy>__<regime>`` slug this failure belongs to."""
        return f"{self.strategy}__{self.cost_regime}"


class AttemptScope(BaseModel):
    """The identity-bearing inputs of one arena invocation (E01b.1).

    ``attempt_id`` hashes exactly :data:`ATTEMPT_FIELD_ORDER` in that order, from
    this canonicalised model. Code identity, timestamps and artifact hashes are
    **not** scope: they never move an attempt ID, and two identical invocations
    share one ID while occupying distinct directories.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(default=ATTEMPT_PROTOCOL, min_length=1, description="Attempt protocol/version identity")
    requested: list[PairRequest] = Field(default_factory=list, description="Every requested pair, in request order")
    universe: list[str] = Field(..., min_length=1, description="Instruments the invocation was scoped to")
    start_date: date
    end_date: date
    initial_cash: float = Field(..., gt=0.0, description="Starting capital")
    markets: list[str] = Field(..., min_length=1, description="Markets the invocation was scoped to")
    costs: list[CostApplication] = Field(
        default_factory=list,
        description="Applied cost applications for the regimes whose fees/taxes are known",
    )

    @field_validator("universe", "markets")
    @classmethod
    def _canonicalise(cls, value: list[str]) -> list[str]:
        canonical = sorted({item.strip() for item in value})
        if not canonical or any(not item for item in canonical):
            raise ValueError("scope identities must be non-empty after canonicalisation")
        return canonical

    @field_validator("costs")
    @classmethod
    def _canonicalise_costs(cls, value: list[CostApplication]) -> list[CostApplication]:
        regimes = [cost.regime for cost in value]
        if len(regimes) != len(set(regimes)):
            raise ValueError("scope costs must name each cost regime at most once")
        return sorted(value, key=lambda cost: cost.regime)

    @field_validator("initial_cash")
    @classmethod
    def _finite_cash(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("initial_cash must be finite")
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> AttemptScope:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self

    def scope_payload(self) -> dict[str, Any]:
        """The canonical attempt mapping, in the documented :data:`ATTEMPT_FIELD_ORDER`."""
        values: dict[str, Any] = {
            "protocol": self.protocol,
            "requested": [
                {"strategy": pair.strategy, "cost_regime": pair.cost_regime}
                for pair in sorted(self.requested, key=lambda pair: (pair.strategy, pair.cost_regime))
            ],
            "universe": list(self.universe),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_cash": self.initial_cash,
            "markets": list(self.markets),
            "costs": [
                {"regime": cost.regime, "fee_ratio": cost.fee_ratio, "tax_ratio": cost.tax_ratio}
                for cost in sorted(self.costs, key=lambda cost: cost.regime)
            ],
        }
        # Derived from the documented tuple, never hand-ordered here.
        return {field: values[field] for field in ATTEMPT_FIELD_ORDER}

    def canonical_scope_json(self) -> str:
        """Compact canonical JSON of :meth:`scope_payload` — the attempt-ID preimage."""
        return json.dumps(self.scope_payload(), separators=(",", ":"), ensure_ascii=True, allow_nan=False)

    def attempt_id(self) -> str:
        """Inputs-only SHA-256 attempt ID (E01b.1)."""
        return hashlib.sha256(self.canonical_scope_json().encode("utf-8")).hexdigest()


class AttemptRecord(BaseModel):
    """The record that makes an arena invocation addressable evidence (E01b.1)."""

    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(default=ATTEMPT_PROTOCOL, min_length=1)
    attempt_id: str = Field(..., description="Inputs-only sha256 attempt ID (E01b.1)")
    attempt_dir: str = Field(..., min_length=1, description="Finalized directory name under <root>/attempts/")
    started_at: datetime = Field(..., description="Aware UTC timestamp when the invocation started")
    finalized_at: datetime = Field(..., description="Aware UTC timestamp when its evidence validated")
    status: AttemptStatus
    requested: list[PairRequest] = Field(default_factory=list)
    outcomes: list[PairOutcome] = Field(default_factory=list)
    run_write_failures: list[RunWriteFailure] = Field(
        default_factory=list,
        description="Every run-evidence write that raised; required by, and exclusive to, status 'write_failed'",
    )
    start_date: date
    end_date: date
    universe: list[str] = Field(..., min_length=1)
    initial_cash: float = Field(..., gt=0.0)
    markets: list[str] = Field(..., min_length=1)
    costs: list[CostApplication] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("attempt_id")
    @classmethod
    def _validate_attempt_id(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("attempt_id must be a lowercase hex sha256 digest")
        return value

    @field_validator("started_at", "finalized_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("universe", "markets")
    @classmethod
    def _canonicalise(cls, value: list[str]) -> list[str]:
        canonical = sorted({item.strip() for item in value})
        if not canonical or any(not item for item in canonical):
            raise ValueError("scope identities must be non-empty after canonicalisation")
        return canonical

    @field_validator("initial_cash")
    @classmethod
    def _finite_cash(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("initial_cash must be finite")
        return value

    @field_validator("costs")
    @classmethod
    def _unique_cost_regimes(cls, value: list[CostApplication]) -> list[CostApplication]:
        regimes = [cost.regime for cost in value]
        if len(regimes) != len(set(regimes)):
            raise ValueError("costs entries must be unique per regime")
        return value

    @field_validator("artifacts")
    @classmethod
    def _unique_artifact_paths(cls, value: list[ArtifactRecord]) -> list[ArtifactRecord]:
        paths = [entry.path for entry in value]
        if len(paths) != len(set(paths)):
            raise ValueError("artifacts entries must be unique per path")
        return value

    @field_validator("notes")
    @classmethod
    def _bounded_notes(cls, value: list[str]) -> list[str]:
        notes: list[str] = []
        for note in value:
            text = " ".join(str(note).split())
            if not text:
                continue
            notes.append(text if len(text) <= _MAX_NOTE_LENGTH else text[: _MAX_NOTE_LENGTH - 3].rstrip() + "...")
        return notes

    @model_validator(mode="after")
    def _finalized_after_started(self) -> AttemptRecord:
        if self.finalized_at < self.started_at:
            raise ValueError("finalized_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _outcomes_cover_the_request(self) -> AttemptRecord:
        """Every requested pair appears exactly once, in request order; nothing else appears."""
        requested_pairs = [(pair.strategy, pair.cost_regime) for pair in self.requested]
        if len(requested_pairs) != len(set(requested_pairs)):
            raise ValueError("requested pairs must be unique: a duplicated pair is one attempt, not two records")
        requested_set = set(requested_pairs)
        outcome_pairs = [(outcome.strategy, outcome.cost_regime) for outcome in self.outcomes]
        unrequested = [pair for pair in outcome_pairs if pair not in requested_set]
        if unrequested:
            raise ValueError(f"outcomes name pairs that were not requested: {unrequested}")
        if outcome_pairs != requested_pairs:
            raise ValueError(
                f"every requested pair must appear exactly once in outcomes, in request order (requested {requested_pairs}, outcomes {outcome_pairs})"
            )
        return self

    @model_validator(mode="after")
    def _status_matches_outcomes(self) -> AttemptRecord:
        """The recorded status must be the status the outcomes actually justify."""
        completed = sum(1 for outcome in self.outcomes if outcome.outcome == "completed")
        total = len(self.requested)
        if self.status == "complete":
            if total == 0 or completed != total:
                raise ValueError("a 'complete' attempt requires every requested pair completed and at least one request")
        elif self.status == "partial":
            if not 0 < completed < total:
                raise ValueError("a 'partial' attempt requires at least one completed and at least one non-completed requested pair")
        elif self.status == "zero_completion":
            if completed:
                raise ValueError("a 'zero_completion' attempt must not record a completed outcome")
            if not self.requested and not self.notes:
                raise ValueError("an empty-request 'zero_completion' attempt must carry notes naming why nothing ran")
        # 'write_failed' records at least one run-evidence write that raised; it may
        # coexist with completed outcomes, so no count rule applies to it.
        return self

    @model_validator(mode="after")
    def _write_failures_match_status(self) -> AttemptRecord:
        """``write_failed`` must name its failure; no other status may claim one.

        The status is a claim that a run-evidence write raised, so the record owns the
        evidence for it: an all-``completed`` record can never claim ``write_failed``,
        and a ``write_failed`` record can never omit the failure it names.
        """
        if self.status == "write_failed" and not self.run_write_failures:
            raise ValueError("a 'write_failed' attempt must record at least one run_write_failures entry naming the run-evidence write that raised")
        if self.status != "write_failed" and self.run_write_failures:
            raise ValueError(f"a '{self.status}' attempt must not record run_write_failures entries: no run-evidence write raised")
        requested = {(pair.strategy, pair.cost_regime) for pair in self.requested}
        unrequested = [
            (failure.strategy, failure.cost_regime) for failure in self.run_write_failures if (failure.strategy, failure.cost_regime) not in requested
        ]
        if unrequested:
            raise ValueError(f"run_write_failures name pairs that were not requested: {unrequested}")
        return self

    @model_validator(mode="after")
    def _attempt_id_matches_scope(self) -> AttemptRecord:
        """Re-derive the ID from the recorded inputs, which is what makes it tamper-evident."""
        if self.attempt_id != self.scope().attempt_id():
            raise ValueError("attempt_id does not match the scope fields recorded in this attempt")
        return self

    def scope(self) -> AttemptScope:
        """Rebuild the :class:`AttemptScope` this record claims."""
        return AttemptScope(
            protocol=self.protocol,
            requested=list(self.requested),
            universe=list(self.universe),
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            markets=list(self.markets),
            costs=list(self.costs),
        )


class FinalisedAttempt(BaseModel):
    """A published attempt directory plus the record that finalised it."""

    model_config = ConfigDict(extra="forbid")

    directory: Path
    record: AttemptRecord


class AttemptValidation(BaseModel):
    """Verdict of :func:`validate_attempt_directory`; ``reasons`` is empty when intact."""

    model_config = ConfigDict(extra="forbid")

    finalized: bool
    directory: str
    reasons: list[str] = Field(default_factory=list)
    record: AttemptRecord | None = None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def attempts_root_for(root: Path | None = None) -> Path:
    """``<findings root>/attempts`` — the parent of every immutable attempt directory."""
    return (root or FINDINGS_DIR) / ATTEMPTS_DIRNAME


def iter_attempt_directories(root: Path | None = None) -> Iterable[Path]:
    """Every candidate attempt directory under ``<root>/attempts`` (temporaries excluded).

    Ordered by name, and a name is UTC-timestamp-prefixed, so name order is time
    order. A candidate is only *evidence* once :func:`validate_attempt_directory`
    says so.
    """
    attempts_root = attempts_root_for(root)
    if not attempts_root.is_dir():
        return []
    return sorted(path for path in attempts_root.iterdir() if path.is_dir() and not path.name.startswith(_TEMP_PREFIX))


def _resolve_attempt_dir_name(attempt_id: str, started_at: datetime) -> str:
    return f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{attempt_id[:12]}"


def _build_scope(
    *,
    requested: Sequence[PairRequest | tuple[str, str]],
    universe: Sequence[str],
    start_date: date,
    end_date: date,
    initial_cash: float,
    markets: Sequence[str],
    costs: Sequence[CostApplication] = (),
) -> AttemptScope:
    """The canonical :class:`AttemptScope` for one invocation's inputs.

    The single place that turns the writer's arguments into a scope, so
    :func:`attempt_id_for` and :func:`write_attempt_record` can never derive different
    IDs from the same inputs.
    """
    pairs = [pair if isinstance(pair, PairRequest) else PairRequest(strategy=pair[0], cost_regime=pair[1]) for pair in requested]
    return AttemptScope(
        protocol=ATTEMPT_PROTOCOL,
        requested=pairs,
        universe=list(universe),
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        markets=list(markets),
        costs=list(costs),
    )


def attempt_id_for(
    *,
    requested: Sequence[PairRequest | tuple[str, str]],
    universe: Sequence[str],
    start_date: date,
    end_date: date,
    initial_cash: float,
    markets: Sequence[str],
    costs: Sequence[CostApplication] = (),
) -> str:
    """The attempt ID these inputs produce (the same derivation the writer uses).

    Exposed so a caller that must bind artifacts *before* publishing (e.g. the arena
    writer binding card evidence refs to the run IDs of this invocation) can name the
    attempt it is about to write without duplicating the preimage rules.
    """
    return _build_scope(
        requested=requested,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        markets=markets,
        costs=costs,
    ).attempt_id()


def bind_cards_to_evidence(
    cards: Sequence[FindingCard],
    *,
    runs_by_slug: Mapping[str, str],
    requested_slugs: Collection[str],
    attempt_id: str,
    benchmark_written: bool,
) -> list[FindingCard]:
    """Bind each unbound card's evidence refs to this invocation's immutable evidence.

    :func:`equity_lake.backtesting.report.build_finding_cards` emits *unbound* refs in
    the frozen legacy vocabulary (``<strategy>__<regime>/`` and
    ``benchmark__equity.parquet``); those names identify a mutable legacy location, so
    a card bearing them would silently point at whatever a different run last wrote
    there. This is the single choke point that rewrites them to run IDs (ADR-0025
    Decision 8) before the card is persisted:

    - ``<strategy>__<regime>/`` → ``run:<run_id>`` when this invocation finalised a run
      for that slug, else ``unevidenced:<slug>`` (the pair produced no immutable run,
      e.g. an undated curve or unrecorded applied costs);
    - ``benchmark__equity.parquet`` → ``attempt:<attempt_id>/benchmark__equity.parquet``
      when this attempt *writes* its own immutable copy of the curve, else
      ``unevidenced:benchmark`` (no copy was written, so the token must not name a path
      that does not exist);
    - any other entry is preserved verbatim: an unknown ref is reported honestly rather
      than guessed at or dropped.

    The candidate set is **explicit**, never inferred from the shape of a string: a ref
    is only rewritten when it is exactly ``f"{slug}/"`` for a slug in
    ``requested_slugs``. A slash-terminated token that merely *looks* like a legacy
    slug — ``custom__token/``, ``run:abc__def/``, ``C:\\reports\\foo__bar/``, or a path
    below a pair directory such as ``momentum__zero/extra.json`` — is therefore
    preserved verbatim instead of being mistaken for this invocation's evidence.

    Duplicates are removed preserving first-seen order. The inputs are never mutated:
    each card is returned as a ``model_copy`` with a new ``evidence_refs`` list.

    Args:
        cards: The cards to bind (their ``evidence_refs`` are the unbound vocabulary).
        runs_by_slug: ``<strategy>__<regime>`` → run ID for every run finalised by this
            invocation.
        requested_slugs: The ``<strategy>__<regime>`` slugs this invocation requested;
            they are the only pair refs eligible for rewriting.
        attempt_id: The ID of the attempt that will own the cards.
        benchmark_written: Whether that attempt writes
            ``benchmark__equity.parquet``. Required, so a caller cannot silently bind a
            card to an artifact the attempt omits (an empty curve writes no copy): a
            forgot flag would otherwise persist a path that resolves to nothing.
            When ``False`` the benchmark token becomes
            :data:`UNEVIDENCED_BENCHMARK_REF` instead of an attempt path.

    Returns:
        New cards with bound, de-duplicated, order-preserving evidence refs.
    """
    benchmark_ref = f"attempt:{attempt_id}/{BENCHMARK_ATTEMPT_ARTIFACT}" if benchmark_written else UNEVIDENCED_BENCHMARK_REF
    requested = set(requested_slugs)
    bound: list[FindingCard] = []
    for card in cards:
        refs: list[str] = []
        for ref in card.evidence_refs:
            if ref == BENCHMARK_ATTEMPT_ARTIFACT:
                resolved = benchmark_ref
            elif ref.endswith("/") and ref[:-1] in requested:
                slug = ref[:-1]
                run_id = runs_by_slug.get(slug)
                resolved = f"run:{run_id}" if run_id is not None else f"unevidenced:{slug}"
            else:
                resolved = ref
            if resolved not in refs:
                refs.append(resolved)
        bound.append(card.model_copy(update={"evidence_refs": refs}))
    return bound


def _derive_status(outcomes: Sequence[PairOutcome], requested: Sequence[PairRequest]) -> AttemptStatus:
    """The status the outcomes justify: complete, partial, or zero completion."""
    completed = sum(1 for outcome in outcomes if outcome.outcome == "completed")
    if completed and completed == len(requested):
        return "complete"
    if completed:
        return "partial"
    return "zero_completion"


def _bounded_fact(value: Any) -> str:
    """Render one summary fact as a short, single-line, non-secret note fragment."""
    if isinstance(value, Mapping):
        rendered = ", ".join(f"{key}={value[key]}" for key in sorted(value, key=str))
    elif isinstance(value, (list, tuple, set, frozenset)):
        rendered = ", ".join(str(item) for item in value)
    else:
        rendered = str(value)
    text = " ".join(rendered.split())
    return text if len(text) <= _MAX_NOTE_LENGTH - 40 else text[: _MAX_NOTE_LENGTH - 43].rstrip() + "..."


def _attempt_notes(outcomes: Sequence[PairOutcome], notes: Sequence[str], summary_facts: Mapping[str, Any] | None) -> list[str]:
    """Explicit notes plus the invocation's bounded, counted facts (deterministic order)."""
    resolved = [str(note) for note in notes if str(note).strip()]
    if summary_facts:
        for key in sorted(summary_facts, key=str):
            rendered = _bounded_fact(summary_facts[key])
            if rendered:
                resolved.append(f"{key}={rendered}")
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.outcome] = counts.get(outcome.outcome, 0) + 1
    if counts:
        resolved.append("outcomes: " + ", ".join(f"{kind}={counts[kind]}" for kind in sorted(counts)))
    return resolved


def _validate_completed_outcomes_backed_by_runs(outcomes: Sequence[PairOutcome], runs: Sequence[FinalisedRun]) -> None:
    """Every completed outcome names a supplied finalized run, and nothing else is supplied.

    A completed outcome is a claim that run evidence exists; without the matching
    :class:`FinalisedRun` the claim cannot be checked, so it is refused rather than
    recorded on trust.
    """
    supplied = {(run.manifest.run_id, run.directory.name): run for run in runs}
    completed = [outcome for outcome in outcomes if outcome.outcome == "completed"]
    if completed and not runs:
        raise ValueError("a completed outcome requires the finalized run evidence it names; no runs were supplied")
    referenced: set[tuple[str, str]] = set()
    for outcome in completed:
        key = (str(outcome.run_id), str(outcome.run_dir))
        if key not in supplied:
            raise ValueError(
                f"completed outcome {outcome.slug} names run {outcome.run_id} in directory {outcome.run_dir!r} but no such finalized run was supplied"
            )
        referenced.add(key)
    unrecorded = sorted(set(supplied) - referenced)
    if unrecorded:
        raise ValueError(f"finalized runs were supplied that no completed outcome names: {unrecorded}")


def _write_benchmark_copy(benchmark: pl.DataFrame, directory: Path) -> Path:
    """Write an immutable copy of the benchmark curve after refusing an unusable one.

    The copy is a real file inside the attempt directory (never a link to the
    findings root), so a later attempt can never change what this one recorded. A
    non-finite or null equity value is refused: the benchmark is comparison
    evidence, and a non-finite number there is a data defect.
    """
    if list(benchmark.columns) != ["date", "equity"]:
        raise ValueError(f"{BENCHMARK_ATTEMPT_ARTIFACT} must carry exactly ['date', 'equity'], got {list(benchmark.columns)}")
    if benchmark["date"].null_count():
        raise ValueError(f"{BENCHMARK_ATTEMPT_ARTIFACT} carries a null date: the benchmark is evidence and needs dated marks")
    for index, value in enumerate(benchmark["equity"].to_list()):
        if value is None:
            raise ValueError(f"{BENCHMARK_ATTEMPT_ARTIFACT} value at row {index} is null: a benchmark point is a measurement")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{BENCHMARK_ATTEMPT_ARTIFACT} value at row {index} is not a number ({value!r})") from None
        if not math.isfinite(numeric):
            raise ValueError(f"{BENCHMARK_ATTEMPT_ARTIFACT} value at row {index} is non-finite ({numeric!r}): never persisted as evidence")
    path = directory / BENCHMARK_ATTEMPT_ARTIFACT
    benchmark.write_parquet(path)
    return path


def write_attempt_record(
    root: Path,
    *,
    requested: Sequence[PairRequest | tuple[str, str]],
    outcomes: Sequence[PairOutcome],
    run_write_failures: Sequence[RunWriteFailure] = (),
    start_date: date,
    end_date: date,
    universe: Sequence[str],
    initial_cash: float,
    markets: Sequence[str],
    costs: Sequence[CostApplication] = (),
    benchmark: pl.DataFrame | None = None,
    cards: Sequence[FindingCard] = (),
    runs: Sequence[FinalisedRun] = (),
    summary_facts: Mapping[str, Any] | None = None,
    status: AttemptStatus | None = None,
    notes: Sequence[str] = (),
    started_at: datetime | None = None,
    finalized_at: datetime | None = None,
) -> FinalisedAttempt:
    """Publish one immutable attempt directory under ``<root>/attempts``.

    Builds in a temporary directory inside ``<root>/attempts`` (prefix ``.tmp-``), so
    a partial write is never visible as an attempt: the benchmark copy (only when the
    curve is non-empty) and the comparison cards are written, proven strict, and
    digested; then the directory is claimed with an atomic ``os.mkdir`` loop
    (``-2``, ``-3`` discriminator, same discipline as the run publisher) and
    ``attempt.json`` is written **last**, so a half-published directory is never a
    finalized attempt. No legacy path is written.

    Args:
        root: Findings root (injectable for tests); defaults to ``FINDINGS_DIR``.
        requested: Every requested pair, in request order (``PairRequest`` or
            ``(strategy, cost_regime)`` tuples).
        outcomes: One classified outcome per requested pair, in request order.
        run_write_failures: One entry per run-evidence write that raised (required by
            ``status='write_failed'``, forbidden for every other status).
        start_date/end_date: The exact date window, as requested.
        universe: The instruments this invocation was scoped to (non-empty).
        initial_cash: Starting capital (finite, positive).
        markets: The markets this invocation was scoped to (non-empty).
        costs: The applied cost applications for the regimes whose fees/taxes are
            known; a regime with no recorded application is simply absent.
        benchmark: The comparison benchmark curve, copied when non-empty.
        cards: The comparison FindingCards this attempt owns.
        runs: The finalized run evidence a ``completed`` outcome names.
        summary_facts: Bounded, non-secret counted facts recorded into ``notes``.
        status: Explicit status (used for ``write_failed``); derived from the
            outcomes when omitted.
        notes: Explicit notes (e.g. why nothing ran).
        started_at/finalized_at: Aware UTC timestamps (injectable for tests). They
            are recorded but are never part of the attempt ID.

    Returns:
        The published :class:`FinalisedAttempt`.

    Raises:
        ValueError: when the record is inconsistent (a completed outcome without its
            run, an outcome for a pair that was not requested, a status the outcomes
            do not justify, an unusable benchmark curve, or a non-finite card value).
    """
    started = (started_at or datetime.now(UTC)).astimezone(UTC)
    resolved_costs = list(costs)
    scope = _build_scope(
        requested=requested,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        markets=markets,
        costs=resolved_costs,
    )
    pairs = list(scope.requested)
    attempt_id = scope.attempt_id()
    resolved_status: AttemptStatus = status if status is not None else _derive_status(outcomes, pairs)
    resolved_notes = _attempt_notes(outcomes, notes, summary_facts)
    _validate_completed_outcomes_backed_by_runs(outcomes, runs)

    attempts_root = attempts_root_for(root)
    attempts_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=attempts_root))
    try:
        if benchmark is not None and not benchmark.is_empty():
            _write_benchmark_copy(benchmark, tmp_dir)
        for card in cards:
            write_card_strictly(card, tmp_dir)
        record = AttemptRecord(
            protocol=ATTEMPT_PROTOCOL,
            attempt_id=attempt_id,
            attempt_dir=_resolve_attempt_dir_name(attempt_id, started),
            started_at=started,
            finalized_at=(finalized_at or datetime.now(UTC)).astimezone(UTC),
            status=resolved_status,
            requested=pairs,
            outcomes=list(outcomes),
            run_write_failures=list(run_write_failures),
            start_date=start_date,
            end_date=end_date,
            universe=list(scope.universe),
            initial_cash=float(initial_cash),
            markets=list(scope.markets),
            costs=list(scope.costs),
            artifacts=artifact_records(tmp_dir, exclude=(ATTEMPT_MANIFEST_NAME,)),
            notes=resolved_notes,
        )

        def _finalize(target: Path) -> None:
            # The marker is written last, inside the claimed directory, with the
            # claimed (possibly discriminated) name recorded in it.
            published = record.model_copy(update={"attempt_dir": target.name})
            (target / ATTEMPT_MANIFEST_NAME).write_text(published.model_dump_json(indent=2), encoding="utf-8")

        target = publish_evidence_directory(tmp_dir, record.attempt_dir, _finalize)
    except BaseException:
        discard_directory(tmp_dir)
        raise

    published_record = record.model_copy(update={"attempt_dir": target.name})
    logger.info(
        "attempt_published",
        attempt_dir=str(target),
        attempt_id=attempt_id,
        status=resolved_status,
        outcomes=len(outcomes),
        cards=len(cards),
    )
    return FinalisedAttempt(directory=target, record=published_record)


def validate_attempt_directory(directory: Path) -> AttemptValidation:
    """Decide whether ``directory`` is a finalized, intact attempt (mirrors a run).

    An attempt is finalized only when ``attempt.json`` parses as an
    :class:`AttemptRecord`, its ``attempt_dir`` matches the directory name, every
    recorded artifact exists with the recorded size and SHA-256, and every recorded
    JSON artifact (cards included) parses **strictly** — no ``NaN``/``Infinity``
    token. ``reasons`` lists every failing check, each distinct, so a caller can
    report *why* evidence is not usable instead of silently skipping it.
    """
    reasons: list[str] = []
    if not directory.is_dir():
        return AttemptValidation(finalized=False, directory=str(directory), reasons=["attempt directory does not exist"])

    manifest_path = directory / ATTEMPT_MANIFEST_NAME
    if not manifest_path.is_file():
        return AttemptValidation(
            finalized=False,
            directory=str(directory),
            reasons=[f"{ATTEMPT_MANIFEST_NAME} missing: the directory was never finalized"],
        )
    try:
        record = AttemptRecord.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse/validation failure is a verdict, not a crash
        return AttemptValidation(finalized=False, directory=str(directory), reasons=[f"{ATTEMPT_MANIFEST_NAME} invalid: {exc}"])

    if record.attempt_dir != directory.name:
        reasons.append(f"attempt_dir '{record.attempt_dir}' does not match directory '{directory.name}'")

    for artifact in record.artifacts:
        path = directory / artifact.path
        if not path.is_file():
            reasons.append(f"artifact missing: '{artifact.path}'")
            continue
        digest, size = file_digest(path)
        if size != artifact.bytes:
            reasons.append(f"artifact size mismatch: '{artifact.path}' (recorded {artifact.bytes}, found {size})")
        if digest != artifact.sha256:
            reasons.append(f"artifact hash mismatch: '{artifact.path}'")

    for artifact in record.artifacts:
        if not artifact.path.endswith(".json"):
            continue
        path = directory / artifact.path
        if not path.is_file():
            continue  # already reported as missing
        try:
            read_json_artifact_strictly(path, artifact.path)
        except ValueError as exc:
            # A distinct reason (non-finite token / unparsable text) so a caller can
            # tell tampered JSON from a missing artifact.
            reasons.append(str(exc))

    return AttemptValidation(
        finalized=not reasons,
        directory=str(directory),
        reasons=reasons,
        record=record if not reasons else None,
    )


def is_finalized_attempt(directory: Path) -> bool:
    """Whether ``directory`` is a finalized, intact attempt (see the validator)."""
    return validate_attempt_directory(directory).finalized
