"""Checkpoint-collapse pruning (ADR-0036).

Pruning is never bare deletion: the accounting state at the cut is written as an ``OPENING``
row-set (one cash row plus one row per open position, because ``OPENING`` carries at most one
instrument per row), then every event strictly before the cut is deleted in one transaction.
The mechanism is report-only by default, refuses when any precondition fails, requires a
verified backup whose sha256 it records, and proves that replay of the pruned ledger equals the
pre-prune book before it replaces anything.

Every test names the rule it guards, so removing that rule makes the test fail. Fixtures are
invented, every path is a ``tmp_path``, and ``PORTFOLIO_DB_PATH`` is redirected into it: the
repository's ``data/portfolio`` and any real operator ledger are never touched.
"""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import equity_lake.portfolio.store as store_module
from equity_lake.cli.__main__ import app
from equity_lake.portfolio import (
    LedgerEvent,
    LedgerProjection,
    LedgerStore,
    LedgerStoreError,
    parse_csv_text,
    parse_marks_text,
    replay_ledger,
)
from equity_lake.portfolio.marks import value_positions
from equity_lake.portfolio.store import (
    PRUNE_CHECKPOINT_SOURCE,
    LedgerPruneManifest,
    portfolio_db_path,
    prune_checkpoint_cut,
    prune_ledger,
    verify_ledger_file,
)
from tests.helpers.cli import strip_ansi  # pyright: ignore[reportMissingImports] -- resolved by pytest's rootdir sys.path

runner = CliRunner()

HEADER = (
    "account,external_source,external_id,event_type,effective_at,received_at,sequence,market,mic,symbol,"
    "currency,cash_effect,quantity,price,fees,basis,basis_status,reverses_external_id\n"
)
CUT = datetime(2024, 1, 4, 0, 0, tzinfo=UTC)
LATER = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
CHECKPOINT_AT = CUT - timedelta(microseconds=1)
PRUNED_IDS = ("evt-0001", "evt-0002")


def _row(
    *,
    external_id: str,
    event_type: str,
    effective_at: str,
    sequence: int,
    market: str = "",
    mic: str = "",
    symbol: str = "",
    cash_effect: str = "0",
    quantity: str = "",
    price: str = "",
    fees: str = "0",
    basis: str = "",
    basis_status: str = "KNOWN",
    received_at: str | None = None,
) -> str:
    fields = [
        "main",
        "fixture",
        external_id,
        event_type,
        effective_at,
        effective_at if received_at is None else received_at,
        str(sequence),
        market,
        mic,
        symbol,
        "USD",
        cash_effect,
        quantity,
        price,
        fees,
        basis,
        basis_status,
        "",
    ]
    return ",".join(fields)


def _timeline_rows() -> list[str]:
    """Deposit + opening before the cut, one buy after it: cash 1000/800, 10/12 shares."""
    return [
        _row(external_id="evt-0001", event_type="DEPOSIT", effective_at="2024-01-02T15:00:00Z", sequence=1, cash_effect="1000.00"),
        _row(
            external_id="evt-0002",
            event_type="OPENING",
            effective_at="2024-01-03T15:00:00Z",
            sequence=2,
            market="us_equity",
            mic="XNAS",
            symbol="AAA",
            quantity="10",
            price="100.00",
            basis="1000.00",
        ),
        _row(
            external_id="evt-0003",
            event_type="BUY",
            effective_at="2024-01-05T15:00:00Z",
            sequence=3,
            market="us_equity",
            mic="XNAS",
            symbol="AAA",
            cash_effect="-200.00",
            quantity="2",
            price="100.00",
        ),
    ]


def _unknown_basis_rows() -> list[str]:
    return [
        _row(external_id="evt-0001", event_type="DEPOSIT", effective_at="2024-01-02T15:00:00Z", sequence=1, cash_effect="500.00"),
        _row(
            external_id="evt-0002",
            event_type="OPENING",
            effective_at="2024-01-03T15:00:00Z",
            sequence=2,
            market="us_equity",
            mic="XNAS",
            symbol="BBB",
            quantity="5",
            price="100.00",
            basis_status="UNKNOWN",
        ),
        _row(
            external_id="evt-0003",
            event_type="BUY",
            effective_at="2024-01-05T15:00:00Z",
            sequence=3,
            market="us_equity",
            mic="XNAS",
            symbol="BBB",
            cash_effect="-100.00",
            quantity="1",
            price="100.00",
        ),
    ]


