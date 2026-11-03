"""Canonical portfolio import CSV: parse, validate, preview, commit (ADR-0024).

One frozen CSV schema is the only supported import format — there is no generic
broker-mapping/plugin surface (ADR-0024 rejected alternatives). Every value is
parsed explicitly: money and quantity from decimal *text* (never via float),
timestamps from ISO-8601 with a required timezone, and identity from an exact
``market``/``MIC``/``symbol`` triple that is never guessed from ticker text.

Row numbers in messages are 1-based *file line numbers*, so the header is line 1
and the first data row is row 2.

Three entry points, one validation path:

* :func:`parse_csv_text` / :func:`parse_csv_file` — file to validated events.
* :func:`preview_events` — full validation plus proposed effects; touches no
  database, creates no directory and writes no ``-journal``/``-wal``/``-shm``
  file (ADR-0024 decision 13).
* :func:`commit_events` — the same validation, then one atomic append.

Every event carries its source row, so batch-level validation issues are
reported as ``row <n>`` too. Messages never echo the amount, quantity, price,
basis or instrument symbol that failed (ADR-0024 decision 11): they name the
row, the column or event type, the external ID and the reason category. Pydantic
failures are rendered by
:func:`equity_lake.portfolio.models.redacted_validation_message`, the package's
single redaction rule, so no input value (and no ``input_value=``) is copied.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from equity_lake.core.instruments import InstrumentKey
from equity_lake.portfolio.models import (
    SUPPORTED_CURRENCY,
    SUPPORTED_MARKET,
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    LedgerIssue,
    LedgerValidationError,
    event_order_key,
    instrument_label,
    quantity_delta,
    redacted_validation_message,
    validate_batch,
)
from equity_lake.portfolio.store import AppendOutcome, LedgerStore

CANONICAL_CSV_PARSER_VERSION: Final[str] = "portfolio-canonical-csv-v1"
CANONICAL_CSV_COLUMNS: Final[tuple[str, ...]] = (
    "account",
    "external_source",
    "external_id",
    "event_type",
    "effective_at",
    "received_at",
    "sequence",
    "market",
    "mic",
    "symbol",
    "currency",
    "cash_effect",
    "quantity",
    "price",
    "fees",
    "basis",
    "basis_status",
    "reverses_external_id",
)

# Split and other quantity-adjusting events are not representable in the ledger
# (ADR-0024 decision 9). They get their own message so the rejection names the
# policy instead of looking like a typo.
QUANTITY_ADJUSTING_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "SPLIT",
        "STOCK_SPLIT",
        "REVERSE_SPLIT",
        "SPLIT_ADJUSTMENT",
        "QUANTITY_ADJUSTMENT",
        "BONUS_ISSUE",
        "STOCK_DIVIDEND",
        "SPIN_OFF",
        "SPINOFF",
        "MERGER",
        "CONVERSION",
        "CONSOLIDATION",
    }
)

_ZERO: Final[Decimal] = Decimal("0")


class _RowError(ValueError):
    """One row-level parsing failure; rendered with its file line number."""


class ImportPreview(BaseModel):
    """Proposed effects of a validated batch, computed without any write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_count: int = Field(ge=0)
    counts_by_type: dict[str, int]
    quantity_deltas: dict[str, Decimal]
    cash_delta: Decimal
    new_event_count: int = Field(ge=0)
    unchanged_event_count: int = Field(ge=0)


def _text(row: Mapping[str, str], name: str) -> str:
    return row[name].strip()


def _required_text(row: Mapping[str, str], name: str, *, what: str) -> str:
    value = _text(row, name)
    if not value:
        raise _RowError(f"{name} is blank; every event carries its {what}")
    return value


