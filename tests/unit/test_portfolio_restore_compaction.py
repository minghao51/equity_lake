"""Ledger restore and compaction (ADR-0031 decisions 4-5).

The two mechanisms share one rule: the record survives, the file does not matter. A restore
re-verifies the candidate, backs up the *current* file (it may hold events the backup lacks),
replaces atomically and re-verifies; a compaction rebuilds with `VACUUM INTO` and proves the
version, row count and `(account, external_id, content_digest)` digest are unchanged. Both are
report-only by default here, and neither ever runs automatically.

Every test names the rule it guards, so removing that rule makes the test fail.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from equity_lake.portfolio import LedgerStore, LedgerStoreError, parse_csv_text
from equity_lake.portfolio.store import (
    PORTFOLIO_SCHEMA_VERSION,
    compact_ledger,
    restore_ledger,
    verify_ledger_file,
)

runner = CliRunner()

HEADER = (
    "account,external_source,external_id,event_type,effective_at,received_at,sequence,market,mic,symbol,"
    "currency,cash_effect,quantity,price,fees,basis,basis_status,reverses_external_id\n"
)
TABLE = "ledger_events"


def _row(seq: int, *, account: str = "main", quantity: str = "10") -> str:
    return (
        f"{account},fixture,evt-{seq:04d},OPENING,2024-01-0{seq}T15:00:00Z,2024-01-0{seq}T21:00:00Z,{seq},"
        f"us_equity,XNAS,AAA,USD,1000.00,{quantity},100.00,0,1000.00,KNOWN,"
    )


def _build_ledger(path: Path, *, rows: int = 2, account: str = "main") -> Path:
    """A real v2 ledger written through the store's own append path."""
    store = LedgerStore(path=path)
    store.append(parse_csv_text(HEADER + "".join(f"{_row(seq, account=account)}\n" for seq in range(1, rows + 1))))
    return path


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return _build_ledger(tmp_path / "portfolio.sqlite3", rows=2)


@pytest.fixture
def backup(ledger: Path, tmp_path: Path) -> Path:
    """A verified copy of the ledger, taken the way the operator takes one."""
    target = tmp_path / "ledger-backup.sqlite3"
    shutil.copy2(ledger, target)
    return target


class TestRestoreReporting:
    def test_report_only_writes_nothing(self, ledger: Path, backup: Path) -> None:
        """Decision 7: a report creates no file, backup, temp file or journal.

        Falsified by: writing anything before the ``apply`` decision.
        """
        before = sorted(p.name for p in ledger.parent.iterdir())
        report = restore_ledger(backup, ledger)
        assert report.applied is False
        assert report.row_count == 2
        assert sorted(p.name for p in ledger.parent.iterdir()) == before

    def test_the_report_names_what_the_current_file_would_lose(self, ledger: Path, backup: Path) -> None:
        """Decision 4: a restore is reported with what it rolled back.

        Falsified by: reporting only the candidate's counters, which hides that the current
        ledger had already recorded more events than the backup.
        """
        store = LedgerStore(path=ledger)
        store.append(parse_csv_text(HEADER + "".join(f"{_row(seq)}\n" for seq in (3, 4))))

        report = restore_ledger(backup, ledger)
        assert report.target_existed is True
        assert report.rolled_back_rows == 2
        assert report.rolled_back_newest_recorded_at is not None
        assert report.row_count == 2


class TestRestoreApply:
    def test_apply_replaces_atomically_backs_up_the_current_file_and_re_verifies(self, ledger: Path, backup: Path) -> None:
        """The current file is backed up before it is replaced, and the result is re-verified.

        Falsified by: replacing before the backup (the current events become unrecoverable) or
        skipping the re-verification (a corrupt copy could become the ledger).
        """
        store = LedgerStore(path=ledger)
        store.append(parse_csv_text(HEADER + "".join(f"{_row(seq)}\n" for seq in (3, 4))))
        current_rows = verify_ledger_file(ledger).row_count

        report = restore_ledger(backup, ledger, apply=True)

        assert report.applied is True
        assert report.backup_filename is not None
        saved = ledger.parent / str(report.backup_filename)
        assert saved.is_file()
        assert verify_ledger_file(saved).row_count == current_rows  # the copy holds the newer state
        restored = verify_ledger_file(ledger, expect_version=PORTFOLIO_SCHEMA_VERSION)
        assert (restored.row_count, restored.record_digest) == (report.row_count, report.record_digest)
        assert sorted(p.name for p in ledger.parent.iterdir() if p.name.endswith(".tmp")) == []

    def test_an_identical_candidate_is_a_verifying_no_op(self, ledger: Path, backup: Path) -> None:
        """Restoring the same bytes writes nothing, even with ``--apply``."""
        report = restore_ledger(backup, ledger, apply=True)
        assert report.already_identical is True
        assert report.applied is False
        assert report.backup_filename is None

    def test_a_missing_current_ledger_can_be_restored(self, ledger: Path, backup: Path) -> None:
        """A lost ledger is recoverable from a verified backup."""
        ledger.unlink()
        report = restore_ledger(backup, ledger, apply=True)
        assert report.applied is True
        assert report.target_existed is False
        assert report.backup_filename is None
        assert verify_ledger_file(ledger).row_count == 2


