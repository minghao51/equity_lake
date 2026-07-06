"""Import boundary tests.

Ensure core modules don't depend on CLI, dashboard, or provider-specific code.
"""

import ast
import importlib
import pkgutil
import sys
from pathlib import Path

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
}


@pytest.mark.parametrize("module_name", CORE_MODULES, ids=CORE_MODULES)
def test_core_has_no_cli_or_provider_deps(module_name: str) -> None:
    ok, err = _can_import_without(module_name, FORBIDDEN_PREFIXES)
    assert ok, f"{module_name} should not depend on CLI/dashboard/sources: {err}"


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
        "equity_lake.feature_jobs",
        "equity_lake.ml_jobs",
        "equity_lake.cli.backtest",
    ],
)
def test_legacy_module_shims_are_absent(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


# =============================================================================
# Static (AST-based) layer boundary enforcement
#
# The runtime ``_can_import_without`` helper above cannot actually block an
# import whose target exists on disk (Python re-imports it after eviction from
# sys.modules). These tests parse each module's AST and assert that no source
# file in a given layer imports a forbidden top-level package. This is the only
# technique that deterministically catches boundary violations.
# =============================================================================


def _imported_top_packages(source: str, file_path: str) -> set[str]:
    """Return equity_lake second-level packages imported by ``source``.

    Only absolute imports (``level == 0``) of ``equity_lake.*`` are considered;
    relative imports (``level > 0``) are intra-package and ignored.
    """
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return set()
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) >= 2 and parts[0] == "equity_lake":
                    packages.add(parts[1])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "equity_lake":
                packages.add(parts[1])
    return packages


# Layer -> forbidden top-level packages (upward / cross-cutting edges).
LAYER_BOUNDARIES: dict[str, set[str]] = {
    "core": {"cli", "dashboard", "sources"},
    "storage": {"cli", "dashboard"},
    "features": {"cli", "dashboard"},
    "ingestion": {"cli", "dashboard"},
}


@pytest.mark.parametrize("layer,forbidden", sorted(LAYER_BOUNDARIES.items()))
def test_layer_does_not_import_forbidden_packages(layer: str, forbidden: set[str]) -> None:
    import equity_lake

    pkg_root = Path(equity_lake.__file__).parent / layer
    if not pkg_root.is_dir():
        pytest.skip(f"no {layer}/ package directory")

    violations: list[str] = []
    for py_file in sorted(pkg_root.rglob("*.py")):
        imported = _imported_top_packages(py_file.read_text(), str(py_file))
        bad = imported & forbidden
        if bad:
            rel = py_file.relative_to(pkg_root.parent)
            violations.append(f"{rel}: imports {sorted(bad)}")

    assert not violations, f"{layer}/ must not import forbidden packages:\n" + "\n".join(violations)
