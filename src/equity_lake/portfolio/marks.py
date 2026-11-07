"""Stored raw as-of book marks and as-of valuation (ADR-0026 decision 5).

Book marks are *explicit local inputs*, not a lake reader and not a quote
service. A version-1 JSON envelope carries ``marks`` (session-close quotes) and
``valuation_blocks`` (identities whose recorded quantity is known to be
uncertain). Nothing here writes a file, creates a directory, reads a price from
the lake or falls back to a network source: the module only parses, validates,
selects and values.

The rules a convenient implementation would break, and which this module keeps:

* **Structural invalidity is all-or-nothing.** Unknown envelope/model versions,
  extra fields, wrong currency, an adjusted price basis, a non-positive price, a
  naive timestamp, or an ``as_of`` that is not the exact MIC's scheduled close
  (including early closes) rejects the *file*. There is no partial acceptance, so
  a caller can never see "most of" an invalid mark set.
* **Selection is exact and latest-eligible.** Only marks for the exact
  ``(market, MIC, symbol)`` with ``as_of <= T`` compete. Same-identity,
  same-instant marks with different content block that instrument; identical
  duplicates collapse. A conflict is never hidden by falling back to an older
  quote, another venue, an adjusted price or a future quote.
* **Age counts completed venue sessions.** A Friday close through a closed
  weekend is age zero. The default bound is one completed session; only a
  stricter zero-session option is offered, never a looser one.
* **A partial total stays partial.** Missing, stale, conflicting or
  quantity-uncertain coverage keeps cash, recorded quantities and the valid
  marked subtotal visible while complete NAV, allocation and
  valuation-dependent P&L are withheld. There is no marked-only denominator.

Stored marks are book marks for a private local book. They are not the Bronze
price-source envelope of ADR-0027 and cannot upgrade research provenance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Context, Decimal
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from equity_lake.core.calendar import instrument_session_close, instrument_sessions_between
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
from equity_lake.portfolio.projections import (
    PROJECTION_DECIMAL_PRECISION,
    LedgerProjection,
    PositionProjection,
    average_cost_display,
    exact_add,
    exact_mul,
    exact_sub,
    round_half_even,
)

MARK_SCHEMA_VERSION: Final[int] = 1
# ADR-0026 decision 5: one completed venue session is the default staleness bound;
# a stricter zero-session bound is allowed, a looser one never is.
DEFAULT_MAX_STALE_SESSIONS: Final[int] = 1
STRICT_MAX_STALE_SESSIONS: Final[int] = 0
MAX_SUPPORTED_STALE_SESSIONS: Final[int] = 1
# Allocation is a display ratio only; it never feeds a calculation.
ALLOCATION_FRACTIONAL_DIGITS: Final[int] = 6

MARK_KEYS: Final[tuple[str, ...]] = (
    "market",
    "mic",
    "symbol",
    "currency",
    "price",
    "session",
    "as_of",
    "source",
    "price_basis",
    "provenance",
)
BLOCK_KEYS: Final[tuple[str, ...]] = ("market", "mic", "symbol", "reason")
DOCUMENT_KEYS: Final[tuple[str, ...]] = ("schema_version", "marks", "valuation_blocks")

_SOURCE_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_ZERO: Final[Decimal] = Decimal("0")


class MarksError(PortfolioLedgerError):
    """Book marks cannot be trusted or used for valuation."""


class MarksValidationError(MarksError):
    """The stored mark document is structurally invalid; nothing is accepted."""

    def __init__(self, issues: Sequence[LedgerIssue]) -> None:
        if not issues:
            raise ValueError("MarksValidationError requires at least one issue")
        self.issues: tuple[LedgerIssue, ...] = tuple(issues)
        detail = "; ".join(issue.render() for issue in self.issues)
        super().__init__(f"stored book marks rejected {len(self.issues)} issue(s): {detail}")


class MarkProvenance(StrEnum):
    """Explicit provenance of a book mark; it is never inferred from a path."""

    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class PriceBasis(StrEnum):
    """Book marks use raw, unadjusted prices (ADR-0024 decision 8)."""

    UNADJUSTED = "unadjusted"


class ValuationBlockReason(StrEnum):
    """The only supported reason a known position cannot be valued (P00-2)."""

    UNSUPPORTED_QUANTITY_ADJUSTMENT = "unsupported_quantity_adjustment"


class MarkStatus(StrEnum):
    """Why a position is, or is not, valued at the requested instant."""

    VALUED = "valued"
    MISSING_MARK = "missing_mark"
    STALE_MARK = "stale_mark"
    CONFLICTING_MARKS = "conflicting_marks"
    QUANTITY_UNCERTAIN = "quantity_uncertain"


class _StrictModel(BaseModel):
    """Strict/frozen model configuration: extra fields and coercion are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PriceMark(_StrictModel):
    """One stored session-close book mark with visible provenance.

    ``as_of`` is a *quote* time, not proof of when a source published it. The
    document validator additionally requires it to equal the exact MIC's
    scheduled close for ``session``.
    """

    market: str
    mic: str
    symbol: str
    currency: str
    price: Decimal
    session: date
    as_of: datetime
    source: str = Field(min_length=1, max_length=64, pattern=_SOURCE_PATTERN)
    price_basis: PriceBasis = PriceBasis.UNADJUSTED
    provenance: MarkProvenance

    @field_validator("currency")
    @classmethod
    def _usd_only(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != SUPPORTED_CURRENCY:
            raise ValueError("mark currency is not supported: book marks are USD only and are never converted")
        return normalized

    @field_validator("as_of")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mark as_of must be timezone-aware; a naive quote time is not a valuation instant")
        return value.astimezone(UTC)

    @field_validator("price")
    @classmethod
    def _positive_bounded_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("mark price must be a finite decimal")
        if value <= _ZERO:
            raise ValueError("mark price must be positive; a zero or negative quote is not a book price")
        digits, exponent, _ = _canonical_decimal_parts(value)
        if len(digits) > MAX_DECIMAL_SIGNIFICANT_DIGITS or abs(exponent) > MAX_DECIMAL_EXPONENT:
            raise ValueError(
                f"mark price is bounded to {MAX_DECIMAL_SIGNIFICANT_DIGITS} significant digits with exponents within "
                f"±{MAX_DECIMAL_EXPONENT}; a book price is exact and never rounded to fit"
            )
        return value

    @field_validator("price_basis")
    @classmethod
    def _unadjusted_only(cls, value: PriceBasis) -> PriceBasis:
        if value is not PriceBasis.UNADJUSTED:
            raise ValueError("mark price_basis must be unadjusted; research-adjusted prices are not book marks")
        return value

    @model_validator(mode="after")
    def _exact_identity(self) -> PriceMark:
        try:
            InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)
        except ValidationError as exc:
            raise ValueError(f"mark instrument identity is invalid: {redacted_validation_message(exc)}") from exc
        if self.market != SUPPORTED_MARKET:
            raise ValueError(f"mark market is not supported: this book covers {SUPPORTED_MARKET} only")
        return self

    @property
    def instrument(self) -> InstrumentKey:
        return InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)


