"""Pin the offline-default and explicit network-test selection contract."""

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest


class _FakeItem:
    def __init__(self, nodeid: str, markers: set[str] | None = None) -> None:
        self.nodeid = nodeid
        self.markers = markers or set()

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.markers.add(marker.name)

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self.markers else None


class _FakeHooks:
    def __init__(self) -> None:
        self.deselected: list[_FakeItem] = []

    def pytest_deselected(self, *, items: list[_FakeItem]) -> None:
        self.deselected.extend(items)


class _FakeConfig:
    def __init__(self, run_network: bool) -> None:
        self.run_network = run_network
        self.hook = _FakeHooks()

    def getoption(self, name: str) -> bool:
        assert name == "--run-network"
        return self.run_network


def _collection_hook(pytestconfig: pytest.Config) -> Callable[..., None]:
    conftest_path = Path(__file__).parents[1] / "conftest.py"
    for plugin in pytestconfig.pluginmanager.get_plugins():
        plugin_path = getattr(plugin, "__file__", None)
        if plugin_path is not None and Path(plugin_path).resolve() == conftest_path.resolve():
            return cast(Callable[..., None], plugin.pytest_collection_modifyitems)
    raise AssertionError(f"Could not find the active test policy in {conftest_path}")


def test_default_pytest_selection_excludes_network_and_integration(pytestconfig: pytest.Config) -> None:
    """The configured default is the offline fast selection, with opt-in off."""
    addopts = pytestconfig.getini("addopts")
    marker_flag = addopts.index("-m")

    assert addopts[marker_flag + 1] == "not network and not integration"
    assert not pytestconfig.getoption("--run-network")


def test_network_collection_is_deselected_without_opt_in(pytestconfig: pytest.Config) -> None:
    """A user marker override cannot bypass the collection-level network guard."""
    network_item = _FakeItem("tests/unit/test_provider.py::test_provider", {"network"})
    offline_item = _FakeItem("tests/unit/test_offline.py::test_synthetic")
    items = [offline_item, network_item]
    config = _FakeConfig(run_network=False)

    _collection_hook(pytestconfig)(config, items)

    assert items == [offline_item]
    assert config.hook.deselected == [network_item]


def test_network_collection_is_allowed_with_explicit_opt_in(pytestconfig: pytest.Config) -> None:
    """The collection guard releases network-marked tests only with the opt-in."""
    network_item = _FakeItem("tests/unit/test_provider.py::test_provider", {"network"})
    items = [network_item]
    config = _FakeConfig(run_network=True)

    _collection_hook(pytestconfig)(config, items)

    assert items == [network_item]
    assert config.hook.deselected == []


@pytest.mark.network
def test_network_marker_override_probe() -> None:
    """A harmless probe used to verify the real pytest collection guard."""


