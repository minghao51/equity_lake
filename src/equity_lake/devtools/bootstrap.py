"""Disposable, reproducible synthetic bootstrap (RS-E01).

One guarded routine that materialises a throwaway checkout of the **tracked** source
(``git archive``), seeds that checkout's canonical ``data/lake/`` with the synthetic
demo generator, and proves it never touched the operator's checkout or lake.

Why a checkout at all: H00 and CI need a canonical ``data/lake/`` whose rows carry the
``synthetic`` provenance envelope, and ``data/*`` is git-ignored — a fresh clone has no
lake. Seeding the operator's checkout would leave synthetic rows in the tables real
ingestion writes, so the bootstrap always seeds a copy instead.

The guards (all preflight, before anything is written):

* the allocation is a *fresh* OS temporary directory that resolves to itself (a symlinked
  alias is refused) and is empty when the caller supplies it;
* the extracted checkout and the destination lake both resolve **beneath** that
  allocation, with no symlink component on the way;
* the destination is never the operator lake from :mod:`equity_lake.core.paths`
  (identity or containment in either direction) — an arbitrary operator lake is not an
  accepted destination, and the legacy explicit-overwrite authorization never widens
  this;
* the target lake must be empty (absent or an empty directory) before seeding;
* the checkout contains tracked source only: no ``.env``, no runtime ``data/`` beyond
  the tracked catalog, and no symlink members;
* module/import provenance is proven from the child process itself: it reports the
  ``equity_lake`` module file, ``PROJECT_ROOT`` and ``LAKE_DIR`` it actually imported,
  and the parent refuses unless all three are the disposable checkout.

A ``CI=true`` environment flag or a marker file is deliberately not evidence anywhere
in this module. Stock/optional datasets the bootstrap does not seed are reported
``available: false`` with a reason — never stubbed with generated rows.

Command line (profile b of the fresh-clone profiles in the getting-started page)::

    uv run python -m equity_lake.devtools.bootstrap --end-date 2026-11-20

Only the tracked source is touched; nothing is written outside the reported allocation.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from equity_lake.core import paths

#: Environment names never handed to the disposable child process.
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")

#: The only tracked files allowed under ``data/`` in the extracted checkout. Ignored
#: runtime data (``data/lake/``, ``data/lake_real/``, research artifacts) never comes
#: from the git archive, and this list makes that a checked claim rather than a comment.
_TRACKED_DATA_ALLOWLIST = frozenset({"data/.gitkeep", "data/catalog.jsonl"})

#: Report sentinel the child prints on stdout (structlog lines are ignored).
_REPORT_PREFIX = "BOOTSTRAP_REPORT "

#: Dataset routes the bootstrap deliberately does not seed; reported unavailable.
_ABSENT_ROUTES: tuple[tuple[str, str], ...] = (
    ("03_gold/features", "no feature job ran in the disposable checkout"),
    ("04_platinum/predictions", "no ML inference ran in the disposable checkout"),
)

_OPTIONAL_DATASET_REASON = (
    "provider-backed optional dataset; the synthetic bootstrap seeds prices only and never stubs optional data "
    "(an explicit, authorized provider fetch is required)"
)
_UNSEEDED_MARKET_REASON = "no synthetic market pack is seeded for this price market (the bootstrap seeds us_equity only)"


class BootstrapRefused(RuntimeError):
    """The bounded bootstrap refused a destination/provenance preflight; nothing was written."""


class BootstrapError(RuntimeError):
    """The disposable checkout could not be created or seeded; nothing was written."""


# ---------------------------------------------------------------------------
# Preflight guards
# ---------------------------------------------------------------------------
def _real_path(value: Path) -> Path:
    """Absolute, symlink-free form of *value* (works for paths that do not exist yet)."""
    return Path(os.path.realpath(os.path.abspath(value)))


def _require_fresh_allocation(allocation: Path) -> Path:
    """A caller-supplied allocation must be a fresh, empty, non-symlinked directory."""
    lexical = Path(os.path.abspath(allocation))
    if Path(os.path.realpath(lexical)) != lexical:
        raise BootstrapRefused(f"refused: the bootstrap allocation is a symlink alias, not a real directory ({allocation})")
    if not lexical.is_dir():
        raise BootstrapRefused(f"refused: the bootstrap allocation is not an existing directory ({allocation})")
    if any(lexical.iterdir()):
        raise BootstrapRefused(f"refused: the bootstrap allocation is not empty ({allocation}); a reused temporary target is not disposable")
    return lexical


def _require_disposable_destination(destination: Path, *, allocation: Path, operator_lake: Path) -> Path:
    """Prove *destination* is a fresh, empty lake beneath the allocation — never the operator's.

    ``(c)`` containment, ``(e)`` symlink aliases, ``(f)`` empty target and ``(g)`` never
    an arbitrary operator lake are decided here, before any process runs. Removing this
    call is the mutation probe for the operator-lake sentinel test.
    """
    allocation_root = _real_path(allocation)
    lexical = Path(os.path.abspath(destination))
    if Path(os.path.realpath(lexical)) != lexical:
        raise BootstrapRefused(f"refused: the lake destination is a symlink alias, not a real path ({destination})")
    resolved = lexical.resolve()
    if resolved == allocation_root or not resolved.is_relative_to(allocation_root):
        raise BootstrapRefused(f"refused: the lake destination {resolved} does not resolve beneath the allocation {allocation_root}")
    current = allocation_root
    for part in resolved.relative_to(allocation_root).parts:
        current = current / part
        if current.is_symlink():
            raise BootstrapRefused(f"refused: the lake destination path carries a symlink component ({current})")
    operator = _real_path(operator_lake)
    if resolved == operator or resolved.is_relative_to(operator) or operator.is_relative_to(resolved):
        raise BootstrapRefused(
            f"refused: the lake destination {resolved} is (or contains) the operator lake {operator}; "
            "the bootstrap never writes an operator lake, with or without the legacy overwrite authorization"
        )
    if resolved.exists():
        if not resolved.is_dir():
            raise BootstrapRefused(f"refused: the lake destination exists and is not a directory ({resolved})")
        if any(resolved.iterdir()):
            raise BootstrapRefused(f"refused: the lake destination is not empty ({resolved}); a nonempty target is never bootstrapped over")
    return resolved


# ---------------------------------------------------------------------------
# Tracked-source checkout
# ---------------------------------------------------------------------------
def _git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(repo_root), *arguments], capture_output=True, check=False)
    except OSError as exc:  # pragma: no cover - git always present in this repository
        raise BootstrapError(f"the disposable bootstrap needs git: {exc}") from exc


def _require_git_checkout(repo_root: Path) -> None:
    probe = _git(repo_root, "rev-parse", "--git-dir")
    if probe.returncode != 0:
        raise BootstrapError(f"refused: {repo_root} is not a git checkout of the tracked source")


def _archive_members(archive: bytes, revision: str, repo_root: Path) -> list[tarfile.TarInfo]:
    """Archive members, proven identical to the revision's tracked file list."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = tar.getmembers()
    except tarfile.TarError as exc:
        raise BootstrapError(f"the tracked-source archive could not be read: {exc}") from exc
    listing = _git(repo_root, "ls-tree", "-r", "--name-only", revision)
    if listing.returncode != 0:
        raise BootstrapError(f"could not list the tracked files at {revision}")
    tracked = sorted(line for line in listing.stdout.decode("utf-8").splitlines() if line)
    names = sorted(member.name for member in members if member.isfile())
    if names != tracked:
        raise BootstrapError("refused: the git archive does not match the tracked file list")
    return members


