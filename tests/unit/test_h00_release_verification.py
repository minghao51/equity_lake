"""Pre-upload release verification: allowlist, containment, bounds and a fresh rescan.

Scope: residual plan §2.4 / RS-H01. The builder already fails closed; this module proves
the *last* bytes that would be uploaded, so each refusal below is a guard on the publish
path rather than a property of the build. Every fixture is a real directory on disk.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from equity_lake.dashboard import h00
from equity_lake.devtools import verify_h00_release as verify

CANARY = "CANARY-RELEASE-SECRET-4c1f"
DIGEST_CANARY = "b" * 64


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