def _run_nested_pytest(*arguments: str, extra_pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run pytest with no provider credentials, plugin autoload, bytecode, or cache."""
    root = Path(__file__).parents[2]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    if extra_pythonpath is not None:
        environment["PYTHONPATH"] = os.pathsep.join((str(extra_pythonpath), str(root / "src"), str(root)))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_real_pytest_marker_override_deselects_network() -> None:
    """The real project conftest protects a unit-marker override in a subprocess."""
    test_file = Path(__file__).resolve().relative_to(Path(__file__).parents[2]).as_posix()
    result = _run_nested_pytest(
        "--collect-only",
        "-vv",
        "-m",
        "unit",
        test_file,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "test_default_pytest_selection_excludes_network_and_integration" in output
    assert "test_network_marker_override_probe" not in output
    assert "deselected" in output


def test_runtime_path_isolation_probe() -> None:
    """Verify default Delta, model, and market paths point outside repository data."""
    from equity_lake.core import paths  # type: ignore[import-untyped]
    from equity_lake.ingestion import types as ingestion_types  # type: ignore[import-untyped]
    from equity_lake.ml import forecasting  # type: ignore[import-untyped]
    from equity_lake.storage import delta as delta_storage  # type: ignore[import-untyped]

    repository_data = paths.PROJECT_ROOT / "data"
    repository_logs = paths.PROJECT_ROOT / "logs"
    assert paths.CONFIG_DIR == paths.PROJECT_ROOT / "config"
    assert repository_data != paths.DATA_DIR
    assert repository_data / "models" != paths.MODELS_DIR
    assert repository_logs != paths.LOGS_DIR

    for name, value in vars(paths).items():
        if name not in {"PROJECT_ROOT", "CONFIG_DIR"} and isinstance(value, Path):
            assert not value.is_relative_to(repository_data), f"{name} still points into checkout data: {value}"
            assert not value.is_relative_to(repository_logs), f"{name} still points into checkout logs: {value}"

    assert delta_storage.LAKE_DIR == paths.LAKE_DIR
    assert delta_storage.delta_table_path("04_platinum/predictions") == paths.PLATINUM_PREDICTIONS_DIR
    assert forecasting.DATA_DIR == paths.DATA_DIR
    assert forecasting.DEFAULT_MODEL_DIR == paths.MODELS_DIR
    for market, metadata in paths.PRICE_MARKETS.items():
        assert paths.market_dir(market) == getattr(paths, metadata.dir_attr)
        assert ingestion_types.MARKET_DIR_MAP[market] == paths.market_dir(market).relative_to(paths.LAKE_DIR).as_posix()


def test_subprocess_starts_with_isolated_runtime_paths() -> None:
    """A fresh pytest process isolates imported aliases before test collection."""
    root = Path(__file__).parents[2]
    test_file = Path(__file__).resolve().relative_to(root).as_posix()
    result = _run_nested_pytest(
        "-m",
        "unit",
        f"{test_file}::test_runtime_path_isolation_probe",
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "1 passed" in output


def test_subprocess_rebinds_preloaded_runtime_aliases() -> None:
    """Aliases imported by an early pytest plugin are redirected before collection."""
    root = Path(__file__).parents[2]
    test_file = Path(__file__).resolve().relative_to(root).as_posix()
    plugin_source = """\
from equity_lake.core.paths import CONFIG_DIR, DATA_DIR, LAKE_DIR, LOGS_DIR, MODELS_DIR, PROJECT_ROOT
from equity_lake.ml.forecasting import DEFAULT_MODEL_DIR, DATA_DIR as FORECASTING_DATA_DIR
from equity_lake.storage.delta import LAKE_DIR as MERGE_LAKE_DIR


def pytest_collection_finish(session):
    from equity_lake.core import paths

    assert DATA_DIR == paths.DATA_DIR
    assert MODELS_DIR == paths.MODELS_DIR
    assert LOGS_DIR == paths.LOGS_DIR
    assert LAKE_DIR == paths.LAKE_DIR
    assert MERGE_LAKE_DIR == paths.LAKE_DIR
    assert FORECASTING_DATA_DIR == paths.DATA_DIR
    assert DEFAULT_MODEL_DIR == paths.MODELS_DIR
    assert PROJECT_ROOT == paths.PROJECT_ROOT
    assert CONFIG_DIR == paths.CONFIG_DIR
"""
    with tempfile.TemporaryDirectory(prefix="equity-lake-pytest-plugin-", dir="/tmp") as temporary_dir:
        plugin_dir = Path(temporary_dir)
        (plugin_dir / "test_preloaded_paths.py").write_text(plugin_source, encoding="utf-8")
        result = _run_nested_pytest(
            "-p",
            "test_preloaded_paths",
            "--collect-only",
            "-m",
            "unit",
            test_file,
            extra_pythonpath=plugin_dir,
        )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "test_default_pytest_selection_excludes_network_and_integration" in output
    assert "test_network_marker_override_probe" not in output
    assert "deselected" in output


def test_ci_fast_gate_and_default_make_target_remain_offline() -> None:
    """CI and the default Make test target do not enable network access."""
    root = Path(__file__).parents[2]
    workflow = (root / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    default_test = makefile.split("test:\n", maxsplit=1)[1].split("\ntest-unit:", maxsplit=1)[0]
    network_test = makefile.split("test-network:\n", maxsplit=1)[1].split("\n# Code Quality", maxsplit=1)[0]

    assert 'pytest -q -m "not network and not integration"' in workflow
    assert "ruff format --check ." in workflow
    assert "uv run pytest" in default_test
    assert "dotenvx" not in default_test
    assert "--run-network" not in default_test
    assert 'uv run pytest tests/ -v --run-network -m "network or integration"' in network_test
    assert "--run-network" in network_test
    assert '"network or integration"' in network_test
