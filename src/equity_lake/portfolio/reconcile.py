"""Canonical statement reconciliation (ADR-0026 decision 6).

One canonical UTF-8 CSV is the only accepted statement format: there is no
broker mapping, no plugin surface and no currency conversion. Reconciliation
compares explicitly supplied statement quantities and cash against the replayed
ledger and reports every line as ``matched``, ``mismatched`` or ``unavailable``.

What this module must never do, and does not:

* insert a balancing event, adjust a mark or widen a tolerance to force agreement;
* treat a missing statement line or a missing ledger holding as an implicit zero
  (a statement-only position *is* compared with ledger zero, because the
  statement states it; an omitted ledger holding is ``unavailable``);
* let a disagreement about the snapshot instant become a numeric delta;
* require price marks. Quantity and cash reconciliation needs none.

Tolerances are inclusive and applied to unrounded absolute differences:
``0.00000001`` share and ``0.01`` USD (ADR-0026 decision 6).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from equity_lake.core.instruments import InstrumentKey
from equity_lake.portfolio.models import (
    MAX_DECIMAL_EXPONENT,
    MAX_DECIMAL_SIGNIFICANT_DIGITS,
    SUPPORTED_CURRENCY,
    SUPPORTED_MARKET,
    LedgerIssue,
    PortfolioLedgerError,
    _canonical_decimal_parts,
    instrument_label,
    redacted_validation_message,
)
from equity_lake.portfolio.projections import LedgerProjection

STATEMENT_COLUMNS: Final[tuple[str, ...]] = (
    "account",
    "as_of",
    "line_type",
    "market",
    "mic",
    "symbol",
    "currency",
    "quantity",
    "cash",
)
QUANTITY_TOLERANCE: Final[Decimal] = Decimal("0.00000001")
CASH_TOLERANCE: Final[Decimal] = Decimal("0.01")

REASON_MATCHED_QUANTITY: Final[str] = "quantity difference is within the inclusive tolerance"
REASON_MISMATCHED_QUANTITY: Final[str] = "quantity difference exceeds the inclusive tolerance"
REASON_MATCHED_CASH: Final[str] = "cash difference is within the inclusive tolerance"
REASON_MISMATCHED_CASH: Final[str] = "cash difference exceeds the inclusive tolerance"
REASON_MISSING_CASH: Final[str] = "the statement carries no cash line, so cash is unavailable rather than zero"
REASON_OMITTED_HOLDING: Final[str] = "the statement omits an open ledger holding, so this line is unavailable rather than zero"
REASON_SNAPSHOT: Final[str] = "the statement snapshot instant differs from the requested --as-of, so no line is compared"

_ZERO: Final[Decimal] = Decimal("0")


class StatementError(PortfolioLedgerError):
    """The supplied statement cannot be compared; it is never silently combined.

    Messages are value-free: they name the 1-based line and the reason category,
    never a quantity, amount, account identifier or instrument symbol.
    """


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StatementLineType(StrEnum):
    """The two line kinds a canonical statement may carry."""

    CASH = "CASH"
    POSITION = "POSITION"


class ReconcileLineType(StrEnum):
    """The comparison a line reports."""

    QUANTITY = "quantity"
    CASH = "cash"


class ReconcileState(StrEnum):
    """Per-line and overall reconciliation outcome."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNAVAILABLE = "unavailable"


class StatementPosition(_StrictModel):
    """One explicitly stated holding quantity at the snapshot instant."""

    instrument: InstrumentKey
    quantity: Decimal

    @field_validator("quantity")
    @classmethod
    def _non_negative_bounded(cls, value: Decimal) -> Decimal:
        return _bounded_non_negative(value, what="statement quantity")


class Statement(_StrictModel):
    """A validated canonical statement snapshot.

    ``cash`` is ``None`` only when the statement carried no cash line: that makes
    reconciliation unavailable, never a zero cash balance.
    """

    account: str
    as_of: datetime
    cash: Decimal | None
    positions: tuple[StatementPosition, ...]

    @field_validator("as_of")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("statement as_of must be timezone-aware; a naive snapshot instant is not comparable")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _unique_positions(self) -> Statement:
        seen: set[tuple[str, str, str]] = set()
        for position in self.positions:
            key = position.instrument.tuple
            if key in seen:
                raise ValueError("statement repeats one position identity; contradictory duplicate rows are rejected")
            seen.add(key)
        return self