class ValuationBlock(_StrictModel):
    """A known position whose recorded quantity is explicitly uncertain.

    The fixture supplies this knowledge; P02 does not discover unreported
    splits, query corporate actions or certify that real history is split-free.
    """

    market: str
    mic: str
    symbol: str
    reason: ValuationBlockReason = ValuationBlockReason.UNSUPPORTED_QUANTITY_ADJUSTMENT

    @model_validator(mode="after")
    def _exact_identity(self) -> ValuationBlock:
        try:
            InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)
        except ValidationError as exc:
            raise ValueError(f"valuation block instrument identity is invalid: {redacted_validation_message(exc)}") from exc
        return self

    @property
    def instrument(self) -> InstrumentKey:
        return InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)


class MarksDocument(_StrictModel):
    """The version-1 stored mark envelope: marks plus explicit valuation blocks."""

    schema_version: int = MARK_SCHEMA_VERSION
    marks: tuple[PriceMark, ...] = ()
    valuation_blocks: tuple[ValuationBlock, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _version_one_only(cls, value: int) -> int:
        if value != MARK_SCHEMA_VERSION:
            raise ValueError(f"unsupported book-mark schema_version; this build reads version {MARK_SCHEMA_VERSION} only")
        return value

    @model_validator(mode="after")
    def _unique_blocks(self) -> MarksDocument:
        seen: set[tuple[str, str, str]] = set()
        for block in self.valuation_blocks:
            key = block.instrument.tuple
            if key in seen:
                raise ValueError("valuation_blocks names the same instrument more than once; contradictory blocks are rejected")
            seen.add(key)
        return self


def _text(payload: Mapping[str, object], name: str, *, where: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {name} must be a non-empty string")
    return value.strip()


def _decimal_text(payload: Mapping[str, object], name: str, *, where: str) -> Decimal:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {name} must be decimal text, never a JSON number or float")
    try:
        return Decimal(value.strip())
    except ArithmeticError as exc:  # InvalidOperation is an ArithmeticError
        # The raw text is never echoed: a rejected price is still a private value.
        raise ValueError(f"{where}: {name} is not a decimal number; book prices are exact decimal text") from exc


def _timestamp_text(payload: Mapping[str, object], name: str, *, where: str) -> datetime:
    text = _text(payload, name, where=where)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{where}: {name} is not an ISO-8601 timestamp; expected an aware UTC instant such as 2024-01-02T21:00:00Z") from exc


def _date_text(payload: Mapping[str, object], name: str, *, where: str) -> date:
    text = _text(payload, name, where=where)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{where}: {name} is not an ISO-8601 date; expected the venue session date such as 2024-01-02") from exc


def _enum_text(payload: Mapping[str, object], name: str, enum: type[StrEnum], *, where: str) -> str:
    text = _text(payload, name, where=where).lower()
    allowed = ", ".join(member.value for member in enum)
    if text not in {member.value for member in enum}:
        # The rejected cell is not echoed: it may be pasted private text.
        raise ValueError(f"{where}: {name} is not one of {allowed}")
    return text


def _check_keys(payload: Mapping[str, object], allowed: Sequence[str], *, where: str) -> None:
    missing = [name for name in allowed if name not in payload]
    unexpected = [name for name in payload if name not in allowed]
    if missing or unexpected:
        raise ValueError(
            f"{where}: expected exactly the fields {list(allowed)!r}; missing {missing!r}; "
            f"{len(unexpected)} unexpected field(s) (unexpected names are not echoed)"
        )


def _mark_from_payload(payload: object, index: int) -> PriceMark:
    where = f"mark {index}"
    if not isinstance(payload, Mapping):
        raise ValueError(f"{where}: must be a JSON object")
    _check_keys(payload, MARK_KEYS, where=where)
    return PriceMark(
        market=_text(payload, "market", where=where),
        mic=_text(payload, "mic", where=where),
        symbol=_text(payload, "symbol", where=where),
        currency=_text(payload, "currency", where=where),
        price=_decimal_text(payload, "price", where=where),
        session=_date_text(payload, "session", where=where),
        as_of=_timestamp_text(payload, "as_of", where=where),
        source=_text(payload, "source", where=where),
        price_basis=PriceBasis(_enum_text(payload, "price_basis", PriceBasis, where=where)),
        provenance=MarkProvenance(_enum_text(payload, "provenance", MarkProvenance, where=where)),
    )


def _block_from_payload(payload: object, index: int) -> ValuationBlock:
    where = f"valuation block {index}"
    if not isinstance(payload, Mapping):
        raise ValueError(f"{where}: must be a JSON object")
    _check_keys(payload, BLOCK_KEYS, where=where)
    return ValuationBlock(
        market=_text(payload, "market", where=where),
        mic=_text(payload, "mic", where=where),
        symbol=_text(payload, "symbol", where=where),
        reason=ValuationBlockReason(_enum_text(payload, "reason", ValuationBlockReason, where=where)),
    )


def _validate_venue_sessions(document: MarksDocument) -> None:
    """Require every mark's ``as_of`` to be the exact MIC's scheduled session close."""
    issues: list[LedgerIssue] = []
    for index, mark in enumerate(document.marks, start=1):
        try:
            close = instrument_session_close(mark.instrument, mark.session)
        except ValueError:
            issues.append(LedgerIssue(f"mark {index}: no exact-venue calendar is available for this MIC", None))
            continue
        if close is None:
            issues.append(LedgerIssue(f"mark {index}: session is not a scheduled session for this exact venue", None))
        elif close != mark.as_of:
            issues.append(
                LedgerIssue(
                    f"mark {index}: as_of is not the scheduled close of the stated session for this exact venue "
                    "(early closes and holidays are decided by that venue's calendar, never a default convention)",
                    None,
                )
            )
    if issues:
        raise MarksValidationError(issues)


def parse_marks_text(text: str) -> MarksDocument:
    """Parse and fully validate a version-1 mark document; no file or DB access."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarksValidationError([LedgerIssue(f"stored book marks are not valid JSON ({type(exc).__name__}); nothing is accepted", None)]) from exc
    if not isinstance(payload, Mapping):
        raise MarksValidationError([LedgerIssue("stored book marks must be a JSON object with a marks array", None)])
    try:
        _check_keys(payload, DOCUMENT_KEYS, where="book-mark document")
    except ValueError as exc:
        raise MarksValidationError([LedgerIssue(str(exc), None)]) from exc

    issues: list[LedgerIssue] = []
    marks: list[PriceMark] = []
    blocks: list[ValuationBlock] = []
    raw_marks = payload.get("marks")
    raw_blocks = payload.get("valuation_blocks")
    if not isinstance(raw_marks, list):
        issues.append(LedgerIssue("book-mark document: marks must be a JSON array", None))
    else:
        for index, item in enumerate(raw_marks, start=1):
            try:
                marks.append(_mark_from_payload(item, index))
            except ValidationError as exc:
                # ValidationError subclasses ValueError, so it is listed first.
                issues.append(LedgerIssue(f"mark {index}: {redacted_validation_message(exc)}", None))
            except ValueError as exc:
                issues.append(LedgerIssue(str(exc), None))
    if not isinstance(raw_blocks, list):
        issues.append(LedgerIssue("book-mark document: valuation_blocks must be a JSON array", None))
    else:
        for index, item in enumerate(raw_blocks, start=1):
            try:
                blocks.append(_block_from_payload(item, index))
            except ValidationError as exc:
                issues.append(LedgerIssue(f"valuation block {index}: {redacted_validation_message(exc)}", None))
            except ValueError as exc:
                issues.append(LedgerIssue(str(exc), None))
    if issues:
        raise MarksValidationError(issues)

    version = payload.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise MarksValidationError([LedgerIssue("book-mark document: schema_version must be an integer", None)])
    try:
        document = MarksDocument(schema_version=version, marks=tuple(marks), valuation_blocks=tuple(blocks))
    except ValidationError as exc:
        raise MarksValidationError([LedgerIssue(redacted_validation_message(exc), None)]) from exc
    _validate_venue_sessions(document)
    return document


def load_marks_file(path: Path) -> MarksDocument:
    """Read and validate one stored mark file; the file is never created."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise MarksValidationError(
            [LedgerIssue(f"could not read stored book marks ({type(exc).__name__}); a read path never creates a mark file", None)]
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarksValidationError([LedgerIssue(f"stored book marks must be UTF-8; decoding failed ({type(exc).__name__})", None)]) from exc
    return parse_marks_text(text)


def _plain_decimal(value: Decimal) -> str:
    """Canonical non-exponential decimal text: exact, never a float, never rounded."""
    digits, exponent, negative = _canonical_decimal_parts(value)
    mantissa = "".join(str(digit) for digit in digits)
    if exponent == 0:
        text = mantissa
    elif exponent > 0:
        text = mantissa + "0" * exponent
    else:
        split = len(mantissa) + exponent
        text = f"{mantissa[:split]}.{mantissa[split:]}" if split > 0 else f"0.{'0' * -split}{mantissa}"
    return f"-{text}" if negative else text


def marks_document_payload(document: MarksDocument) -> dict[str, object]:
    """Serializable form: decimals as strings, sorted keys, no floats anywhere."""
    return {
        "schema_version": document.schema_version,
        "marks": [
            {
                "market": mark.market,
                "mic": mark.mic,
                "symbol": mark.symbol,
                "currency": mark.currency,
                "price": _plain_decimal(mark.price),
                "session": mark.session.isoformat(),
                "as_of": mark.as_of.isoformat(),
                "source": mark.source,
                "price_basis": mark.price_basis.value,
                "provenance": mark.provenance.value,
            }
            for mark in document.marks
        ],
        "valuation_blocks": [
            {"market": block.market, "mic": block.mic, "symbol": block.symbol, "reason": block.reason.value} for block in document.valuation_blocks
        ],
    }


def dump_marks_text(document: MarksDocument) -> str:
    """Validate venue rules, then serialize; a fixture writer validates before writing."""
    _validate_venue_sessions(document)
    return json.dumps(marks_document_payload(document), sort_keys=True, indent=2) + "\n"


def completed_sessions_after(instrument: InstrumentKey, session: date, as_of: datetime) -> int:
    """Count the exact venue's sessions that closed after ``session`` and by ``as_of``.

    A session counts once its scheduled close (early closes included) is at or
    before ``as_of``, so Friday's close through a closed weekend is zero sessions
    old and a Monday close is one. No calendar-day, weekday or market-union
    approximation is involved.
    """
    instant = as_of.astimezone(UTC)
    # A close can sit on the following UTC date for venues east of UTC, so the
    # session scan ends one day past the instant and filters on the close itself.
    end = (instant + timedelta(days=1)).date()
    sessions = instrument_sessions_between(instrument, session + timedelta(days=1), end)
    completed = 0
    for candidate in sessions:
        close = instrument_session_close(instrument, candidate)
        if close is not None and close <= instant:
            completed += 1
    return completed


def _require_supported_bound(max_stale_sessions: int) -> int:
    if max_stale_sessions not in {STRICT_MAX_STALE_SESSIONS, DEFAULT_MAX_STALE_SESSIONS}:
        raise MarksError(
            f"--max-stale-sessions must be {STRICT_MAX_STALE_SESSIONS} or {DEFAULT_MAX_STALE_SESSIONS}; "
            f"a bound above {MAX_SUPPORTED_STALE_SESSIONS} completed session(s) is not supported (ADR-0026 decision 5)"
        )
    return max_stale_sessions


def _select_mark(marks: Sequence[PriceMark], as_of: datetime) -> tuple[PriceMark | None, MarkStatus]:
    """Latest eligible mark for one exact identity, or a named reason it is unusable."""
    eligible = [mark for mark in marks if mark.as_of <= as_of]
    if not eligible:
        return None, MarkStatus.MISSING_MARK
    latest = max(mark.as_of for mark in eligible)
    candidates = [mark for mark in eligible if mark.as_of == latest]
    first = candidates[0]
    if any(candidate != first for candidate in candidates[1:]):
        # Ambiguous same-instant quotes block the instrument: no older quote and
        # no other venue is substituted to hide the conflict.
        return None, MarkStatus.CONFLICTING_MARKS
    return first, MarkStatus.VALUED


class PositionValuation(_StrictModel):
    """One position at the valuation instant, with its mark and honest availability."""

    instrument: InstrumentKey
    quantity: Decimal
    quantity_certain: bool
    basis: Decimal | None
    realized_gain: Decimal | None
    mark_price: Decimal | None
    mark_source: str | None
    mark_provenance: MarkProvenance | None
    mark_session: date | None
    mark_as_of: datetime | None
    mark_age_sessions: int | None
    max_stale_sessions: int
    value: Decimal | None
    unrealized_gain: Decimal | None
    status: MarkStatus
    blocker: str | None

    @property
    def average_cost(self) -> Decimal | None:
        """Display cents only; valuation never uses a display value."""
        return average_cost_display(self.basis, self.quantity)


class PortfolioValuation(_StrictModel):
    """As-of book valuation: complete NAV or an explicit partial report.

    ``marked_position_value`` is the valid marked subtotal and never includes an
    unvalued holding. ``nav``, ``unrealized_gain``, ``total_gain`` and
    ``allocation`` are ``None`` while any required coverage is unavailable, so a
    partial subtotal can never be mistaken for a total.
    """

    account: str
    as_of: datetime
    cash: Decimal
    marked_position_value: Decimal
    complete: bool
    nav: Decimal | None
    unrealized_gain: Decimal | None
    realized_gain: Decimal | None
    net_dividend_income: Decimal
    standalone_fees: Decimal
    total_gain: Decimal | None
    allocation: dict[str, Decimal] | None
    positions: tuple[PositionValuation, ...]
    blockers: tuple[str, ...]
    max_stale_sessions: int

    @property
    def has_open_positions(self) -> bool:
        return bool(self.positions)


def _blocker_text(status: MarkStatus, label: str) -> str:
    if status is MarkStatus.MISSING_MARK:
        return f"{label}: no stored mark at or before the valuation instant; value and NAV are withheld"
    if status is MarkStatus.STALE_MARK:
        return f"{label}: the latest stored mark is older than the staleness bound; value and NAV are withheld"
    if status is MarkStatus.CONFLICTING_MARKS:
        return f"{label}: same-instant stored marks disagree; the instrument is blocked rather than choosing one"
    return f"{label}: recorded quantity is uncertain (unsupported quantity adjustment); valuation is blocked"


def _valuation_of(
    position: PositionProjection,
    marks: Sequence[PriceMark],
    blocked: set[tuple[str, str, str]],
    context: Context,
    *,
    as_of: datetime,
    max_stale_sessions: int,
) -> PositionValuation:
    label = instrument_label(position.instrument)
    if position.instrument.tuple in blocked:
        status = MarkStatus.QUANTITY_UNCERTAIN
        return PositionValuation(
            instrument=position.instrument,
            quantity=position.quantity,
            quantity_certain=False,
            basis=position.basis,
            realized_gain=position.realized_gain,
            mark_price=None,
            mark_source=None,
            mark_provenance=None,
            mark_session=None,
            mark_as_of=None,
            mark_age_sessions=None,
            max_stale_sessions=max_stale_sessions,
            value=None,
            unrealized_gain=None,
            status=status,
            blocker=_blocker_text(status, label),
        )

    mark, status = _select_mark(marks, as_of)
    age = None
    if mark is not None:
        age = completed_sessions_after(position.instrument, mark.session, as_of)
        if age > max_stale_sessions:
            status = MarkStatus.STALE_MARK

    # A selected-but-unusable mark keeps its identity, source, session and age in
    # the report: that is how staleness is diagnosed. Only the *value* is withheld.
    value: Decimal | None = None
    unrealized: Decimal | None = None
    if status is MarkStatus.VALUED and mark is not None:
        # Valuation arithmetic obeys the same exactness rule as the replay: it runs
        # in the projection's context and never depends on the global one.
        value = exact_mul(context, "a position market value", position.quantity, mark.price)
        unrealized = None if position.basis is None else exact_sub(context, "a position unrealized gain", value, position.basis)
    return PositionValuation(
        instrument=position.instrument,
        quantity=position.quantity,
        quantity_certain=True,
        basis=position.basis,
        realized_gain=position.realized_gain,
        mark_price=None if mark is None else mark.price,
        mark_source=None if mark is None else mark.source,
        mark_provenance=None if mark is None else mark.provenance,
        mark_session=None if mark is None else mark.session,
        mark_as_of=None if mark is None else mark.as_of,
        mark_age_sessions=age,
        max_stale_sessions=max_stale_sessions,
        value=value,
        unrealized_gain=unrealized,
        status=status,
        blocker=None if status is MarkStatus.VALUED else _blocker_text(status, label),
    )


def _sum_exact(context: Context, what: str, values: Sequence[Decimal | None]) -> Decimal:
    """Sum the present values exactly, in the projection's own context."""
    total = _ZERO
    for value in values:
        if value is not None:
            total = exact_add(context, what, total, value)
    return total


def value_positions(
    projection: LedgerProjection,
    document: MarksDocument,
    *,
    max_stale_sessions: int = DEFAULT_MAX_STALE_SESSIONS,
) -> PortfolioValuation:
    """Value the replay's open positions at ``projection.as_of``.

    Only ``projection.as_of`` is used as the valuation instant, so the marks,
    the statement comparison and the replay can never disagree about ``T``.
    Missing, stale, conflicting or quantity-uncertain coverage keeps cash,
    quantities and the valid marked subtotal and withholds complete NAV,
    allocation and valuation-dependent P&L.
    """
    bound = _require_supported_bound(max_stale_sessions)
    # Consumers validate on read: a document built in memory is held to the same
    # venue rules as one parsed from a file.
    _validate_venue_sessions(document)
    as_of = projection.as_of
    grouped: dict[tuple[str, str, str], list[PriceMark]] = {}
    for mark in document.marks:
        grouped.setdefault(mark.instrument.tuple, []).append(mark)
    blocked = {block.instrument.tuple for block in document.valuation_blocks}
    # Valuation shares the replay's exactness rule: one explicit context, never the
    # process-global one (ADR-0026 decision 3).
    context = Context(prec=PROJECTION_DECIMAL_PRECISION)

    valuations = tuple(
        _valuation_of(position, grouped.get(position.instrument.tuple, ()), blocked, context, as_of=as_of, max_stale_sessions=bound)
        for position in projection.positions
    )
    blockers = tuple(valuation.blocker for valuation in valuations if valuation.blocker is not None)
    complete = not blockers

    marked_subtotal = _sum_exact(context, "a marked position subtotal", [valuation.value for valuation in valuations])

    unrealized: Decimal | None = None
    if complete and all(valuation.unrealized_gain is not None for valuation in valuations):
        unrealized = _sum_exact(context, "a portfolio unrealized gain", [valuation.unrealized_gain for valuation in valuations])

    nav: Decimal | None = None
    total_gain: Decimal | None = None
    allocation: dict[str, Decimal] | None = None
    if complete:
        nav = exact_add(context, "a net asset value", projection.cash, marked_subtotal)
        if projection.realized_gain is not None and unrealized is not None:
            gross_gain = exact_add(context, "a total book gain", projection.realized_gain, unrealized)
            with_income = exact_add(context, "a total book gain", gross_gain, projection.net_dividend_income)
            total_gain = exact_sub(context, "a total book gain", with_income, projection.standalone_fees)
        if nav != _ZERO:
            # Allocation includes cash and every open holding; a zero NAV has no
            # share of anything and is reported as unavailable, not as zeros.
            weights: dict[str, Decimal] = {"cash": round_half_even(_ratio(projection.cash, nav), ALLOCATION_FRACTIONAL_DIGITS)}
            for valuation in valuations:
                if valuation.value is not None:
                    weights[instrument_label(valuation.instrument)] = round_half_even(_ratio(valuation.value, nav), ALLOCATION_FRACTIONAL_DIGITS)
            allocation = dict(sorted(weights.items()))

    return PortfolioValuation(
        account=projection.account,
        as_of=as_of,
        cash=projection.cash,
        marked_position_value=marked_subtotal,
        complete=complete,
        nav=nav,
        unrealized_gain=unrealized,
        realized_gain=projection.realized_gain,
        net_dividend_income=projection.net_dividend_income,
        standalone_fees=projection.standalone_fees,
        total_gain=total_gain,
        allocation=allocation,
        positions=valuations,
        blockers=blockers,
        max_stale_sessions=bound,
    )


def _ratio(value: Decimal, total: Decimal) -> Fraction:
    """Exact ratio for a display weight; never a rounded accumulator."""
    return Fraction(value) / Fraction(total)


__all__ = [
    "ALLOCATION_FRACTIONAL_DIGITS",
    "DEFAULT_MAX_STALE_SESSIONS",
    "MARK_SCHEMA_VERSION",
    "MAX_SUPPORTED_STALE_SESSIONS",
    "MarkProvenance",
    "MarkStatus",
    "MarksDocument",
    "MarksError",
    "MarksValidationError",
    "PortfolioValuation",
    "PositionValuation",
    "PriceBasis",
    "PriceMark",
    "STRICT_MAX_STALE_SESSIONS",
    "ValuationBlock",
    "ValuationBlockReason",
    "completed_sessions_after",
    "dump_marks_text",
    "load_marks_file",
    "marks_document_payload",
    "parse_marks_text",
    "value_positions",
]
