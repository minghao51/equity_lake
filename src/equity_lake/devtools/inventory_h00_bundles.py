"""Inventory the retained H00 rollback bundles without touching any of them (stage H3).

Module CLI entrypoint (``python -m``, invoked by the operator and by the unit tests): plain
``print``/``stderr`` output instead of structlog, by devtool convention.

The question this answers, and nothing more: *which retained ``h00-release-bundle`` copies are
verified recovery points, which release is live, which releases are protected, and which
artifact ids would be a deletion candidate if the owner ever approved one?* It is offline and
read-only: no network, no ``gh``/API call, no deletion API, no write into any input directory.
The single report write is ``--out`` (an auxiliary artifact, ADR-0006: not cataloged, not
pointblank-validated); without it the run writes no report at all.

**Nothing here deletes anything.** It only names artifact ids. Deletion is a separate owner
approval under proposed ADR-0038, taken in a window with no concurrent publish or rollback.

Rules, all failing closed with a named reason (a refused copy is never a silent skip):

* **Scope.** Only artifacts named exactly :data:`BUNDLE_ARTIFACT_NAME` belonging to
  ``h00-publish.yml`` runs. Every other artifact (the ``github-pages`` Pages artifact, another
  workflow) is counted as out of scope: never a candidate, never protection.
* **Per-copy classification.** A missing directory, a symlinked directory, a nested entry or
  symlink, an entry set that is neither the three public files nor those three plus
  ``receipt.json``, public bytes failing :func:`~equity_lake.devtools.verify_h00_release.verify_release`,
  a missing receipt, an invalid receipt, a receipt that does not match those bytes, and platform
  expiry are each named. A copy is a **usable recovery point** only when its public bytes verify
  *and* its receipt verifies against them *and* the artifact has not expired.
* **Release identity.** A release key is the ordered tuple of the three verified public sha256
  values, so copies with the same key are one release and the extras are reported as duplicates.
  The active release is the key computed from ``--active``; unverifiable active bytes are refused
  rather than guessed.
* **Deployment evidence.** In this workflow ``conclusion == "success"`` means build *and* deploy
  succeeded; a run waiting on the environment reviewer has ``conclusion: null`` and has deployed
  nothing; a ``failure`` run deployed nothing. Distinct releases are ranked by ``created_at``
  because the API does not expose the deploy job's completion time.
* **Protection.** The active release, the three most recent distinct verified releases (active
  counted if it is among them) and every ``--pin``. Each protected release must keep at least one
  readable copy, and that copy must not expire before ``--window-end``; otherwise the inventory is
  refused.
* **Candidate suggestions.** Only copies of unprotected releases, and only when the inventory is
  complete and trustworthy: a truncated, unlabelled or duplicated listing, a pending run, a copy that
  no longer proves, or a protected release that is not recoverable withholds the whole list. Every
  withholding reason is printed, so the list is never silently empty. These ids are **suggestions for
  a separate owner approval under proposed ADR-0038**; naming one authorizes nothing.

The verifier needs a directory holding exactly the three public files, so a bundle's receipt is
split away by copying the three public files into an ephemeral directory under the OS temporary
directory. Each is opened with ``O_NOFOLLOW`` and both the reported digest and the staged bytes come
from that one handle, so nothing the report names can differ from what the verifier checked. No input
file is modified, no link is followed and nothing is staged next to the bundles.

Exit codes: ``0`` inventory complete (candidates may or may not exist), ``1`` inventory
incomplete or refused (missing directory, unverifiable active bytes, protected release with no
usable copy, …), ``2`` nothing to audit (no in-scope bundle artifact at all).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from equity_lake.dashboard import h00
from equity_lake.devtools.verify_h00_release import (
    ALLOWED_FILENAMES,
    ReleaseReceipt,
    ReleaseVerificationError,
    verify_receipt,
    verify_release,
)

REPORT_SCHEMA_VERSION: Final[Literal[1]] = 1

#: The only artifact in scope: the private retained bundle of the H00 publisher (ADR-0035 d6).
BUNDLE_ARTIFACT_NAME: Final = "h00-release-bundle"
#: The only workflow in scope.
PUBLISH_WORKFLOW_FILE: Final = "h00-publish.yml"
PUBLISH_WORKFLOW_NAME: Final = "Publish H00 Sanitized Page"
#: The receipt sits beside the three public files inside the retained bundle, never in the public directory.
RECEIPT_FILENAME: Final = "receipt.json"
#: How many distinct verified releases are protected besides the active one and owner pins.
PROTECTED_RELEASE_COUNT: Final = 3

#: The three public files in the one order this module stages and names them. The builder's tuple and
#: the verifier's allowlist must name the same files, or the staging order and the classification set
#: could drift apart; the import-time check below refuses that instead of reporting a half-truth.
PUBLIC_FILENAMES: Final[tuple[str, ...]] = tuple(h00.ARTIFACT_FILENAMES)
if len(set(PUBLIC_FILENAMES)) != len(PUBLIC_FILENAMES) or set(PUBLIC_FILENAMES) != set(ALLOWED_FILENAMES):
    raise RuntimeError("the builder's public filenames and the verifier's allowlist disagree")

#: Named refusal reasons. Every copy-level and inventory-level refusal is one of these strings.
DIRECTORY_MISSING = "bundle directory missing"
DIRECTORY_SYMLINKED = "bundle directory is a symbolic link"
NESTED_OR_SYMLINK = "nested directory or symbolic link in the bundle"
ENTRY_SET = "entry set is not the three public files plus the receipt"
PUBLIC_BYTES_INVALID = "public bytes failed verification"
RECEIPT_MISSING = "receipt missing"
RECEIPT_INVALID = "receipt invalid"
RECEIPT_MISMATCH = "receipt does not match the public bytes"
ARTIFACT_EXPIRED = "artifact expired"
RUN_MISSING = "no matching h00-publish run for a listed bundle artifact"
BUNDLE_ENTRY_UNREADABLE = "bundle entry could not be read"
OUT_OF_SCOPE_NAME = "artifact name is not h00-release-bundle"
OUT_OF_SCOPE_WORKFLOW = "artifact belongs to a run of another workflow"
ACTIVE_UNKNOWN = "the active bytes do not verify"
PIN_UNMATCHED = "owner pin does not match a listed artifact"
PROTECTED_WITH_NO_COPY = "a protected release has no usable copy"
PROTECTED_WITH_NO_WINDOW_COPY = "a protected release has no copy that outlives the maintenance window"
#: Named inventory gaps read from the two listings: a truncated, unlabelled, empty or duplicated
#: snapshot can hide a retained copy or a deploy, so it refuses the report instead of looking complete.
LISTING_TRUNCATED = "listing is truncated"
LISTING_NO_TOTAL_COUNT = "listing has no total_count"
LISTING_EMPTY = "listing is empty alongside a non-empty other listing"
DUPLICATE_ARTIFACT_ID = "duplicate artifact id"
DUPLICATE_RUN_ID = "duplicate run id"

#: Withholding reasons for the candidate suggestions (distinct from refusals: these block pruning only).
WITHHOLD_TOO_FEW_RELEASES = "fewer than three distinct verified releases"
WITHHOLD_ACTIVE_UNKNOWN = "the active release is unknown"
WITHHOLD_INCOMPLETE = "the inventory is incomplete"
WITHHOLD_PENDING_RUN = "a publish or rollback run is pending or in progress"
WITHHOLD_PROTECTION = "a protected release is not recoverable"
WITHHOLD_WINDOW = "a protected release has no copy that outlives the maintenance window"

#: The defects and gaps that make the inventory untrustworthy for pruning (not merely copyless).
INVENTORY_GAPS: Final[frozenset[str]] = frozenset(
    {
        DIRECTORY_MISSING,
        DIRECTORY_SYMLINKED,
        NESTED_OR_SYMLINK,
        BUNDLE_ENTRY_UNREADABLE,
        ENTRY_SET,
        PUBLIC_BYTES_INVALID,
        RECEIPT_INVALID,
        RECEIPT_MISMATCH,
        RUN_MISSING,
        LISTING_TRUNCATED,
        LISTING_NO_TOTAL_COUNT,
        LISTING_EMPTY,
        DUPLICATE_ARTIFACT_ID,
        DUPLICATE_RUN_ID,
    }
)

#: One named inventory gap: a reason and a value-free detail. Loaders report these instead of raising.
Gap = tuple[str, str]

#: Copy-state vocabulary (value-free labels, never file contents).
STATE_NOT_CHECKED: Final = "not checked"
STATE_VERIFIED: Final = "verified"
STATE_PRESENT: Final = "present"
STATE_MISSING: Final = "missing"
STATE_SYMLINK: Final = "symbolic link"

MAX_SUMMARY_LINES: Final = 10
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)


class InventoryInputError(ValueError):
    """The inventory inputs cannot be read as given (unreadable, or the wrong JSON shape)."""


class PublicFile(BaseModel):
    """One allowlisted public file's identity (value-free: a name, a digest and a size)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class BundleCopy(BaseModel):
    """One artifact's downloaded bundle, classified with a named reason when it is unusable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: int
    artifact_name: str
    workflow_run_id: int | None
    in_scope: bool
    directory_state: str
    entries: int
    expired: bool
    created_at: datetime | None
    expires_at: datetime | None
    public_state: str
    receipt_state: str
    usable: bool
    reason: str | None
    release_key: str | None
    public_bytes: int
    duplicate_of_artifact_id: int | None
    survives_window: bool | None
    detail: str = ""


class ReleaseSummary(BaseModel):
    """One distinct release: the verified public digests plus its copies, deployment and role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    files: tuple[PublicFile, ...]
    public_bytes: int
    copy_ids: tuple[int, ...]
    usable_copy_ids: tuple[int, ...]
    sole_readable_copy_id: int | None
    active: bool
    protected: bool
    protected_by: tuple[str, ...]
    most_recent_deployment_at: datetime | None
    deployment_run_id: int | None
    survives_window: bool


