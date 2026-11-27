"""Verified local backup export and the read-only backup inventory (RS-P02, ADR-0031 d3).

ADR-0031 decision 3 defines a backup as an exact-file copy, taken in a no-writer window, that
is accepted only after the five verification checks pass, with manual retention and no
scheduled, remote or cloud copy. RS-P02 delivers exactly that local posture: a manual export
into an operator-chosen private directory (`equity portfolio backup`), a report-only inventory
of backup files (`equity portfolio backups`), recovery points across migration, and the
documented invariant that every ledger event is retained.

Out of scope and deliberately absent here: encrypted/off-host/remote/cloud export or drill (a
new ADR is required first), backup-file pruning, and any event deletion.

Every test names the rule it guards, so removing that rule makes the test fail. Fixtures are
invented and every path is a ``tmp_path``: the repository's ``data/portfolio`` and the real
operator ledger are never touched.
"""

from __future__ import annotations

import re
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import equity_lake.portfolio.store as store_module
from equity_lake.cli.__main__ import app
from equity_lake.portfolio import LedgerStore, LedgerStoreError, parse_csv_text, replay_ledger
from equity_lake.portfolio.store import (
    export_verified_backup,
    inventory_backups,
    portfolio_db_path,
    restore_ledger,
    verify_ledger_file,
)
from tests.helpers.cli import strip_ansi  # pyright: ignore[reportMissingImports] -- resolved by pytest's rootdir sys.path

runner = CliRunner()

HEADER = (
    "account,external_source,external_id,event_type,effective_at,received_at,sequence,market,mic,symbol,"
    "currency,cash_effect,quantity,price,fees,basis,basis_status,reverses_external_id\n"
)
AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
FROZEN = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
FROZEN_STAMP = "20261122T120000Z"


def _row(seq: int, *, quantity: str = "10") -> str:
    return (
        f"main,fixture,evt-{seq:04d},OPENING,2024-01-0{seq}T15:00:00Z,2024-01-0{seq}T21:00:00Z,{seq},"
        f"us_equity,XNAS,AAA,USD,1000.00,{quantity},100.00,0,1000.00,KNOWN,"
    )


def _build_ledger(path: Path, *, rows: int = 2) -> Path:
    """A real v2 ledger written through the store's own append path."""
    store = LedgerStore(path=path)
    store.append(parse_csv_text(HEADER + "".join(f"{_row(seq)}\n" for seq in range(1, rows + 1))))
    return path


def _replay_fingerprint(path: Path) -> tuple[str, tuple[tuple[tuple[str, str], int], ...], int]:
    """What the stored record means at one instant: cash, per-symbol quantity, events replayed."""
    projection = replay_ledger(LedgerStore(path=path).load_events(), account="main", as_of=AS_OF)
    positions = tuple(
        sorted(
            ((position.instrument.mic, position.instrument.symbol), int(position.quantity))
            for position in projection.positions
            if position.quantity != 0
        )
    )
    return str(projection.cash), positions, projection.replayed_event_count


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the canonical ledger into the temp dir: the repo's data/portfolio is never used."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(store_module, "PORTFOLIO_DB_PATH", portfolio_db_path(data_dir))
    return data_dir


@pytest.fixture
def ledger() -> Path:
    """A real v2 ledger at the canonical (temp-redirected) path the CLI reads."""
    return _build_ledger(portfolio_db_path(), rows=2)


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def _assert_no_backup_files(scope: Path) -> None:
    """No backup file, temporary file or rollback journal exists anywhere under ``scope``."""
    assert list(scope.rglob("*.bak*")) == []
    assert list(scope.rglob("*.tmp")) == []
    assert list(scope.rglob("*-journal")) == []
    assert list(scope.rglob("*-wal")) == []
    assert list(scope.rglob("*-shm")) == []


def _assert_nothing_exported(destination: Path, scope: Path) -> None:
    """The destination was not created and no backup artifact appeared anywhere in ``scope``."""
    assert not destination.exists()
    _assert_no_backup_files(scope)