def _parse_decimal(name: str, text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        # The raw text is never echoed: a rejected amount is still a private value.
        raise _RowError(f"{name} is not a decimal number; money and quantity are exact decimals, never binary floats") from exc


def _optional_decimal(row: Mapping[str, str], name: str) -> Decimal | None:
    text = _text(row, name)
    return None if not text else _parse_decimal(name, text)


def _required_decimal(row: Mapping[str, str], name: str) -> Decimal:
    text = _text(row, name)
    if not text:
        raise _RowError(f"{name} is blank; an exact decimal is required")
    return _parse_decimal(name, text)


def _timestamp(row: Mapping[str, str], name: str) -> datetime:
    text = _text(row, name)
    if not text:
        raise _RowError(f"{name} is blank; an aware UTC timestamp is required")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        # The raw text is never echoed: it may carry a private note or amount.
        raise _RowError(f"{name} is not an ISO-8601 timestamp; expected an aware UTC value such as 2024-01-02T15:00:00Z") from exc


def _sequence(row: Mapping[str, str]) -> int:
    text = _text(row, "sequence")
    if not text:
        raise _RowError("sequence is blank; the deterministic source sequence orders the ledger")
    try:
        return int(text, 10)
    except ValueError as exc:
        # The raw text is never echoed: a sequence column can be pasted prose.
        raise _RowError("sequence is not an integer; ordering is never inferred from file order") from exc


def _instrument(row: Mapping[str, str]) -> InstrumentKey | None:
    market = _text(row, "market")
    mic = _text(row, "mic")
    symbol = _text(row, "symbol")
    if not (market or mic or symbol):
        return None
    if not (market and mic and symbol):
        raise _RowError("instrument identity is incomplete; market, MIC and symbol are all required and ticker text is never guessed (ADR-0016)")
    if market != SUPPORTED_MARKET:
        raise _RowError(f"market is not supported; this ledger covers {SUPPORTED_MARKET} only (ADR-0024 decision 1)")
    try:
        return InstrumentKey(market=market, mic=mic, symbol=symbol)
    except ValidationError as exc:
        raise _RowError(f"instrument identity rejected: {redacted_validation_message(exc)}; identity is never guessed (ADR-0016)") from exc


def _event_type(row: Mapping[str, str]) -> LedgerEventType:
    text = _text(row, "event_type").upper()
    if not text:
        raise _RowError(f"event_type is blank; expected one of {', '.join(kind.value for kind in LedgerEventType)}")
    if text in QUANTITY_ADJUSTING_EVENT_TYPES:
        raise _RowError(
            f"event_type {text!r} is a split or other quantity-adjusting event, which is not approved (ADR-0024 decision 9); "
            "no split accounting exists and a position whose quantity basis is uncertain must stay unsupported"
        )
    try:
        return LedgerEventType(text)
    except ValueError as exc:
        # An unknown event-type cell may contain pasted private text; only the
        # column name and the closed set of accepted names are echoed.
        raise _RowError(f"event_type is unknown; expected one of {', '.join(kind.value for kind in LedgerEventType)}") from exc


def _event_from_row(row: Mapping[str, str], *, source_digest: str, source_row: int) -> LedgerEvent:
    fees = _optional_decimal(row, "fees")
    basis_status_text = _text(row, "basis_status").upper() or BasisStatus.KNOWN.value
    try:
        basis_status = BasisStatus(basis_status_text)
    except ValueError as exc:
        raise _RowError(f"basis_status is unknown; expected {BasisStatus.KNOWN.value} or {BasisStatus.UNKNOWN.value}") from exc
    return LedgerEvent(
        parser_version=CANONICAL_CSV_PARSER_VERSION,
        source_digest=source_digest,
        account=_required_text(row, "account", what="account label"),
        external_source=_required_text(row, "external_source", what="external source"),
        external_id=_required_text(row, "external_id", what="external source event ID"),
        event_type=_event_type(row),
        effective_at=_timestamp(row, "effective_at"),
        received_at=_timestamp(row, "received_at"),
        source_row=source_row,
        sequence=_sequence(row),
        instrument=_instrument(row),
        currency=_text(row, "currency") or SUPPORTED_CURRENCY,
        cash_effect=_required_decimal(row, "cash_effect"),
        quantity=_optional_decimal(row, "quantity"),
        price=_optional_decimal(row, "price"),
        fees=_ZERO if fees is None else fees,
        basis=_optional_decimal(row, "basis"),
        basis_status=basis_status,
        reverses_external_id=_text(row, "reverses_external_id") or None,
    )


def parse_csv_text(text: str, *, source_digest: str | None = None) -> tuple[LedgerEvent, ...]:
    """Parse canonical CSV text into validated events, aggregating every row error."""
    rows = csv.reader(io.StringIO(text, newline=""))
    try:
        fieldnames = tuple(next(rows))
    except StopIteration as exc:
        raise LedgerValidationError([LedgerIssue("canonical CSV is empty; the frozen header row is required")]) from exc
    missing = [column for column in CANONICAL_CSV_COLUMNS if column not in fieldnames]
    unexpected = [column for column in fieldnames if column not in CANONICAL_CSV_COLUMNS]
    if missing or unexpected:
        unexpected_positions = [index + 1 for index, column in enumerate(fieldnames) if column not in CANONICAL_CSV_COLUMNS]
        raise LedgerValidationError(
            [
                LedgerIssue(
                    f"canonical CSV header is not the frozen schema; missing {missing!r}; "
                    f"{len(unexpected)} unexpected column(s) at position(s) {unexpected_positions!r} (unexpected names are not echoed); "
                    f"expected {list(CANONICAL_CSV_COLUMNS)!r}"
                )
            ]
        )

    digest = source_digest or hashlib.sha256(text.encode("utf-8")).hexdigest()
    events: list[LedgerEvent] = []
    issues: list[LedgerIssue] = []
    for values in rows:
        line = rows.line_num
        if not any(value.strip() for value in values):
            continue
        if len(values) != len(fieldnames):
            issues.append(
                LedgerIssue(f"row has {len(values)} field(s) but the header has {len(fieldnames)}; extra or missing fields are rejected", line)
            )
            continue
        row = dict(zip(fieldnames, values, strict=True))
        try:
            events.append(_event_from_row(row, source_digest=digest, source_row=line))
        except _RowError as exc:
            issues.append(LedgerIssue(str(exc), line))
        except ValidationError as exc:
            issues.append(LedgerIssue(redacted_validation_message(exc), line))
    if issues:
        raise LedgerValidationError(issues)
    return tuple(events)


def parse_csv_file(path: Path) -> tuple[LedgerEvent, ...]:
    """Parse one canonical CSV file; the file digest is recorded per event."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LedgerValidationError([LedgerIssue(f"canonical CSV must be UTF-8; decoding failed ({type(exc).__name__})")]) from exc
    return parse_csv_text(text, source_digest=hashlib.sha256(raw).hexdigest())


def _stored_events(data_dir: Path | None) -> tuple[LedgerEvent, ...]:
    """Stored events, or an empty tuple; a missing ledger is never created here."""
    store = LedgerStore(data_dir=data_dir)
    return store.load_events() if store.exists() else ()


def preview_events(events: Sequence[LedgerEvent], *, data_dir: Path | None = None) -> ImportPreview:
    """Full validation plus proposed effects, with no filesystem or database write.

    Only two read-only operations happen here: an existence check on the ledger
    path and, when it already exists, a read-only query for stored events. No
    directory, database, journal or checkpoint file is created.
    """
    batch = tuple(events)
    new_events = validate_batch(_stored_events(data_dir), batch)

    counts: dict[str, int] = {}
    deltas: dict[str, Decimal] = {}
    cash_delta = _ZERO
    for event in sorted(batch, key=event_order_key):
        counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
        cash_delta += event.cash_effect
        if event.instrument is not None and event.quantity is not None:
            label = instrument_label(event.instrument)
            deltas[label] = deltas.get(label, _ZERO) + quantity_delta(event)

    return ImportPreview(
        event_count=len(batch),
        counts_by_type=dict(sorted(counts.items())),
        quantity_deltas=dict(sorted(deltas.items())),
        cash_delta=cash_delta,
        new_event_count=len(new_events),
        unchanged_event_count=len(batch) - len(new_events),
    )


def commit_events(events: Sequence[LedgerEvent], *, data_dir: Path | None = None) -> AppendOutcome:
    """Validate against the stored ledger, then append the batch atomically."""
    batch = tuple(events)
    validate_batch(_stored_events(data_dir), batch)
    return LedgerStore(data_dir=data_dir).append(batch)


__all__ = [
    "CANONICAL_CSV_COLUMNS",
    "CANONICAL_CSV_PARSER_VERSION",
    "ImportPreview",
    "QUANTITY_ADJUSTING_EVENT_TYPES",
    "commit_events",
    "parse_csv_file",
    "parse_csv_text",
    "preview_events",
]
