"""Pre-upload release verification: allowlist, containment, bounds and a fresh rescan.

Scope: residual plan §2.4 / RS-H01. The builder already fails closed; this module proves
the *last* bytes that would be uploaded, so each refusal below is a guard on the publish
path rather than a property of the build. Every fixture is a real directory on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from equity_lake.dashboard import h00
from equity_lake.devtools import verify_h00_release as verify

CANARY = "CANARY-RELEASE-SECRET-4c1f"
DIGEST_CANARY = "b" * 64
REPO_ROOT = Path(__file__).parents[2]
RECEIPT_NAME = "receipt.json"


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the real devtool CLI in a subprocess, the way the workflow invokes it."""
    inherited = os.environ.get("PYTHONPATH", "")
    path = os.pathsep.join([str(REPO_ROOT / "src"), inherited]) if inherited else str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "equity_lake.devtools.verify_h00_release", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": path},
    )


def _stage_public(source: Path, target: Path) -> None:
    """Copy exactly the three public names, as the workflow does for `dist/`."""
    target.mkdir(parents=True, exist_ok=True)
    for name in h00.ARTIFACT_FILENAMES:
        shutil.copy2(source / name, target / name)


def _digests(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in h00.ARTIFACT_FILENAMES}


def _retained_names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


@pytest.fixture
def canary_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the scan's `.env` comparison at a fixture secret (the `secrets` class)."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"WANDB_ENTITY={CANARY}\n", encoding="utf-8")
    monkeypatch.setattr(h00, "_dotenv_path", lambda: dotenv)
    return CANARY


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """A minimal release directory holding exactly the allowlisted three files."""
    built_at = "2026-11-01T00:00:00+00:00"
    root = tmp_path / "dist"
    root.mkdir()
    payload = {"built_at": built_at, "stale": False, "provenance": "synthetic"}
    report = {
        "passed": True,
        "root": "demo_lake_only",
        "provenance": "synthetic",
        "files": list(h00.ARTIFACT_FILENAMES),
        "build_utc": built_at,
        "negative_scan": {"state": "clean", "classes": sorted(h00.forbidden_needles(root).keys())},
    }
    (root / h00.INDEX_FILENAME).write_text("<!doctype html><html><body>synthetic demo page</body></html>\n", encoding="utf-8")
    (root / h00.PAYLOAD_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    (root / h00.REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    return root


def _refuses(directory: Path, match: str) -> None:
    with pytest.raises(verify.ReleaseVerificationError, match=match):
        verify.verify_release(directory)


class TestCleanRelease:
    def test_the_allowlisted_three_file_release_passes(self, release: Path) -> None:
        summary = verify.verify_release(release)
        assert summary["files"] == 3
        assert summary["bytes"] > 0

    def test_cli_exit_zero_and_prints_no_file_content(self, release: Path, capsys: pytest.CaptureFixture[str]) -> None:
        verify.main(["--dir", str(release)])
        captured = capsys.readouterr()
        assert "passed" in captured.out
        assert CANARY not in captured.out + captured.err


class TestRefusals:
    """Each violation is refused with a value-free message and a non-zero exit."""

    def test_missing_directory_is_refused(self, tmp_path: Path) -> None:
        _refuses(tmp_path / "absent", "does not exist")

    def test_extra_file_is_refused(self, release: Path) -> None:
        (release / "health-report.json").write_text("{}", encoding="utf-8")
        _refuses(release, "not the allowlist")

    def test_missing_allowlisted_file_is_refused(self, release: Path) -> None:
        (release / h00.REPORT_FILENAME).unlink()
        _refuses(release, "not the allowlist")

    def test_nested_directory_is_refused(self, release: Path) -> None:
        nested = release / "dashboard"
        nested.mkdir()
        (nested / h00.INDEX_FILENAME).write_text("escaped\n", encoding="utf-8")
        _refuses(release, "nested directory|not the allowlist")

    def test_symlinked_entry_is_refused(self, release: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.html"
        outside.write_text("<!doctype html>outside\n", encoding="utf-8")
        (release / "extra.html").symlink_to(outside)
        _refuses(release, "symbolic link")

    def test_symlinked_release_directory_is_refused(self, release: Path, tmp_path: Path) -> None:
        alias = tmp_path / "alias"
        alias.symlink_to(release, target_is_directory=True)
        _refuses(alias, "symbolic link")

    def test_oversize_file_is_refused(self, release: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(verify, "MAX_FILE_BYTES", 8)
        _refuses(release, "size bounds")

    def test_oversize_total_is_refused(self, release: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(verify, "MAX_TOTAL_BYTES", 8)
        _refuses(release, "total is outside the size bounds")

    def test_canary_secret_inserted_after_the_build_is_refused(self, release: Path, canary_dotenv: str) -> None:
        index = release / h00.INDEX_FILENAME
        index.write_text(index.read_text(encoding="utf-8") + f"<!-- {canary_dotenv} -->\n", encoding="utf-8")
        with pytest.raises(verify.ReleaseVerificationError) as caught:
            verify.verify_release(release)
        assert "forbidden classes: secrets" in str(caught.value)
        # The message names classes only: the canary must not be echoed into CI logs.
        assert canary_dotenv not in str(caught.value)

    def test_a_digest_canary_is_refused_without_any_dotenv(self, release: Path) -> None:
        payload = release / h00.PAYLOAD_FILENAME
        payload.write_text(payload.read_text(encoding="utf-8").replace('"synthetic"', f'"{DIGEST_CANARY}"'), encoding="utf-8")
        _refuses(release, "forbidden classes: digests")

    def test_absolute_path_canary_is_refused(self, release: Path) -> None:
        index = release / h00.INDEX_FILENAME
        index.write_text(index.read_text(encoding="utf-8") + "<p>/home/operator/work/equity_lake</p>\n", encoding="utf-8")
        _refuses(release, "forbidden classes: paths")

    def test_a_real_slice_fingerprint_is_refused(self, release: Path) -> None:
        payload = release / h00.PAYLOAD_FILENAME
        payload.write_text(payload.read_text(encoding="utf-8").replace("synthetic", "lake_real", 1), encoding="utf-8")
        _refuses(release, "forbidden classes: real_slice")

    def test_a_dropped_needle_class_in_the_report_is_refused(self, release: Path) -> None:
        report_path = release / h00.REPORT_FILENAME
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["negative_scan"]["classes"] = [name for name in report["negative_scan"]["classes"] if name != "secrets"]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        _refuses(release, "current forbidden classes")

    def test_a_report_that_disagrees_on_the_build_instant_is_refused(self, release: Path) -> None:
        report_path = release / h00.REPORT_FILENAME
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["build_utc"] = "2026-11-02T00:00:00+00:00"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        _refuses(release, "disagree on the build instant")

    def test_a_report_without_a_passing_scan_is_refused(self, release: Path) -> None:
        report_path = release / h00.REPORT_FILENAME
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["passed"] = False
        report_path.write_text(json.dumps(report), encoding="utf-8")
        _refuses(release, "passing scan")

    def test_the_cli_exits_non_zero_on_a_violation(self, release: Path) -> None:
        (release / "extra.txt").write_text("x\n", encoding="utf-8")
        with pytest.raises(SystemExit) as caught:
            verify.main(["--dir", str(release)])
        assert caught.value.code == 1

    def test_a_non_utf8_entry_is_refused(self, release: Path) -> None:
        (release / h00.INDEX_FILENAME).write_bytes(b"\xff\xfe\x00binary")
        _refuses(release, "UTF-8")


class TestMutationProof:
    """One-rule mutations: remove the rescan or the set check and the fixture gets through."""

    def test_neutralising_the_rescan_lets_the_canary_through(self, release: Path, canary_dotenv: str, monkeypatch: pytest.MonkeyPatch) -> None:
        index = release / h00.INDEX_FILENAME
        index.write_text(index.read_text(encoding="utf-8") + f"<!-- {canary_dotenv} -->\n", encoding="utf-8")
        monkeypatch.setattr(h00, "scan_contents", lambda contents, *groups: {})
        assert verify.verify_release(release)["files"] == 3

    def test_neutralising_the_allowlist_lets_an_extra_file_through(self, release: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (release / "dashboard-data.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(verify, "ALLOWED_FILENAMES", frozenset({*h00.ARTIFACT_FILENAMES, "dashboard-data.json"}))
        assert verify.verify_release(release)["files"] == 4


class TestBuilderArtifactIsVerifiable:
    """The real composed artifact passes, so the workflow step is not vacuous."""

    def test_a_built_artifact_passes_verification(self, tmp_path: Path) -> None:
        from equity_lake.devtools.seed_demo import seed_demo

        lake = tmp_path / "sandbox" / "data" / "lake"
        seed_demo(years=0.1, tickers=["AAPL"], lake_dir=lake)
        artifact = h00.build_sanitized_page(lake_dir=lake, allow_sandbox_root=True, output_dir=tmp_path / "dist")
        assert verify.verify_release(artifact.directory)["files"] == 3

    def test_a_copied_artifact_with_a_canary_still_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from equity_lake.devtools.seed_demo import seed_demo

        dotenv = tmp_path / ".env"
        dotenv.write_text(f"WANDB_ENTITY={CANARY}\n", encoding="utf-8")
        monkeypatch.setattr(h00, "_dotenv_path", lambda: dotenv)
        lake = tmp_path / "sandbox" / "data" / "lake"
        seed_demo(years=0.1, tickers=["AAPL"], lake_dir=lake)
        artifact = h00.build_sanitized_page(lake_dir=lake, allow_sandbox_root=True, output_dir=tmp_path / "built")
        tampered = tmp_path / "tampered"
        shutil.copytree(artifact.directory, tampered)
        index = tampered / h00.INDEX_FILENAME
        index.write_text(index.read_text(encoding="utf-8").replace("</main>", f"<p>{CANARY}</p></main>"), encoding="utf-8")
        _refuses(tampered, "forbidden classes")


class TestDeploymentReceipt:
    """The private receipt binds a verified bundle to a commit without entering the public three."""

    def test_a_receipt_records_every_file_and_reverifies(self, release: Path, tmp_path: Path) -> None:
        """The receipt hashes the verified bytes, and checking it re-proves the same bundle."""
        receipt = verify.build_receipt(release, commit="a" * 40)
        path = verify.write_receipt(receipt, tmp_path / "release" / "receipt.json", release_dir=release)

        assert {entry.name for entry in receipt.files} == set(h00.ARTIFACT_FILENAMES)
        assert receipt.commit == "a" * 40
        assert receipt.total_bytes == sum(entry.size for entry in receipt.files)
        summary = verify.verify_receipt(release, path, expect_commit="a" * 40)
        assert summary["files"] == 3

    def test_the_retained_bundle_reverifies_only_after_the_receipt_leaves(self, release: Path, tmp_path: Path) -> None:
        """Rehearse the rollback download: one private artifact, four entries.

        `h00-release-bundle` holds the three public files *and* the receipt, so a rollback
        downloads a directory the published allowlist rejects. `h00-publish.yml` moves the
        receipt out before verifying; both halves of that sequence are pinned here.
        """
        receipt = verify.write_receipt(verify.build_receipt(release, commit="a" * 40), tmp_path / "receipt.json", release_dir=release)
        shutil.copy2(receipt, release / "receipt.json")  # the bundle as it is retained and downloaded

        with pytest.raises(verify.ReleaseVerificationError, match="not the allowlist"):
            verify.verify_release(release)

        moved = tmp_path / "rollback-receipt.json"
        shutil.move(str(release / "receipt.json"), moved)
        summary = verify.verify_receipt(release, moved, expect_commit="a" * 40)
        assert summary["files"] == len(h00.ARTIFACT_FILENAMES)

    def test_a_receipt_is_refused_inside_the_public_release_directory(self, release: Path) -> None:
        """Writing the receipt into the artifact would break the three-file allowlist."""
        receipt = verify.build_receipt(release, commit=None)

        with pytest.raises(verify.ReleaseVerificationError, match="may not be written inside the public release"):
            verify.write_receipt(receipt, release / "receipt.json", release_dir=release)

    def test_a_tampered_file_no_longer_matches_the_receipt(self, release: Path, tmp_path: Path) -> None:
        """A changed byte invalidates the recorded digest, so a rollback refuses it."""
        path = verify.write_receipt(verify.build_receipt(release), tmp_path / "receipt.json", release_dir=release)
        (release / h00.INDEX_FILENAME).write_text("<!doctype html><html><body>tampered</body></html>\n", encoding="utf-8")

        with pytest.raises(verify.ReleaseVerificationError, match="does not match the receipt"):
            verify.verify_receipt(release, path)

    def test_a_receipt_bound_to_another_commit_refuses(self, release: Path, tmp_path: Path) -> None:
        """The artifact-to-commit binding is checked when the caller states the commit."""
        path = verify.write_receipt(verify.build_receipt(release, commit="a" * 40), tmp_path / "receipt.json", release_dir=release)

        with pytest.raises(verify.ReleaseVerificationError, match="bound to a different commit"):
            verify.verify_receipt(release, path, expect_commit="c" * 40)

    def test_a_missing_receipt_refuses(self, release: Path, tmp_path: Path) -> None:
        """A rollback without its receipt has no evidence, so it is refused."""
        with pytest.raises(verify.ReleaseVerificationError, match="receipt is missing"):
            verify.verify_receipt(release, tmp_path / "absent.json")

    def test_a_malformed_receipt_refuses(self, release: Path, tmp_path: Path) -> None:
        """A hand-written or truncated receipt is not a receipt."""
        bad = tmp_path / "receipt.json"
        bad.write_text('{"artifact": "h00-sanitized-demo-lake"}', encoding="utf-8")

        with pytest.raises(verify.ReleaseVerificationError, match="not a valid receipt document"):
            verify.verify_receipt(release, bad)

    def test_a_receipt_cannot_attest_bytes_that_fail_the_verifier(self, release: Path, canary_dotenv: str) -> None:
        """Building a receipt re-verifies, so a canary can never be receipted."""
        (release / h00.INDEX_FILENAME).write_text(f"<!doctype html><html><body>{CANARY}</body></html>\n", encoding="utf-8")

        with pytest.raises(verify.ReleaseVerificationError):
            verify.build_receipt(release)


class TestRollbackLineageChains:
    """A rollback's own retained bundle is a valid source for the next rollback."""

    def test_the_second_rollback_verifies_from_the_first_rollbacks_bundle(self, release: Path, tmp_path: Path) -> None:
        """Pin the SECOND hop of the retained lineage, through the real CLI.

        Hop 1 is the build: `dist` is verified, a receipt is written outside it, and the
        three public files plus the receipt are retained as one bundle. A rollback then
        downloads that bundle, moves the receipt out, verifies the bytes and — the fix —
        moves the *original* receipt back, so its own bundle again holds four entries.
        Without that restage the re-uploaded bundle holds three entries and hop 2 here
        fails at `--check-receipt` with "the release receipt is missing".
        """
        commit = "a" * 40

        # Hop 1 — build and retain: receipt written beside the bundle, three files staged.
        bundle1 = tmp_path / "bundle-1"
        bundle1.mkdir()
        written = _cli("--dir", str(release), "--receipt-out", str(bundle1 / RECEIPT_NAME), "--commit", commit)
        assert written.returncode == 0, written.stderr
        _stage_public(release, bundle1)
        receipt1 = (bundle1 / RECEIPT_NAME).read_bytes()
        assert _retained_names(bundle1) == sorted([*h00.ARTIFACT_FILENAMES, RECEIPT_NAME])
        public1 = _digests(bundle1)

        # Rollback #1 — download, move the receipt out, verify, restore it verbatim.
        release1 = tmp_path / "rollback-1" / "release"
        shutil.copytree(bundle1, release1)
        moved1 = tmp_path / "rollback-1" / "rollback-receipt.json"
        shutil.move(str(release1 / RECEIPT_NAME), moved1)
        assert _retained_names(release1) == sorted(h00.ARTIFACT_FILENAMES)
        checked1 = _cli("--dir", str(release1), "--check-receipt", str(moved1))
        assert checked1.returncode == 0, checked1.stderr
        assert "receipt verification passed" in checked1.stdout
        shutil.move(str(moved1), release1 / RECEIPT_NAME)

        # The restage puts the ORIGINAL bytes back: four entries, not a regenerated receipt.
        assert _retained_names(release1) == sorted([*h00.ARTIFACT_FILENAMES, RECEIPT_NAME])
        assert (release1 / RECEIPT_NAME).read_bytes() == receipt1

        # Rollback #1's Pages staging holds exactly the three allowlisted names, no receipt.
        dist1 = tmp_path / "rollback-1" / "dist"
        _stage_public(release1, dist1)
        assert _retained_names(dist1) == sorted(h00.ARTIFACT_FILENAMES)
        assert not (dist1 / RECEIPT_NAME).exists()
        staged1 = _cli("--dir", str(dist1))
        assert staged1.returncode == 0, staged1.stderr
        assert _digests(dist1) == public1

        # Rollback #2 — sourced from rollback #1's re-uploaded bundle: the second hop.
        bundle2 = tmp_path / "bundle-2"
        bundle2.mkdir()
        for name in (*h00.ARTIFACT_FILENAMES, RECEIPT_NAME):
            shutil.copy2(release1 / name, bundle2 / name)
        assert _retained_names(bundle2) == sorted([*h00.ARTIFACT_FILENAMES, RECEIPT_NAME])
        release2 = tmp_path / "rollback-2" / "release"
        shutil.copytree(bundle2, release2)
        moved2 = tmp_path / "rollback-2" / "rollback-receipt.json"
        shutil.move(str(release2 / RECEIPT_NAME), moved2)
        checked2 = _cli("--dir", str(release2), "--check-receipt", str(moved2))
        assert checked2.returncode == 0, checked2.stderr
        assert (moved2).read_bytes() == receipt1
        shutil.move(str(moved2), release2 / RECEIPT_NAME)
        dist2 = tmp_path / "rollback-2" / "dist"
        _stage_public(release2, dist2)
        assert _retained_names(dist2) == sorted(h00.ARTIFACT_FILENAMES)
        staged2 = _cli("--dir", str(dist2))
        assert staged2.returncode == 0, staged2.stderr
        assert _digests(dist2) == public1, "hop 2 must deploy the same verified public bytes"

        # The broken alternative is genuinely refused: a re-uploaded bundle without the
        # receipt leaves the next rollback with no evidence.
        without_receipt = tmp_path / "broken" / "release"
        shutil.copytree(bundle2, without_receipt)
        (without_receipt / RECEIPT_NAME).unlink()
        broken = _cli("--dir", str(without_receipt), "--check-receipt", str(tmp_path / "broken" / "rollback-receipt.json"))
        assert broken.returncode != 0
        assert "the release receipt is missing" in broken.stderr

    def test_the_pages_staging_never_receives_the_restaged_receipt(self, release: Path, tmp_path: Path) -> None:
        """Restoring the receipt to `release/` must not leak into the public `dist/`."""
        (release / RECEIPT_NAME).write_text("{}\n", encoding="utf-8")  # the restaged retained bundle
        public = tmp_path / "pages"
        _stage_public(release, public)

        assert _retained_names(public) == sorted(h00.ARTIFACT_FILENAMES)
        assert verify.verify_release(public)["files"] == len(h00.ARTIFACT_FILENAMES)
        with pytest.raises(verify.ReleaseVerificationError, match="not the allowlist"):
            verify.verify_release(release)