class TestExportReporting:
    def test_report_only_creates_no_directory_file_or_journal(self, ledger: Path, tmp_path: Path) -> None:
        """ADR-0031 decision 7: a plan writes nothing at all — no directory, no file, no journal.

        Falsified by: creating the destination directory (or the copy) before the ``apply``
        decision, which turns a plan into a private file write.
        """
        destination = tmp_path / "private-backups"
        before = verify_ledger_file(ledger).sha256
        siblings_before = _names(ledger.parent)

        report = export_verified_backup(destination, ledger)

        assert report.applied is False
        assert report.directory_exists is False
        assert report.backup_filename is None
        assert report.backup_sha256 is None
        assert report.row_count == 2
        assert report.schema_version == 2
        assert report.size_bytes == ledger.stat().st_size
        _assert_nothing_exported(destination, tmp_path)
        assert verify_ledger_file(ledger).sha256 == before
        assert _names(ledger.parent) == siblings_before

    def test_the_report_names_the_planned_file_without_claiming_it_exists(self, ledger: Path, tmp_path: Path) -> None:
        """A plan reports the destination and the ledger's version; the name is derived on apply."""
        destination = tmp_path / "private-backups"
        report = export_verified_backup(destination, ledger)
        assert report.directory == str(destination)
        assert report.filename == "portfolio.sqlite3"
        assert report.backup_filename is None


