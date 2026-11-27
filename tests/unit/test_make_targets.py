"""Make targets should be offline-safe and invoke maintained examples."""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The documented artifact set, repo-relative: exactly what `make reset-lake` may
# remove. Kept independent of `core.paths` (pytest isolates those to /tmp) so the
# target contract is verified against the documented path list itself.
_RESET_TARGETS = (
    _REPOSITORY_ROOT / "data/lake/01_bronze",
    _REPOSITORY_ROOT / "data/lake/02_silver",
    _REPOSITORY_ROOT / "data/lake/03_gold",
    _REPOSITORY_ROOT / "data/lake/04_platinum/predictions",
    _REPOSITORY_ROOT / "data/models",
)


def _make_dry_run(target: str, *variables: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target, *variables],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def test_default_test_target_explicitly_excludes_network_and_integration() -> None:
    output = _make_dry_run("test")
    assert '-m "not network and not integration"' in output
    assert "dotenvx run" not in output


def test_backtest_shortcuts_use_existing_test_files() -> None:
    quick_test = _make_dry_run("quick-test")
    full_test = _make_dry_run("test-backtest")

    assert "tests/unit/test_backtest_generator.py" in quick_test
    assert "examples/quick_test.py" not in quick_test
    assert "examples/backtest_demo.py" not in full_test
    for test_file in (
        "test_backtest_adjustment.py",
        "test_backtest_generator.py",
        "test_backtest_metrics.py",
        "test_backtesting_regressions.py",
    ):
        assert test_file in full_test
        assert (_REPOSITORY_ROOT / "tests" / "unit" / test_file).is_file()


def test_demo_target_seeds_only_without_setup_or_provider_steps() -> None:
    output = _make_dry_run("demo")

    assert "uv run equity demo seed" in output
    assert "make setup" not in output
    assert "make dev-setup" not in output
    assert "make validate" not in output
    assert "equity ingest" not in output
    assert "dotenvx" not in output


def test_showcase_target_backtests_the_seeded_sample_lake() -> None:
    """`make showcase` must stay offline: seed the sample lake, then arena on --lake."""
    output = _make_dry_run("showcase")

    assert "uv run equity demo seed" in output
    assert "uv run equity arena run" in output
    assert "--lake data/sample" in output
    # Offline-safe: no provider keys, no ingestion, no network setup steps.
    assert "dotenvx" not in output
    assert "equity ingest" not in output
    assert "make setup" not in output


def test_preload_target_is_bounded_and_offline() -> None:
    """`make preload` drives the bounded helper, oldest-first, with no ingestion."""
    output = _make_dry_run("preload")

    assert "uv run python -m equity_lake.devtools.preload" in output
    assert "--days 60" in output  # bounded default, overridable with DAYS=
    assert "dotenvx" not in output
    assert "equity ingest" not in output
    assert "make setup" not in output


def test_reset_lake_target_maps_dry_and_invokes_the_maintained_helper() -> None:
    """`make reset-lake` calls the devtool; `DRY=1` adds `--dry-run` and nothing else."""
    default = _make_dry_run("reset-lake")
    dry = _make_dry_run("reset-lake", "DRY=1")

    assert "uv run python -m equity_lake.devtools.reset_lake" in default
    assert "--dry-run" not in default
    assert "uv run python -m equity_lake.devtools.reset_lake --dry-run" in dry
    assert "dotenvx" not in default
    assert "equity ingest" not in default


def test_reset_lake_dry_run_is_non_destructive_and_lists_the_documented_paths() -> None:
    """R7 acceptance: `make reset-lake DRY=1` lists exactly the artifact set, writes nothing."""
    before = {str(path): _fingerprint(path) for path in _RESET_TARGETS}

    result = subprocess.run(
        ["make", "--no-print-directory", "reset-lake", "DRY=1"],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["dry_run"] is True
    assert payload["ok"] is True
    # The state of *this* checkout's lake is whatever the documented workflows left
    # behind (reset -> real ingest -> `unknown`), so only vocabulary membership is
    # contractual here; the state assertions live in the tmp-lake test below.
    assert payload["synthetic_provenance"] in {"synthetic", "empty", "unknown"}
    assert [entry["path"] for entry in payload["targets"]] == [str(path) for path in _RESET_TARGETS]
    assert payload["removed"] == []
    assert {str(path): _fingerprint(path) for path in _RESET_TARGETS} == before


def test_reset_lake_state_assertions_run_against_a_test_owned_lake(tmp_path: Path) -> None:
    """The provenance result is checked on a lake the test owns, not the repo lake (R7)."""
    lake = tmp_path / "lake"
    models = tmp_path / "models"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "equity_lake.devtools.reset_lake",
            "--dry-run",
            "--lake-dir",
            str(lake),
            "--models-dir",
            str(models),
        ],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # An absent tmp lake has no rows anywhere under the reset targets.
    assert payload["synthetic_provenance"] == "empty"
    assert payload["dry_run"] is True
    assert payload["removed"] == []
    # Same documented artifact set, re-rooted at the tmp lake/models directories.
    assert [entry["path"] for entry in payload["targets"]] == [
        str(lake / "01_bronze"),
        str(lake / "02_silver"),
        str(lake / "03_gold"),
        str(lake / "04_platinum/predictions"),
        str(models),
    ]
    # A dry run creates nothing in the roots it was pointed at.
    assert not lake.exists()
    assert not models.exists()


def test_reset_lake_errors_out_on_an_unrecognized_dry_value(tmp_path: Path) -> None:
    """`DRY=2` / `DRY=yes` must error, never silently take the destructive form.

    `uv` is stubbed on PATH: if the guard regressed, nothing destructive can run and
    the sentinel records that the helper was reached.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    sentinel = tmp_path / "helper-invoked"
    stub = stub_dir / "uv"
    stub.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    # The reset targets live under the gitignored `data/` root, so a clean checkout
    # (CI) has none of them. Compare fingerprints instead of demanding ``exists()``:
    # the invariant is that a refused invocation changes nothing, which holds whether
    # or not a local lake has been seeded.
    before = {str(path): _fingerprint(path) for path in _RESET_TARGETS}

    for value in ("2", "yes", "true"):
        result = subprocess.run(
            ["make", "--no-print-directory", "reset-lake", f"DRY={value}"],
            cwd=_REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
            env=env,
        )
        assert result.returncode != 0, result.stdout
        assert "DRY must be 0, 1, or empty" in result.stderr
        assert f"(got '{value}')" in result.stderr
    assert not sentinel.exists()
    assert {str(path): _fingerprint(path) for path in _RESET_TARGETS} == before


def _fingerprint(path: Path) -> tuple[bool, int]:
    return (path.exists(), len(list(path.rglob("*"))) if path.is_dir() else 0)
