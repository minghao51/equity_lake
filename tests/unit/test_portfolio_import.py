"""Synthetic canonical-CSV import contracts: parsing, validation, dry-run (ADR-0024).

Fixtures are invented: one short synthetic account label and invented
instruments/amounts. No broker file, no real account data and no lake write.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.portfolio.csv_import import (  # type: ignore[import-untyped]
    CANONICAL_CSV_COLUMNS,
    CANONICAL_CSV_PARSER_VERSION,
    ImportPreview,
    commit_events,
    parse_csv_file,
    parse_csv_text,
    preview_events,
)
from equity_lake.portfolio.models import LedgerEventType, LedgerValidationError  # type: ignore[import-untyped]
from equity_lake.portfolio.store import AppendOutcome, LedgerStore, LedgerStoreError, portfolio_db_path  # type: ignore[import-untyped]

ACCOUNT = "acct-synth-01"
SYNT = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNT")
DECIMAL_LIKE = re.compile(r"\d+\.\d+")
COMPANION_SUFFIXES = ("-journal", "-wal", "-shm")

_DEPOSIT = {
    "account": ACCOUNT,
    "external_source": "synthetic-broker",
    "external_id": "evt-0001",
    "event_type": "DEPOSIT",
    "effective_at": "2024-01-02T15:00:00Z",
    "received_at": "2024-01-02T21:00:00Z",
    "sequence": "1",
    "market": "",
    "mic": "",
    "symbol": "",
    "currency": "USD",
    "cash_effect": "500.00",
    "quantity": "",
    "price": "",
    "fees": "",
    "basis": "",
    "basis_status": "",
    "reverses_external_id": "",
}

_OPENING = {
    **_DEPOSIT,
    "external_id": "evt-0001",
    "event_type": "OPENING",
    "sequence": "1",
    "market": "us_equity",
    "mic": "XNAS",
    "symbol": "SYNT",
    "cash_effect": "1000.00",
    "quantity": "10",
    "price": "100.00",
    "basis": "1000.00",
}

_BUY = {
    **_DEPOSIT,
    "external_id": "evt-0002",
    "event_type": "BUY",
    "sequence": "2",
    "market": "us_equity",
    "mic": "XNAS",
    "symbol": "SYNT",
    "cash_effect": "-605.00",
    "quantity": "5",
    "price": "120.00",
    "fees": "5.00",
}


def _row(base: dict[str, str] | None = None, **overrides: str) -> dict[str, str]:
    row = dict(_DEPOSIT if base is None else base)
    row.update(overrides)
    return row


def _csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CANONICAL_CSV_COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in CANONICAL_CSV_COLUMNS])
    return buffer.getvalue()


def _sell_row(
    *, external_id: str = "evt-0002", sequence: str = "2", quantity: str = "9", price: str = "130.00", fees: str = "2.00"
) -> dict[str, str]:
    cash = Decimal(quantity) * Decimal(price) - Decimal(fees)
    return _row(
        _BUY,
        external_id=external_id,
        event_type="SELL",
        sequence=sequence,
        quantity=quantity,
        price=price,
        fees=fees,
        cash_effect=str(cash),
    )


def _hand_oracle_rows() -> list[dict[str, str]]:
    """Opening 10 at 100 + cash 1000; buy 5 at 120 fee 5; sell 4 at 130 fee 2; dividend 11."""
    return [
        _row(_OPENING),
        _row(_BUY),
        _row(
            _BUY,
            external_id="evt-0003",
            event_type="SELL",
            sequence="3",
            cash_effect="518.00",
            quantity="4",
            price="130.00",
            fees="2.00",
        ),
        _row(
            _BUY,
            external_id="evt-0004",
            event_type="CASH_DIVIDEND",
            sequence="4",
            cash_effect="11.00",
            quantity="",
            price="",
            fees="",
        ),
    ]


def _snapshot(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _stored_rows(path: Path) -> list[tuple[object, ...]]:
    import sqlite3

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in connection.execute("SELECT * FROM ledger_events ORDER BY row_id")]
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_canonical_csv_parses_exact_decimals_and_aware_timestamps() -> None:
    events = parse_csv_text(_csv([_row(_OPENING), _row(_BUY)]))

    assert [event.event_type for event in events] == [LedgerEventType.OPENING, LedgerEventType.BUY]
    opening, buy = events
    assert opening.basis == Decimal("1000.00")
    assert buy.cash_effect == Decimal("-605.00")
    assert buy.price == Decimal("120.00")
    assert buy.fees == Decimal("5.00")
    assert isinstance(buy.quantity, Decimal) and buy.quantity == Decimal("5")
    assert buy.instrument == SYNT
    assert buy.parser_version == CANONICAL_CSV_PARSER_VERSION
    assert buy.effective_at.tzinfo is not None
    assert str(buy.effective_at.utcoffset()) == "0:00:00"
    assert buy.received_at > buy.effective_at


def test_parse_records_the_file_digest_per_event(tmp_path: Path) -> None:
    path = tmp_path / "ledger.csv"
    path.write_text(_csv([_row(_OPENING)]), encoding="utf-8")
    (event,) = parse_csv_file(path)
    assert len(event.source_digest) == 64
    assert event.source_digest != "0" * 64


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(_row(effective_at="2024-01-02T15:00:00"), "timezone-aware", id="naive-effective-at"),
        pytest.param(_row(received_at="2024-01-02T21:00:00"), "timezone-aware", id="naive-received-at"),
        pytest.param(_row(cash_effect="NaN"), "finite number", id="nan-cash-effect"),
        pytest.param(_row(cash_effect="Infinity"), "finite number", id="infinite-cash-effect"),
        pytest.param(_row(cash_effect="1.2.3"), "not a decimal number", id="malformed-decimal"),
        pytest.param(_row(_BUY, market="cn_ashare", mic="XSHG"), "market is not supported", id="wrong-market"),
        pytest.param(_row(_BUY, market="mars", mic="XSHG"), "market is not supported", id="unknown-market"),
        pytest.param(_row(_BUY, mic="XHKG"), "instrument identity rejected", id="unknown-mic"),
        pytest.param(_row(_BUY, symbol=""), "instrument identity is incomplete", id="partial-identity"),
        pytest.param(_row(_BUY, market="", mic="", symbol=""), "requires a canonical instrument identity", id="blank-identity"),
        pytest.param(_row(currency="EUR"), "USD-only", id="wrong-currency"),
        pytest.param(_row(event_type="SPLIT"), "ADR-0024 decision 9", id="split"),
        pytest.param(_row(event_type="split"), "ADR-0024 decision 9", id="split-lowercase"),
        pytest.param(_row(event_type="STOCK_SPLIT"), "ADR-0024 decision 9", id="stock-split"),
        pytest.param(_row(event_type="TRANSFER_IN"), "unknown; expected one of", id="unknown-event-type"),
        pytest.param(_row(_BUY, quantity="-5"), "positive quantity", id="negative-quantity"),
        pytest.param(_row(_BUY, quantity="0"), "positive quantity", id="zero-quantity"),
        pytest.param(_row(_BUY, price="0"), "positive price", id="zero-price"),
        pytest.param(_row(external_id="evt#0001"), "external_id", id="malformed-external-id"),
        pytest.param(_row(account=""), "account is blank", id="blank-account"),
        pytest.param(_row(received_at="2024-01-02T14:00:00Z"), "must not precede", id="received-before-effective"),
        pytest.param(_row(sequence="first"), "not an integer", id="malformed-sequence"),
        pytest.param(_row(_BUY, fees="-1"), "fees must be non-negative", id="negative-fees"),
        pytest.param(_row(_OPENING, basis=""), "must state its total basis", id="opening-without-basis"),
        pytest.param(
            _row(_OPENING, basis_status="UNKNOWN", basis="1000.00"), "unknown basis must not carry a basis amount", id="unknown-basis-with-amount"
        ),
        pytest.param(_row(basis_status="PARTIAL"), "basis_status is unknown", id="unknown-basis-status"),
        pytest.param(_row(_DEPOSIT, market="us_equity", mic="XNAS", symbol="SYNT"), "must not name an instrument", id="instrument-on-deposit"),
    ],
)
def test_invalid_rows_are_rejected_with_the_row_number(row: dict[str, str], expected: str) -> None:
    with pytest.raises(LedgerValidationError) as excinfo:
        parse_csv_text(_csv([row]))
    message = str(excinfo.value)
    assert "row 2" in message, message
    assert expected in message, message


def test_every_invalid_row_is_reported_once_with_its_own_line_number() -> None:
    rows = [_row(_DEPOSIT), _row(cash_effect="NaN"), _row(event_type="SPLIT")]
    with pytest.raises(LedgerValidationError) as excinfo:
        parse_csv_text(_csv(rows))

    issues = excinfo.value.issues
    assert [issue.row for issue in issues] == [3, 4]
    assert "NaN" in issues[0].message or "finite number" in issues[0].message
    assert "ADR-0024 decision 9" in issues[1].message
    assert "rejected 2 issue(s)" in str(excinfo.value)


def test_header_drift_is_rejected() -> None:
    header = ",".join(column for column in CANONICAL_CSV_COLUMNS if column != "basis")
    with pytest.raises(LedgerValidationError) as excinfo:
        parse_csv_text(f"{header}\n")
    assert "missing ['basis']" in str(excinfo.value)

    with pytest.raises(LedgerValidationError) as excinfo:
        parse_csv_text(f"{','.join(CANONICAL_CSV_COLUMNS)},broker_note\n")
    assert "unexpected column(s) at position(s) [19]" in str(excinfo.value)
    assert "broker_note" not in str(excinfo.value)


def test_row_with_the_wrong_field_count_is_rejected() -> None:
    text = _csv([_row(_DEPOSIT)])
    lines = text.splitlines()
    with pytest.raises(LedgerValidationError) as excinfo:
        parse_csv_text("\n".join([lines[0], f"{lines[1]},extra"]) + "\n")
    assert "has 19 field(s) but the header has 18" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Redaction: one rule for every ledger error (ADR-0024 decision 11)
# ---------------------------------------------------------------------------


def _parse_failure(row: dict[str, str]) -> Callable[[Path], str]:
    def run(_tmp_path: Path) -> str:
        with pytest.raises(LedgerValidationError) as excinfo:
            parse_csv_text(_csv([row]))
        return str(excinfo.value)

    return run


def _conflict_failure(tmp_path: Path) -> str:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING), _row(_BUY)])), data_dir=data_dir)
    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv([_row(_BUY, price="999.99", cash_effect="-999.99")])), data_dir=data_dir)
    return str(excinfo.value)


def _unusable_database_failure(tmp_path: Path) -> str:
    db_path = portfolio_db_path(tmp_path / "data")
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not-a-ledger-private-label")
    with pytest.raises(LedgerStoreError) as excinfo:
        LedgerStore(path=db_path).load_events()
    return str(excinfo.value)


@pytest.mark.parametrize(
    ("run", "private_tokens", "expected_row"),
    [
        pytest.param(
            _parse_failure(_row(effective_at="2024-01-02 12.5 shares")),
            ("2024-01-02 12.5 shares", "12.5", "shares"),
            2,
            id="malformed-timestamp-carrying-holding-text",
        ),
        pytest.param(
            _parse_failure(_row(cash_effect="987654.32.00")),
            ("987654.32.00", "987654.32", "987654"),
            2,
            id="malformed-decimal-carrying-an-amount",
        ),
        pytest.param(
            _parse_failure(_row(_BUY, mic="XHKG", symbol="PRIVATESYMBOL")),
            ("PRIVATESYMBOL", "XHKG", "SYNT"),
            2,
            id="instrument-with-a-bad-mic",
        ),
        pytest.param(
            _parse_failure(_row(account="acct 998877 private broker label")),
            ("998877", "private broker label"),
            2,
            id="private-looking-account-label",
        ),
        pytest.param(
            _parse_failure(_row(event_type="TRANSFER_IN_PRIVATE")),
            ("TRANSFER_IN_PRIVATE", "PRIVATE"),
            2,
            id="unknown-event-type",
        ),
        pytest.param(
            _parse_failure(_row(basis_status="PARTIAL_PRIVATE")),
            ("PARTIAL_PRIVATE", "PRIVATE"),
            2,
            id="bad-basis-status",
        ),
        pytest.param(_conflict_failure, ("999.99", "SYNT"), 2, id="conflict-on-changed-content"),
        pytest.param(_unusable_database_failure, ("not-a-ledger-private-label",), None, id="unusable-database-file"),
    ],
)
def test_ledger_errors_name_the_row_and_never_echo_private_values(
    run: Callable[[Path], str], private_tokens: tuple[str, ...], expected_row: int | None, tmp_path: Path
) -> None:
    message = run(tmp_path)

    if expected_row is not None:
        assert f"row {expected_row}" in message, message
    for token in private_tokens:
        assert token not in message, (token, message)
    assert "input_value" not in message, message
    assert "input_value=" not in message, message
    assert DECIMAL_LIKE.search(message) is None, message


# ---------------------------------------------------------------------------
# Dry-run preview: full validation, proposed effects, zero writes
# ---------------------------------------------------------------------------


def test_preview_writes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    events = parse_csv_text(_csv(_hand_oracle_rows()))
    before = _snapshot(tmp_path)

    preview = preview_events(events, data_dir=data_dir)

    assert preview.new_event_count == len(events)
    assert _snapshot(tmp_path) == before == []
    assert not data_dir.exists()
    assert not (data_dir / "portfolio").exists()
    assert not list(tmp_path.rglob("*.sqlite3*"))
    for suffix in ("-journal", "-wal", "-shm"):
        assert not list(tmp_path.rglob(f"*{suffix}"))
    assert LedgerStore(data_dir=data_dir).exists() is False


def test_preview_against_a_stored_ledger_creates_no_journal_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    events = parse_csv_text(_csv(_hand_oracle_rows()))
    commit_events(events, data_dir=data_dir)
    before = _snapshot(tmp_path)
    db_path = portfolio_db_path(data_dir)
    db_before = _stored_rows(db_path)

    preview = preview_events(events, data_dir=data_dir)

    assert preview.new_event_count == 0
    assert preview.unchanged_event_count == len(events)
    assert _snapshot(tmp_path) == before
    assert _stored_rows(db_path) == db_before
    assert [suffix for suffix in COMPANION_SUFFIXES if db_path.with_name(f"{db_path.name}{suffix}").exists()] == []
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["portfolio.sqlite3"]


def test_preview_reports_proposed_effects(tmp_path: Path) -> None:
    events = parse_csv_text(_csv(_hand_oracle_rows()))
    preview = preview_events(events, data_dir=tmp_path / "data")

    assert isinstance(preview, ImportPreview)
    assert preview.event_count == 4
    assert preview.counts_by_type == {"BUY": 1, "CASH_DIVIDEND": 1, "OPENING": 1, "SELL": 1}
    assert preview.quantity_deltas == {"XNAS:SYNT": Decimal("11")}
    assert preview.cash_delta == Decimal("924.00")
    assert preview.new_event_count == 4
    assert preview.unchanged_event_count == 0


def test_preview_rejects_an_oversell_selling_to_exactly_zero_allowed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING)])), data_dir=data_dir)
    sell_all = _row(_BUY, external_id="evt-0002", event_type="SELL", sequence="2", quantity="10", price="130.00", cash_effect="1298.00", fees="2.00")

    with pytest.raises(LedgerValidationError) as excinfo:
        preview_events(parse_csv_text(_csv([{**sell_all, "quantity": "11", "cash_effect": "1428.00"}])), data_dir=data_dir)
    assert "oversells are rejected" in str(excinfo.value)

    preview = preview_events(parse_csv_text(_csv([sell_all])), data_dir=data_dir)
    assert preview.quantity_deltas == {"XNAS:SYNT": Decimal("-10")}
    assert _snapshot(data_dir) == ["portfolio", "portfolio/portfolio.sqlite3"]


# ---------------------------------------------------------------------------
# Commit, idempotency and rollback through the CSV boundary
# ---------------------------------------------------------------------------


def test_import_file_then_reimport_reports_unchanged(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = tmp_path / "ledger.csv"
    path.write_text(_csv(_hand_oracle_rows()), encoding="utf-8")
    events = parse_csv_file(path)

    first = commit_events(events, data_dir=data_dir)
    rows_after_first = _stored_rows(portfolio_db_path(data_dir))
    second = commit_events(parse_csv_file(path), data_dir=data_dir)

    assert first == AppendOutcome(inserted=4, unchanged=0)
    assert second == AppendOutcome(inserted=0, unchanged=4)
    assert _stored_rows(portfolio_db_path(data_dir)) == rows_after_first
    assert len(LedgerStore(data_dir=data_dir).load_events()) == 4


def test_reimporting_a_stored_sell_alone_is_unchanged_not_an_oversell(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sell = _sell_row(quantity="9")
    commit_events(parse_csv_text(_csv([_row(_OPENING), sell])), data_dir=data_dir)
    before = _stored_rows(portfolio_db_path(data_dir))

    reimported = parse_csv_text(_csv([sell]))
    preview = preview_events(reimported, data_dir=data_dir)
    outcome = commit_events(reimported, data_dir=data_dir)

    assert preview.new_event_count == 0
    assert preview.unchanged_event_count == 1
    assert outcome == AppendOutcome(inserted=0, unchanged=1)
    assert _stored_rows(portfolio_db_path(data_dir)) == before
    assert len(LedgerStore(data_dir=data_dir).load_events()) == 2


def test_a_changed_stored_sell_fails_as_a_content_conflict_not_an_oversell(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING), _sell_row(quantity="9")])), data_dir=data_dir)
    before = _stored_rows(portfolio_db_path(data_dir))

    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv([_sell_row(quantity="8")])), data_dir=data_dir)
    message = str(excinfo.value)
    assert "different content" in message
    assert "negative" not in message
    assert "row 2" in message
    assert _stored_rows(portfolio_db_path(data_dir)) == before


def test_a_genuinely_new_oversell_against_stored_state_names_the_row(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING), _sell_row(quantity="9")])), data_dir=data_dir)
    before = _stored_rows(portfolio_db_path(data_dir))

    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv([_sell_row(external_id="evt-0003", sequence="3", quantity="5")])), data_dir=data_dir)
    message = str(excinfo.value)
    assert "row 2" in message
    assert "would take the position negative" in message
    assert _stored_rows(portfolio_db_path(data_dir)) == before


def test_import_errors_name_the_row_without_disclosing_holdings_or_symbols(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING), _sell_row(quantity="9")])), data_dir=data_dir)

    cases: list[tuple[list[dict[str, str]], int]] = [
        ([_sell_row(external_id="evt-0003", sequence="3", quantity="5")], 2),
        ([_sell_row(quantity="8")], 2),
        ([_row(_BUY, price="12O.00")], 2),
        ([_row(_DEPOSIT, external_id="evt-0100", sequence="7"), _row(_DEPOSIT, external_id="evt-0100", sequence="8")], 3),
    ]
    messages: list[str] = []
    for rows, expected_row in cases:
        with pytest.raises(LedgerValidationError) as excinfo:
            commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)
        message = str(excinfo.value)
        messages.append(message)
        assert f"row {expected_row}" in message, message

    for message in messages:
        assert DECIMAL_LIKE.search(message) is None, message
        assert "SYNT" not in message, message
        assert "available" not in message.lower(), message


def test_duplicate_external_ids_inside_one_batch_are_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    rows = [_row(_DEPOSIT), _row(_DEPOSIT, sequence="2", cash_effect="25.00")]
    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)
    assert "appears twice in one batch" in str(excinfo.value)
    assert not data_dir.exists()


def test_invalid_last_row_persists_nothing_and_leaves_prior_batch_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING)])), data_dir=data_dir)
    before = _stored_rows(portfolio_db_path(data_dir))

    rows = [_row(_BUY), _row(_BUY, external_id="evt-0003", sequence="3", event_type="SELL", quantity="99", cash_effect="12870.00")]
    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)

    assert "oversells are rejected" in str(excinfo.value)
    assert _stored_rows(portfolio_db_path(data_dir)) == before
    assert len(LedgerStore(data_dir=data_dir).load_events()) == 1


def test_invalid_batch_into_a_fresh_directory_creates_no_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with pytest.raises(LedgerValidationError):
        commit_events(parse_csv_text(_csv([_row(_BUY, quantity="-1")])), data_dir=data_dir)
    assert _snapshot(tmp_path) == []
    assert not data_dir.exists()


def test_changed_content_in_a_second_file_fails_and_changes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    commit_events(parse_csv_text(_csv([_row(_OPENING)])), data_dir=data_dir)
    before = _stored_rows(portfolio_db_path(data_dir))

    changed = _row(_OPENING, cash_effect="999.00")
    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv([changed])), data_dir=data_dir)

    assert "already stored with different content" in str(excinfo.value)
    assert _stored_rows(portfolio_db_path(data_dir)) == before


def test_reversal_row_is_accepted_and_names_its_target(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    reversal = _row(
        _BUY,
        external_id="evt-0005",
        event_type="SELL",
        sequence="3",
        quantity="5",
        price="120.00",
        fees="0.00",
        cash_effect="600.00",
        reverses_external_id="evt-0002",
    )
    rows = [_row(_OPENING), _row(_BUY), reversal]
    outcome = commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)

    assert outcome.inserted == 3
    stored = LedgerStore(data_dir=data_dir).load_events()
    assert stored[-1].reverses_external_id == "evt-0002"
    assert [event.external_id for event in stored] == ["evt-0001", "evt-0002", "evt-0005"]


def test_reversal_row_naming_an_unknown_target_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    rows = [_row(_OPENING), _row(_BUY, reverses_external_id="evt-9999")]
    with pytest.raises(LedgerValidationError) as excinfo:
        commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)
    assert "reverses unknown external_id 'evt-9999'" in str(excinfo.value)
    assert not data_dir.exists()


def test_multiple_fills_of_one_instrument_accumulate_in_sequence_order(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    rows = [
        _row(_OPENING),
        _row(_BUY, external_id="evt-0002", sequence="2", quantity="2.5", price="120.00", cash_effect="-302.50", fees="2.50"),
        _row(_BUY, external_id="evt-0003", sequence="3", quantity="1.25", price="121.00", cash_effect="-151.25"),
        _row(_BUY, external_id="evt-0004", sequence="4", event_type="SELL", quantity="3.75", price="125.00", cash_effect="468.75", fees="0.00"),
    ]
    preview = preview_events(parse_csv_text(_csv(rows)), data_dir=data_dir)
    assert preview.quantity_deltas == {"XNAS:SYNT": Decimal("10.00")}

    commit_events(parse_csv_text(_csv(rows)), data_dir=data_dir)
    stored = LedgerStore(data_dir=data_dir).load_events()
    assert [event.sequence for event in stored] == [1, 2, 3, 4]


class TestHeaderIntegrity:
    """The frozen header must name each column exactly once (ADR-0024 decision 4).

    ``parse_csv_text`` builds each row with ``dict(zip(fieldnames, values))``, so a
    repeated column name silently keeps only the LAST value. A duplicated
    ``cash_effect`` therefore stored a different amount than the row intended,
    with no error — the exact class of silent misinterpretation the frozen
    header exists to prevent.
    """

    def test_duplicate_canonical_header_column_is_rejected(self) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([*CANONICAL_CSV_COLUMNS, "cash_effect"])
        writer.writerow([*[_row(_DEPOSIT)[column] for column in CANONICAL_CSV_COLUMNS], "1.00"])

        with pytest.raises(LedgerValidationError) as excinfo:
            parse_csv_text(buffer.getvalue())

        message = str(excinfo.value)
        assert "repeats 2 column name" in message
        assert "exactly once" in message
        # The duplicated value and the stored-looking amount are never echoed.
        assert DECIMAL_LIKE.search(message) is None, message

    def test_duplicate_header_never_silently_reinterprets_a_money_column(self) -> None:
        """Pre-fix this parsed successfully and kept the LAST cash_effect (1.00)."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([*CANONICAL_CSV_COLUMNS, "cash_effect"])
        writer.writerow([*[_row(_DEPOSIT, cash_effect="500.00")[column] for column in CANONICAL_CSV_COLUMNS], "1.00"])

        with pytest.raises(LedgerValidationError, match="exactly once"):
            parse_csv_text(buffer.getvalue())

    def test_duplicate_header_is_rejected_before_any_row_is_parsed(self) -> None:
        duplicated = "account,account," + ",".join(column for column in CANONICAL_CSV_COLUMNS if column != "account") + "\n"

        with pytest.raises(LedgerValidationError, match="exactly once"):
            parse_csv_text(duplicated)

    def test_header_only_file_is_still_an_empty_import(self) -> None:
        """Unchanged pre-existing behaviour: no data rows means no events to import."""
        assert parse_csv_text(",".join(CANONICAL_CSV_COLUMNS) + "\n") == ()