def _validate_members(members: Sequence[tarfile.TarInfo]) -> None:
    """Tracked source only: no links, no traversal, no ``.env``, no ignored runtime data."""
    for member in members:
        name = member.name
        if member.issym() or member.islnk():
            raise BootstrapRefused(f"refused: the tracked-source archive carries a link member ({name})")
        if not (member.isfile() or member.isdir()):
            raise BootstrapRefused(f"refused: the tracked-source archive carries a non-regular member ({name})")
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise BootstrapRefused(f"refused: the tracked-source archive carries an escaping path ({name})")
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            raise BootstrapRefused(f"refused: the checkout would materialise a dotenv file ({name})")
        if name.startswith("data/") and name not in _TRACKED_DATA_ALLOWLIST:
            raise BootstrapRefused(f"refused: the checkout would materialise runtime data ({name})")


def _materialise_checkout(repo_root: Path, allocation: Path) -> tuple[Path, str, int]:
    """Extract the tracked source at ``HEAD`` into ``<allocation>/checkout``."""
    revision_probe = _git(repo_root, "rev-parse", "HEAD")
    if revision_probe.returncode != 0:
        raise BootstrapError(f"could not resolve HEAD in {repo_root}")
    revision = revision_probe.stdout.decode("utf-8").strip()
    archive = _git(repo_root, "archive", "--format=tar", revision)
    if archive.returncode != 0:
        raise BootstrapError(f"git archive failed: {archive.stderr.decode('utf-8', 'replace').strip()}")
    members = _archive_members(archive.stdout, revision, repo_root)
    _validate_members(members)
    checkout = allocation / "checkout"
    checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        tar.extractall(path=checkout, members=members, filter="data")
    # The extracted tree, not the archive listing, is what gets seeded.
    if (checkout / ".env").exists():
        raise BootstrapRefused("refused: the disposable checkout holds a dotenv file")
    if (checkout / "data" / "lake").exists() or (checkout / "data" / "lake_real").exists():
        raise BootstrapRefused("refused: the disposable checkout already holds runtime data")
    aliases = sorted(path.relative_to(checkout).as_posix() for path in checkout.rglob("*") if path.is_symlink())
    if aliases:
        raise BootstrapRefused(f"refused: the disposable checkout holds symlink aliases ({aliases[:3]})")
    return checkout, revision, len(members)


