"""Unit coverage for the report-only H00 bundle inventory (stage H3; ADR-0038 proposed).

Every fixture is synthetic: a bundle directory of three small public files plus a genuine
receipt built by :func:`~equity_lake.devtools.verify_h00_release.build_receipt`, and the two
``gh api`` listings as JSON. Nothing here reads the network, an artifact or a real lake.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from equity_lake.dashboard import h00
from equity_lake.devtools import inventory_h00_bundles as inv
from equity_lake.devtools.verify_h00_release import build_receipt

BUILT_AT = "2026-11-27T10:16:00+00:00"
WINDOW_END = date(2026, 12, 1)
EXPIRES_AT = "2027-02-25T10:15:53Z"

RUN_FIRST = 45980611891
RUN_SECOND = 46011953819
RUN_FAILED = 46020397986
RUN_ROLLBACK = 46021167248

WORKFLOW_PATH = ".github/workflows/h00-publish.yml"


def _release_files(marker: str) -> dict[str, bytes]:
    """Bytes that pass ``verify_release``; ``marker`` makes each release a distinct one."""
    payload = {"artifact": h00.ARTIFACT_NAME, "built_at": BUILT_AT, "marker": marker}
    report = {
        "passed": True,
        "root": "demo_lake_only",
        "provenance": "synthetic",
        "files": list(h00.ARTIFACT_FILENAMES),
        "build_utc": BUILT_AT,
        "negative_scan": {"state": "clean", "classes": sorted(h00.forbidden_needles(Path(".")).keys())},
    }
    return {
        "index.html": f"<html><body>synthetic demo {marker}</body></html>".encode(),
        "payload.json": json.dumps(payload).encode(),
        "sanitization-report.json": json.dumps(report).encode(),
    }


def _materialize(directory: Path, files: Mapping[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)


def _receipt(files: Mapping[str, bytes], tmp_path: Path) -> dict[str, Any]:
    """A genuine receipt for these bytes, built by the verifier's own builder."""
    staging = tmp_path / f"receipt-staging-{uuid4().hex[:8]}"
    _materialize(staging, files)
    document: dict[str, Any] = json.loads(build_receipt(staging).model_dump_json())
    return document


def _bundle(
    bundles: Path,
    artifact_id: int,
    files: Mapping[str, bytes],
    *,
    tmp_path: Path,
    receipt: bool = True,
    receipt_document: Mapping[str, Any] | None = None,
    receipt_text: str | None = None,
) -> Path:
    """One artifact's downloaded bundle: the three public files plus an optional receipt."""
    directory = bundles / str(artifact_id)
    _materialize(directory, files)
    if receipt_text is not None:
        (directory / "receipt.json").write_text(receipt_text, encoding="utf-8")
    elif receipt_document is not None:
        (directory / "receipt.json").write_text(json.dumps(receipt_document), encoding="utf-8")
    elif receipt:
        (directory / "receipt.json").write_text(json.dumps(_receipt(files, tmp_path)), encoding="utf-8")
    return directory