def _build(rows: list[str], *, path: Path | None = None) -> Path:
    ledger = portfolio_db_path() if path is None else path
    LedgerStore(path=ledger).append(parse_csv_text(HEADER + "".join(f"{row}\n" for row in rows)))
    return ledger


def _marks_text(symbol: str = "AAA", price: str = "125") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "marks": [
                {
                    "market": "us_equity",
                    "mic": "XNAS",
                    "symbol": symbol,
                    "currency": "USD",
                    "price": price,
                    "session": "2024-01-05",
                    "as_of": "2024-01-05T21:00:00+00:00",
                    "source": "fixture",
                    "price_basis": "unadjusted",
                    "provenance": "synthetic",
                }
            ],
            "valuation_blocks": [],
        }
    )


def _fingerprint(path: Path, as_of: datetime) -> tuple[object, ...]:
    projection = replay_ledger(LedgerStore(path=path).load_events(), account="main", as_of=as_of)
    positions = tuple(sorted((position.instrument.tuple, position.quantity, position.basis) for position in projection.positions))
    splits = tuple(sorted((split.instrument.tuple, split.ex_date, split.ratio) for split in projection.splits))
    return (projection.cash, positions, splits)


def _nav(path: Path, *, symbol: str = "AAA") -> tuple[bool, Decimal | None, Decimal]:
    projection = replay_ledger(LedgerStore(path=path).load_events(), account="main", as_of=LATER)
    valuation = value_positions(projection, parse_marks_text(_marks_text(symbol)), max_stale_sessions=1)
    return valuation.complete, valuation.nav, valuation.marked_position_value


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """Every path under ``root`` with its content hash and mode, so any write is visible."""
    snapshot: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            snapshot[f"{path.relative_to(root)}/"] = ("dir", mode)
        else:
            snapshot[str(path.relative_to(root))] = (hashlib.sha256(path.read_bytes()).hexdigest(), mode)
    return snapshot


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the canonical ledger into the temp dir; the repository's data is never used."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(store_module, "PORTFOLIO_DB_PATH", portfolio_db_path(data_dir))
    return data_dir


@pytest.fixture
def ledger() -> Path:
    return _build(_timeline_rows())


class TestReportOnly:
    def test_report_only_writes_nothing(self, ledger: Path, tmp_path: Path) -> None:
        """ADR-0036 decision 8: a plan writes no file, backup, copy, manifest, directory or journal.

        Falsified by: creating the manifest directory or the backup before the ``apply`` decision.
        """
        before = _snapshot(tmp_path)
        report = prune_ledger(ledger, before=CUT)

        assert report.applied is False
        assert report.manifest_filename is None
        assert report.backup_source == "none"
        assert report.pruned_event_count == 2
        assert report.row_count_after == 3
        assert report.checkpoint_cash == Decimal("1000.00")
        assert _snapshot(tmp_path) == before
        assert verify_ledger_file(ledger).sha256 == verify_ledger_file(ledger).sha256

    def test_the_plan_names_the_ids_and_the_planned_digest(self, ledger: Path) -> None:
        """Decision 8: the plan reports the cut, ids to prune and the planned record digest."""
        source = verify_ledger_file(ledger)
        report = prune_ledger(ledger, before=CUT)
        assert report.pruned_event_external_ids == PRUNED_IDS
        assert report.record_digest_before == source.record_digest
        assert report.record_digest_after != source.record_digest
        assert report.checkpoint_effective_at == CHECKPOINT_AT