# ---------------------------------------------------------------------------
# The disposable child (module/import provenance + the seed itself)
# ---------------------------------------------------------------------------
def _child_environment(checkout: Path) -> dict[str, str]:
    """Environment for the disposable child: no provider credentials, checkout first on the path."""
    environment = {name: value for name, value in os.environ.items() if not any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)}
    environment["PYTHONPATH"] = str(checkout / "src")
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


_PROBE = """
import json
import sys
from datetime import date
from pathlib import Path

from equity_lake.core import paths
from equity_lake.core.price_provenance import price_batch_digest
from equity_lake.devtools.seed_demo import seed_demo
from equity_lake.storage.delta import read_delta

job = json.loads(sys.argv[1])
allocation = Path(job["allocation"]).resolve()
destination = Path(job["lake_dir"]).resolve()
project_root = Path(paths.PROJECT_ROOT).resolve()
lake_dir = Path(paths.LAKE_DIR).resolve()
if not project_root.is_relative_to(allocation):
    raise SystemExit("refused: this python imported equity_lake outside the disposable checkout")
if not lake_dir.is_relative_to(allocation):
    raise SystemExit("refused: this python resolved core.paths.LAKE_DIR outside the allocation")
if not destination.is_relative_to(allocation):
    raise SystemExit("refused: the lake destination is outside the allocation")
if destination.exists() and any(destination.iterdir()):
    raise SystemExit("refused: the lake destination is not empty")
summary = seed_demo(
    years=job["years"],
    tickers=job["tickers"],
    seed=job["seed"],
    end_date=date.fromisoformat(job["end_date"]) if job["end_date"] else None,
    lake_dir=destination,
    overwrite_production_lake=True,
)
frame = read_delta("01_bronze/market_data/us_equity", lake_dir=destination)
report = {
    "module": paths.__file__,
    "project_root": str(project_root),
    "lake_dir": str(lake_dir),
    "summary": summary,
    "price_digest": price_batch_digest(frame),
    "provenance_digest": sorted(set(frame["price_source_sha256"].to_list())),
}
print("BOOTSTRAP_REPORT " + json.dumps(report, sort_keys=True))
"""