class Refusal(BaseModel):
    """One named refusal, aggregated over the copies it applies to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    count: int
    detail: str


class Withholding(BaseModel):
    """One reason the candidate list is withheld; the list is never silently empty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    detail: str


class H00BundleInventory(BaseModel):
    """The value-free inventory report: ids, digests, dates, counts and named reasons only.

    ``candidate_artifact_ids`` are suggestions for a separate owner approval under proposed ADR-0038;
    naming one authorizes nothing and this report never deletes, expires or rewrites an artifact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_schema_version: Literal[1] = REPORT_SCHEMA_VERSION
    bundles_path: str
    active_path: str
    artifacts_path: str
    runs_path: str
    window_end: date
    artifacts_listed: int
    artifacts_in_scope: int
    artifacts_out_of_scope: int
    runs_listed: int
    deployments: int
    failed_runs: int
    pending_runs: int
    copies: tuple[BundleCopy, ...]
    releases: tuple[ReleaseSummary, ...]
    active_release_key: str | None
    active_verified: bool
    pinned_artifact_ids: tuple[int, ...]
    candidate_artifact_ids: tuple[int, ...]
    withheld: tuple[Withholding, ...]
    refusals: tuple[Refusal, ...]

    @property
    def complete(self) -> bool:
        """True when nothing was refused: the inventory can be trusted for a pruning decision."""
        return not self.refusals

    @property
    def verified_releases(self) -> int:
        """How many distinct releases have at least one usable copy."""
        return sum(1 for release in self.releases if release.usable_copy_ids)


@dataclass(frozen=True, slots=True)
class _Artifact:
    """One row of the artifacts listing, in scope or not."""

    id: int
    name: str
    expired: bool
    created_at: datetime | None
    expires_at: datetime | None
    run_id: int | None


@dataclass(frozen=True, slots=True)
class _Run:
    """One row of the workflow-runs listing."""

    id: int
    status: str
    conclusion: str | None
    created_at: datetime | None
    in_workflow: bool


@dataclass(frozen=True, slots=True)
class _CopyResult:
    """A classified copy plus the public files it proved (both absent when it proved nothing)."""

    copy: BundleCopy
    files: tuple[PublicFile, ...] | None


class StagingRefused(RuntimeError):
    """One bundle entry could not be staged without following a link; ``reason`` is the named refusal."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _open_regular(path: Path) -> int:
    """Open one bundle entry read-only without following a link, or refuse with a named reason.

    ``O_NOFOLLOW`` makes a symbolic link fail instead of being followed, and the entry must be a
    regular file, so a link or a device node swapped in after the directory listing cannot be read
    as public bytes.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        handle = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise StagingRefused(NESTED_OR_SYMLINK, "an entry is a symbolic link") from exc
        raise StagingRefused(BUNDLE_ENTRY_UNREADABLE, "an entry could not be opened") from exc
    if not stat.S_ISREG(os.fstat(handle).st_mode):
        os.close(handle)
        raise StagingRefused(NESTED_OR_SYMLINK, "an entry is not a regular file")
    return handle


@contextmanager
def _staged_public_files(bundle: Path, names: Sequence[str]) -> Iterator[tuple[Path, tuple[PublicFile, ...]]]:
    """Copy the named public files into an ephemeral directory, from no-follow handles.

    The verifier refuses a fourth entry, so a bundle's ``receipt.json`` is split away before it
    runs. Each entry is opened with ``O_NOFOLLOW`` and must be a regular file, and both the reported
    digest and the staged bytes come from that one handle: the verifier can never check bytes a
    reopened path would describe differently. The directory is removed before the caller returns.
    """
    with tempfile.TemporaryDirectory(prefix="h00-inventory-") as staging_name:
        staging = Path(staging_name)
        files: list[PublicFile] = []
        for name in names:
            handle = _open_regular(bundle / name)
            digest = hashlib.sha256()
            size = 0
            try:
                with (staging / name).open("wb") as target:
                    while chunk := os.read(handle, 1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        target.write(chunk)
            except OSError as exc:
                raise StagingRefused(BUNDLE_ENTRY_UNREADABLE, "an entry could not be staged") from exc
            finally:
                os.close(handle)
            files.append(PublicFile(name=name, sha256=digest.hexdigest(), size=size))
        yield staging, tuple(files)


def release_key(files: Sequence[PublicFile]) -> str:
    """The release key: the ordered public sha256 tuple, short-form for the report."""
    return "/".join(entry.sha256[:12] for entry in files)


def _load_list(path: Path, *, list_key: str, label: str) -> tuple[list[dict[str, Any]], list[Gap]]:
    """Read a ``gh api`` listing and its own completeness evidence.

    A ``gh api`` object carries ``total_count``; the snapshot is only trustworthy when that count
    matches the entries actually parsed. A truncated count, a missing count and a bare list are
    reported as named gaps so a snapshot that lost a copy or a deploy cannot look complete.
    """
    if not path.is_file():
        raise InventoryInputError(f"the {label} listing {path} is not a readable file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InventoryInputError(f"the {label} listing {path} is not valid JSON") from exc
    items: object = document
    gaps: list[Gap] = []
    if isinstance(document, dict):
        items = document.get(list_key)
        if items is None:
            raise InventoryInputError(f"the {label} listing {path} carries no {list_key!r} list")
        total = document.get("total_count")
        if not isinstance(total, int) or isinstance(total, bool):
            gaps.append((LISTING_NO_TOTAL_COUNT, f"the {label} listing carries no integer total_count"))
        elif total != len(items) if isinstance(items, list) else False:
            gaps.append((LISTING_TRUNCATED, f"the {label} listing records total_count {total} but holds {len(items)}"))
    else:
        gaps.append((LISTING_NO_TOTAL_COUNT, f"the {label} listing is a bare list with no total_count"))
    if not isinstance(items, list):
        raise InventoryInputError(f"the {label} listing {path} is neither a list nor an object with {list_key!r}")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise InventoryInputError(f"the {label} listing {path} holds a {list_key!r} entry that is not an object")
        rows.append(item)
    return rows, gaps


def _int_field(row: Mapping[str, Any], key: str, *, label: str) -> int:
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InventoryInputError(f"the {label} listing has an entry without an integer {key!r}")
    return value


def _optional_int(row: Mapping[str, Any], key: str) -> int | None:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _timestamp(value: object) -> datetime | None:
    """Parse an API timestamp into an aware UTC instant, or ``None`` when absent/unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_artifacts(path: Path) -> tuple[list[_Artifact], list[Gap]]:
    """Parse the artifacts listing into artifacts, in listing order, plus its inventory gaps."""
    rows, gaps = _load_list(path, list_key="artifacts", label="artifacts")
    artifacts: list[_Artifact] = []
    seen: set[int] = set()
    for row in rows:
        name = row.get("name")
        if not isinstance(name, str):
            raise InventoryInputError("the artifacts listing has an entry without a name")
        artifact_id = _int_field(row, "id", label="artifacts")
        if artifact_id in seen:
            # One id resolves to one artifact; two rows for it would hide one behind the other.
            gaps.append((DUPLICATE_ARTIFACT_ID, f"artifact id {artifact_id} appears more than once"))
        seen.add(artifact_id)
        run = row.get("workflow_run")
        run_id = _optional_int(run, "id") if isinstance(run, dict) else None
        artifacts.append(
            _Artifact(
                id=artifact_id,
                name=name,
                expired=row.get("expired") is True,
                created_at=_timestamp(row.get("created_at")),
                expires_at=_timestamp(row.get("expires_at")),
                run_id=run_id,
            )
        )
    return artifacts, gaps


