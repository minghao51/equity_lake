"""OCC retry and independent-writer evidence for the local Delta merge seam."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from deltalake.exceptions import CommitFailedError, SchemaMismatchError

from equity_lake.storage import delta as delta_storage  # type: ignore[import-untyped]
from equity_lake.storage.delta import DeltaMergeError, merge_delta, read_delta, write_delta  # type: ignore[import-untyped]

_TRADING_DATE = date(2026, 10, 17)


def _seed_table(tmp_path: Path) -> None:
    write_delta(
        pl.DataFrame({"ticker": ["SEED"], "date": [_TRADING_DATE], "close": [1.0]}),
        "prices",
        mode="overwrite",
        lake_dir=tmp_path,
    )


def test_commit_conflict_retries_with_refreshed_table_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_table(tmp_path)
    original_execute = delta_storage._execute_merge
    attempted_tables: list[Any] = []

    def conflict_once(dt: Any, frame: pl.DataFrame, predicate: str) -> None:
        attempted_tables.append(dt)
        if len(attempted_tables) == 1:
            raise CommitFailedError("synthetic optimistic concurrency conflict")
        original_execute(dt, frame, predicate)

    monkeypatch.setattr(delta_storage, "_execute_merge", conflict_once)
    batch = pl.DataFrame({"ticker": ["AAPL"], "date": [_TRADING_DATE], "close": [10.0]})

    assert merge_delta(batch, "prices", key_columns=["ticker", "date"], lake_dir=tmp_path)

    assert len(attempted_tables) == 2
    assert attempted_tables[0] is not attempted_tables[1]
    assert attempted_tables[0] is not attempted_tables[1]  # a fresh snapshot was used for the retry
    assert read_delta("prices", lake_dir=tmp_path).height == 2


def test_only_typed_commit_conflicts_retry_and_exhaustion_is_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_table(tmp_path)
    attempted: list[Any] = []

    def always_conflict(dt: Any, _frame: pl.DataFrame, _predicate: str) -> None:
        attempted.append(dt)
        raise CommitFailedError("synthetic OCC exhaustion")

    monkeypatch.setattr(delta_storage, "_execute_merge", always_conflict)
    batch = pl.DataFrame({"ticker": ["AAPL"], "date": [_TRADING_DATE], "close": [10.0]})

    with pytest.raises(DeltaMergeError) as exc_info:
        merge_delta(batch, "prices", key_columns=["ticker", "date"], lake_dir=tmp_path)

    assert isinstance(exc_info.value.__cause__, CommitFailedError)
    assert len(attempted) == 3
    assert len({id(dt) for dt in attempted}) == 3  # initial plus two freshly loaded retry snapshots
    assert read_delta("prices", lake_dir=tmp_path).height == 1


def test_schema_failure_is_not_retried_as_an_occ_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_table(tmp_path)
    attempts = 0

    def reject_schema(_dt: Any, _frame: pl.DataFrame, _predicate: str) -> None:
        nonlocal attempts
        attempts += 1
        raise SchemaMismatchError("synthetic incompatible schema")

    monkeypatch.setattr(delta_storage, "_execute_merge", reject_schema)
    batch = pl.DataFrame({"ticker": ["AAPL"], "date": [_TRADING_DATE], "close": [10.0]})

    with pytest.raises(DeltaMergeError):
        merge_delta(batch, "prices", key_columns=["ticker", "date"], lake_dir=tmp_path)

    assert attempts == 1
    assert read_delta("prices", lake_dir=tmp_path).height == 1


def test_independent_subprocess_writers_preserve_both_commits(tmp_path: Path) -> None:
    """Two processes merge from a deliberately shared stale snapshot."""
    _seed_table(tmp_path)
    release = tmp_path / "release-writers"
    writer_script = r"""\
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl
import equity_lake.storage.delta as storage

root = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
ticker = sys.argv[4]
original = storage._execute_merge
entered = False

def wait_after_snapshot(dt, frame, predicate):
    global entered
    if not entered:
        entered = True
        ready.touch()
        deadline = time.monotonic() + 20
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("writer barrier was not released")
            time.sleep(0.01)
    return original(dt, frame, predicate)