def _verify_checkout_provenance(
    report: Mapping[str, Any],
    *,
    allocation: Path,
    checkout: Path,
    destination: Path,
    operator_root: Path,
) -> None:
    """Refuse unless the child imported *the disposable* checkout and resolved its lake there.

    This is proposition ``(d)``: the module that generated the rows is the extracted
    tracked source, not the checkout the operator is running from.
    """
    allocation_root = _real_path(allocation)
    operator_src = _real_path(Path(operator_root) / "src")
    checkout_src = _real_path(Path(checkout) / "src")
    try:
        module = Path(str(report["module"])).resolve()
        project_root = Path(str(report["project_root"])).resolve()
        lake_dir = Path(str(report["lake_dir"])).resolve()
    except (KeyError, TypeError, ValueError) as exc:
        raise BootstrapError("the disposable child did not report its module provenance") from exc
    if module.is_relative_to(operator_src) and not module.is_relative_to(checkout_src):
        raise BootstrapError(f"refused: the child imported equity_lake from the operator checkout ({module})")
    for label, candidate in (("module", module), ("PROJECT_ROOT", project_root), ("LAKE_DIR", lake_dir)):
        if not candidate.is_relative_to(allocation_root):
            raise BootstrapError(f"refused: the child's {label} ({candidate}) is outside the disposable allocation {allocation_root}")
    if project_root != _real_path(checkout):
        raise BootstrapError(f"refused: the child's PROJECT_ROOT ({project_root}) is not the disposable checkout")
    if lake_dir != destination:
        raise BootstrapError(f"refused: the child's LAKE_DIR ({lake_dir}) is not the bootstrap destination {destination}")


def _run_child(*, checkout: Path, allocation: Path, destination: Path, job: Mapping[str, Any], timeout: float = 900.0) -> dict[str, Any]:
    payload = json.dumps({**job, "allocation": str(allocation), "lake_dir": str(destination)})
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE, payload],
            cwd=checkout,
            env=_child_environment(checkout),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - a hung child is an environment fault
        raise BootstrapError(f"the disposable child timed out after {timeout:.0f}s") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        raise BootstrapError(f"the disposable child refused or failed (exit {completed.returncode}): {' '.join(detail)}")
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_REPORT_PREFIX):
            try:
                parsed: dict[str, Any] = json.loads(line[len(_REPORT_PREFIX) :])
            except ValueError as exc:
                raise BootstrapError("the disposable child produced an unreadable bootstrap report") from exc
            return parsed
    raise BootstrapError("the disposable child produced no bootstrap report")


# ---------------------------------------------------------------------------
# Honest unavailability (never stubbed)
# ---------------------------------------------------------------------------
def unavailable_datasets(lake_root: Path) -> list[dict[str, Any]]:
    """Optional/provider-backed datasets the bootstrap did not seed, each with a reason.

    Availability is read from the seeded lake, not assumed: a route that exists is
    reported available. The bootstrap never fabricates a stub table for anything here.
    """
    entries: list[dict[str, Any]] = []
    for dataset in sorted(paths.OPTIONAL_ENRICHMENT_MARKETS):
        entries.append({"dataset": dataset, "available": False, "reason": _OPTIONAL_DATASET_REASON})
    for market in sorted(paths.PRICE_MARKETS):
        relative = f"01_bronze/market_data/{market}"
        available = (lake_root / relative).is_dir()
        entries.append(
            {
                "dataset": market,
                "available": available,
                "reason": None if available else _UNSEEDED_MARKET_REASON,
            }
        )
    for relative, reason in _ABSENT_ROUTES:
        available = (lake_root / relative).is_dir()
        entries.append({"dataset": relative, "available": available, "reason": None if available else reason})
    return entries