def load_runs(path: Path) -> tuple[list[_Run], list[Gap]]:
    """Parse the workflow-runs listing into runs, plus its inventory gaps."""
    rows, gaps = _load_list(path, list_key="workflow_runs", label="workflow runs")
    runs: list[_Run] = []
    seen: set[int] = set()
    for row in rows:
        run_id = _int_field(row, "id", label="workflow runs")
        if run_id in seen:
            gaps.append((DUPLICATE_RUN_ID, f"run id {run_id} appears more than once"))
        seen.add(run_id)
        workflow_path = row.get("path")
        basename = Path(workflow_path).name if isinstance(workflow_path, str) else ""
        in_workflow = basename == PUBLISH_WORKFLOW_FILE or (not basename and row.get("name") == PUBLISH_WORKFLOW_NAME)
        conclusion = row.get("conclusion")
        runs.append(
            _Run(
                id=run_id,
                status=str(row.get("status") or ""),
                conclusion=conclusion if isinstance(conclusion, str) else None,
                created_at=_timestamp(row.get("created_at")),
                in_workflow=in_workflow,
            )
        )
    return runs, gaps


def _verify_receipt_document(staging: Path, receipt_path: Path) -> tuple[str, str]:
    """Return the receipt state and a value-free detail for one bundle's receipt."""
    try:
        document = ReleaseReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        return RECEIPT_INVALID, f"the receipt document could not be validated ({exc.__class__.__name__})"
    if document.artifact != h00.ARTIFACT_NAME:
        return RECEIPT_INVALID, "the receipt names a different artifact"
    if list(document.allowlist) != list(PUBLIC_FILENAMES):
        return RECEIPT_INVALID, "the receipt records a different allowlist"
    try:
        verify_receipt(staging, receipt_path)
    except ReleaseVerificationError as exc:
        return RECEIPT_MISMATCH, str(exc)
    return STATE_VERIFIED, ""