def _artifact(
    artifact_id: int,
    run_id: int | None = RUN_FIRST,
    *,
    name: str = inv.BUNDLE_ARTIFACT_NAME,
    expired: bool = False,
    created_at: str = "2026-11-27T10:16:32Z",
    expires_at: str = EXPIRES_AT,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": artifact_id,
        "name": name,
        "expired": expired,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if run_id is not None:
        document["workflow_run"] = {"id": run_id}
    return document


def _run(
    run_id: int,
    *,
    created_at: str = "2026-11-27T10:15:52Z",
    conclusion: str | None = "success",
    status: str = "completed",
    path: str = WORKFLOW_PATH,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": "Publish H00 Sanitized Page",
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "path": path,
    }


def _write_listing(path: Path, key: str, rows: Sequence[Mapping[str, Any]], *, bare: bool = False) -> Path:
    document: Any = list(rows) if bare else {"total_count": len(rows), key: list(rows)}
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@dataclass(frozen=True)
class _Case:
    """One synthetic inventory: the two listings plus the bundle and active directories."""

    bundles: Path
    active: Path
    artifacts: Path
    runs: Path
    pins: tuple[int, ...] = ()
    window_end: date = WINDOW_END

    def argv(self) -> list[str]:
        argv = [
            "--bundles",
            str(self.bundles),
            "--active",
            str(self.active),
            "--artifacts",
            str(self.artifacts),
            "--runs",
            str(self.runs),
            "--window-end",
            self.window_end.isoformat(),
            *[token for pin in self.pins for token in ("--pin", str(pin))],
        ]
        return argv

    def inventory(self) -> inv.H00BundleInventory:
        return inv.inventory(
            bundles=self.bundles,
            active=self.active,
            artifacts_path=self.artifacts,
            runs_path=self.runs,
            pins=self.pins,
            window_end=self.window_end,
        )

    def printed(self) -> str:
        """The compact human summary this case prints, plus its exit code in ``self.exit_code()``."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            inv.main(self.argv())
        return buffer.getvalue()

    def exit_code(self) -> int:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            return inv.main(self.argv())


@dataclass
class _Workspace:
    """Fixture scaffolding bound to one ``tmp_path``."""

    root: Path
    files: dict[str, dict[str, bytes]] = field(default_factory=dict)

    def bundles(self) -> Path:
        directory = self.root / "bundles"
        directory.mkdir(exist_ok=True)
        return directory

    def release(self, marker: str) -> dict[str, bytes]:
        self.files[marker] = _release_files(marker)
        return self.files[marker]

    def bundle(self, artifact_id: int, marker: str, **kwargs: Any) -> Path:
        return _bundle(self.bundles(), artifact_id, self.release(marker), tmp_path=self.root, **kwargs)

    def active(self, marker: str) -> Path:
        directory = self.root / "live"
        _materialize(directory, self.release(marker))
        return directory

    def case(
        self,
        artifacts: Sequence[Mapping[str, Any]],
        runs: Sequence[Mapping[str, Any]],
        *,
        active_marker: str | None = None,
        pins: tuple[int, ...] = (),
        window_end: date = WINDOW_END,
        bare: bool = False,
    ) -> _Case:
        active = self.active(active_marker or "alpha")
        return _Case(
            bundles=self.bundles(),
            active=active,
            artifacts=_write_listing(self.root / "artifacts.json", "artifacts", artifacts, bare=bare),
            runs=_write_listing(self.root / "runs.json", "workflow_runs", runs, bare=bare),
            pins=pins,
            window_end=window_end,
        )


def _workspace(tmp_path: Path) -> _Workspace:
    return _Workspace(root=tmp_path)


def _copy(report: inv.H00BundleInventory, artifact_id: int) -> inv.BundleCopy:
    return next(copy for copy in report.copies if copy.artifact_id == artifact_id)


def _release(report: inv.H00BundleInventory, key: str) -> inv.ReleaseSummary:
    return next(release for release in report.releases if release.key == key)


def _reasons(report: inv.H00BundleInventory) -> list[str]:
    return [refusal.reason for refusal in report.refusals]


def _withheld(report: inv.H00BundleInventory) -> list[str]:
    return [withheld.reason for withheld in report.withheld]


# The observed platform shape: two distinct verified releases plus a receipt-less copy of the
# active release, uploaded by the rollback that ran before the receipt-restaging fix.
def test_rollback_shape_two_releases_and_a_receiptless_copy(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(15188823010, "alpha")
    work.bundle(15203433999, "beta")
    work.bundle(15207528817, "beta", receipt=False)
    case = work.case(
        [
            _artifact(15207528817, RUN_ROLLBACK, created_at="2026-11-27T15:02:11Z"),
            _artifact(15203433999, RUN_SECOND, created_at="2026-11-27T14:00:49Z"),
            _artifact(15188823010, RUN_FIRST),
        ],
        [
            _run(RUN_ROLLBACK, created_at="2026-11-27T15:01:36Z"),
            _run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"),
            _run(RUN_FIRST),
        ],
        active_marker="beta",
    )
    report = case.inventory()

    assert report.artifacts_in_scope == 3
    assert report.artifacts_out_of_scope == 0
    assert report.runs_listed == 3
    assert report.deployments == 3
    assert report.verified_releases == 2
    assert len(report.releases) == 2

    receiptless = _copy(report, 15207528817)
    assert receiptless.usable is False
    assert receiptless.reason == inv.RECEIPT_MISSING
    assert receiptless.public_state == inv.STATE_VERIFIED
    assert receiptless.release_key == _copy(report, 15203433999).release_key
    assert receiptless.duplicate_of_artifact_id == 15203433999
    assert receiptless.public_bytes == _copy(report, 15203433999).public_bytes

    assert report.active_verified is True
    assert report.active_release_key == _copy(report, 15203433999).release_key
    assert report.candidate_artifact_ids == ()
    assert _withheld(report) == [inv.WITHHOLD_TOO_FEW_RELEASES]
    assert report.complete is True
    assert "fewer than three distinct verified releases" in case.printed()
    assert case.exit_code() == 0


def test_duplicate_content_is_one_release_and_a_duplicate_copy(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(201, "alpha")
    work.bundle(202, "alpha")
    work.bundle(203, "alpha")
    case = work.case(
        [_artifact(203, RUN_SECOND), _artifact(202, RUN_SECOND), _artifact(201, RUN_FIRST)],
        [_run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"), _run(RUN_FIRST)],
    )
    report = case.inventory()

    assert len(report.releases) == 1
    assert report.releases[0].copy_ids == (201, 202, 203)
    assert report.releases[0].usable_copy_ids == (201, 202, 203)
    assert _copy(report, 201).duplicate_of_artifact_id is None
    assert _copy(report, 202).duplicate_of_artifact_id == 201
    assert _copy(report, 203).duplicate_of_artifact_id == 201
    assert report.candidate_artifact_ids == ()


def test_expired_artifact_is_not_a_recovery_point(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(301, "alpha")
    work.bundle(302, "beta")
    case = work.case(
        [_artifact(302, RUN_SECOND, expired=True), _artifact(301, RUN_FIRST)],
        [_run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"), _run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    expired = _copy(report, 302)
    assert expired.expired is True
    assert expired.usable is False
    assert expired.reason == inv.ARTIFACT_EXPIRED
    assert expired.public_state == inv.STATE_VERIFIED
    assert expired.receipt_state == inv.STATE_VERIFIED
    assert report.verified_releases == 1
    assert report.candidate_artifact_ids == ()
    assert inv.WITHHOLD_TOO_FEW_RELEASES in _withheld(report)


def test_pending_run_withholds_the_candidate_list(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(401, "alpha")
    work.bundle(402, "beta")
    work.bundle(403, "gamma")
    work.bundle(404, "delta")
    case = work.case(
        [
            _artifact(404, 46030000000, created_at="2026-11-27T16:00:00Z"),
            _artifact(403, RUN_ROLLBACK, created_at="2026-11-27T15:02:11Z"),
            _artifact(402, RUN_SECOND, created_at="2026-11-27T14:00:49Z"),
            _artifact(401, RUN_FIRST, created_at="2026-11-27T10:16:32Z"),
        ],
        [
            _run(46030000000, created_at="2026-11-27T16:00:00Z", conclusion=None, status="in_progress"),
            _run(RUN_ROLLBACK, created_at="2026-11-27T15:01:36Z"),
            _run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"),
            _run(RUN_FIRST),
        ],
        active_marker="gamma",
    )
    report = case.inventory()

    assert report.pending_runs == 1
    assert report.deployments == 3
    assert report.verified_releases == 4
    assert report.candidate_artifact_ids == ()
    assert inv.WITHHOLD_PENDING_RUN in _withheld(report)
    assert "46030000000" in next(item.detail for item in report.withheld if item.reason == inv.WITHHOLD_PENDING_RUN)
    assert report.complete is True
    assert "WITHHELD a publish or rollback run is pending or in progress" in case.printed()
    assert case.exit_code() == 0


def test_failure_run_deployed_nothing(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(501, "alpha")
    work.bundle(502, "beta")
    work.bundle(503, "gamma")
    case = work.case(
        [_artifact(503, RUN_FAILED, created_at="2026-11-27T14:57:00Z"), _artifact(502, RUN_SECOND), _artifact(501, RUN_FIRST)],
        [
            _run(RUN_FAILED, created_at="2026-11-27T14:56:24Z", conclusion="failure"),
            _run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"),
            _run(RUN_FIRST),
        ],
        active_marker="beta",
    )
    report = case.inventory()

    assert report.failed_runs == 1
    assert report.deployments == 2
    failed_release = next(release for release in report.releases if release.copy_ids == (503,))
    assert failed_release.most_recent_deployment_at is None
    assert failed_release.deployment_run_id is None
    # It is still a verified release and is still protected; it just carries no deployment evidence.
    assert failed_release.protected is True
    assert report.candidate_artifact_ids == ()


def test_artifact_without_a_matching_run_is_an_incomplete_inventory(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(601, "alpha", receipt=False)
    case = work.case(
        [_artifact(601, 46099999999)],
        [_run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    assert report.runs_listed == 1
    assert inv.RUN_MISSING in _reasons(report)
    assert _copy(report, 601).reason == inv.RUN_MISSING
    assert _copy(report, 601).usable is False
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert report.complete is False
    assert case.exit_code() == 1


def test_out_of_scope_artifacts_are_counted_but_never_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(701, "alpha")
    case = work.case(
        [
            _artifact(700, RUN_FIRST, name="github-pages"),
            _artifact(701, RUN_FIRST),
            _artifact(702, RUN_FIRST, name="h00-release-bundle-old"),
        ],
        [_run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    assert report.artifacts_listed == 3
    assert report.artifacts_in_scope == 1
    assert report.artifacts_out_of_scope == 2
    assert _copy(report, 700).in_scope is False
    assert _copy(report, 700).reason == inv.OUT_OF_SCOPE_NAME
    assert _copy(report, 702).reason == inv.OUT_OF_SCOPE_NAME
    assert all(copy.usable is False for copy in report.copies if not copy.in_scope)
    assert report.candidate_artifact_ids == ()
    assert report.complete is True


def test_missing_bundle_directory_is_refused(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    case = work.case([_artifact(801, RUN_FIRST)], [_run(RUN_FIRST)], active_marker="alpha")
    report = case.inventory()

    copy = _copy(report, 801)
    assert copy.directory_state == inv.STATE_MISSING
    assert copy.reason == inv.DIRECTORY_MISSING
    assert copy.usable is False
    assert inv.DIRECTORY_MISSING in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_nested_directory_symlink_entry_and_symlinked_bundle_are_refused(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    nested = work.bundle(901, "alpha")
    (nested / "payload").mkdir()
    linked = work.bundle(902, "beta")
    (linked / "payload").symlink_to(linked / "payload.json")
    target = work.bundle(903, "gamma")
    (work.bundles() / "904").symlink_to(target, target_is_directory=True)
    case = work.case(
        [_artifact(904, RUN_SECOND), _artifact(903, RUN_SECOND), _artifact(902, RUN_FIRST), _artifact(901, RUN_FIRST)],
        [_run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"), _run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    assert _copy(report, 901).reason == inv.NESTED_OR_SYMLINK
    assert _copy(report, 902).reason == inv.NESTED_OR_SYMLINK
    assert _copy(report, 904).directory_state == inv.STATE_SYMLINK
    assert _copy(report, 904).reason == inv.DIRECTORY_SYMLINKED
    assert _copy(report, 903).usable is True
    assert report.verified_releases == 1
    assert inv.NESTED_OR_SYMLINK in _reasons(report)
    assert inv.DIRECTORY_SYMLINKED in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert case.exit_code() == 1


def test_a_tampered_byte_against_the_receipt_is_refused(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    directory = work.bundle(1001, "alpha")
    (directory / "index.html").write_bytes(b"<html><body>synthetic demo alpha, retyped</body></html>")
    case = work.case([_artifact(1001, RUN_FIRST)], [_run(RUN_FIRST)], active_marker="alpha")
    report = case.inventory()

    copy = _copy(report, 1001)
    assert copy.public_state == inv.STATE_VERIFIED
    assert copy.receipt_state == inv.RECEIPT_MISMATCH
    assert copy.reason == inv.RECEIPT_MISMATCH
    assert copy.usable is False
    assert inv.RECEIPT_MISMATCH in _reasons(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_receipt_for_other_bytes_and_an_invalid_receipt_are_refused(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(1101, "alpha", receipt_document=_receipt(_release_files("delta"), tmp_path))
    work.bundle(1102, "beta", receipt_text="{not a receipt")
    case = work.case(
        [_artifact(1102, RUN_SECOND), _artifact(1101, RUN_FIRST)],
        [_run(RUN_SECOND, created_at="2026-11-27T14:00:10Z"), _run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    assert _copy(report, 1101).reason == inv.RECEIPT_MISMATCH
    assert _copy(report, 1102).reason == inv.RECEIPT_INVALID
    assert inv.RECEIPT_MISMATCH in _reasons(report)
    assert inv.RECEIPT_INVALID in _reasons(report)
    assert report.verified_releases == 0
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_unknown_active_bytes_are_refused_not_guessed(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(1201, "alpha")
    case = work.case([_artifact(1201, RUN_FIRST)], [_run(RUN_FIRST)], active_marker="alpha")
    # Replace the served bytes with a report that claims nothing: the verifier refuses them.
    (case.active / "sanitization-report.json").write_text(json.dumps({"passed": False}), encoding="utf-8")
    report = inv.inventory(
        bundles=case.bundles,
        active=case.active,
        artifacts_path=case.artifacts,
        runs_path=case.runs,
        window_end=case.window_end,
    )

    assert report.active_verified is False
    assert report.active_release_key is None
    assert inv.ACTIVE_UNKNOWN in _reasons(report)
    assert inv.WITHHOLD_ACTIVE_UNKNOWN in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_an_owner_pin_protects_an_older_release(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    for artifact_id, marker in ((1301, "alpha"), (1302, "beta"), (1303, "gamma"), (1304, "delta"), (1305, "epsilon")):
        work.bundle(artifact_id, marker)
    case = work.case(
        [
            _artifact(1305, 46050000005, created_at="2026-11-27T15:00:00Z"),
            _artifact(1304, 46050000004, created_at="2026-11-27T14:00:00Z"),
            _artifact(1303, 46050000003, created_at="2026-11-27T13:00:00Z"),
            _artifact(1302, 46050000002, created_at="2026-11-27T12:00:00Z"),
            _artifact(1301, 46050000001, created_at="2026-11-27T11:00:00Z"),
        ],
        [
            _run(46050000005, created_at="2026-11-27T15:00:00Z"),
            _run(46050000004, created_at="2026-11-27T14:00:00Z"),
            _run(46050000003, created_at="2026-11-27T13:00:00Z"),
            _run(46050000002, created_at="2026-11-27T12:00:00Z"),
            _run(46050000001, created_at="2026-11-27T11:00:00Z"),
        ],
        active_marker="epsilon",
        pins=(1301,),
    )
    report = case.inventory()

    assert report.verified_releases == 5
    pinned = _release(report, _copy(report, 1301).release_key or "")
    assert pinned.protected is True
    assert "owner pin" in pinned.protected_by
    assert report.pinned_artifact_ids == (1301,)
    # The pinned release and the three most recent distinct releases leave only 1302 unprotected.
    assert report.candidate_artifact_ids == (1302,)
    assert report.withheld == ()
    assert report.complete is True
    assert "candidate suggestions (owner approval under ADR-0038 required): 1302" in case.printed()
    assert case.exit_code() == 0


def test_the_only_readable_copy_of_a_protected_release_is_never_a_candidate(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(1401, "alpha")
    work.bundle(1402, "beta")
    work.bundle(1403, "gamma")
    work.bundle(1404, "gamma", receipt=False)
    work.bundle(1405, "delta")
    case = work.case(
        [
            _artifact(1405, 46060000005, created_at="2026-11-27T15:00:00Z"),
            _artifact(1404, 46060000004, created_at="2026-11-27T14:00:00Z"),
            _artifact(1403, 46060000004, created_at="2026-11-27T14:00:00Z"),
            _artifact(1402, 46060000002, created_at="2026-11-27T12:00:00Z"),
            _artifact(1401, 46060000001, created_at="2026-11-27T11:00:00Z"),
        ],
        [
            _run(46060000005, created_at="2026-11-27T15:00:00Z"),
            _run(46060000004, created_at="2026-11-27T14:00:00Z"),
            _run(46060000002, created_at="2026-11-27T12:00:00Z"),
            _run(46060000001, created_at="2026-11-27T11:00:00Z"),
        ],
        active_marker="delta",
    )
    report = case.inventory()

    gamma_key = _copy(report, 1403).release_key or ""
    gamma = _release(report, gamma_key)
    assert gamma.protected is True
    assert gamma.usable_copy_ids == (1403,)
    assert gamma.sole_readable_copy_id == 1403
    assert _copy(report, 1404).reason == inv.RECEIPT_MISSING
    assert 1403 not in report.candidate_artifact_ids
    assert 1404 not in report.candidate_artifact_ids
    # Four distinct releases: the active one plus the three most recent leave only alpha unprotected.
    assert report.candidate_artifact_ids == (1401,)


def test_a_copy_expiring_before_the_window_end_does_not_protect(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    work.bundle(1501, "alpha")
    work.bundle(1502, "beta")
    work.bundle(1503, "gamma")
    case = work.case(
        [_artifact(1503, 46070000003), _artifact(1502, 46070000002), _artifact(1501, 46070000001)],
        [
            _run(46070000003, created_at="2026-11-27T13:00:00Z"),
            _run(46070000002, created_at="2026-11-27T12:00:00Z"),
            _run(46070000001, created_at="2026-11-27T11:00:00Z"),
        ],
        active_marker="gamma",
        window_end=date(2027, 3, 1),
    )
    report = case.inventory()

    assert report.verified_releases == 3
    assert all(release.survives_window is False for release in report.releases)
    assert inv.PROTECTED_WITH_NO_WINDOW_COPY in _reasons(report)
    assert inv.WITHHOLD_WINDOW in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert report.complete is False
    assert case.exit_code() == 1


def test_nothing_to_audit_when_no_bundle_artifact_is_listed(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    case = work.case(
        [_artifact(1600, RUN_FIRST, name="github-pages")],
        [_run(RUN_FIRST)],
        active_marker="alpha",
    )
    report = case.inventory()

    assert report.artifacts_listed == 1
    assert report.artifacts_in_scope == 0
    assert report.artifacts_out_of_scope == 1
    assert report.copies[0].reason == inv.OUT_OF_SCOPE_NAME
    assert case.exit_code() == 2


def _stack(work: _Workspace, *, markers: Sequence[str] = ("alpha", "beta", "gamma")) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One verified release per marker, each built by its own successful run, oldest first."""
    artifacts: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        artifact_id = 2000 + index
        run_id = 47000000000 + index
        work.bundle(artifact_id, marker)
        artifacts.append(_artifact(artifact_id, run_id, created_at=f"2026-11-27T1{index}:00:00Z"))
        runs.append(_run(run_id, created_at=f"2026-11-27T1{index}:00:00Z"))
    return artifacts, runs


def test_staging_refuses_a_symlinked_entry_rather_than_following_it(tmp_path: Path) -> None:
    files = _release_files("alpha")
    directory = tmp_path / "bundle"
    _materialize(directory, files)
    elsewhere = tmp_path / "elsewhere-payload"
    elsewhere.write_bytes(files["payload.json"])
    (directory / "payload.json").unlink()
    (directory / "payload.json").symlink_to(elsewhere)

    with pytest.raises(inv.StagingRefused) as refusal, inv._staged_public_files(directory, inv.PUBLIC_FILENAMES):
        pytest.fail("a symlinked entry must never be staged")
    assert refusal.value.reason == inv.NESTED_OR_SYMLINK
    assert refusal.value.detail == "an entry is a symbolic link"

    # A directory swapped in for an entry is refused too, and a vanished entry is named, never silent.
    (directory / "payload.json").unlink()
    (directory / "payload.json").mkdir()
    with pytest.raises(inv.StagingRefused) as nested, inv._staged_public_files(directory, inv.PUBLIC_FILENAMES):
        pytest.fail("a nested directory must never be staged")
    assert nested.value.reason == inv.NESTED_OR_SYMLINK
    assert nested.value.detail == "an entry is not a regular file"

    (directory / "payload.json").rmdir()
    with pytest.raises(inv.StagingRefused) as unreadable, inv._staged_public_files(directory, inv.PUBLIC_FILENAMES):
        pytest.fail("a missing entry must never be staged")
    assert unreadable.value.reason == inv.BUNDLE_ENTRY_UNREADABLE


def test_a_truncated_artifacts_listing_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    case.artifacts.write_text(json.dumps({"total_count": len(artifacts) + 1, "artifacts": artifacts}), encoding="utf-8")
    report = case.inventory()

    assert report.verified_releases == 3
    assert inv.LISTING_TRUNCATED in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert report.complete is False
    assert case.exit_code() == 1


def test_a_truncated_runs_listing_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    case.runs.write_text(json.dumps({"total_count": len(runs) + 2, "workflow_runs": runs}), encoding="utf-8")
    report = case.inventory()

    assert inv.LISTING_TRUNCATED in _reasons(report)
    detail = next(refusal.detail for refusal in report.refusals if refusal.reason == inv.LISTING_TRUNCATED)
    assert "5" in detail and "3" in detail
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_a_listing_without_total_count_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    case.artifacts.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    report = case.inventory()

    assert inv.LISTING_NO_TOTAL_COUNT in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_a_bare_listing_without_total_count_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma", bare=True)
    report = case.inventory()

    assert report.artifacts_in_scope == 3
    bare = [refusal for refusal in report.refusals if refusal.reason == inv.LISTING_NO_TOTAL_COUNT]
    assert len(bare) == 1 and bare[0].count == 2  # aggregated: one reason, both listings
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_a_duplicate_artifact_id_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    duplicated = [*artifacts, dict(artifacts[0])]
    case.artifacts.write_text(json.dumps({"total_count": len(duplicated), "artifacts": duplicated}), encoding="utf-8")
    report = case.inventory()

    assert inv.DUPLICATE_ARTIFACT_ID in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_a_duplicate_run_id_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    duplicated = [*runs, dict(runs[0])]
    case.runs.write_text(json.dumps({"total_count": len(duplicated), "workflow_runs": duplicated}), encoding="utf-8")
    report = case.inventory()

    assert inv.DUPLICATE_RUN_ID in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1


def test_an_empty_listing_alongside_a_populated_one_withholds_candidates(tmp_path: Path) -> None:
    work = _workspace(tmp_path)
    artifacts, runs = _stack(work)
    case = work.case(artifacts, runs, active_marker="gamma")
    case.runs.write_text(json.dumps({"total_count": 0, "workflow_runs": []}), encoding="utf-8")
    report = case.inventory()

    assert inv.LISTING_EMPTY in _reasons(report)
    assert inv.WITHHOLD_INCOMPLETE in _withheld(report)
    assert report.candidate_artifact_ids == ()
    assert case.exit_code() == 1