class TestExportApply:
    def test_apply_writes_a_verified_mode_restricted_copy(self, ledger: Path, tmp_path: Path) -> None:
        """A copy is not a backup until it verifies (ADR-0031 decision 3), and it is private.

        Falsified by: copying without re-verifying the copy, or leaving default permissions on
        the private directory and the backup file.
        """
        destination = tmp_path / "private-backups"
        source = verify_ledger_file(ledger)

        report = export_verified_backup(destination, ledger, apply=True)

        assert report.applied is True
        assert report.backup_filename is not None
        written = destination / report.backup_filename
        assert written.is_file()
        assert written.name.startswith("portfolio.sqlite3.")
        assert written.name.endswith(".v2.bak")
        assert stat.S_IMODE(written.stat().st_mode) == 0o600
        assert stat.S_IMODE(destination.stat().st_mode) == 0o700
        verified = verify_ledger_file(written, expect_version=2)
        assert (verified.sha256, verified.row_count, verified.record_digest) == (source.sha256, source.row_count, source.record_digest)
        assert report.backup_sha256 == source.sha256
        assert _names(destination) == [written.name]  # no temporary file is left behind
        assert verify_ledger_file(ledger).sha256 == source.sha256  # the ledger is not modified

    def test_an_existing_backup_name_is_never_overwritten(self, ledger: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Manual retention: a second copy never replaces a name that is already taken.

        Falsified by: overwriting (or silently renaming around) an existing file, which destroys
        the recovery point the operator already had.
        """

        class _FrozenClock(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> Any:
                return FROZEN if tz is not None else FROZEN.replace(tzinfo=None)

        monkeypatch.setattr(store_module, "datetime", _FrozenClock)
        destination = tmp_path / "private-backups"
        destination.mkdir()
        squatter = destination / f"portfolio.sqlite3.{FROZEN_STAMP}.v2.bak"
        squatter.write_bytes(b"an operator's own file, not a ledger")

        with pytest.raises(LedgerStoreError, match="never overwritten"):
            export_verified_backup(destination, ledger, apply=True)

        assert squatter.read_bytes() == b"an operator's own file, not a ledger"
        assert _names(destination) == [squatter.name]

    def test_a_copy_that_does_not_match_its_source_is_discarded(self, ledger: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The verification gate: a readable copy of another record is refused and discarded.

        The decoy is a perfectly valid v2 ledger, so only the row-count/record-digest comparison
        with the source can catch it — a corrupt file would already fail verification itself.
        Falsified by: removing that comparison in ``_write_verified_backup``, which would name a
        different record as this ledger's recovery point (the RS-P02 mutation record).
        """
        destination = tmp_path / "private-backups"
        destination.mkdir()
        decoy = _build_ledger(tmp_path / "decoy" / "portfolio.sqlite3", rows=1)
        assert verify_ledger_file(decoy).row_count == 1
        real_copy = store_module.shutil.copy2

        def substituting_copy(source: Any, target: Any, **kwargs: Any) -> Any:
            return real_copy(decoy, target, **kwargs)

        monkeypatch.setattr(store_module.shutil, "copy2", substituting_copy)

        with pytest.raises(LedgerStoreError, match="does not match its source"):
            export_verified_backup(destination, ledger, apply=True)
        assert _names(destination) == []  # neither the copy nor its temporary name survives

    def test_a_truncated_copy_is_discarded(self, ledger: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A copy that is no longer a usable database is refused by verification itself."""
        destination = tmp_path / "private-backups"
        destination.mkdir()
        real_copy = store_module.shutil.copy2

        def corrupting_copy(source: Any, target: Any, **kwargs: Any) -> Any:
            result = real_copy(source, target, **kwargs)
            Path(str(target)).write_bytes(Path(str(target)).read_bytes()[:120])
            return result

        monkeypatch.setattr(store_module.shutil, "copy2", corrupting_copy)

        with pytest.raises(LedgerStoreError, match="not a usable SQLite database"):
            export_verified_backup(destination, ledger, apply=True)
        assert _names(destination) == []


class TestExportRefusals:
    def test_a_missing_ledger_refuses(self, tmp_path: Path) -> None:
        """An export never creates a ledger: a missing book is not an empty one."""
        with pytest.raises(LedgerStoreError, match="does not exist"):
            export_verified_backup(tmp_path / "private-backups", tmp_path / "absent.sqlite3", apply=True)
        assert not (tmp_path / "private-backups").exists()

    def test_a_rollback_journal_refuses_before_any_write(self, ledger: Path, tmp_path: Path) -> None:
        """A journal means a writer did not finish; never copy that half-written state.

        Falsified by: dropping the journal check, which would export a half-written book and
        still call it a backup.
        """
        journal = ledger.with_name(f"{ledger.name}-journal")
        journal.write_bytes(b"")
        try:
            with pytest.raises(LedgerStoreError, match="journal"):
                export_verified_backup(tmp_path / "private-backups", ledger, apply=True)
        finally:
            journal.unlink()
        assert not (tmp_path / "private-backups").exists()

    def test_a_busy_ledger_refuses_before_any_write(self, ledger: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A live writer means no no-writer window: refuse, with no retry loop (decision 8).

        Falsified by: retrying or forcing the open, which could copy a state that is being
        rewritten. The lock is real here; only the busy timeout is shortened so the test is fast.
        """

        def _impatient(path: Path) -> sqlite3.Connection:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=0")
            return connection

        monkeypatch.setattr(store_module, "_read_only_connection", _impatient)
        writer = sqlite3.connect(ledger)
        try:
            writer.execute("BEGIN EXCLUSIVE")
            with pytest.raises(LedgerStoreError, match="not a usable SQLite database \\(OperationalError\\)"):
                export_verified_backup(tmp_path / "private-backups", ledger, apply=True)
        finally:
            writer.rollback()
            writer.close()
        assert not (tmp_path / "private-backups").exists()

    def test_a_corrupt_ledger_refuses_before_any_write(self, tmp_path: Path) -> None:
        """A file that is not a SQLite database is refused and preserved, never rewritten."""
        broken = tmp_path / "production" / "portfolio.sqlite3"
        broken.parent.mkdir()
        broken.write_bytes(b"not a database")
        with pytest.raises(LedgerStoreError, match="not a usable SQLite database"):
            export_verified_backup(tmp_path / "private-backups", broken, apply=True)
        assert broken.read_bytes() == b"not a database"
        assert not (tmp_path / "private-backups").exists()

    def test_an_unsupported_version_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """The version gate names what this build supports; nothing is exported half-blind."""
        connection = sqlite3.connect(ledger)
        try:
            connection.execute("PRAGMA user_version=7")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(LedgerStoreError, match="unsupported private portfolio ledger schema version 7"):
            export_verified_backup(tmp_path / "private-backups", ledger, apply=True)
        assert not (tmp_path / "private-backups").exists()

    def test_a_symlinked_destination_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """A private copy is written into a real private directory, never through a symlink.

        Falsified by: following the link, which would place the private book wherever it points.
        """
        real = tmp_path / "elsewhere"
        real.mkdir()
        link = tmp_path / "private-backups"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(LedgerStoreError, match="symlink"):
            export_verified_backup(link, ledger, apply=True)
        assert _names(real) == []

    def test_a_destination_that_is_not_a_directory_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """A file where the directory should be is refused, not replaced."""
        destination = tmp_path / "private-backups"
        destination.write_bytes(b"")
        with pytest.raises(LedgerStoreError, match="not a directory"):
            export_verified_backup(destination, ledger, apply=True)
        assert destination.read_bytes() == b""


class TestInventory:
    def test_a_verified_backup_is_listed_with_its_metadata(self, ledger: Path, tmp_path: Path) -> None:
        """The inventory reports name, size, mtime, version, rows, digest and the verdict."""
        destination = tmp_path / "private-backups"
        report = export_verified_backup(destination, ledger, apply=True)
        source = verify_ledger_file(ledger)

        inventory = inventory_backups(destination)

        assert inventory.exists is True
        assert inventory.skipped == ()
        assert len(inventory.candidates) == 1
        candidate = inventory.candidates[0]
        assert candidate.filename == report.backup_filename
        assert candidate.verified is True
        assert candidate.size_bytes == (destination / str(report.backup_filename)).stat().st_size
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", candidate.modified_at) is not None
        assert candidate.schema_version == source.schema_version
        assert candidate.row_count == source.row_count == 2
        assert candidate.sha256 == source.sha256
        assert candidate.detail is None

    def test_a_tampered_backup_fails_verification(self, ledger: Path, tmp_path: Path) -> None:
        """A damaged copy is a refused candidate, never silently accepted and never repaired.

        Falsified by: reporting a candidate from its name and size alone without re-verifying it.
        """
        destination = tmp_path / "private-backups"
        destination.mkdir()
        tampered = destination / "portfolio.sqlite3.20260101T000000Z.v2.bak"
        tampered.write_bytes(ledger.read_bytes()[:120])
        before = tampered.read_bytes()

        inventory = inventory_backups(destination)

        assert len(inventory.candidates) == 1
        candidate = inventory.candidates[0]
        assert candidate.verified is False
        assert candidate.schema_version is None
        assert candidate.row_count is None
        assert candidate.detail is not None
        assert tampered.read_bytes() == before  # preserved for inspection, never repaired

    def test_symlinks_and_foreign_files_are_skipped_and_never_mutated(self, ledger: Path, tmp_path: Path) -> None:
        """A listing reads only real backup files: no symlink is followed, nothing is deleted.

        Falsified by: calling ``is_file``/``verify`` on the entry (which follows the link), or
        treating an unrelated file as a candidate.
        """
        destination = tmp_path / "private-backups"
        export_verified_backup(destination, ledger, apply=True)
        note = destination / "notes.txt"
        note.write_text("operator note", encoding="utf-8")
        (destination / "subdir").mkdir()
        link = destination / "link-to-ledger.bak"
        link.symlink_to(ledger)
        target_before = (ledger.stat().st_size, verify_ledger_file(ledger).sha256)
        listing_before = _names(destination)

        inventory = inventory_backups(destination)

        reasons = {skip.filename: skip.reason for skip in inventory.skipped}
        assert reasons["notes.txt"] == "not a ledger backup name"
        assert reasons["link-to-ledger.bak"] == "symlink; never followed or read"
        assert reasons["subdir"] == "not a regular file"
        assert [candidate.filename for candidate in inventory.candidates] == list(name for name in listing_before if name.endswith(".v2.bak"))
        assert _names(destination) == listing_before  # nothing was deleted or created
        assert note.read_text(encoding="utf-8") == "operator note"
        assert (ledger.stat().st_size, verify_ledger_file(ledger).sha256) == target_before

    def test_a_missing_directory_is_reported_absent_and_never_created(self, tmp_path: Path) -> None:
        """The inventory never creates the directory it lists."""
        directory = tmp_path / "private-backups"
        inventory = inventory_backups(directory)
        assert inventory.exists is False
        assert inventory.candidates == ()
        assert not directory.exists()

    def test_a_symlinked_directory_refuses(self, tmp_path: Path) -> None:
        """The listing never walks through a symlinked directory."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "private-backups"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(LedgerStoreError, match="symlink"):
            inventory_backups(link)


class TestRecoveryDrill:
    def test_restore_from_an_export_into_an_isolated_target_reproduces_the_bytes(self, ledger: Path, tmp_path: Path) -> None:
        """The drill: a verified export restores into an empty isolated target byte for byte.

        Falsified by: an export that is not an exact copy, or a restore that changes the record.
        The production-side ledger hash is measured before and after and must not move.
        """
        destination = tmp_path / "private-backups"
        production_before = verify_ledger_file(ledger).sha256
        fingerprint_before = _replay_fingerprint(ledger)
        report = export_verified_backup(destination, ledger, apply=True)
        exported = destination / str(report.backup_filename)

        isolated = tmp_path / "isolated" / "portfolio.sqlite3"
        restore_report = restore_ledger(exported, isolated, apply=True)

        assert restore_report.applied is True
        assert restore_report.target_existed is False
        restored = verify_ledger_file(isolated)
        assert restored.sha256 == production_before
        assert (restored.row_count, restored.record_digest) == (report.row_count, report.record_digest)
        assert _replay_fingerprint(isolated) == fingerprint_before
        assert verify_ledger_file(ledger).sha256 == production_before  # production untouched
        assert _replay_fingerprint(ledger) == fingerprint_before
        # The isolated copy is a working ledger, not just bytes: it appends through the store.
        LedgerStore(path=isolated).append(parse_csv_text(HEADER + f"{_row(3, quantity='5')}\n"))
        assert verify_ledger_file(isolated).row_count == 3
        assert verify_ledger_file(ledger).row_count == 2

    def test_a_no_op_migration_writes_no_backup(self, ledger: Path) -> None:
        """A current ledger needs no migration, so it has no migration backup to offer.

        Documented consequence: `equity portfolio backup` is the way to obtain a recovery point;
        a no-op `migrate --apply` must never be run just to produce a `.bak`.
        """
        before = _names(ledger.parent)
        report = store_module.migrate_ledger(ledger, apply=True)
        assert report.already_current is True
        assert report.backup_filename is None
        assert _names(ledger.parent) == before
        assert list(ledger.parent.glob("*.bak")) == []


class TestCliSurface:
    def test_backup_help_documents_both_modes(self) -> None:
        result = runner.invoke(app, ["portfolio", "backup", "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0, result.output
        assert "--output-dir" in strip_ansi(result.stdout)
        assert "--apply" in strip_ansi(result.stdout)

    def test_backups_help_documents_the_read_only_listing(self) -> None:
        result = runner.invoke(app, ["portfolio", "backups", "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0, result.output
        assert "--directory" in strip_ansi(result.stdout)

    def test_the_cli_report_only_writes_nothing(self, ledger: Path, tmp_path: Path) -> None:
        """`equity portfolio backup` without ``--apply`` prints a plan and writes nothing."""
        destination = tmp_path / "private-backups"
        before = verify_ledger_file(ledger).sha256

        result = runner.invoke(app, ["portfolio", "backup", "--output-dir", str(destination)])

        assert result.exit_code == 0, result.output
        assert "Report only" in result.stdout
        _assert_nothing_exported(destination, tmp_path)
        assert verify_ledger_file(ledger).sha256 == before

    def test_the_cli_apply_writes_and_reports_the_verified_copy(self, ledger: Path, tmp_path: Path) -> None:
        destination = tmp_path / "private-backups"

        result = runner.invoke(app, ["portfolio", "backup", "--output-dir", str(destination), "--apply"])

        assert result.exit_code == 0, result.output
        assert "Exported a verified backup" in result.stdout
        files = list(destination.glob("*.v2.bak"))
        assert len(files) == 1
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600

    def test_the_cli_refuses_without_a_ledger_and_creates_nothing(self, tmp_path: Path) -> None:
        destination = tmp_path / "private-backups"
        result = runner.invoke(app, ["portfolio", "backup", "--output-dir", str(destination), "--apply"])
        assert result.exit_code == 1
        assert "portfolio backup:" in result.output + str(result.stderr)
        assert not destination.exists()

    def test_the_cli_inventory_lists_and_exits_zero(self, ledger: Path, tmp_path: Path) -> None:
        destination = tmp_path / "private-backups"
        export_verified_backup(destination, ledger, apply=True)

        result = runner.invoke(app, ["portfolio", "backups", "--directory", str(destination)])

        assert result.exit_code == 0, result.output
        assert "1 verified" in result.stdout
        assert "Read only" in result.stdout

    def test_the_cli_inventory_exits_one_on_a_tampered_backup(self, ledger: Path, tmp_path: Path) -> None:
        destination = tmp_path / "private-backups"
        destination.mkdir()
        (destination / "portfolio.sqlite3.20260101T000000Z.v2.bak").write_bytes(ledger.read_bytes()[:120])

        result = runner.invoke(app, ["portfolio", "backups", "--directory", str(destination)])

        assert result.exit_code == 1
        assert "refused" in result.stdout
        assert "not recovery points" in result.output + str(result.stderr)

    def test_the_cli_inventory_exits_one_on_a_missing_directory(self, tmp_path: Path) -> None:
        destination = tmp_path / "private-backups"
        result = runner.invoke(app, ["portfolio", "backups", "--directory", str(destination)])
        assert result.exit_code == 1
        assert not destination.exists()