def _classify_copy(bundles: Path, artifact: _Artifact, window_end: date) -> _CopyResult:
    """Classify one in-scope artifact's bundle directory, naming the first defect it shows."""
    directory = bundles / str(artifact.id)
    directory_state = STATE_MISSING
    entries = 0
    public_state = STATE_NOT_CHECKED
    receipt_state = STATE_NOT_CHECKED
    reason: str | None = None
    files: tuple[PublicFile, ...] = ()
    detail = ""

    if directory.is_symlink():
        directory_state = STATE_SYMLINK
        reason = DIRECTORY_SYMLINKED
        detail = "the bundle directory is a symbolic link"
    elif not directory.is_dir():
        reason = DIRECTORY_MISSING
        detail = "no directory named by the artifact id under --bundles"
    else:
        directory_state = STATE_PRESENT
        listing = sorted(directory.iterdir(), key=lambda entry: entry.name)
        entries = len(listing)
        # `not entry.is_file()` also catches a fifo or device node: the bundle is flat regular files.
        if any(entry.is_symlink() or not entry.is_file() for entry in listing):
            reason = NESTED_OR_SYMLINK
            detail = f"{entries} entries, at least one is not a regular file"
        else:
            names = {entry.name for entry in listing}
            allowlist = set(PUBLIC_FILENAMES)
            if names == allowlist:
                has_receipt = False
            elif names == allowlist | {RECEIPT_FILENAME}:
                has_receipt = True
            else:
                has_receipt = False
                reason = ENTRY_SET
                detail = f"{entries} entries, unexpected {len(names - allowlist - {RECEIPT_FILENAME})}, missing {len(allowlist - names)}"
            if reason is None:
                try:
                    with _staged_public_files(directory, PUBLIC_FILENAMES) as (staging, staged_files):
                        verify_release(staging)
                        if not has_receipt:
                            receipt_state = RECEIPT_MISSING
                            detail = "the bundle holds the three public files and no receipt"
                        else:
                            receipt_state, detail = _verify_receipt_document(staging, directory / RECEIPT_FILENAME)
                except StagingRefused as exc:
                    reason = exc.reason
                    detail = exc.detail
                except ReleaseVerificationError as exc:
                    public_state = PUBLIC_BYTES_INVALID
                    reason = PUBLIC_BYTES_INVALID
                    detail = str(exc)
                else:
                    public_state = STATE_VERIFIED
                    files = staged_files
                    if not has_receipt:
                        reason = RECEIPT_MISSING
                    elif receipt_state != STATE_VERIFIED:
                        reason = receipt_state
    if reason is None and artifact.expired:
        reason = ARTIFACT_EXPIRED
        detail = "the platform has expired this artifact"
    copy = BundleCopy(
        artifact_id=artifact.id,
        artifact_name=artifact.name,
        workflow_run_id=artifact.run_id,
        in_scope=True,
        directory_state=directory_state,
        entries=entries,
        expired=artifact.expired,
        created_at=artifact.created_at,
        expires_at=artifact.expires_at,
        public_state=public_state,
        receipt_state=receipt_state,
        usable=reason is None,
        reason=reason,
        release_key=release_key(files) if files else None,
        public_bytes=sum(entry.size for entry in files),
        duplicate_of_artifact_id=None,
        survives_window=None if artifact.expires_at is None else artifact.expires_at.date() > window_end,
        detail=detail,
    )
    return _CopyResult(copy=copy, files=files or None)


