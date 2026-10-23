"""Make targets should be offline-safe and invoke maintained examples."""

import subprocess
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _make_dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
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
