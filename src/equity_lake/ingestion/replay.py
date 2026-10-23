"""Explicit, bounded, idempotent replay orchestration.

The replay layer deliberately depends on caller-supplied source, processing,
Silver-write, and authoritative Silver-read seams. It never sends historical
notifications and never treats its auxiliary checkpoint as proof that Silver
rows were committed.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from equity_lake.core.paths import DATA_DIR

MAX_REPLAY_ITEMS = 100
MAX_REPLAY_BATCH_SIZE = 25


class ReplayError(RuntimeError):
    """Base class for replay contract failures."""


class ReplayCheckpointError(ReplayError):
    """Raised when a replay checkpoint is corrupt, unreadable, or out of scope."""


class ReplayDurabilityError(ReplayError):
    """Raised when Silver does not authoritatively confirm a completed batch."""


class ReplayAuthorizationError(ReplayError):
    """Raised when paid processing lacks an explicit bounded authorization."""


class ReplayScope(BaseModel):
    """Required source/date/market/instrument scope for one replay invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    trading_date: date
    market: str = Field(min_length=1)
    instruments: tuple[str, ...]

    @field_validator("source", "market")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scope values must not be blank")
        return normalized

    @field_validator("instruments")
    @classmethod
    def _normalize_instruments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({instrument.strip() for instrument in value if instrument.strip()}))
        if not normalized:
            raise ValueError("replay requires at least one non-empty instrument")
        return normalized