def _out_of_scope_copy(artifact: _Artifact, reason: str, window_end: date) -> _CopyResult:
    """One listed artifact that is never a candidate and never protection."""
    copy = BundleCopy(
        artifact_id=artifact.id,
        artifact_name=artifact.name,
        workflow_run_id=artifact.run_id,
        in_scope=False,
        directory_state=STATE_NOT_CHECKED,
        entries=0,
        expired=artifact.expired,
        created_at=artifact.created_at,
        expires_at=artifact.expires_at,
        public_state=STATE_NOT_CHECKED,
        receipt_state=STATE_NOT_CHECKED,
        usable=False,
        reason=reason,
        release_key=None,
        public_bytes=0,
        duplicate_of_artifact_id=None,
        survives_window=None if artifact.expires_at is None else artifact.expires_at.date() > window_end,
    )
    return _CopyResult(copy=copy, files=None)


class _Ledger:
    """Accumulates named refusals in first-seen order, keeping one value-free detail each."""

    def __init__(self) -> None:
        self._by_reason: dict[str, list[Any]] = {}

    def add(self, reason: str, detail: str) -> None:
        entry = self._by_reason.setdefault(reason, [0, detail])
        entry[0] += 1
        if not entry[1]:
            entry[1] = detail

    def rows(self) -> tuple[Refusal, ...]:
        return tuple(Refusal(reason=reason, count=entry[0], detail=entry[1]) for reason, entry in self._by_reason.items())


def _rank_key(copy: BundleCopy, runs_by_id: Mapping[int, _Run]) -> tuple[datetime, int]:
    """Deployment evidence for one copy: the successful run's ``created_at``, else the epoch.

    ``created_at`` stands in for deployment time because the API does not expose the deploy job's
    completion time; a failed, pending or unattributable run is no deployment at all.
    """
    run = runs_by_id.get(copy.workflow_run_id) if copy.workflow_run_id is not None else None
    if run is not None and run.status == "completed" and run.conclusion == "success":
        return (run.created_at or _EPOCH, run.id)
    return (_EPOCH, 0)