class TestApply:
    def test_apply_prunes_and_preserves_the_book(self, ledger: Path, tmp_path: Path) -> None:
        """Decisions 2 and 7: the book at and after the cut is exactly the pre-prune book.

        Falsified by: deleting without a checkpoint, a checkpoint with a wrong cash/quantity/
        basis, or replacing before the replay-equality proof.
        """
        before_cut = _fingerprint(ledger, CUT)
        before_later = _fingerprint(ledger, LATER)
        before_nav = _nav(ledger)

        report = prune_ledger(ledger, before=CUT, apply=True)

        assert report.applied is True
        assert report.already_pruned is False
        assert report.backup_source == "created"
        assert report.manifest_filename is not None
        assert report.record_digest_after == verify_ledger_file(ledger).record_digest
        assert _fingerprint(ledger, CUT) == before_cut
        assert _fingerprint(ledger, LATER) == before_later
        assert _nav(ledger) == before_nav
        assert report.checkpoint_position_count == 1
        assert report.backup_filename is not None
        assert (ledger.parent / report.backup_filename).exists()
        assert verify_ledger_file(ledger.parent / report.backup_filename).sha256 == report.backup_sha256

    def test_the_pruned_events_are_gone_and_the_retained_one_survives(self, ledger: Path) -> None:
        """Decision 2(e): every event strictly before the cut is deleted, never one at/after it."""
        prune_ledger(ledger, before=CUT, apply=True)
        events = LedgerStore(path=ledger).load_events()
        ids = {event.external_id for event in events}
        assert set(PRUNED_IDS).isdisjoint(ids)
        assert "evt-0003" in ids
        assert all(event.effective_at >= CUT for event in events if event.external_source != PRUNE_CHECKPOINT_SOURCE)

    def test_the_checkpoint_sorts_before_every_retained_event(self, ledger: Path) -> None:
        """The checkpoint must sort first in logical order, not rely on a sequence tie-break.

        Falsified by: using a checkpoint sequence above the earliest retained sequence, which
        replay would order after retained events and expose as a temporal inversion.
        """
        prune_ledger(ledger, before=CUT, apply=True)
        events = LedgerStore(path=ledger).load_events()
        checkpoint = [event for event in events if event.external_source == PRUNE_CHECKPOINT_SOURCE]
        retained = [event for event in events if event.external_source != PRUNE_CHECKPOINT_SOURCE]

        assert checkpoint
        assert retained
        assert all(event.effective_at == CHECKPOINT_AT for event in checkpoint)
        assert max(event.sequence for event in checkpoint) < min(event.sequence for event in retained)
        assert all(left.effective_at < right.effective_at for left in checkpoint for right in retained)
        assert events[0].external_source == PRUNE_CHECKPOINT_SOURCE

    def test_an_unknown_basis_stays_unknown(self, tmp_path: Path) -> None:
        """ADR-0024 decision 6: an explicitly unknown basis is never back-filled by a prune."""
        ledger = _build(_unknown_basis_rows())
        prune_ledger(ledger, before=CUT, apply=True)

        events = LedgerStore(path=ledger).load_events()
        position_rows = [event for event in events if event.external_source == PRUNE_CHECKPOINT_SOURCE and event.quantity is not None]
        assert len(position_rows) == 1
        assert position_rows[0].basis is None
        assert position_rows[0].basis_status.value == "UNKNOWN"

        projection = replay_ledger(events, account="main", as_of=LATER)
        position = next(position for position in projection.positions if position.instrument.symbol == "BBB")
        assert position.basis is None
        assert position.realized_gain is None or position.realized_gain == 0

    def test_the_manifest_records_the_prune(self, ledger: Path) -> None:
        """Decision 5: the manifest records the cut, ids, digests, backup, version and UTC stamp."""
        report = prune_ledger(ledger, before=CUT, apply=True)
        directory = ledger.parent / "prune"
        manifests = sorted(directory.glob("*.json"))
        assert len(manifests) == 1
        manifest = LedgerPruneManifest.model_validate_json(manifests[0].read_text(encoding="utf-8"))

        assert manifest.filename == ledger.name
        assert manifest.cut == CUT
        assert manifest.checkpoint_effective_at == CHECKPOINT_AT
        assert "row-set" in manifest.checkpoint_layout
        assert manifest.pruned_event_external_ids == PRUNED_IDS
        assert list(manifest.pruned_event_external_ids) == sorted(manifest.pruned_event_external_ids)
        assert manifest.pruned_event_count == 2
        assert manifest.retained_event_count == 1
        assert manifest.record_digest_before == report.record_digest_before
        assert manifest.record_digest_after == report.record_digest_after
        assert manifest.backup_filename == report.backup_filename
        assert manifest.backup_sha256 == report.backup_sha256
        assert manifest.checkpoint_external_ids == tuple(sorted(report.checkpoint_external_ids))
        assert manifest.tool_version
        assert manifest.created_at.tzinfo is not None
        assert manifest.created_at.utcoffset() == timedelta(0)
        assert report.backup_filename is not None
        assert (ledger.parent / report.backup_filename).exists()

    def test_a_second_run_for_the_same_cut_is_a_no_op(self, ledger: Path) -> None:
        """Decision 6: a second run reports the existing checkpoint and writes nothing."""
        first = prune_ledger(ledger, before=CUT, apply=True)
        hash_after_first = verify_ledger_file(ledger).sha256
        manifests_after_first = sorted((ledger.parent / "prune").glob("*.json"))

        second = prune_ledger(ledger, before=CUT, apply=True)

        assert second.already_pruned is True
        assert second.applied is False
        assert second.pruned_event_count == 0
        assert second.checkpoint_external_ids == first.checkpoint_external_ids
        assert verify_ledger_file(ledger).sha256 == hash_after_first
        assert sorted((ledger.parent / "prune").glob("*.json")) == manifests_after_first
        events = LedgerStore(path=ledger).load_events()
        assert sum(1 for event in events if event.external_source == PRUNE_CHECKPOINT_SOURCE) == len(first.checkpoint_external_ids)


