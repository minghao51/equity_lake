"""Private append-only portfolio ledger event contract (ADR-0024 decisions 4-7, 9).

This module owns *only* the event contract: the closed set of event kinds,
exact ``Decimal`` money/quantity, aware UTC timestamps, canonical instrument
identity, hard field rules, and the quantity-only invariant used to reject
oversells. Positions, cash, cost basis, P&L and NAV projections are deliberately
absent (P02), and this module never reads a price, a model or a signal.

Everything here is deterministic and offline: no filesystem or database access,
no network, no clock. Given the same events in any file order, the validation
outcome and the derived quantities are identical.

Money and quantity semantics are exact at any magnitude. The idempotency digest
renders each ``Decimal`` as canonical plain decimal text built from its digit
tuple with trailing zeros stripped — never through ``Decimal.normalize()``, whose
context precision silently rounds long values. Decimal magnitude is bounded (see
``MAX_DECIMAL_SIGNIFICANT_DIGITS``/``MAX_DECIMAL_EXPONENT``) so a pathological
literal cannot be used to defeat the digest without a clear rejection.

Error messages never disclose holdings: no amount, quantity, balance, price or
instrument symbol appears in any issue or exception (ADR-0024 decision 11). An
issue names the 1-based import row (when known), the event type, the external ID
and a reason category.

Pydantic failures are rendered by :func:`redacted_validation_message`, which is
the single redaction rule for this package: it keeps the location, the error type
and the validator sentence, never copies ``input_value``, and scrubs every quoted
input value from the sentence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from equity_lake.core.instruments import InstrumentKey

LEDGER_EVENT_SCHEMA_VERSION: Final[Literal[1]] = 1
SUPPORTED_MARKET: Final[str] = "us_equity"
SUPPORTED_CURRENCY: Final[str] = "USD"

# Exact decimals are bounded so canonical text (and therefore the semantic
# digest) can never be stretched into an unbounded string. Both bounds are far
# above any brokerage amount; the ledger never rounds to fit them.
MAX_DECIMAL_SIGNIFICANT_DIGITS: Final[int] = 38
MAX_DECIMAL_EXPONENT: Final[int] = 38

_LABEL_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_DIGEST_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
_ZERO: Final[Decimal] = Decimal("0")
REDACTED_VALUE: Final[str] = "<redacted>"

_SHARE_EVENTS: Final[frozenset[str]] = frozenset({"BUY", "SELL"})
_INSTRUMENT_EVENTS: Final[frozenset[str]] = frozenset({"BUY", "SELL", "CASH_DIVIDEND"})
_CASH_FLOW_EVENTS: Final[frozenset[str]] = frozenset({"DEPOSIT", "WITHDRAWAL"})


class LedgerEventType(StrEnum):
    """The complete, closed set of ledger event kinds (ADR-0024 decision 4).

    There is intentionally no split, transfer-in, spin-off or conversion kind:
    ADR-0024 decision 9 keeps quantity-adjusting events unsupported, so they
    cannot be represented, let alone approximated.
    """

    OPENING = "OPENING"
    BUY = "BUY"
    SELL = "SELL"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"
    CASH_DIVIDEND = "CASH_DIVIDEND"


class BasisStatus(StrEnum):
    """Whether an ``OPENING`` position states its basis (ADR-0024 decision 6)."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


def _input_texts(value: object) -> tuple[str, ...]:
    """String leaves of a Pydantic error ``input`` payload (enum members skipped)."""
    if isinstance(value, Enum):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(text for leaf in value.values() for text in _input_texts(leaf))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(text for leaf in value for text in _input_texts(leaf))
    return ()


