"""Second lake root selection and isolation (ADR-0030).

The canonical lake stays `data/lake/`. A real-source slice may live under a different
root inside `data/`, selected **only** explicitly: no default, no fallback, no union and
no copying between roots. These tests hold that contract at the three seams it lives on —
the path resolver, the storage write/read, and the CLI/monitor surface.

Falsifiers are named per test: each assertion is written so that removing the rule it
guards makes it fail (the resolver's containment, the `lake_dir` thread through
`upsert_dataset`, the orchestrator's coverage read, and the monitor's path rebasing).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from typer.testing import CliRunner

from equity_lake.core import paths
from equity_lake.core.paths import LAKE_DIR, REAL_LAKE_DIR, resolve_lake_root
from equity_lake.ingestion import orchestrator
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ingestion.types import SourceStatus
from equity_lake.monitoring.health import GOLD_FEATURES_DIR, PipelineMonitor, _price_market_paths
from equity_lake.storage.delta import delta_table_path, merge_delta, read_delta

TABLE = "01_bronze/market_data/us_equity"


def _frame(day: date = date(2024, 1, 4)) -> pl.DataFrame:
    """A frame complete enough for the price-schema validator the presence check reuses."""
    return pl.DataFrame(
        {
            "ticker": ["AAA"],
            "date": [day],
            "open": [124.0],
            "high": [126.0],
            "low": [123.0],
            "close": [125.0],
            "volume": [1000],
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAA"],
        }
    )


def _cn_frame(day: date = date(2024, 1, 4)) -> pl.DataFrame:
    """A cn_ashare frame — a price market with no split reconstruction, so this test
    isolates the lake-root threading rather than the ADR-0029 transform."""
    return pl.DataFrame(
        {
            "ticker": ["600000"],
            "date": [day],
            "open": [8.0],
            "high": [8.5],
            "low": [7.9],
            "close": [8.2],
            "volume": [5000],
            "market": ["cn_ashare"],
            "mic": ["XSHG"],
            "symbol": ["600000"],
        }
    )


def _contained_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A root inside a temporary DATA_DIR, so containment applies to tmp paths."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    root = tmp_path / "lake_real"
    return root


def test_resolve_lake_root_accepts_a_contained_explicit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit root under data/ resolves; ``None`` means the canonical lake."""
    root = _contained_root(tmp_path, monkeypatch)
    assert resolve_lake_root(None) is None  # no coercion, no default root
    assert resolve_lake_root(root) == root.resolve()
    assert resolve_lake_root(str(root)) == root.resolve()  # CLI passes text


def test_resolve_lake_root_rejects_paths_outside_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root outside data/ or data/ itself is refused, never silently accepted.

    Falsified by: returning the resolved path without the containment check, which
    would let ``--lake-dir /tmp/elsewhere`` write wherever the operator pointed.
    """
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    outside = tmp_path.parent / "elsewhere"
    with pytest.raises(ValueError, match="resolve under"):
        resolve_lake_root(outside)
    with pytest.raises(ValueError, match="not data/ itself"):
        resolve_lake_root(tmp_path)


def test_resolve_lake_root_rejects_a_symlink_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve()`` follows symlinks, so a link out of data/ cannot smuggle a root in."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "lake_real").symlink_to(outside)
    with pytest.raises(ValueError, match="resolve under"):
        resolve_lake_root(data / "lake_real")


def test_resolve_lake_root_rejects_an_alias_of_the_canonical_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A symlink that lands on the canonical lake is not a second root.

    Falsified by: returning the resolved path without comparing it to the canonical
    root, which lets `--lake-dir data/lake_real` write into the demo lake.
    """
    data = tmp_path / "data"
    data.mkdir()
    canonical = data / "lake"
    canonical.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "LAKE_DIR", canonical)
    alias = data / "lake_real"
    alias.symlink_to(canonical, target_is_directory=True)

    with pytest.raises(ValueError, match="alias"):
        resolve_lake_root(alias)
    assert resolve_lake_root(canonical) == canonical.resolve()  # the canonical path itself is fine