def inventory(
    *,
    bundles: Path,
    active: Path,
    artifacts_path: Path,
    runs_path: Path,
    pins: Sequence[int] = (),
    window_end: date | None = None,
) -> H00BundleInventory:
    """Build the inventory report from the local snapshots.

    Args:
        bundles: one directory per artifact id, holding that downloaded artifact's contents.
        active: the three files currently served by the live site.
        artifacts_path: the ``actions/artifacts`` listing as JSON.
        runs_path: the ``h00-publish.yml`` workflow-runs listing as JSON.
        pins: owner-pinned artifact ids that are always protected.
        window_end: last date of the proposed maintenance window (default: today, UTC).
    """
    endpoint = window_end or datetime.now(UTC).date()
    artifacts, artifact_gaps = load_artifacts(artifacts_path)
    runs, run_gaps = load_runs(runs_path)
    h00_runs = [run for run in runs if run.in_workflow]
    runs_by_id = {run.id: run for run in h00_runs}
    ledger = _Ledger()
    for reason, detail in (*artifact_gaps, *run_gaps):
        ledger.add(reason, detail)
    if not artifacts and runs:
        ledger.add(LISTING_EMPTY, "the artifacts listing is empty while the runs listing is not; the snapshot lost its artifacts")
    elif artifacts and not runs:
        ledger.add(LISTING_EMPTY, "the runs listing is empty while the artifacts listing is not; the snapshot lost its runs")

    active_verified = False
    active_files: tuple[PublicFile, ...] = ()
    staged_files: tuple[PublicFile, ...] = ()
    if not active.is_dir():
        ledger.add(ACTIVE_UNKNOWN, "no --active directory of served bytes")
    else:
        try:
            with _staged_public_files(active, PUBLIC_FILENAMES) as (staging, staged_files):
                verify_release(staging)
        except (StagingRefused, ReleaseVerificationError) as exc:
            ledger.add(ACTIVE_UNKNOWN, str(exc))
        else:
            active_verified = True
            active_files = staged_files
    active_key = release_key(active_files) if active_verified else None

    results: list[_CopyResult] = []
    artifacts_in_scope = 0
    for artifact in artifacts:
        if artifact.name != BUNDLE_ARTIFACT_NAME:
            results.append(_out_of_scope_copy(artifact, OUT_OF_SCOPE_NAME, endpoint))
            continue
        artifacts_in_scope += 1
        run = runs_by_id.get(artifact.run_id) if artifact.run_id is not None else None
        if artifact.run_id is not None and run is not None and not run.in_workflow:
            results.append(_out_of_scope_copy(artifact, OUT_OF_SCOPE_WORKFLOW, endpoint))
            artifacts_in_scope -= 1
            continue
        result = _classify_copy(bundles, artifact, endpoint)
        if artifact.run_id is None or run is None:
            ledger.add(RUN_MISSING, f"artifact {artifact.id} names a run that is not in the h00-publish listing")
            result = _CopyResult(copy=result.copy.model_copy(update={"usable": False, "reason": RUN_MISSING}), files=result.files)
        results.append(result)

    def add_copy_refusals(copy: BundleCopy) -> None:
        if copy.in_scope and copy.reason is not None and copy.reason in INVENTORY_GAPS:
            ledger.add(copy.reason, f"artifact {copy.artifact_id}: {_copy_detail(copy)}")

    for result in results:
        add_copy_refusals(result.copy)

    # Release identity: group every copy whose public bytes verified, then mark duplicates.
    files_by_key: dict[str, tuple[PublicFile, ...]] = {}
    copies_by_key: dict[str, list[BundleCopy]] = {}
    for result in results:
        key = result.copy.release_key
        if key is None or result.files is None:
            continue
        files_by_key.setdefault(key, result.files)
        copies_by_key.setdefault(key, []).append(result.copy)
    marked: dict[int, BundleCopy] = {}
    for key, members in copies_by_key.items():
        first = min(member.artifact_id for member in members)
        copies_by_key[key] = [
            member if member.artifact_id == first else member.model_copy(update={"duplicate_of_artifact_id": first}) for member in members
        ]
        marked.update({member.artifact_id: member for member in copies_by_key[key]})
    # The duplicate marker is presentational: it names the copy the others repeat, nothing more.
    copies = tuple(marked.get(result.copy.artifact_id, result.copy) for result in results)

    verified_keys = {key for key, members in copies_by_key.items() if any(member.usable for member in members)}
    deployments_by_key: dict[str, tuple[datetime, int]] = {}
    for key, members in copies_by_key.items():
        evidence: list[tuple[datetime, int]] = [_rank_key(member, runs_by_id) for member in members if member.usable]
        deployments_by_key[key] = max(evidence) if evidence else (_EPOCH, 0)
    ranked = sorted(verified_keys, key=lambda key: (deployments_by_key[key][0], deployments_by_key[key][1]), reverse=True)

    pinned = sorted({pin for pin in pins})
    known_ids = {artifact.id for artifact in artifacts}
    pinned_keys: dict[int, str | None] = {}
    for pin in pinned:
        if pin not in known_ids:
            ledger.add(PIN_UNMATCHED, f"pinned artifact {pin} is not in the artifacts listing")
            continue
        pinned_keys[pin] = next((copy.release_key for copy in copies if copy.artifact_id == pin), None)
        if pinned_keys[pin] is None:
            ledger.add(PIN_UNMATCHED, f"pinned artifact {pin} has no verified public bytes, so no release to protect")

    protected_by: dict[str, list[str]] = {}
    if active_key is not None:
        protected_by.setdefault(active_key, []).append("active")
    for key in ranked[:PROTECTED_RELEASE_COUNT]:
        protected_by.setdefault(key, []).append(f"most recent {PROTECTED_RELEASE_COUNT} deployments")
    for key in pinned_keys.values():
        if key is not None:
            protected_by.setdefault(key, []).append("owner pin")

    def _display_position(key: str) -> tuple[bool, float, str]:
        """Protected releases first, then most recently deployed first, then by key."""
        return (key not in protected_by, -deployments_by_key.get(key, (_EPOCH, 0))[0].timestamp(), key)

    releases: list[ReleaseSummary] = []
    for key in sorted(set(files_by_key) | set(protected_by), key=_display_position):
        members = copies_by_key.get(key, [])
        usable_ids = sorted(member.artifact_id for member in members if member.usable)
        files = files_by_key.get(key) or (active_files if key == active_key else ())
        deployment_at, deployment_run = deployments_by_key.get(key, (_EPOCH, 0))
        protected = key in protected_by
        surviving = [member for member in members if member.usable and member.survives_window]
        releases.append(
            ReleaseSummary(
                key=key,
                files=files,
                public_bytes=sum(entry.size for entry in files),
                copy_ids=tuple(sorted(member.artifact_id for member in members)),
                usable_copy_ids=tuple(usable_ids),
                sole_readable_copy_id=usable_ids[0] if len(usable_ids) == 1 else None,
                active=key == active_key,
                protected=protected,
                protected_by=tuple(protected_by.get(key, ())),
                most_recent_deployment_at=None if deployment_at == _EPOCH else deployment_at,
                deployment_run_id=None if deployment_run == 0 else deployment_run,
                survives_window=bool(surviving),
            )
        )

    # Protection accounting: every protected release must keep one readable copy that outlives the window.
    for release in releases:
        if not release.protected:
            continue
        if not release.usable_copy_ids:
            ledger.add(PROTECTED_WITH_NO_COPY, f"release {release.key} has no copy with verified bytes and a matching receipt")
        elif not release.survives_window:
            ledger.add(PROTECTED_WITH_NO_WINDOW_COPY, f"release {release.key} has no usable copy expiring after {endpoint.isoformat()}")

    pending = [run for run in h00_runs if run.status != "completed" or run.conclusion is None]
    failed = [run for run in h00_runs if run.status == "completed" and run.conclusion not in (None, "success")]

    refusals = ledger.rows()
    withheld: list[Withholding] = []
    if not active_verified:
        withheld.append(Withholding(reason=WITHHOLD_ACTIVE_UNKNOWN, detail="the --active bytes do not verify, so the live release cannot be mapped"))
    verified_count = len(verified_keys)
    if verified_count < PROTECTED_RELEASE_COUNT:
        plural = "" if verified_count == 1 else "s"
        verb = "exists" if verified_count == 1 else "exist"
        withheld.append(
            Withholding(
                reason=WITHHOLD_TOO_FEW_RELEASES,
                detail=f"{verified_count} distinct verified release{plural} {verb}; {PROTECTED_RELEASE_COUNT} are needed",
            )
        )
    defects = [refusal for refusal in refusals if refusal.reason in INVENTORY_GAPS]
    if defects:
        withheld.append(
            Withholding(
                reason=WITHHOLD_INCOMPLETE,
                detail=", ".join(f"{refusal.reason} ({refusal.count})" for refusal in defects),
            )
        )
    if pending:
        withheld.append(
            Withholding(
                reason=WITHHOLD_PENDING_RUN,
                detail=f"{len(pending)} h00-publish run{'' if len(pending) == 1 else 's'} not completed: "
                + ", ".join(str(run.id) for run in pending),
            )
        )
    if any(refusal.reason == PROTECTED_WITH_NO_COPY for refusal in refusals):
        withheld.append(Withholding(reason=WITHHOLD_PROTECTION, detail=PROTECTED_WITH_NO_COPY))
    if any(refusal.reason == PROTECTED_WITH_NO_WINDOW_COPY for refusal in refusals):
        withheld.append(Withholding(reason=WITHHOLD_WINDOW, detail=PROTECTED_WITH_NO_WINDOW_COPY))

    candidates: tuple[int, ...] = ()
    if not withheld:
        sole_protected = {release.sole_readable_copy_id for release in releases if release.protected and release.sole_readable_copy_id is not None}
        untouched = set(pinned)
        candidates = tuple(
            sorted(
                copy.artifact_id
                for copy in copies
                if copy.in_scope
                and copy.usable
                and copy.artifact_id not in untouched
                and copy.release_key is not None
                and copy.release_key not in protected_by
                and copy.artifact_id not in sole_protected
            )
        )

    return H00BundleInventory(
        bundles_path=str(bundles),
        active_path=str(active),
        artifacts_path=str(artifacts_path),
        runs_path=str(runs_path),
        window_end=endpoint,
        artifacts_listed=len(artifacts),
        artifacts_in_scope=artifacts_in_scope,
        artifacts_out_of_scope=len(artifacts) - artifacts_in_scope,
        runs_listed=len(h00_runs),
        deployments=sum(1 for run in h00_runs if run.status == "completed" and run.conclusion == "success"),
        failed_runs=len(failed),
        pending_runs=len(pending),
        copies=copies,
        releases=tuple(releases),
        active_release_key=active_key,
        active_verified=active_verified,
        pinned_artifact_ids=tuple(pinned),
        candidate_artifact_ids=candidates,
        withheld=tuple(withheld),
        refusals=refusals,
    )