storage._execute_merge = wait_after_snapshot
storage.merge_delta(
    pl.DataFrame({"ticker": [ticker], "date": [date(2026, 10, 17)], "close": [10.0]}),
    "prices",
    key_columns=["ticker", "date"],
    lake_dir=root,
)
"""
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    for ticker in ("AAPL", "MSFT"):
        ready = tmp_path / f"ready-{ticker}"
        process = subprocess.Popen(
            [sys.executable, "-c", writer_script, str(tmp_path), str(ready), str(release), ticker],
            cwd=Path.cwd(),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append((ticker, process))

    deadline = time.monotonic() + 25
    while not all((tmp_path / f"ready-{ticker}").exists() for ticker in ("AAPL", "MSFT")):
        failed = [(ticker, process.poll()) for ticker, process in processes if process.poll() is not None]
        if failed:
            release.touch()
            details = [(ticker, process.communicate()) for ticker, process in processes]
            pytest.fail(f"subprocess writer exited before the barrier: {failed}; output={details}")
        if time.monotonic() >= deadline:
            release.touch()
            details = [(ticker, process.communicate(timeout=10)) for ticker, process in processes]
            pytest.fail(f"subprocess writers did not reach the barrier: {details}")
        time.sleep(0.01)

    release.touch()
    for ticker, process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"writer {ticker} failed: stdout={stdout}\nstderr={stderr}"

    result = read_delta("prices", lake_dir=tmp_path)
    assert result.height == 3
    assert result["ticker"].to_list().count("AAPL") == 1
    assert result["ticker"].to_list().count("MSFT") == 1


def test_concurrent_first_writers_keyed_merge_without_duplicate_creation(tmp_path: Path) -> None:
    """Concurrent absent-table checks must not become blind duplicate appends."""
    release = tmp_path / "release-creators"
    creator_script = r"""\
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl
import equity_lake.storage.delta as storage

root = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
article_id = sys.argv[4]
original_open = storage._open_existing_delta_table
entered = False

def wait_after_absence(path):
    global entered
    table = original_open(path)
    if path == root / "fresh" and table is None and not entered:
        entered = True
        ready.touch()
        deadline = time.monotonic() + 20
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("creator barrier was not released")
            time.sleep(0.01)
    return table

storage._open_existing_delta_table = wait_after_absence
storage.merge_delta(
    pl.DataFrame({"article_id": [article_id], "date": [date(2026, 10, 17)], "value": [1]}),
    "fresh",
    key_columns=["article_id"],
    lake_dir=root,
)
"""
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    for article_id in ("article-A", "article-B"):
        ready = tmp_path / f"creator-ready-{article_id}"
        process = subprocess.Popen(
            [sys.executable, "-c", creator_script, str(tmp_path), str(ready), str(release), article_id],
            cwd=Path.cwd(),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append((article_id, process))

    deadline = time.monotonic() + 25
    while not all((tmp_path / f"creator-ready-{article_id}").exists() for article_id, _ in processes):
        failed = [(article_id, process.poll()) for article_id, process in processes if process.poll() is not None]
        if failed:
            release.touch()
            details = [(article_id, process.communicate()) for article_id, process in processes]
            pytest.fail(f"creator exited before the barrier: {failed}; output={details}")
        if time.monotonic() >= deadline:
            release.touch()
            details = [(article_id, process.communicate(timeout=10)) for article_id, process in processes]
            pytest.fail(f"creators did not reach the barrier: {details}")
        time.sleep(0.01)

    release.touch()
    for article_id, process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"creator {article_id} failed: stdout={stdout}\\nstderr={stderr}"

    result = read_delta("fresh", lake_dir=tmp_path)
    assert result.height == 2
    assert set(result["article_id"]) == {"article-A", "article-B"}


def test_non_conflict_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_table(tmp_path)
    attempts = 0

    def fail_io(_dt: Any, _frame: pl.DataFrame, _predicate: str) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("synthetic I/O failure")

    monkeypatch.setattr(delta_storage, "_execute_merge", fail_io)
    batch = pl.DataFrame({"ticker": ["AAPL"], "date": [_TRADING_DATE], "close": [10.0]})

    with pytest.raises(DeltaMergeError):
        merge_delta(batch, "prices", key_columns=["ticker", "date"], lake_dir=tmp_path)

    assert attempts == 1