def test_resolve_lake_root_rejects_a_root_inside_the_canonical_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root nested inside the demo root is refused (ADR-0030 decision 10).

    Falsified by: rejecting only *exact* equality with the canonical lake, which accepts
    `data/lake/01_bronze` and writes `01_bronze/01_bronze/market_data/…` — a real row
    inside `data/lake/`, the one thing the ADR forbids under any circumstance.
    """
    data = tmp_path / "data"
    canonical = data / "lake"
    (canonical / "01_bronze").mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "LAKE_DIR", canonical)

    nested = (canonical / "01_bronze", canonical / "03_gold", canonical / "01_bronze" / "market_data")
    for root in nested:
        with pytest.raises(ValueError, match="inside the canonical lake"):
            resolve_lake_root(root)
    assert resolve_lake_root(canonical) == canonical.resolve()  # the canonical path itself stays valid
    assert resolve_lake_root(data / "lake_real") == (data / "lake_real").resolve()


def test_a_real_root_refuses_out_of_scope_markets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-canonical root carries the us_equity slice only (ADR-0030 decision 5).

    Falsified by: dropping the market guard, which would let a real root hold another
    market's rows while the ADR permits only the us_equity slice.
    """
    import typer

    from equity_lake.cli._app import _require_real_root_scope

    root = tmp_path / "lake_real"
    _require_real_root_scope(["us_equity"], root)  # in scope: no raise
    _require_real_root_scope(["cn_ashare"], None)  # canonical root: unchanged behaviour
    with pytest.raises(typer.Exit):
        _require_real_root_scope(["cn_ashare"], root)


def test_a_write_lands_only_under_the_explicit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same table name written to an explicit root never touches the canonical root.

    Falsified by: dropping ``lake_dir`` from ``upsert_dataset``'s ``merge_delta`` call
    (the write then falls back to ``data/lake``), or adding any automatic fallback.
    """
    canonical = tmp_path / "lake"
    real = tmp_path / "lake_real"
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)

    assert merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=real) is True

    assert (real / TABLE / "_delta_log").is_dir()
    assert not (canonical / TABLE).exists()  # no fallback, no mirror
    assert read_delta(TABLE, lake_dir=real).height == 1


def test_upsert_dataset_threads_the_root_and_never_defaults_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``upsert_dataset`` passes the root through untouched (``None`` stays ``None``).

    Falsified by: substituting ``LAKE_DIR`` for ``None`` inside the writer, which would
    hide the canonical default from callers and defeat the "explicit only" contract.
    """
    import equity_lake.storage.delta as delta_module

    seen: list[Path | None] = []

    def _fake_merge(*_args: object, **kwargs: object) -> bool:
        seen.append(kwargs.get("lake_dir"))  # type: ignore[arg-type]
        return True

    monkeypatch.setattr(delta_module, "merge_delta", _fake_merge)
    from equity_lake.ingestion.writers import upsert_dataset

    frame = pl.DataFrame(
        {
            "ticker": ["AAA"],
            "date": [date(2024, 1, 4)],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAA"],
        }
    )
    assert upsert_dataset(frame, TABLE, date(2024, 1, 4), skip_schema_validation=True, validate_quality=False, lake_dir=tmp_path / "r")
    assert upsert_dataset(frame, TABLE, date(2024, 1, 4), skip_schema_validation=True, validate_quality=False) is True
    assert seen[0] == tmp_path / "r"
    assert seen[1] is None


def test_the_orchestrator_reads_the_selected_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A presence check honours the explicit root; the canonical root stays empty.

    Falsified by: leaving ``_read_partition_coverage`` hard-wired to ``LAKE_DIR``, which
    would make ``skipped_existing`` decisions from the wrong lake.
    """
    import duckdb

    from equity_lake.storage.lake_reader import ensure_delta_extension

    canonical = tmp_path / "lake"
    real = tmp_path / "lake_real"
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)
    monkeypatch.setattr(orchestrator, "LAKE_DIR", canonical)
    merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=real)

    con = duckdb.connect(":memory:")
    try:
        ensure_delta_extension(con)
        valid, present = orchestrator._read_partition_coverage("us_equity", "01_bronze/market_data/us_equity", date(2024, 1, 4), con, real)
        canonical_valid, canonical_present = orchestrator._read_partition_coverage(
            "us_equity", "01_bronze/market_data/us_equity", date(2024, 1, 4), con, canonical
        )
    finally:
        con.close()

    assert valid is True and present == {"AAA"}
    assert canonical_valid is False and canonical_present == set()


def test_a_dry_run_writes_nothing_under_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run creates no table and no root (ADR-0030 keeps dry-run side-effect free)."""
    root = tmp_path / "lake_real"
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path / "lake")
    from equity_lake.ingestion.writers import upsert_dataset

    frame = pl.DataFrame({"ticker": ["AAA"], "date": [date(2024, 1, 4)], "close": [1.0]})
    assert upsert_dataset(frame, TABLE, date(2024, 1, 4), dry_run=True, lake_dir=root) is True
    assert not root.exists()


