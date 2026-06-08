"""Import boundary tests.

Ensure core modules don't depend on CLI, dashboard, or provider-specific code.
"""

import importlib
import pkgutil
import sys

import pytest


def _walk_submodules(package_name: str) -> list[str]:
    """Return all sub-module names under a package."""
    try:
        pkg = importlib.import_module(package_name)
    except ImportError:
        return []
    mods = [package_name]
    if not hasattr(pkg, "__path__"):
        return mods
    for _importer, modname, _ispkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}."):
        mods.append(modname)
    return mods


def _can_import_without(module_name: str, forbidden: set[str]) -> tuple[bool, str]:
    """Try importing a module after removing forbidden modules from sys.modules."""
    saved = {}
    for key in list(sys.modules):
        for f in forbidden:
            if key == f or key.startswith(f + "."):
                saved[key] = sys.modules.pop(key)
    try:
        importlib.import_module(module_name)
        return True, ""
    except ImportError as exc:
        return False, str(exc)
    finally:
        sys.modules.update(saved)


CORE_MODULES = _walk_submodules("equity_lake.core")

FORBIDDEN_PREFIXES = {
    "equity_lake.cli",
    "equity_lake.dashboard",
    "equity_lake.sources",
    "equity_lake.loaders",
}


@pytest.mark.parametrize("module_name", CORE_MODULES, ids=CORE_MODULES)
def test_core_has_no_cli_or_provider_deps(module_name: str) -> None:
    ok, err = _can_import_without(module_name, FORBIDDEN_PREFIXES)
    assert ok, f"{module_name} should not depend on CLI/dashboard/sources/loaders: {err}"


def test_domain_tree_does_not_exist() -> None:
    from pathlib import Path

    import equity_lake

    pkg_path = getattr(equity_lake, "__path__", [])
    for p in pkg_path if isinstance(pkg_path, list) else [pkg_path]:
        assert not (Path(p) / "domain").exists(), "domain/ tree should have been removed"


@pytest.mark.parametrize(
    "module_name",
    [
        "equity_lake.run_pipeline",
        "equity_lake.pipeline",
        "equity_lake.feature_jobs",
        "equity_lake.ml_jobs",
        "equity_lake.cli.backtest",
    ],
)
def test_legacy_module_shims_are_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