class ReplayItem(BaseModel):
    """One typed candidate from the explicitly scoped source selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    trading_date: date
    market: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("item_id", "source", "market", "instrument")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("replay item identity fields must not be blank")
        return normalized


class ReplayCheckpoint(BaseModel):
    """Typed auxiliary checkpoint; only the last durable batch is recorded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    scope: ReplayScope
    completed_item_ids: list[str] = Field(max_length=MAX_REPLAY_BATCH_SIZE)

    @field_validator("completed_item_ids")
    @classmethod
    def _unique_ids(cls, value: list[str]) -> list[str]:
        if any(not item_id.strip() for item_id in value):
            raise ValueError("checkpoint item IDs must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("checkpoint item IDs must be unique")
        return sorted(value)


class ReplaySpendLedger(BaseModel):
    """Durable auxiliary paid-work accounting, separate from the Silver table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    scope: ReplayScope
    approved_estimate: Decimal
    actual_spend_cap: Decimal
    estimated_cost: Decimal = Decimal("0")
    actual_spend: Decimal = Decimal("0")
    pending_item_ids: list[str] = Field(default_factory=list, max_length=MAX_REPLAY_BATCH_SIZE)
    pending_estimate: Decimal | None = None

    @field_validator("approved_estimate", "actual_spend_cap", "estimated_cost", "actual_spend", "pending_estimate")
    @classmethod
    def _finite_nonnegative_money(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return value
        if not value.is_finite() or value < 0:
            raise ValueError("replay ledger amounts must be finite and non-negative")
        return value

    @field_validator("pending_item_ids")
    @classmethod
    def _unique_pending_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item_id.strip() for item_id in value):
            raise ValueError("pending replay item IDs must be non-empty and unique")
        return sorted(value)


@dataclass(frozen=True)
class PaidWorkBudget:
    """Explicit per-batch paid-work limits passed to the processor."""

    approved_estimate: Decimal
    actual_spend_cap: Decimal
    remaining_allowance: Decimal
    batch_estimate: Decimal
    prior_actual_spend: Decimal


@dataclass(frozen=True)
class ReplayBatchResult:
    """Processor output and provider-reported actual cost for one batch."""

    rows: Sequence[Mapping[str, Any]]
    actual_cost: Decimal


@dataclass(frozen=True)
class ReplayResult:
    """Bounded summary of completed and deferred work for one invocation."""

    scope: ReplayScope
    selected_item_ids: tuple[str, ...]
    processed_item_ids: tuple[str, ...]
    already_processed_count: int
    deferred_count: int
    batch_count: int
    estimated_cost: Decimal
    actual_spend: Decimal
    remaining_allowance: Decimal


ProcessBatch = Callable[[Sequence[ReplayItem], PaidWorkBudget], ReplayBatchResult]
EstimateBatchCost = Callable[[Sequence[ReplayItem]], Decimal]
WriteSilver = Callable[[Sequence[Mapping[str, Any]]], bool | None]
ReadProcessedIds = Callable[[ReplayScope], Iterable[str]]


def _replay_bounded_unlocked(
    *,
    scope: ReplayScope,
    items: Iterable[ReplayItem],
    checkpoint_path: Path,
    state_namespace: str,
    process_batch: ProcessBatch | None,
    estimate_batch_cost: EstimateBatchCost | None = None,
    write_silver: WriteSilver,
    read_processed_ids: ReadProcessedIds,
    paid_processing_authorized: bool = False,
    approved_estimate: Decimal | None = None,
    actual_spend_cap: Decimal = Decimal("0"),
) -> ReplayResult:
    """Replay at most 100 scoped items in deterministic batches of at most 25.

    ``read_processed_ids`` is the authoritative idempotency source and must
    query durable Silver for *scope*. A checkpoint is validated and written as
    an atomic auxiliary hint, but is never used to skip items. The Silver write
    callback must return only after the write is durable; this function rereads
    authoritative state before advancing the checkpoint.

    Every batch is estimated before processing and receives the remaining
    invocation allowance. The processor must report finite actual cost in its
    result; the durable ledger accounts for reported cost across checkpoint
    restarts. A pending reservation is reconciled only when authoritative Silver
    confirms every reserved item; an ambiguous/missing Silver outcome fails
    closed rather than risk repeating paid work.

    Processing is denied by default. Before any processing callback can run,
    callers must explicitly authorize paid processing and provide a finite
    approved estimate plus a distinct positive actual-spend cap. No notification
    callback is accepted, so this historical replay path cannot send alerts.
    """
    budget = _validate_paid_budget(
        paid_processing_authorized=paid_processing_authorized,
        approved_estimate=approved_estimate,
        actual_spend_cap=actual_spend_cap,
    )
    _load_checkpoint(checkpoint_path, scope)

    scoped_by_id: dict[str, ReplayItem] = {}
    for item in items:
        if not _matches_scope(item, scope):
            continue
        existing = scoped_by_id.get(item.item_id)
        if existing is not None and existing != item:
            raise ValueError(f"Conflicting replay items share item_id={item.item_id!r}")
        scoped_by_id[item.item_id] = item

    scoped_items = sorted(scoped_by_id.values(), key=lambda item: item.item_id)
    initially_processed = _authoritative_ids(read_processed_ids, scope)
    pending_items = [item for item in scoped_items if item.item_id not in initially_processed]
    selected_items = pending_items[:MAX_REPLAY_ITEMS]
    deferred_count = max(0, len(pending_items) - len(selected_items))
    if selected_items and budget is None:
        raise ReplayAuthorizationError("pending replay work is disabled by the zero-paid-authorization default")
    if selected_items and estimate_batch_cost is None:
        raise ReplayAuthorizationError("paid replay requires a scope-relevant batch cost estimate")
    if selected_items and process_batch is None:
        raise ReplayAuthorizationError("paid replay requires a processor that reports actual batch cost")

    ledger_path = _spend_ledger_path(scope, state_namespace)
    ledger = _load_spend_ledger(ledger_path, scope, budget) if budget is not None else None
    actual_spend = ledger.actual_spend if ledger is not None else Decimal("0")
    estimated_cost = ledger.estimated_cost if ledger is not None else Decimal("0")
    if ledger is not None and ledger.pending_item_ids:
        # A prior process may have committed Silver but died before reconciling
        # its ledger. Silver is authoritative: reconcile only when every
        # reserved item is present, otherwise the provider outcome is ambiguous
        # and replay must remain fail-closed.
        if set(ledger.pending_item_ids) <= initially_processed:
            ledger = ledger.model_copy(update={"pending_item_ids": [], "pending_estimate": None})
            _write_spend_ledger_atomic(ledger_path, ledger)
        else:
            raise ReplayAuthorizationError("an interrupted paid batch is pending in the durable spend ledger")
    if budget is not None and actual_spend > budget.actual_spend_cap:
        raise ReplayAuthorizationError("durable replay spend already exceeds the authorized cap")

    processed_this_run: list[str] = []
    skipped_count = sum(item.item_id in initially_processed for item in scoped_items)
    batch_count = 0

    for offset in range(0, len(selected_items), MAX_REPLAY_BATCH_SIZE):
        candidates = selected_items[offset : offset + MAX_REPLAY_BATCH_SIZE]
        latest_processed = _authoritative_ids(read_processed_ids, scope)
        batch = [item for item in candidates if item.item_id not in latest_processed]
        skipped_count += len(candidates) - len(batch)
        if not batch:
            continue

        if budget is None or estimate_batch_cost is None or process_batch is None:
            raise ReplayAuthorizationError("paid-work budget disappeared before processing")
        batch_estimate = _validate_money(estimate_batch_cost(batch), "batch estimate")
        if estimated_cost + batch_estimate > budget.approved_estimate:
            raise ReplayAuthorizationError("cumulative scoped batch estimate exceeds the approved estimate")
        remaining_allowance = budget.actual_spend_cap - actual_spend
        if batch_estimate > remaining_allowance:
            raise ReplayAuthorizationError("batch estimate exceeds the remaining actual-spend allowance")

        if ledger is None:
            raise ReplayAuthorizationError("paid-work spend ledger was not initialized")
        ledger = ledger.model_copy(
            update={
                "pending_item_ids": [item.item_id for item in batch],
                "pending_estimate": batch_estimate,
            }
        )
        _write_spend_ledger_atomic(ledger_path, ledger)
        batch_budget = PaidWorkBudget(
            approved_estimate=budget.approved_estimate,
            actual_spend_cap=budget.actual_spend_cap,
            remaining_allowance=remaining_allowance,
            batch_estimate=batch_estimate,
            prior_actual_spend=actual_spend,
        )
        batch_result = process_batch(batch, batch_budget)
        if not isinstance(batch_result, ReplayBatchResult):
            raise ReplayAuthorizationError("processor must report rows and finite actual cost for every paid batch")
        batch_actual_cost = _validate_money(batch_result.actual_cost, "actual batch cost")
        actual_spend += batch_actual_cost
        estimated_cost += batch_estimate
        ledger = ledger.model_copy(
            update={
                "estimated_cost": estimated_cost,
                "actual_spend": actual_spend,
                # Keep the reservation until authoritative Silver confirms all
                # rows. If the process dies after provider cost is known, the
                # cost remains accounted and the batch cannot be paid twice.
                "pending_item_ids": [item.item_id for item in batch],
                "pending_estimate": batch_estimate,
            }
        )
        _write_spend_ledger_atomic(ledger_path, ledger)
        if actual_spend > budget.actual_spend_cap:
            raise ReplayAuthorizationError("provider-reported cumulative actual spend exceeds the authorized cap")

        output_rows = list(batch_result.rows)
        _validate_output_rows(output_rows, batch, scope)
        write_result = write_silver(output_rows)
        if write_result is False:
            raise ReplayDurabilityError("Silver writer reported failure; checkpoint was not advanced")

        durable_ids = _authoritative_ids(read_processed_ids, scope)
        batch_ids = {item.item_id for item in batch}
        missing_ids = batch_ids - durable_ids
        if missing_ids:
            raise ReplayDurabilityError(f"Silver write is not durable for replay item IDs: {sorted(missing_ids)}")

        ledger = ledger.model_copy(update={"pending_item_ids": [], "pending_estimate": None})
        _write_spend_ledger_atomic(ledger_path, ledger)
        _write_checkpoint_atomic(
            checkpoint_path,
            ReplayCheckpoint(scope=scope, completed_item_ids=sorted(batch_ids)),
        )
        processed_this_run.extend(sorted(batch_ids))
        batch_count += 1

    return ReplayResult(
        scope=scope,
        selected_item_ids=tuple(item.item_id for item in selected_items),
        processed_item_ids=tuple(processed_this_run),
        already_processed_count=skipped_count,
        deferred_count=deferred_count,
        batch_count=batch_count,
        estimated_cost=estimated_cost,
        actual_spend=actual_spend,
        remaining_allowance=(budget.actual_spend_cap - actual_spend) if budget is not None else Decimal("0"),
    )


def replay_bounded(
    *,
    scope: ReplayScope,
    items: Iterable[ReplayItem],
    checkpoint_path: Path,
    state_namespace: str,
    process_batch: ProcessBatch | None,
    estimate_batch_cost: EstimateBatchCost | None = None,
    write_silver: WriteSilver,
    read_processed_ids: ReadProcessedIds,
    paid_processing_authorized: bool = False,
    approved_estimate: Decimal | None = None,
    actual_spend_cap: Decimal = Decimal("0"),
) -> ReplayResult:
    """Serialize paid work for one ledger through provider, Silver, and reconciliation."""
    ledger_path = _spend_ledger_path(scope, state_namespace)
    if not paid_processing_authorized:
        return _replay_bounded_unlocked(
            scope=scope,
            items=items,
            checkpoint_path=checkpoint_path,
            state_namespace=state_namespace,
            process_batch=process_batch,
            estimate_batch_cost=estimate_batch_cost,
            write_silver=write_silver,
            read_processed_ids=read_processed_ids,
            paid_processing_authorized=paid_processing_authorized,
            approved_estimate=approved_estimate,
            actual_spend_cap=actual_spend_cap,
        )
    # Validate the caller-selected checkpoint before waiting on the canonical
    # scope lock; the unlocked body repeats this check after lock acquisition.
    _load_checkpoint(checkpoint_path, scope)
    with _scope_file_lock(ledger_path):
        return _replay_bounded_unlocked(
            scope=scope,
            items=items,
            checkpoint_path=checkpoint_path,
            state_namespace=state_namespace,
            process_batch=process_batch,
            estimate_batch_cost=estimate_batch_cost,
            write_silver=write_silver,
            read_processed_ids=read_processed_ids,
            paid_processing_authorized=paid_processing_authorized,
            approved_estimate=approved_estimate,
            actual_spend_cap=actual_spend_cap,
        )


@contextmanager
def _scope_file_lock(ledger_path: Path) -> Iterator[None]:
    """Hold an OS-released advisory lock for one durable paid-work ledger."""
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    descriptor: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReplayAuthorizationError(f"paid-work scope lock must be a regular file: {lock_path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except ReplayAuthorizationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ReplayAuthorizationError(f"unable to acquire paid-work scope lock at {lock_path}") from exc

    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_paid_budget(
    *,
    paid_processing_authorized: bool,
    approved_estimate: Decimal | None,
    actual_spend_cap: Decimal,
) -> PaidWorkBudget | None:
    if not paid_processing_authorized:
        if approved_estimate is not None or actual_spend_cap != Decimal("0"):
            raise ReplayAuthorizationError("paid-work estimate and cap require explicit authorization")
        return None
    if approved_estimate is None:
        raise ReplayAuthorizationError("paid work requires an approved estimate and positive actual-spend cap")
    estimate = _validate_money(approved_estimate, "approved estimate")
    cap = _validate_money(actual_spend_cap, "actual-spend cap")
    if cap <= 0:
        raise ReplayAuthorizationError("paid work requires a positive actual-spend cap")
    if estimate > cap:
        raise ReplayAuthorizationError("approved estimate must be non-negative and no greater than the spend cap")
    return PaidWorkBudget(
        approved_estimate=estimate,
        actual_spend_cap=cap,
        remaining_allowance=cap,
        batch_estimate=Decimal("0"),
        prior_actual_spend=Decimal("0"),
    )


def _validate_money(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ReplayAuthorizationError(f"{label} must be a known finite non-negative Decimal")
    return value


def _matches_scope(item: ReplayItem, scope: ReplayScope) -> bool:
    return (
        item.source == scope.source
        and item.trading_date == scope.trading_date
        and item.market == scope.market
        and item.instrument in scope.instruments
    )


def _authoritative_ids(read_processed_ids: ReadProcessedIds, scope: ReplayScope) -> set[str]:
    result = read_processed_ids(scope)
    if result is None:
        raise ReplayDurabilityError("authoritative Silver reader returned no state")
    return {str(item_id) for item_id in result}


def _validate_output_rows(rows: Sequence[Mapping[str, Any]], batch: Sequence[ReplayItem], scope: ReplayScope) -> None:
    batch_by_id = {item.item_id: item for item in batch}
    output_ids: set[str] = set()
    for row in rows:
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise ReplayDurabilityError("processed Silver rows must include a non-empty item_id")
        if item_id not in batch_by_id:
            raise ReplayDurabilityError(f"processor returned an out-of-batch item_id={item_id!r}")
        source = row.get("source")
        trading_date = _as_date(row.get("date"))
        market = row.get("market")
        instrument = row.get("instrument")
        if source != scope.source or trading_date != scope.trading_date or market != scope.market:
            raise ReplayDurabilityError(f"processor returned an out-of-scope source/date/market for item_id={item_id!r}")
        if not isinstance(instrument, str) or instrument not in scope.instruments:
            raise ReplayDurabilityError(f"processor returned an out-of-scope instrument for item_id={item_id!r}")
        output_ids.add(item_id)
    missing_ids = set(batch_by_id) - output_ids
    if missing_ids:
        raise ReplayDurabilityError(f"processor produced no Silver rows for item IDs: {sorted(missing_ids)}")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _spend_ledger_path(scope: ReplayScope, state_namespace: str) -> Path:
    """Return the canonical durable paid-work ledger for a Silver scope.

    The caller-supplied checkpoint is only a recovery hint and never chooses
    budget state or its lock location. Namespace and scope are independently
    hashed so distinct Silver stores cannot share accounting accidentally and
    no caller-controlled path components enter the auxiliary-state path.
    """
    namespace = state_namespace.strip() if isinstance(state_namespace, str) else ""
    if not namespace:
        raise ReplayAuthorizationError("paid-work state requires a non-empty Silver namespace")
    namespace_digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
    scope_digest = hashlib.sha256(scope.model_dump_json().encode("utf-8")).hexdigest()
    return DATA_DIR / "replay" / namespace_digest / f"{scope_digest}.spend.json"


def _load_checkpoint(path: Path, scope: ReplayScope) -> ReplayCheckpoint | None:
    text = _read_regular_file(path, "replay checkpoint", ReplayCheckpointError)
    if text is None:
        return None
    try:
        checkpoint = ReplayCheckpoint.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise ReplayCheckpointError(f"Invalid replay checkpoint at {path}") from exc
    if checkpoint.scope != scope:
        raise ReplayCheckpointError(f"Replay checkpoint scope does not match invocation scope: {path}")
    return checkpoint


def _load_spend_ledger(
    path: Path,
    scope: ReplayScope,
    budget: PaidWorkBudget,
) -> ReplaySpendLedger:
    text = _read_regular_file(path, "paid-work spend ledger", ReplayAuthorizationError)
    if text is None:
        return ReplaySpendLedger(
            scope=scope,
            approved_estimate=budget.approved_estimate,
            actual_spend_cap=budget.actual_spend_cap,
        )
    try:
        ledger = ReplaySpendLedger.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise ReplayAuthorizationError(f"Invalid paid-work spend ledger at {path}") from exc
    if ledger.scope != scope:
        raise ReplayAuthorizationError(f"Paid-work ledger scope does not match invocation scope: {path}")
    if ledger.approved_estimate != budget.approved_estimate or ledger.actual_spend_cap != budget.actual_spend_cap:
        raise ReplayAuthorizationError("paid-work authorization must remain identical when resuming its spend ledger")
    if ledger.estimated_cost > ledger.approved_estimate:
        raise ReplayAuthorizationError("durable replay estimates already exceed the approved estimate")
    return ledger


def _read_regular_file(path: Path, label: str, error_type: type[ReplayError]) -> str | None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        if exc.errno != errno.ENOENT:
            raise error_type(f"Unable to inspect {label} at {path}") from exc
        _raise_for_dangling_ancestor(path, label, error_type)
        return None
    except OSError as exc:
        raise error_type(f"Unable to inspect {label} at {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise error_type(f"{label.capitalize()} must be a regular file: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise error_type(f"Unable to read {label} at {path}") from exc


def _raise_for_dangling_ancestor(path: Path, label: str, error_type: type[ReplayError]) -> None:
    candidate = path
    for _ in range(len(path.parts)):
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError as exc:
            if exc.errno != errno.ENOENT:
                raise error_type(f"Unable to inspect {label} at {candidate}") from exc
        except OSError as exc:
            raise error_type(f"Unable to inspect {label} at {candidate}") from exc
        else:
            if stat.S_ISLNK(candidate_stat.st_mode):
                try:
                    candidate.stat()
                except FileNotFoundError as exc:
                    if exc.errno == errno.ENOENT:
                        raise error_type(f"Dangling symlink in {label} path at {candidate}") from exc
                    raise error_type(f"Unable to inspect {label} at {candidate}") from exc
                except OSError as exc:
                    raise error_type(f"Unable to inspect {label} at {candidate}") from exc
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent


def _write_checkpoint_atomic(path: Path, checkpoint: ReplayCheckpoint) -> None:
    validated = ReplayCheckpoint.model_validate(checkpoint.model_dump())
    _write_model_atomic(path, validated)


def _write_spend_ledger_atomic(path: Path, ledger: ReplaySpendLedger) -> None:
    validated = ReplaySpendLedger.model_validate(ledger.model_dump())
    _write_model_atomic(path, validated)


def _write_model_atomic(path: Path, model: BaseModel) -> None:
    _read_regular_file(path, "replay state", ReplayCheckpointError)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(model.model_dump_json(indent=2))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


__all__ = [
    "MAX_REPLAY_BATCH_SIZE",
    "MAX_REPLAY_ITEMS",
    "EstimateBatchCost",
    "PaidWorkBudget",
    "ProcessBatch",
    "ReplayAuthorizationError",
    "ReplayBatchResult",
    "ReplayCheckpoint",
    "ReplayCheckpointError",
    "ReplayDurabilityError",
    "ReplayError",
    "ReplayItem",
    "ReplayResult",
    "ReplayScope",
    "replay_bounded",
]