def test_the_serial_branch_writes_into_the_selected_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A serial run (the backfill path) must thread the root into the write.

    Falsified by: calling ``_finalize_market`` without ``lake_dir`` in the serial branch,
    which silently wrote a real-root backfill into the canonical demo root — the one place
    ADR-0030 forbids. The parallel branch always passed it; only this call did not.
    """
    canonical = tmp_path / "lake"
    real = tmp_path / "lake_real"
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)
    monkeypatch.setattr(orchestrator, "LAKE_DIR", canonical)

    with patch.object(orchestrator, "fetch_market_data", return_value=_cn_frame()):
        results = run_daily_ingestion(date(2024, 1, 4), ["cn_ashare"], dry_run=False, parallel=False, lake_dir=real)

    assert results["cn_ashare"].status is SourceStatus.WRITTEN
    assert (real / "01_bronze/market_data/cn_ashare").is_dir()
    assert not (canonical / "01_bronze/market_data/cn_ashare").exists()


def test_the_monitor_rebases_its_paths_and_keeps_the_default(tmp_path: Path) -> None:
    """Monitoring reports on one root; with no explicit root the constants are unchanged.

    Falsified by: rebasing through a module-level mutation, which would leak the
    explicit root into every later monitor (and into the demo lake's own report).
    """
    root = tmp_path / "lake_real"
    assert _price_market_paths(root)["us_equity"] == root / "01_bronze/market_data/us_equity"
    assert _price_market_paths()["us_equity"] == paths.market_dir("us_equity")

    monitor = PipelineMonitor(lake_dir=root)
    try:
        assert monitor._lake_path(GOLD_FEATURES_DIR) == root / "03_gold/features"
    finally:
        monitor.close()
    default_monitor = PipelineMonitor()
    try:
        assert default_monitor._lake_path(GOLD_FEATURES_DIR) == GOLD_FEATURES_DIR
        assert default_monitor.lake_dir is None
    finally:
        default_monitor.close()


def test_the_ingest_commands_expose_lake_dir_and_reject_a_foreign_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--lake-dir`` exists on the write and monitor commands and fails closed.

    Falsified by: accepting the flag without validating it, or omitting it from a
    command that can write to the lake.
    """
    from equity_lake.cli.__main__ import app

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    runner = CliRunner()
    outside = tmp_path.parent / "elsewhere"

    for command in ("ingest", "backfill", "corporate-actions", "monitor"):
        help_text = runner.invoke(app, [command, "--help"], catch_exceptions=False).stdout
        assert "--lake-dir" in help_text, command

    # An invalid date keeps the command offline even if the root rule is removed, so the
    # falsifying mutation cannot trigger a real provider fetch.
    failed = runner.invoke(app, ["ingest", "--date", "not-a-date", "--lake-dir", str(outside)])
    assert failed.exit_code == 1
    assert "--lake-dir rejected" in failed.stdout


def test_the_real_root_is_a_documented_sibling_never_the_default() -> None:
    """The constant names the sibling root; nothing defaults to it."""
    assert LAKE_DIR.parent / "lake_real" == REAL_LAKE_DIR
    assert REAL_LAKE_DIR.parent == LAKE_DIR.parent
    assert REAL_LAKE_DIR != LAKE_DIR


def test_the_skip_decision_uses_the_selected_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``skipped_existing`` decisions read the selected root, not the canonical one.

    Falsified by: dropping ``lake_dir`` from ``_filter_markets_with_gaps``' call to
    ``_read_partition_coverage``, which would judge a real-root run against the demo lake
    and skip fetching rows the real root does not have. The sibling test calls the callee
    directly, so it cannot catch that wiring.
    """
    canonical = tmp_path / "lake"
    real = tmp_path / "lake_real"
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)
    monkeypatch.setattr(orchestrator, "LAKE_DIR", canonical)
    merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=real)

    scope: dict[str, list[str] | None] = {"us_equity": ["AAA"]}
    real_fetch, real_present = orchestrator._filter_markets_with_gaps(["us_equity"], date(2024, 1, 4), scope, None, real)
    default_fetch, default_present = orchestrator._filter_markets_with_gaps(["us_equity"], date(2024, 1, 4), scope, None, canonical)

    assert real_present == {"us_equity"} and real_fetch == {}
    assert default_present == set() and default_fetch == {"us_equity": ["AAA"]}


def test_the_scope_guard_runs_on_every_write_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scope guard is wired into every command that can write to a root.

    Falsified by: removing any one ``_require_real_root_scope(...)`` call, which lets that
    command write a non-``us_equity`` slice into the real root. Every case fails before any
    provider call — an invalid ``--date`` for ``ingest``, ``--dry-run``/``--days-back`` for
    the others — so the falsifying mutation stays offline.
    """
    from equity_lake.cli.__main__ import app

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    root = tmp_path / "lake_real"
    runner = CliRunner()
    cases = {
        "ingest": ["ingest", "--date", "not-a-date", "--lake-dir", str(root), "--markets", "cn_ashare"],
        "backfill": ["backfill", "--days-back", "1", "--lake-dir", str(root), "--markets", "cn_ashare"],
        "corporate-actions": ["corporate-actions", "--market", "cn_ashare", "--dry-run", "--lake-dir", str(root)],
    }
    for command, argv in cases.items():
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, (command, result.stdout)
        assert "us_equity slice only" in result.stdout, (command, result.stdout)


def test_the_scope_guard_refuses_enrichment_identifiers_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--markets`` also accepts enrichment datasets; a real root refuses them, never crashes.

    Falsified by: resolving every entry through ``canonical_market``, which raises an
    unhandled ``ValueError`` for ``macro``/``us_news``. ``ingest`` defaults to the configured
    market list, which contains them, so that crash breaks the documented landing command
    and replaces the refusal message with a traceback.
    """
    from equity_lake.cli.__main__ import app

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    root = tmp_path / "lake_real"
    runner = CliRunner()
    cases = (
        ["ingest", "--date", "not-a-date", "--lake-dir", str(root)],  # configured defaults include enrichments
        ["ingest", "--date", "not-a-date", "--lake-dir", str(root), "--markets", "us_equity,us_news"],
        ["backfill", "--days-back", "1", "--lake-dir", str(root), "--markets", "us_equity,macro"],
        ["corporate-actions", "--market", "macro", "--dry-run", "--lake-dir", str(root)],
    )
    for argv in cases:
        result = runner.invoke(app, argv)
        assert isinstance(result.exception, SystemExit), (argv, result.exception)
        assert result.exit_code == 1, (argv, result.stdout)
        assert "us_equity slice only" in result.stdout, (argv, result.stdout)
        assert "Unknown price market" not in result.stdout, argv


# ---------------------------------------------------------------------------
# Canary symlinks and containment at the resolution seam (RS-D01)
# ---------------------------------------------------------------------------


def _canary_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A temp ``data/`` holding the canonical and real roots, patched into ``core.paths``."""
    data = tmp_path / "data"
    lake = data / "lake"
    real = data / "lake_real"
    lake.mkdir(parents=True)
    real.mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "LAKE_DIR", lake)
    monkeypatch.setattr(paths, "REAL_LAKE_DIR", real)
    return lake, real