def redacted_validation_message(exc: ValidationError) -> str:
    """Render a ``ValidationError`` without copying any input value (ADR-0024 decision 11).

    ``str(exc)`` embeds ``[input_value=...]``, so it is never used. Each error
    contributes its location, its type and the validator's sentence; the sentence
    is scrubbed of every quoted input value, which is how a nested
    ``InstrumentKey`` message stops echoing a symbol or MIC. Top-level model
    sentences are authored in this module and are value-free by construction.
    """
    parts: list[str] = []
    for error in exc.errors():
        sentence = str(error["msg"]).removeprefix("Value error, ")
        for quoted_input in sorted({repr(text) for text in _input_texts(error.get("input"))}, key=len, reverse=True):
            sentence = sentence.replace(quoted_input, REDACTED_VALUE)
        location = ".".join(str(item) for item in error["loc"])
        rendered = f"{location}: {sentence} ({error['type']})" if location else f"{sentence} ({error['type']})"
        parts.append(rendered)
    return "; ".join(parts)


@dataclass(frozen=True, slots=True)
class LedgerIssue:
    """One actionable validation finding, optionally tied to a CSV row."""

    message: str
    row: int | None = None

    def render(self) -> str:
        return self.message if self.row is None else f"row {self.row}: {self.message}"


class PortfolioLedgerError(ValueError):
    """Base class for private-ledger contract failures."""


class LedgerValidationError(PortfolioLedgerError):
    """One or more rows/events were rejected; nothing was written."""

    def __init__(self, issues: Sequence[LedgerIssue]) -> None:
        if not issues:
            raise ValueError("LedgerValidationError requires at least one issue")
        self.issues: tuple[LedgerIssue, ...] = tuple(issues)
        detail = "; ".join(issue.render() for issue in self.issues)
        super().__init__(f"private portfolio ledger rejected {len(self.issues)} issue(s): {detail}")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_decimal_parts(value: Decimal) -> tuple[tuple[int, ...], int, bool]:
    """Exact canonical digit tuple/exponent pair: trailing zeros stripped, no rounding.

    ``Decimal.normalize()`` rounds to the active context precision, so it collapses
    distinct long values. Working from ``as_tuple()`` is exact for every digit count
    and still scale-insensitive: ``1.50`` and ``1.5`` share one canonical form,
    while any two distinct magnitudes keep distinct digit tuples.
    """
    sign, digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        raise ValueError("money and quantity must be finite decimals")
    exponent = raw_exponent
    stripped = list(digits)
    while len(stripped) > 1 and stripped[-1] == 0:
        stripped.pop()
        exponent += 1
    if all(digit == 0 for digit in stripped):
        # Every zero spelling (0, 0.00, -0) is the same value: one canonical zero.
        return (0,), 0, False
    return tuple(stripped), exponent, bool(sign)


def _decimal_text(value: Decimal | None) -> str | None:
    """Canonical decimal text: numeric, never exponent-only, never a float, never rounded."""
    if value is None:
        return None
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


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