# ---------------------------------------------------------------------------
# The one public entry point
# ---------------------------------------------------------------------------
def bootstrap_disposable_lake(
    *,
    seed: int = 42,
    end_date: date | None = None,
    years: float = 1.0,
    tickers: Sequence[str] | None = None,
    allocation: str | Path | None = None,
    lake_dir: str | Path | None = None,
    overwrite_production_lake: bool = False,
    keep: bool = False,
) -> dict[str, Any]:
    """Materialise a disposable tracked-source checkout and seed its canonical lake.

    Args:
        seed: Deterministic synthetic seed (fixed by default).
        end_date: Last day of the synthetic window; pin it with *seed*.
        years: Bounded history window (years of calendar days).
        tickers: Explicit tickers; ``None`` resolves the `demo` config group.
        allocation: A *fresh, empty* directory to own; defaults to a new OS temp dir.
            A reused or nonempty allocation is refused.
        lake_dir: Destination lake inside the allocation; defaults to the checkout's
            canonical ``data/lake``. Anything outside the allocation — in particular an
            operator lake — is refused.
        overwrite_production_lake: Accepted for parity with the legacy seeder; it never
            authorizes writing the operator lake, only the disposable checkout's own
            canonical ``data/lake``.
        keep: Keep the allocation after seeding (CI builds the H00 artifact from it).

    Returns:
        The bootstrap report: allocation/checkout/revision, the child's module
        provenance, the seed summary, the logical price digest, and the honest
        unavailable-dataset list.

    Raises:
        BootstrapRefused: a preflight guard refused a destination/allocation; nothing ran.
        BootstrapError: the disposable checkout could not be produced or seeded.
    """
    del overwrite_production_lake  # parity only: it never widens a destination guard
    operator_root = paths.PROJECT_ROOT
    operator_lake = paths.LAKE_DIR
    _require_git_checkout(operator_root)

    created_allocation = allocation is None
    if allocation is None:
        allocation_path = Path(tempfile.mkdtemp(prefix="equity-lake-bootstrap-")).resolve()
    else:
        allocation_path = _require_fresh_allocation(Path(allocation))

    try:
        checkout = allocation_path / "checkout"
        requested_lake: Path = checkout / "data" / "lake"
        if lake_dir is not None:
            requested_lake = Path(lake_dir)
        destination = _require_disposable_destination(requested_lake, allocation=allocation_path, operator_lake=operator_lake)
        checkout, revision, member_count = _materialise_checkout(operator_root, allocation_path)
        # The extraction itself must not have created the destination behind our back.
        if destination.exists() and any(destination.iterdir()):
            raise BootstrapRefused(f"refused: the lake destination is not empty ({destination})")

        report = _run_child(
            checkout=checkout,
            allocation=allocation_path,
            destination=destination,
            job={
                "seed": seed,
                "end_date": end_date.isoformat() if end_date is not None else None,
                "years": years,
                "tickers": list(tickers) if tickers is not None else None,
            },
        )
        _verify_checkout_provenance(
            report,
            allocation=allocation_path,
            checkout=checkout,
            destination=destination,
            operator_root=operator_root,
        )
        summary = report["summary"]
        result: dict[str, Any] = {
            "allocation": str(allocation_path),
            "checkout": str(checkout),
            "revision": revision,
            "tracked_members": member_count,
            "lake_dir": str(destination),
            "module": report["module"],
            "project_root": report["project_root"],
            "seed": seed,
            "end_date": end_date.isoformat() if end_date is not None else None,
            "source": summary["source"],
            "tickers": summary["tickers"],
            "rows": summary["rows"],
            "days": summary["days"],
            "price_digest": report["price_digest"],
            "provenance_digest": report["provenance_digest"],
            "unavailable": unavailable_datasets(destination),
            "kept": keep,
        }
        return result
    finally:
        if created_allocation and not keep:
            import shutil

            shutil.rmtree(allocation_path, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for profile (b): the disposable canonical synthetic lake."""
    parser = argparse.ArgumentParser(description="Seed a disposable synthetic data/lake from tracked source only (RS-E01).")
    parser.add_argument(
        "--end-date", type=date.fromisoformat, required=True, help="Last day of the synthetic window (YYYY-MM-DD); required for reproducibility"
    )
    parser.add_argument("--seed", type=int, default=42, help="Synthetic RNG seed (default: 42)")
    parser.add_argument("--years", type=float, default=1.0, help="Bounded history window in years (default: 1.0)")
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (default: the demo config group)")
    parser.add_argument("--keep", action="store_true", help="Keep the allocation (needed to build the H00 artifact from it)")
    arguments = parser.parse_args(argv)
    tickers = [ticker.strip() for ticker in arguments.tickers.split(",") if ticker.strip()] if arguments.tickers else None
    try:
        report = bootstrap_disposable_lake(
            seed=arguments.seed,
            end_date=arguments.end_date,
            years=arguments.years,
            tickers=tickers,
            keep=arguments.keep,
        )
    except (BootstrapRefused, BootstrapError) as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = [
    "BootstrapError",
    "BootstrapRefused",
    "bootstrap_disposable_lake",
    "main",
    "unavailable_datasets",
]