def _bounded_non_negative(value: Decimal, *, what: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{what} must be a finite decimal")
    if value < _ZERO:
        raise ValueError(f"{what} must be non-negative; zero is stated explicitly, negatives are not supported")
    digits, exponent, _ = _canonical_decimal_parts(value)
    if len(digits) > MAX_DECIMAL_SIGNIFICANT_DIGITS or abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise ValueError(
            f"{what} is bounded to {MAX_DECIMAL_SIGNIFICANT_DIGITS} significant digits with exponents within "
            f"±{MAX_DECIMAL_EXPONENT}; statement values are exact and never rounded to fit"
        )
    return value


class _RowError(ValueError):
    """One row-level parsing failure; rendered with its 1-based file line."""


def _text(row: Mapping[str, str], name: str) -> str:
    return row[name].strip()


def _decimal(text: str, *, name: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        # The raw text is never echoed: a rejected amount is still a private value.
        raise _RowError(f"{name} is not a decimal number; statement values are exact decimals, never binary floats") from exc


def _line_type(row: Mapping[str, str]) -> StatementLineType:
    text = _text(row, "line_type").upper()
    allowed = ", ".join(kind.value for kind in StatementLineType)
    if text not in {kind.value for kind in StatementLineType}:
        raise _RowError(f"line_type is unknown; expected one of {allowed}")
    return StatementLineType(text)


def _identity(row: Mapping[str, str]) -> InstrumentKey:
    market = _text(row, "market")
    mic = _text(row, "mic")
    symbol = _text(row, "symbol")
    if not (market and mic and symbol):
        raise _RowError("instrument identity is incomplete; market, MIC and symbol are all required and ticker text is never guessed (ADR-0016)")
    if market != SUPPORTED_MARKET:
        raise _RowError(f"market is not supported; this book covers {SUPPORTED_MARKET} only")
    try:
        return InstrumentKey(market=market, mic=mic, symbol=symbol)
    except ValidationError as exc:
        raise _RowError(f"instrument identity rejected: {redacted_validation_message(exc)}; identity is never guessed (ADR-0016)") from exc


def _currency(row: Mapping[str, str]) -> None:
    text = _text(row, "currency").upper()
    if text != SUPPORTED_CURRENCY:
        raise _RowError(f"currency is not supported; statements are {SUPPORTED_CURRENCY}-only and are never converted")


def _timestamp(row: Mapping[str, str]) -> datetime:
    text = _text(row, "as_of")
    if not text:
        raise _RowError("as_of is blank; an aware snapshot instant is required on every row")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _RowError("as_of is not an ISO-8601 timestamp; expected an aware UTC value such as 2024-01-05T21:00:00Z") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _RowError("as_of must be timezone-aware; a naive snapshot instant cannot be compared")
    return parsed.astimezone(UTC)


def parse_statement_text(text: str) -> Statement:
    """Parse canonical statement CSV text into a validated snapshot.

    Every structural problem is collected and reported by 1-based line, so a
    rejected statement is refused whole instead of being partly compared.
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        fieldnames = tuple(next(reader))
    except StopIteration as exc:
        raise StatementError("statement CSV is empty; the canonical header row is required") from exc
    missing = [column for column in STATEMENT_COLUMNS if column not in fieldnames]
    unexpected = [column for column in fieldnames if column not in STATEMENT_COLUMNS]
    duplicated_positions = [index + 1 for index, column in enumerate(fieldnames) if fieldnames.count(column) > 1]
    if missing or unexpected:
        unexpected_positions = [index + 1 for index, column in enumerate(fieldnames) if column not in STATEMENT_COLUMNS]
        raise StatementError(
            f"statement header is not the canonical schema; missing {missing!r}; "
            f"{len(unexpected)} unexpected column(s) at position(s) {unexpected_positions!r} (unexpected names are not echoed); "
            f"expected {list(STATEMENT_COLUMNS)!r}"
        )
    if duplicated_positions:
        # ``dict(zip(...))`` would silently keep the last value of a repeated name,
        # so a duplicated canonical column is refused instead of being reinterpreted.
        raise StatementError(
            f"statement header repeats {len(duplicated_positions)} column name(s) at position(s) {duplicated_positions!r}; "
            "each canonical column must appear exactly once"
        )

    accounts: set[str] = set()
    instants: set[datetime] = set()
    cash_rows: list[tuple[int, Decimal]] = []
    positions: list[StatementPosition] = []
    seen_identities: set[tuple[str, str, str]] = set()
    issues: list[LedgerIssue] = []

    for values in reader:
        line = reader.line_num
        if not any(value.strip() for value in values):
            continue
        if len(values) != len(fieldnames):
            issues.append(
                LedgerIssue(
                    f"row has {len(values)} field(s) but the header has {len(fieldnames)}; extra or missing fields are rejected",
                    line,
                )
            )
            continue
        row = dict(zip(fieldnames, values, strict=True))
        try:
            account = _text(row, "account")
            if not account:
                raise _RowError("account is blank; every statement row carries one neutral account label")
            accounts.add(account)
            instants.add(_timestamp(row))
            _currency(row)
            kind = _line_type(row)
            if kind is StatementLineType.CASH:
                if any(_text(row, name) for name in ("market", "mic", "symbol")):
                    raise _RowError("a CASH row must leave market, MIC and symbol blank")
                if _text(row, "quantity"):
                    raise _RowError("a CASH row must leave quantity blank")
                cash_text = _text(row, "cash")
                if not cash_text:
                    raise _RowError("a CASH row requires its cash amount; a blank cash cell is not zero")
                cash_rows.append((line, _bounded_non_negative(_decimal(cash_text, name="cash"), what="statement cash")))
                continue
            if _text(row, "cash"):
                raise _RowError("a POSITION row must leave cash blank")
            identity = _identity(row)
            if identity.tuple in seen_identities:
                raise _RowError("statement repeats this position identity; contradictory duplicate rows are rejected")
            seen_identities.add(identity.tuple)
            quantity_text = _text(row, "quantity")
            if not quantity_text:
                raise _RowError("a POSITION row requires its quantity; a blank quantity cell is not zero")
            positions.append(
                StatementPosition(
                    instrument=identity,
                    quantity=_bounded_non_negative(_decimal(quantity_text, name="quantity"), what="statement quantity"),
                )
            )
        except ValidationError as exc:
            # ValidationError subclasses ValueError, so it is listed first and redacted.
            issues.append(LedgerIssue(redacted_validation_message(exc), line))
        except ValueError as exc:
            # ``_RowError`` and the bounded-decimal helpers both raise ValueError;
            # catching the base class keeps a rejected value a reported row issue
            # rather than an escaping traceback.
            issues.append(LedgerIssue(str(exc), line))

    if len(accounts) > 1:
        issues.append(LedgerIssue("statement rows carry more than one account label; one statement describes one account", None))
    if len(instants) > 1:
        issues.append(LedgerIssue("statement rows disagree about the snapshot instant; one statement is one snapshot", None))
    if len(cash_rows) > 1:
        issues.append(LedgerIssue("statement carries more than one CASH row; contradictory duplicates are rejected", None))
    if issues:
        raise StatementError("statement rejected: " + "; ".join(issue.render() for issue in issues))
    if not accounts or not instants:
        # A header-only (or all-blank) statement has no snapshot instant and no
        # line to compare: it is malformed, not an empty-but-comparable snapshot.
        raise StatementError("statement has no data rows; a snapshot instant and at least one line are required")

    return Statement(
        account=accounts.pop(),
        as_of=instants.pop(),
        cash=None if not cash_rows else cash_rows[0][1],
        positions=tuple(sorted(positions, key=lambda position: position.instrument.tuple)),
    )


def parse_statement_file(path: Path) -> Statement:
    """Read one canonical statement CSV; the file is never created or modified."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise StatementError(f"could not read the statement ({type(exc).__name__}); a read path never creates a statement") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementError(f"statement must be UTF-8; decoding failed ({type(exc).__name__})") from exc
    return parse_statement_text(text)


class ReconcileLine(_StrictModel):
    """One comparison with both values, the signed delta and the applied tolerance."""

    line_type: ReconcileLineType
    instrument: InstrumentKey | None
    statement_value: Decimal | None
    ledger_value: Decimal
    delta: Decimal | None
    tolerance: Decimal
    state: ReconcileState
    reason: str

    @property
    def label(self) -> str:
        return "cash" if self.instrument is None else instrument_label(self.instrument)


class ReconciliationReport(_StrictModel):
    """Every line outcome plus the overall state (unavailable > mismatched > matched)."""

    account: str
    as_of: datetime
    state: ReconcileState
    lines: tuple[ReconcileLine, ...]
    quantity_tolerance: Decimal = QUANTITY_TOLERANCE
    cash_tolerance: Decimal = CASH_TOLERANCE

    @property
    def matched(self) -> bool:
        return self.state is ReconcileState.MATCHED


def _quantity_line(
    instrument: InstrumentKey,
    statement_value: Decimal | None,
    ledger_value: Decimal,
    *,
    unavailable_reason: str | None = None,
) -> ReconcileLine:
    if statement_value is None:
        return ReconcileLine(
            line_type=ReconcileLineType.QUANTITY,
            instrument=instrument,
            statement_value=None,
            ledger_value=ledger_value,
            delta=None,
            tolerance=QUANTITY_TOLERANCE,
            state=ReconcileState.UNAVAILABLE,
            reason=unavailable_reason or REASON_OMITTED_HOLDING,
        )
    if unavailable_reason is not None:
        return ReconcileLine(
            line_type=ReconcileLineType.QUANTITY,
            instrument=instrument,
            statement_value=statement_value,
            ledger_value=ledger_value,
            delta=None,
            tolerance=QUANTITY_TOLERANCE,
            state=ReconcileState.UNAVAILABLE,
            reason=unavailable_reason,
        )
    delta = statement_value - ledger_value
    matched = abs(delta) <= QUANTITY_TOLERANCE
    return ReconcileLine(
        line_type=ReconcileLineType.QUANTITY,
        instrument=instrument,
        statement_value=statement_value,
        ledger_value=ledger_value,
        delta=delta,
        tolerance=QUANTITY_TOLERANCE,
        state=ReconcileState.MATCHED if matched else ReconcileState.MISMATCHED,
        reason=REASON_MATCHED_QUANTITY if matched else REASON_MISMATCHED_QUANTITY,
    )


def _cash_line(statement_value: Decimal | None, ledger_value: Decimal, *, unavailable_reason: str | None = None) -> ReconcileLine:
    if statement_value is None:
        return ReconcileLine(
            line_type=ReconcileLineType.CASH,
            instrument=None,
            statement_value=None,
            ledger_value=ledger_value,
            delta=None,
            tolerance=CASH_TOLERANCE,
            state=ReconcileState.UNAVAILABLE,
            reason=REASON_MISSING_CASH,
        )
    if unavailable_reason is not None:
        return ReconcileLine(
            line_type=ReconcileLineType.CASH,
            instrument=None,
            statement_value=statement_value,
            ledger_value=ledger_value,
            delta=None,
            tolerance=CASH_TOLERANCE,
            state=ReconcileState.UNAVAILABLE,
            reason=unavailable_reason,
        )
    delta = statement_value - ledger_value
    matched = abs(delta) <= CASH_TOLERANCE
    return ReconcileLine(
        line_type=ReconcileLineType.CASH,
        instrument=None,
        statement_value=statement_value,
        ledger_value=ledger_value,
        delta=delta,
        tolerance=CASH_TOLERANCE,
        state=ReconcileState.MATCHED if matched else ReconcileState.MISMATCHED,
        reason=REASON_MATCHED_CASH if matched else REASON_MISMATCHED_CASH,
    )


def reconcile(projection: LedgerProjection, statement: Statement) -> ReconciliationReport:
    """Compare one statement snapshot against the replayed ledger at the same instant.

    The ledger is never mutated and no mark is consulted. A statement-only
    position is compared with ledger quantity zero (the statement states it); a
    ledger holding the statement omits, or a statement without a cash line, is
    ``unavailable`` — never an implicit zero.
    """
    if statement.account != projection.account:
        raise StatementError("the statement account label differs from the ledger account label; one statement reconciles one private book")

    mismatch_instant = statement.as_of != projection.as_of
    unavailable_reason = REASON_SNAPSHOT if mismatch_instant else None

    lines: list[ReconcileLine] = [
        _cash_line(statement.cash, projection.cash, unavailable_reason=unavailable_reason),
    ]
    ledger_quantities = {position.instrument.tuple: position.quantity for position in projection.positions if position.quantity > _ZERO}
    statement_quantities = {position.instrument.tuple: position.quantity for position in statement.positions}
    for key in sorted(set(ledger_quantities) | set(statement_quantities)):
        instrument = InstrumentKey(market=key[0], mic=key[1], symbol=key[2])
        lines.append(
            _quantity_line(
                instrument,
                statement_quantities.get(key),
                ledger_quantities.get(key, _ZERO),
                unavailable_reason=unavailable_reason,
            )
        )

    states = {line.state for line in lines}
    if ReconcileState.UNAVAILABLE in states:
        overall = ReconcileState.UNAVAILABLE
    elif ReconcileState.MISMATCHED in states:
        overall = ReconcileState.MISMATCHED
    else:
        overall = ReconcileState.MATCHED

    return ReconciliationReport(
        account=projection.account,
        as_of=projection.as_of,
        state=overall,
        lines=tuple(lines),
    )


__all__ = [
    "CASH_TOLERANCE",
    "QUANTITY_TOLERANCE",
    "REASON_MISSING_CASH",
    "REASON_OMITTED_HOLDING",
    "REASON_SNAPSHOT",
    "ReconcileLine",
    "ReconcileLineType",
    "ReconcileState",
    "ReconciliationReport",
    "STATEMENT_COLUMNS",
    "Statement",
    "StatementError",
    "StatementLineType",
    "StatementPosition",
    "parse_statement_file",
    "parse_statement_text",
    "reconcile",
]