class LedgerEvent(_StrictModel):
    """One versioned, append-only ledger event (ADR-0024 decisions 4-7).

    ``cash_effect`` is the signed cash movement in USD, ``quantity`` and
    ``price`` are per-share and non-negative only for share events, ``fees`` is
    the fee already reflected in ``cash_effect``, and ``basis`` is the *total*
    book cost in USD of an ``OPENING`` position. ``sequence`` is the
    deterministic source sequence; file order and wall-clock arrival never
    order events.

    Strict validation is part of the contract: every money/quantity field must
    already be a ``Decimal`` (binary floats and raw strings are refused) and
    Pydantic refuses non-finite decimal values, so a caller has to parse decimal
    text explicitly before constructing an event.
    """

    schema_version: Literal[1] = LEDGER_EVENT_SCHEMA_VERSION

    # Provenance: which parser produced the row and the digest of its source.
    parser_version: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    source_digest: str = Field(pattern=_DIGEST_PATTERN)

    # Private scope: a short synthetic-local account label, never a credential
    # or a full broker account identifier (ADR-0024 decision 11).
    account: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    external_source: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    external_id: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)

    event_type: LedgerEventType
    effective_at: datetime
    received_at: datetime
    # 1-based file line number of the import row that produced this event. Import
    # provenance for actionable messages only: never part of the semantic content
    # digest and never persisted, so a re-import stays a no-op wherever it appears.
    source_row: int | None = Field(default=None, ge=1)
    sequence: int = Field(ge=1, le=2_000_000_000)

    instrument: InstrumentKey | None = None
    currency: str = Field(default=SUPPORTED_CURRENCY, min_length=1, max_length=8)

    cash_effect: Decimal
    quantity: Decimal | None = None
    price: Decimal | None = None
    fees: Decimal = _ZERO
    basis: Decimal | None = None
    basis_status: BasisStatus = BasisStatus.KNOWN

    reverses_external_id: str | None = Field(default=None, pattern=_LABEL_PATTERN)

    @field_validator("effective_at", "received_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware; a naive timestamp is not a ledger time (use UTC)")
        return value.astimezone(UTC)

    @field_validator("cash_effect", "quantity", "price", "fees", "basis")
    @classmethod
    def _exact_bounded_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite():
            raise ValueError("money and quantity must be finite decimals")
        digits, exponent, _ = _canonical_decimal_parts(value)
        if len(digits) > MAX_DECIMAL_SIGNIFICANT_DIGITS or abs(exponent) > MAX_DECIMAL_EXPONENT:
            raise ValueError(
                f"value carries {len(digits)} significant digit(s) with exponent {exponent}; ledger money and quantity are exact and "
                f"bounded to {MAX_DECIMAL_SIGNIFICANT_DIGITS} significant digits with exponents within ±{MAX_DECIMAL_EXPONENT}, "
                "because the ledger never rounds: fix the source export or record the amount as a separate event"
            )
        return value

    @field_validator("currency")
    @classmethod
    def _usd_only(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != SUPPORTED_CURRENCY:
            raise ValueError("currency is not supported: this ledger is USD-only (ADR-0024 decision 1) and performs no FX conversion")
        return normalized

    @model_validator(mode="after")
    def _event_rules(self) -> LedgerEvent:
        if self.instrument is not None and self.instrument.market != SUPPORTED_MARKET:
            raise ValueError(f"instrument market is not supported: this ledger covers {SUPPORTED_MARKET} only (ADR-0024 decision 1)")
        if self.received_at < self.effective_at:
            raise ValueError("received_at must not precede effective_at; ordering is never inferred from arrival order")

        kind = self.event_type.value
        if kind in _INSTRUMENT_EVENTS:
            _require(
                self.instrument is not None,
                f"{kind} requires a canonical instrument identity; ticker text is never guessed (ADR-0016)",
            )
        if kind in _SHARE_EVENTS:
            _require(
                self.quantity is not None and self.quantity > 0,
                f"{kind} requires a positive quantity; zero, negative and short quantities are not supported",
            )
            _require(
                self.price is not None and self.price > 0,
                f"{kind} requires a positive price; a zero or negative price is not a ledger price",
            )
            _require(self.basis is None, "basis is only stated on OPENING events; every other basis is derived by replay")
        if kind == LedgerEventType.BUY.value:
            _require(self.cash_effect < 0, "BUY cash_effect is signed and must be negative (cash out)")
        if kind == LedgerEventType.SELL.value:
            _require(self.cash_effect > 0, "SELL cash_effect is signed and must be positive (cash in)")
        if kind == LedgerEventType.DEPOSIT.value:
            _require(self.cash_effect > 0, "DEPOSIT cash_effect must be positive")
        if kind == LedgerEventType.WITHDRAWAL.value:
            _require(self.cash_effect < 0, "WITHDRAWAL cash_effect must be negative")
        if kind == LedgerEventType.FEE.value:
            _require(self.cash_effect < 0, "FEE cash_effect is signed and must be negative")
            _require(
                self.quantity is None and self.price is None and self.basis is None,
                "FEE must not carry a quantity, price or basis",
            )
        if kind == LedgerEventType.CASH_DIVIDEND.value:
            _require(self.cash_effect > 0, "CASH_DIVIDEND cash_effect must be the positive net amount received")
            _require(self.price is None and self.basis is None, "CASH_DIVIDEND must not carry a price or basis")
        if kind in _CASH_FLOW_EVENTS:
            _require(
                self.instrument is None and self.quantity is None and self.price is None and self.basis is None,
                f"{kind} is an external cash flow and must not name an instrument, quantity, price or basis",
            )
        if kind == LedgerEventType.OPENING.value:
            _require(self.cash_effect >= 0, "OPENING cash_effect must be zero or positive; short and margin openings are not supported")
            if self.quantity is not None:
                _require(self.quantity > 0, "OPENING quantity must be positive")
                _require(self.instrument is not None, "OPENING with a position requires a canonical instrument")
                _require(self.price is None or self.price > 0, "OPENING price must be positive when stated")
                if self.basis_status is BasisStatus.KNOWN:
                    _require(
                        self.basis is not None,
                        "OPENING position must state its total basis, or state basis_status=UNKNOWN (ADR-0024 decision 6)",
                    )
            else:
                _require(self.instrument is None, "OPENING that names an instrument must state the opening quantity")
                _require(
                    self.basis is None and self.basis_status is BasisStatus.KNOWN,
                    "OPENING cash-only row must not state a basis",
                )
        if self.basis is not None:
            _require(self.basis >= 0, "basis must be non-negative")
        if self.basis_status is BasisStatus.UNKNOWN:
            _require(self.basis is None, "an unknown basis must not carry a basis amount; unknown basis is unavailable, never zero")
        _require(self.fees >= 0, "fees must be non-negative")
        if self.reverses_external_id is not None:
            _require(
                self.reverses_external_id != self.external_id,
                "an event cannot reverse itself; a correction is a separate reversal/repair event (ADR-0024 decision 5)",
            )
        return self

    def content_payload(self) -> dict[str, object]:
        """Semantic content used for idempotency, excluding provenance-only fields."""
        instrument = self.instrument
        return {
            "schema_version": self.schema_version,
            "account": self.account,
            "external_source": self.external_source,
            "external_id": self.external_id,
            "event_type": self.event_type.value,
            "effective_at": self.effective_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "sequence": self.sequence,
            "instrument": None if instrument is None else list(instrument.tuple),
            "currency": self.currency,
            "cash_effect": _decimal_text(self.cash_effect),
            "quantity": _decimal_text(self.quantity),
            "price": _decimal_text(self.price),
            "fees": _decimal_text(self.fees),
            "basis": _decimal_text(self.basis),
            "basis_status": self.basis_status.value,
            "reverses_external_id": self.reverses_external_id,
        }

    @property
    def content_digest(self) -> str:
        """Digest of the semantic content: a re-import with equal content is a no-op."""
        payload = json.dumps(self.content_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def event_order_key(event: LedgerEvent) -> tuple[str, int, str]:
    """Deterministic logical order: source sequence, never file or arrival order."""
    return (event.account, event.sequence, event.external_id)


def instrument_label(instrument: InstrumentKey) -> str:
    return f"{instrument.mic}:{instrument.symbol}"


def quantity_delta(event: LedgerEvent) -> Decimal:
    """Quantity change contributed by one event (quantity-adjusting kinds are absent)."""
    if event.quantity is None:
        return _ZERO
    if event.event_type is LedgerEventType.SELL:
        return -event.quantity
    if event.event_type in {LedgerEventType.OPENING, LedgerEventType.BUY}:
        return event.quantity
    return _ZERO


def _position_key(event: LedgerEvent) -> tuple[str, str, str, str] | None:
    if event.instrument is None:
        return None
    return (event.account, *event.instrument.tuple)


def oversell_issues(existing: Sequence[LedgerEvent], batch: Sequence[LedgerEvent]) -> list[LedgerIssue]:
    """Replay quantity only, in deterministic order, to reject short positions.

    Sells may bring a position to exactly zero. No cash, basis or price is
    consulted, so this invariant says nothing about valuation (P02).

    The timeline is the stored events plus only the *new* events of the batch:
    an already-stored event that is re-imported unchanged is replayed once, not
    applied twice, so idempotent re-import is a no-op here too. Changed content
    for a stored external ID is a content conflict reported elsewhere, never an
    oversell.

    Messages stay actionable without disclosing holdings (ADR-0024 decision 11):
    they name the row, the event type, the external ID and the reason category —
    never the quantity, the available balance, a price or the instrument symbol.
    """
    stored_keys = {(event.account, event.external_id) for event in existing}
    timeline = [*existing, *(event for event in batch if (event.account, event.external_id) not in stored_keys)]

    positions: dict[tuple[str, str, str, str], Decimal] = {}
    issues: list[LedgerIssue] = []
    for event in sorted(timeline, key=event_order_key):
        key = _position_key(event)
        if key is None:
            continue
        remaining = positions.get(key, _ZERO) + quantity_delta(event)
        if remaining < 0:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} for external_id {event.external_id!r} would take the position negative "
                    "in the current sequence; oversells are rejected (ADR-0024 decision 9)",
                    event.source_row,
                )
            )
            continue
        positions[key] = remaining
    return issues