def _copy_detail(copy: BundleCopy) -> str:
    """A value-free detail for one refused copy."""
    if copy.detail:
        return copy.detail
    if copy.reason == RUN_MISSING:
        return "the artifact names a run outside the h00-publish listing"
    return f"directory {copy.directory_state}, {copy.entries} entries, public {copy.public_state}, receipt {copy.receipt_state}"


def _fmt_instant(value: datetime | None) -> str:
    return "never" if value is None else value.strftime("%Y-%m-%dT%H:%M:%SZ")


def print_summary(report: H00BundleInventory) -> None:
    """Print the compact, value-free summary (the JSON report carries every copy and release)."""
    usable = sum(1 for copy in report.copies if copy.usable)
    in_scope = [copy for copy in report.copies if copy.in_scope]
    print(f"h00 bundle inventory: window end {report.window_end.isoformat()}, {report.active_path} as the active bytes")
    print(
        f"artifacts: {report.artifacts_listed} listed, {report.artifacts_in_scope} in scope "
        f"(name {BUNDLE_ARTIFACT_NAME} on {PUBLISH_WORKFLOW_FILE}), {report.artifacts_out_of_scope} out of scope"
    )
    print(
        f"runs: {report.runs_listed} {PUBLISH_WORKFLOW_FILE} runs, {report.deployments} deployments, {report.failed_runs} failed, "
        f"{report.pending_runs} pending or in progress"
    )
    print(f"copies: {len(in_scope)} in scope -> {usable} usable, {len(in_scope) - usable} unclassified")
    for copy in in_scope[:MAX_SUMMARY_LINES]:
        state = "usable" if copy.usable else f"UNUSABLE ({copy.reason})"
        key = "" if copy.release_key is None else f" release {copy.release_key}"
        duplicate = "" if copy.duplicate_of_artifact_id is None else f" duplicate of {copy.duplicate_of_artifact_id}"
        print(
            f"copy {copy.artifact_id} run {copy.workflow_run_id} {state}{key}{duplicate} "
            f"bytes={copy.public_bytes} expires={_fmt_instant(copy.expires_at)}"
        )
    if len(in_scope) > MAX_SUMMARY_LINES:
        print(f"copy ... {len(in_scope) - MAX_SUMMARY_LINES} more (see the JSON report)")
    print(f"releases: {len(report.releases)} distinct, {report.verified_releases} verified, active {report.active_release_key or 'unknown'}")
    for release in report.releases[:MAX_SUMMARY_LINES]:
        role = ",".join(release.protected_by) if release.protected_by else "unprotected"
        print(
            f"release {release.key} {role} copies={len(release.copy_ids)} usable={len(release.usable_copy_ids)} "
            f"bytes={release.public_bytes} deployed={_fmt_instant(release.most_recent_deployment_at)} "
            f"run={release.deployment_run_id} survives_window={release.survives_window}"
        )
    if len(report.releases) > MAX_SUMMARY_LINES:
        print(f"release ... {len(report.releases) - MAX_SUMMARY_LINES} more (see the JSON report)")
    for refusal in report.refusals:
        print(f"REFUSED {refusal.reason}: {refusal.count} - {refusal.detail}")
    for withheld in report.withheld:
        print(f"WITHHELD {withheld.reason}: {withheld.detail}")
    if report.candidate_artifact_ids:
        suggested = ", ".join(str(artifact_id) for artifact_id in report.candidate_artifact_ids)
        print(f"candidate suggestions (owner approval under ADR-0038 required): {suggested}")
    elif report.withheld:
        print("candidate suggestions: none (withheld)")
    else:
        print("candidate suggestions: none (every in-scope copy is protected)")
    if report.refusals:
        noun = "reason" if len(report.refusals) == 1 else "reasons"
        result = f"refused ({len(report.refusals)} named {noun})"
    elif report.withheld:
        result = "no candidate suggestions (withheld)"
    else:
        result = "inventory complete"
    print(f"RESULT: {result}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: 0 complete, 1 incomplete or refused, 2 nothing to audit."""
    parser = argparse.ArgumentParser(description="Inventory the retained H00 rollback bundles (read-only, offline, deletes nothing)")
    parser.add_argument("--bundles", required=True, help="directory of one subdirectory per artifact id, named by artifact id")
    parser.add_argument("--active", required=True, help="directory of the three files currently served by the live site")
    parser.add_argument("--artifacts", required=True, help="JSON of the actions/artifacts listing (object or bare list)")
    parser.add_argument("--runs", required=True, help="JSON of the h00-publish.yml workflow-runs listing (object or bare list)")
    parser.add_argument("--pin", action="append", type=int, default=[], metavar="ARTIFACT_ID", help="owner-pinned recovery point (repeatable)")
    parser.add_argument("--window-end", default=None, help="last date of the proposed maintenance window, ISO YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--out", default=None, help="write the JSON report here (an auxiliary artifact, ADR-0006; nothing is written without it)")
    args = parser.parse_args(argv)
    endpoint: date | None = None
    if args.window_end is not None:
        try:
            endpoint = date.fromisoformat(args.window_end)
        except ValueError as exc:
            print(f"REFUSED: --window-end must be an ISO date (YYYY-MM-DD): {exc}", file=sys.stderr)
            return 1
    try:
        report = inventory(
            bundles=Path(args.bundles),
            active=Path(args.active),
            artifacts_path=Path(args.artifacts),
            runs_path=Path(args.runs),
            pins=tuple(args.pin),
            window_end=endpoint,
        )
    except InventoryInputError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    if args.out is not None:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print_summary(report)
    if report.artifacts_in_scope == 0:
        return 2
    return 0 if report.complete else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ACTIVE_UNKNOWN",
    "ARTIFACT_EXPIRED",
    "BUNDLE_ARTIFACT_NAME",
    "BUNDLE_ENTRY_UNREADABLE",
    "DIRECTORY_MISSING",
    "DIRECTORY_SYMLINKED",
    "DUPLICATE_ARTIFACT_ID",
    "DUPLICATE_RUN_ID",
    "ENTRY_SET",
    "H00BundleInventory",
    "INVENTORY_GAPS",
    "InventoryInputError",
    "LISTING_EMPTY",
    "LISTING_NO_TOTAL_COUNT",
    "LISTING_TRUNCATED",
    "NESTED_OR_SYMLINK",
    "OUT_OF_SCOPE_NAME",
    "OUT_OF_SCOPE_WORKFLOW",
    "PROTECTED_WITH_NO_COPY",
    "PROTECTED_WITH_NO_WINDOW_COPY",
    "PUBLIC_BYTES_INVALID",
    "PUBLIC_FILENAMES",
    "RECEIPT_INVALID",
    "RECEIPT_MISMATCH",
    "RECEIPT_MISSING",
    "RUN_MISSING",
    "StagingRefused",
    "WITHHOLD_ACTIVE_UNKNOWN",
    "WITHHOLD_INCOMPLETE",
    "WITHHOLD_PENDING_RUN",
    "WITHHOLD_PROTECTION",
    "WITHHOLD_TOO_FEW_RELEASES",
    "WITHHOLD_WINDOW",
    "ReleaseSummary",
    "inventory",
    "main",
    "release_key",
]