class TestRestoreRefusals:
    def test_a_different_account_label_refuses(self, tmp_path: Path, backup: Path) -> None:
        """One private book holds one account (ADR-0024 decision 1); no documented change exists.

        Falsified by: restoring across account labels, which silently mixes two books.
        """
        other = _build_ledger(tmp_path / "other.sqlite3", rows=2, account="brokerage")
        with pytest.raises(LedgerStoreError, match="account label"):
            restore_ledger(backup, other, apply=True)

    def test_the_candidate_itself_refuses(self, ledger: Path) -> None:
        """A restore needs a backup to restore *from*; the current file is not one."""
        with pytest.raises(LedgerStoreError, match="current ledger"):
            restore_ledger(ledger, ledger, apply=True)

    def test_an_unverified_candidate_refuses(self, ledger: Path, tmp_path: Path) -> None:
        """A corrupt candidate must never be swapped in.

        Falsified by: replacing before verifying — the ledger would be destroyed by a truncated
        backup, which is exactly what a restore is for.
        """
        corrupt = tmp_path / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a database")
        with pytest.raises(LedgerStoreError):
            restore_ledger(corrupt, ledger, apply=True)
        assert verify_ledger_file(ledger).row_count == 2  # the ledger is untouched

    def test_a_rollback_journal_refuses(self, ledger: Path, backup: Path) -> None:
        """A journal means a writer did not finish; never replace that state.

        Falsified by: dropping the journal check, which would restore over a half-written book.
        """
        journal = ledger.with_name(f"{ledger.name}-journal")
        journal.write_bytes(b"")
        try:
            with pytest.raises(LedgerStoreError, match="journal"):
                restore_ledger(backup, ledger, apply=True)
        finally:
            journal.unlink()

    def test_insufficient_free_space_refuses_before_writing(self, ledger: Path, backup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The space pre-check refuses rather than failing halfway through a copy."""
        from types import SimpleNamespace

        import equity_lake.portfolio.store as store_module

        # The candidate must differ from the current file, otherwise the restore is a no-op
        # and never reaches the space check.
        LedgerStore(path=ledger).append(parse_csv_text(HEADER + "".join(f"{_row(seq)}\n" for seq in (3, 4))))
        monkeypatch.setattr(store_module.shutil, "disk_usage", lambda _path: SimpleNamespace(total=0, used=0, free=1))
        with pytest.raises(LedgerStoreError, match="bytes free"):
            restore_ledger(backup, ledger, apply=True)
        assert not list(ledger.parent.glob("*.tmp"))
        assert not list(ledger.parent.glob("*.bak"))


class TestCompaction:
    def test_report_only_writes_nothing(self, ledger: Path) -> None:
        """Decision 7 applies to compaction too: no rebuild, no backup, no temp file."""
        before = sorted(p.name for p in ledger.parent.iterdir())
        report = compact_ledger(ledger)
        assert report.applied is False
        assert report.before_bytes == ledger.stat().st_size
        assert sorted(p.name for p in ledger.parent.iterdir()) == before

    def test_apply_preserves_the_record_and_reports_the_size(self, ledger: Path) -> None:
        """Decision 5: version, row count and record digest are unchanged by the rebuild.

        Falsified by: replacing without the post-conditions, which would accept a rebuild that
        dropped or rewrote events.
        """
        before = verify_ledger_file(ledger, expect_version=PORTFOLIO_SCHEMA_VERSION)
        report = compact_ledger(ledger, apply=True)
        after = verify_ledger_file(ledger, expect_version=PORTFOLIO_SCHEMA_VERSION)

        assert report.applied is True
        assert (after.schema_version, after.row_count, after.record_digest) == (
            before.schema_version,
            before.row_count,
            before.record_digest,
        )
        assert report.after_bytes == ledger.stat().st_size
        assert report.backup_filename is not None
        assert (ledger.parent / str(report.backup_filename)).is_file()  # the pre-compaction file is kept
        assert not list(ledger.parent.glob("*.tmp"))

    def test_the_ledger_still_replays_after_compaction(self, ledger: Path) -> None:
        """The record, not the file: a compacted ledger reads back through the store."""
        compact_ledger(ledger, apply=True)
        assert len(LedgerStore(path=ledger).load_events()) == 2

    def test_a_missing_ledger_refuses(self, tmp_path: Path) -> None:
        """Compaction never creates a ledger."""
        with pytest.raises(LedgerStoreError):
            compact_ledger(tmp_path / "absent.sqlite3", apply=True)

    def test_insufficient_free_space_refuses_before_rebuilding(self, ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The rebuild needs room for a second copy plus its backup, checked first."""
        from types import SimpleNamespace

        import equity_lake.portfolio.store as store_module

        monkeypatch.setattr(store_module.shutil, "disk_usage", lambda _path: SimpleNamespace(total=0, used=0, free=1))
        with pytest.raises(LedgerStoreError, match="bytes free"):
            compact_ledger(ledger, apply=True)
        assert not list(ledger.parent.glob("*.tmp"))


class TestCliSurface:
    def test_restore_help_documents_both_modes(self) -> None:
        result = runner.invoke(__import__("equity_lake.cli.__main__", fromlist=["app"]).app, ["portfolio", "restore", "--help"])
        assert result.exit_code == 0
        assert "--from" in result.stdout
        assert "--apply" in result.stdout

    def test_compact_help_documents_both_modes(self) -> None:
        result = runner.invoke(__import__("equity_lake.cli.__main__", fromlist=["app"]).app, ["portfolio", "compact", "--help"])
        assert result.exit_code == 0
        assert "--apply" in result.stdout

    def test_restore_without_a_ledger_exits_non_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refusal is a non-zero exit with the reason, never a traceback."""
        import equity_lake.portfolio.store as store_module
        from equity_lake.cli.__main__ import app

        monkeypatch.setattr(store_module, "PORTFOLIO_DB_PATH", tmp_path / "portfolio" / "portfolio.sqlite3")
        candidate = tmp_path / "nothing.sqlite3"
        result = runner.invoke(app, ["portfolio", "restore", "--from", str(candidate)])
        assert result.exit_code == 1
        assert "does not exist" in result.stdout + str(result.stderr)