def validate_batch(existing: Sequence[LedgerEvent], batch: Sequence[LedgerEvent]) -> tuple[LedgerEvent, ...]:
    """Validate a batch against stored events and return only the new events.

    Checks duplicate IDs inside the batch, ambiguous source sequences,
    idempotency conflicts with stored content, reversal references, and the
    quantity-only oversell invariant. Nothing is written: this is also the
    dry-run path (ADR-0024 decision 13).
    """
    issues: list[LedgerIssue] = []
    existing_by_key = {(event.account, event.external_id): event for event in existing}
    existing_by_sequence = {(event.account, event.sequence): event.external_id for event in existing}

    seen: dict[tuple[str, str], LedgerEvent] = {}
    batch_sequences: dict[tuple[str, int], str] = {}
    for event in batch:
        key = (event.account, event.external_id)
        if key in seen:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} external_id {event.external_id!r} appears twice in one batch; duplicate external IDs are rejected",
                    event.source_row,
                )
            )
            continue
        seen[key] = event

        sequence_key = (event.account, event.sequence)
        if sequence_key in batch_sequences:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} external_id {event.external_id!r} reuses deterministic source sequence "
                    f"{event.sequence} of {batch_sequences[sequence_key]!r}; ambiguous ordering is rejected",
                    event.source_row,
                )
            )
        batch_sequences[sequence_key] = event.external_id

        stored = existing_by_key.get(key)
        if stored is not None and stored.content_digest != event.content_digest:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} external_id {event.external_id!r} is already stored with different content; corrections "
                    "are explicit reversal/repair events, never an in-place rewrite (ADR-0024 decision 5)",
                    event.source_row,
                )
            )
        occupied = existing_by_sequence.get(sequence_key)
        if occupied is not None and occupied != event.external_id:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} external_id {event.external_id!r} reuses stored sequence {event.sequence} of "
                    f"{occupied!r}; ambiguous ordering is rejected",
                    event.source_row,
                )
            )

    known = set(existing_by_key) | set(seen)
    for event in batch:
        target = event.reverses_external_id
        if target is not None and (event.account, target) not in known:
            issues.append(
                LedgerIssue(
                    f"{event.event_type.value} external_id {event.external_id!r} reverses unknown external_id {target!r}; "
                    "a correction must name an existing event",
                    event.source_row,
                )
            )

    if not issues:
        issues.extend(oversell_issues(existing, batch))
    if issues:
        raise LedgerValidationError(issues)

    return tuple(event for event in batch if (event.account, event.external_id) not in existing_by_key)


__all__ = [
    "BasisStatus",
    "LEDGER_EVENT_SCHEMA_VERSION",
    "MAX_DECIMAL_EXPONENT",
    "MAX_DECIMAL_SIGNIFICANT_DIGITS",
    "LedgerEvent",
    "LedgerEventType",
    "LedgerIssue",
    "LedgerValidationError",
    "PortfolioLedgerError",
    "REDACTED_VALUE",
    "SUPPORTED_CURRENCY",
    "SUPPORTED_MARKET",
    "event_order_key",
    "instrument_label",
    "oversell_issues",
    "quantity_delta",
    "redacted_validation_message",
    "validate_batch",
]