class TestRefusals:
    def test_a_naive_or_non_past_instant_refuses(self, ledger: Path) -> None:
        """Decision 3(c): the cut must be timezone-aware and strictly in the past."""
        before = verify_ledger_file(ledger).sha256
        with pytest.raises(LedgerStoreError, match="timezone-aware"):
            prune_ledger(ledger, before=datetime(2024, 1, 4))
        with pytest.raises(LedgerStoreError, match="strictly in the past"):
            prune_ledger(ledger, before=datetime.now(UTC) + timedelta(days=1))
        assert verify_ledger_file(ledger).sha256 == before

    def test_nothing_to_prune_refuses(self, ledger: Path) -> None:
        """Decision 3(c): a cut before every event has nothing to collapse."""
        with pytest.raises(LedgerStoreError, match="nothing to prune"):
            prune_ledger(ledger, before=datetime(2024, 1, 1, tzinfo=UTC))

    def test_a_missing_backup_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """Decision 3(b): ``--apply`` needs a verified backup; a missing one is not created implicitly."""
        before = verify_ledger_file(ledger).sha256
        with pytest.raises(LedgerStoreError, match="does not exist"):
            prune_ledger(ledger, before=CUT, apply=True, backup=tmp_path / "missing.bak")
        assert verify_ledger_file(ledger).sha256 == before

    def test_a_short_backup_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """Decision 3(b): an unverifiable stub is never accepted as the reversal."""
        stub = tmp_path / "short.bak"
        stub.write_bytes(b"not a ledger")
        with pytest.raises(LedgerStoreError):
            prune_ledger(ledger, before=CUT, apply=True, backup=stub)

    def test_a_backup_of_another_ledger_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """Decision 3(b): the backup must be this exact file, not merely some verified ledger."""
        other = _build(_unknown_basis_rows(), path=tmp_path / "other.sqlite3")
        before = verify_ledger_file(ledger).sha256
        with pytest.raises(LedgerStoreError, match="does not match the current ledger"):
            prune_ledger(ledger, before=CUT, apply=True, backup=other)
        assert verify_ledger_file(ledger).sha256 == before

    def test_a_foreign_target_path_refuses(self, tmp_path: Path) -> None:
        """Decision 3(d): only the configured private ledger may be pruned."""
        foreign = _build(_timeline_rows(), path=tmp_path / "fixture-copy.sqlite3")
        with pytest.raises(LedgerStoreError, match="not the configured private ledger"):
            prune_ledger(foreign, before=CUT)

    def test_negative_checkpoint_cash_refuses(self) -> None:
        """An ``OPENING`` row cannot express negative cash, so the checkpoint builder refuses it."""
        projection = LedgerProjection(
            account="main",
            as_of=CUT,
            positions=(),
            cash=Decimal("-1"),
            deposits=Decimal("0"),
            withdrawals=Decimal("0"),
            embedded_fees=Decimal("0"),
            standalone_fees=Decimal("0"),
            dividend_fees=Decimal("0"),
            net_dividend_income=Decimal("0"),
            realized_gain=Decimal("0"),
            replayed_event_count=0,
        )
        with pytest.raises(LedgerStoreError, match="negative cash"):
            store_module._checkpoint_events(projection, account="main", cut=CUT, sequences=(1,))

    def test_a_checkpoint_that_does_not_replay_to_the_same_book_refuses(self, ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decision 7: a checkpoint that changes the book is refused, never "fixed up".

        The checkpoint builder is corrupted deliberately. With the replay-equality proof in
        place the prune refuses; removing that proof lets the wrong prune succeed and this
        named test fails (the mutation/restore proof records that failure).
        """
        real_builder = store_module._checkpoint_events

        def corrupted(projection: LedgerProjection, *, account: str, cut: datetime, sequences: tuple[int, ...]) -> tuple[LedgerEvent, ...]:
            rows = real_builder(projection, account=account, cut=cut, sequences=sequences)
            cash, *positions = rows
            if positions:
                positions[0] = positions[0].model_copy(update={"basis": Decimal("1000.01")})
            return (cash, *positions)

        monkeypatch.setattr(store_module, "_checkpoint_events", corrupted)
        before = verify_ledger_file(ledger).sha256
        with pytest.raises(LedgerStoreError, match="does not reproduce the same book"):
            prune_ledger(ledger, before=CUT, apply=True)
        assert verify_ledger_file(ledger).sha256 == before


class TestDisclosure:
    def test_summary_discloses_the_pruned_history(self, ledger: Path, tmp_path: Path) -> None:
        """Decision 4: collapsed categories are disclosed, never rendered as unexplained zeros."""
        prune_ledger(ledger, before=CUT, apply=True)
        marks = tmp_path / "marks.json"
        marks.write_text(_marks_text(), encoding="utf-8")

        result = runner.invoke(app, ["portfolio", "summary", "--as-of", "2024-01-05T21:00:00Z", "--marks", str(marks)])

        assert result.exit_code == 0, result.output
        assert "PRUNED HISTORY" in result.stdout
        assert "cannot be reconstructed" in result.stdout
        assert "deposits 0.00" in result.stdout
        assert "[pruned history: excludes events before the checkpoint cut" in result.stdout
        assert "realized gain 0.00 [pruned history" in result.stdout
        assert "NAV 2300.00" in result.stdout

    def test_positions_discloses_the_pruned_history(self, ledger: Path, tmp_path: Path) -> None:
        """Decision 4: ``positions`` reads realized gain, so it must disclose a pruned history."""
        prune_ledger(ledger, before=CUT, apply=True)
        marks = tmp_path / "marks.json"
        marks.write_text(_marks_text(), encoding="utf-8")

        result = runner.invoke(app, ["portfolio", "positions", "--as-of", "2024-01-05T21:00:00Z", "--marks", str(marks)])

        assert result.exit_code == 0, result.output
        assert "PRUNED HISTORY" in result.stdout
        assert "realized gain 0.00" in result.stdout
        assert "quantity 12" in result.stdout
        assert "basis 1200.00" in result.stdout

    def test_the_checkpoint_cut_is_read_back_from_the_ledger(self, ledger: Path) -> None:
        """A reader cannot mistake the truncated history for a complete one (decision 5)."""
        prune_ledger(ledger, before=CUT, apply=True)
        assert prune_checkpoint_cut(LedgerStore(path=ledger).load_events()) == CUT
        assert prune_checkpoint_cut([]) is None


class TestCli:
    def test_prune_help_states_the_command_contract(self) -> None:
        result = runner.invoke(app, ["portfolio", "prune", "--help"], env={"COLUMNS": "200"})

        assert result.exit_code == 0, result.output
        help_stdout = strip_ansi(result.stdout)
        assert "--before" in help_stdout
        assert "--apply" in help_stdout
        assert "--backup" in help_stdout
        assert "report-only" in help_stdout
        assert "verified backup" in help_stdout
        assert "manifest" in help_stdout
        assert "Exit codes" in help_stdout
        assert "no-op" in help_stdout

    def test_cli_report_only_prints_the_plan_and_writes_nothing(self, ledger: Path, tmp_path: Path) -> None:
        before = _snapshot(tmp_path)
        result = runner.invoke(app, ["portfolio", "prune", "--before", "2024-01-04T00:00:00Z"])

        assert result.exit_code == 0, result.output
        assert "Report only: nothing was written" in result.stdout
        assert "checkpoint row-set" in result.stdout
        assert _snapshot(tmp_path) == before

    def test_cli_apply_refusal_is_value_free(self, tmp_path: Path) -> None:
        """ADR-0024 decision 11: a refusal never echoes a stored amount or instrument."""
        ledger = _build(
            [
                _row(external_id="evt-0001", event_type="DEPOSIT", effective_at="2024-01-02T15:00:00Z", sequence=1, cash_effect="987654.32"),
                _row(
                    external_id="evt-0002",
                    event_type="OPENING",
                    effective_at="2024-01-03T15:00:00Z",
                    sequence=2,
                    market="us_equity",
                    mic="XNAS",
                    symbol="PRIVATESYM",
                    quantity="10",
                    price="100.00",
                    basis="1000.00",
                ),
            ]
        )
        result = runner.invoke(
            app,
            ["portfolio", "prune", "--before", "2024-01-04T00:00:00Z", "--apply", "--backup", str(tmp_path / "missing.bak")],
        )

        assert result.exit_code == 1
        assert "portfolio prune:" in result.output
        assert "PRIVATESYM" not in result.output
        assert "987654.32" not in result.output
        assert verify_ledger_file(ledger).row_count == 2