def test_a_canary_symlink_inside_the_real_root_refuses_demo_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A link from the real root into the demo lake is refused when a reader resolves it.

    Falsified by: dropping ``refuse_cross_root_symlink`` from ``delta_table_path``, which
    makes the canary silently serve demo rows as if they were real-root rows.
    """
    lake, real = _canary_roots(tmp_path, monkeypatch)
    merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=lake)
    (real / "01_bronze").symlink_to(lake / "01_bronze", target_is_directory=True)

    with pytest.raises(ValueError, match="never mixed through a symlink"):
        delta_table_path(TABLE, lake_dir=real)
    with pytest.raises(ValueError, match="never mixed through a symlink"):
        read_delta(TABLE, lake_dir=real)


def test_a_canary_symlink_inside_the_demo_root_refuses_real_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse direction is refused too: the demo root never serves real rows."""
    lake, real = _canary_roots(tmp_path, monkeypatch)
    merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=real)
    (lake / "01_bronze").symlink_to(real / "01_bronze", target_is_directory=True)

    with pytest.raises(ValueError, match="never mixed through a symlink"):
        delta_table_path(TABLE, lake_dir=lake)


def test_the_guard_applies_to_the_canonical_default_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``lake_dir=None`` (the canonical default) is guarded, not exempt."""
    lake, real = _canary_roots(tmp_path, monkeypatch)
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", lake)
    (lake / "01_bronze").symlink_to(real / "01_bronze", target_is_directory=True)

    with pytest.raises(ValueError, match="never mixed through a symlink"):
        delta_table_path(TABLE)


def test_a_canary_symlink_escaping_its_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root-internal link that leaves the root entirely is not a contained route."""
    _lake, real = _canary_roots(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (real / "03_gold").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="resolves outside its root"):
        delta_table_path("03_gold/features", lake_dir=real)


def test_a_legitimate_explicit_root_still_writes_and_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard never blocks honest use: an explicit root keeps working, unredirected."""
    lake, real = _canary_roots(tmp_path, monkeypatch)

    assert delta_table_path(TABLE, lake_dir=real) == real / TABLE
    assert merge_delta(_frame(), TABLE, key_columns=["ticker", "date"], lake_dir=real) is True
    assert (real / TABLE / "_delta_log").is_dir()
    assert read_delta(TABLE, lake_dir=real).height == 1
    assert not (lake / TABLE).exists()


def test_the_guard_is_inert_for_a_lake_outside_the_named_roots(tmp_path: Path) -> None:
    """A test/scratch root is joined exactly as before: the guard adds no side effects."""
    scratch = tmp_path / "scratch_lake"

    assert delta_table_path(TABLE, lake_dir=scratch) == scratch / TABLE
    assert not scratch.exists()  # resolution creates nothing
