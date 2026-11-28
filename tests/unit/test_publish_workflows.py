"""Publish-path workflow contracts: one manual, SHA-pinned publisher and nothing else.

Scope: residual plan RS-H01 (§2.1/§2.4). These are structural assertions over the workflow
files, which is exactly what they can prove and no more: that at most one executable
deployment step exists, that it cannot be triggered by a merge or a cron, that it is
pinned, and that the legacy deployers kept no Pages-write permission, artifact upload or
`sync_schedule --check` step. They cannot prove that the `github-pages` environment has
required reviewers — that is a platform setting and must be verified in the repository
settings, not read out of YAML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PUBLISHER_NAME = "h00-publish.yml"
PUBLISHER = WORKFLOW_DIR / PUBLISHER_NAME
PAGES_CONCURRENCY_GROUP = "github-pages"

_SHA_PIN = re.compile(r"^[A-Za-z0-9_.\-/]+@[0-9a-f]{40}$")
_USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)\s*(?:#\s*(?P<comment>.*?))?\s*$")


def _workflow_paths() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def _workflow_texts() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in _workflow_paths()}


def _code(text: str) -> str:
    """Workflow source without comments.

    A comment may *describe* a retired deployer or the protected environment; only
    uncommented lines can execute, so reference scans use this form.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  #", 1)[0] if "  #" in line else line)
    return "\n".join(lines)


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} is not a YAML mapping"
    return loaded


def _triggers(document: dict[str, Any]) -> set[str]:
    """Event names from the `on:` key (PyYAML resolves bare `on` to boolean True)."""
    raw: dict[Any, Any] = dict(document)
    trigger = raw.get("on", raw.get(True))
    if isinstance(trigger, str):
        return {trigger}
    if isinstance(trigger, list):
        return {str(item) for item in trigger}
    return {str(key) for key in (trigger or {})}


def _uses_references(text: str) -> list[tuple[str, str | None]]:
    return [(match.group("ref"), match.group("comment")) for match in map(_USES_LINE.match, text.splitlines()) if match]


def _executable_paths() -> list[Path]:
    """Files that could actually run a deployment: workflows, source, root build files.

    Documentation is excluded on purpose — prose and stage records may name a retired
    step without being able to execute it.
    """
    paths = [path for path in (ROOT / ".github").rglob("*") if path.is_file()]
    paths += list((ROOT / "src").rglob("*.py"))
    paths += [ROOT / name for name in ("Makefile", "Dockerfile", "docker-compose.yml", "pyproject.toml") if (ROOT / name).is_file()]
    return paths


class TestSinglePublicationPath:
    """One deployer, manual only, SHA-pinned, sharing one Pages concurrency group."""

    def test_every_workflow_parses_as_yaml(self) -> None:
        paths = _workflow_paths()
        assert paths, "no workflow files found"
        for path in paths:
            document = _load(path)
            assert "jobs" in document, f"{path.name} declares no jobs"

    def test_exactly_one_deploy_pages_reference_survives_repo_wide(self) -> None:
        offenders = {
            path.name: _code(path.read_text(encoding="utf-8", errors="ignore")).count("deploy-pages")
            for path in _executable_paths()
            if "deploy-pages" in _code(path.read_text(encoding="utf-8", errors="ignore"))
        }
        assert offenders == {PUBLISHER_NAME: 1}, f"expected one deploy-pages step in {PUBLISHER_NAME} only, found {offenders}"

    def test_publisher_is_workflow_dispatch_only(self) -> None:
        triggers = _triggers(_load(PUBLISHER))
        assert triggers == {"workflow_dispatch"}, f"the publisher must be manual only, found {sorted(triggers)}"

    def test_no_deploying_workflow_has_a_push_or_schedule_trigger(self) -> None:
        for name, text in _workflow_texts().items():
            if "deploy-pages" not in _code(text):
                continue
            triggers = _triggers(_load(WORKFLOW_DIR / name))
            assert "push" not in triggers, f"{name} can publish on a merge"
            assert "schedule" not in triggers, f"{name} can publish on a cron"

    def test_every_publisher_action_is_pinned_to_a_full_sha_with_a_version_comment(self) -> None:
        references = _uses_references(PUBLISHER.read_text(encoding="utf-8"))
        assert len(references) >= 4, "the publisher should pin checkout, python, uv, the artifact upload and the deploy"
        for reference, comment in references:
            assert _SHA_PIN.match(reference), f"{reference!r} is not pinned to a 40-hex commit SHA"
            assert comment, f"{reference!r} has no resolved version comment"

    def test_one_shared_pages_concurrency_group_that_never_cancels(self) -> None:
        document = _load(PUBLISHER)
        concurrency = document["concurrency"]
        assert concurrency["group"] == PAGES_CONCURRENCY_GROUP
        assert concurrency["cancel-in-progress"] is False
        for name, other in _workflow_texts().items():
            if name == PUBLISHER_NAME:
                continue
            assert PAGES_CONCURRENCY_GROUP not in _code(other), f"{name} joins the Pages concurrency group without publishing"

    def test_only_the_publisher_deploy_job_holds_pages_write(self) -> None:
        document = _load(PUBLISHER)
        assert document["permissions"] == {"contents": "read"}
        for job_name, job in document["jobs"].items():
            permissions = job.get("permissions")
            if job_name == "deploy":
                assert permissions == {"pages": "write", "id-token": "write"}
            else:
                # The build job may read this repository's artifacts so the rollback path
                # can download a retained bundle; it must never hold deployment rights.
                assert permissions == {"contents": "read", "actions": "read"}, f"job {job_name} scopes permissions it does not need"
                assert "pages: write" not in permissions
                assert "id-token: write" not in permissions
        assert "environment" in document["jobs"]["deploy"], "the deploy job must declare the protected environment"
        assert document["jobs"]["deploy"]["environment"]["name"] == PAGES_CONCURRENCY_GROUP
        for name, text in _workflow_texts().items():
            if name == PUBLISHER_NAME:
                continue
            assert "pages: write" not in _code(text), f"{name} still requests Pages write permission"
            assert "id-token: write" not in _code(text), f"{name} still requests an OIDC token"


class TestPublisherBuild:
    """The publisher builds only the sanitized three-file artifact and verifies it first."""

    def test_it_builds_only_the_h00_artifact(self) -> None:
        text = _code(PUBLISHER.read_text(encoding="utf-8"))
        runs = [step.get("run", "") for job in _load(PUBLISHER)["jobs"].values() for step in job["steps"]]
        assert any("equity dashboard h00-build" in run for run in runs)
        for forbidden in ("equity dashboard build", "equity monitor", "catalog", "site/"):
            assert forbidden not in text, f"the publisher still builds {forbidden!r}"

    def test_the_allowlist_verification_runs_between_build_and_upload(self) -> None:
        steps = _load(PUBLISHER)["jobs"]["build"]["steps"]
        runs = [str(step.get("run", "")) for step in steps]
        build_index = next(index for index, run in enumerate(runs) if "h00-build" in run)
        verify_index = next(index for index, run in enumerate(runs) if "verify_h00_release --dir dist" in run)
        upload_index = next(index for index, step in enumerate(steps) if "upload-pages-artifact" in str(step.get("uses", "")))
        assert build_index < verify_index < upload_index, "verification must run on the bytes that are about to be uploaded"

    def test_it_uploads_only_the_verified_directory_as_the_retained_bundle(self) -> None:
        """The uploaded public bytes are the verified ones, retained for rollback (90 days)."""
        upload = next(step for step in _load(PUBLISHER)["jobs"]["build"]["steps"] if "upload-pages-artifact" in str(step.get("uses", "")))
        assert upload["with"]["path"] == "dist"
        assert int(upload["with"]["retention-days"]) == 90


class TestLegacyDeployersRetired:
    """Neither legacy deployer can publish, and neither retains the broken sync check."""

    def test_the_dashboard_pages_workflow_is_gone(self) -> None:
        assert not (WORKFLOW_DIR / "pages.yml").exists()

    def test_the_catalog_site_workflow_no_longer_deploys(self) -> None:
        catalog = WORKFLOW_DIR / "catalog-site-build.yml"
        assert catalog.is_file()
        text = _code(catalog.read_text(encoding="utf-8"))
        document = _load(catalog)
        assert set(document["jobs"]) == {"build"}
        assert document["permissions"] == {"contents": "read"}
        assert "upload-pages-artifact" not in text
        assert "deploy-pages" not in text

    def test_the_catalog_drift_check_is_untouched(self) -> None:
        catalog_check = _load(WORKFLOW_DIR / "catalog-check.yml")
        assert "catalog-generate" in catalog_check["jobs"]["catalog-freshness"]["steps"][-2]["run"]
        assert "catalog-generate" in (WORKFLOW_DIR / "catalog-check.yml").read_text(encoding="utf-8")

    def test_the_sync_schedule_check_is_gone_from_every_workflow(self) -> None:
        for name, text in _workflow_texts().items():
            assert "sync_schedule" not in _code(text), f"{name} still runs the retired schedule-sync check"

    def test_the_retired_schedule_module_and_its_test_are_deleted(self) -> None:
        assert not (ROOT / "src" / "equity_lake" / "devtools" / "sync_schedule.py").exists()
        assert not (ROOT / "tests" / "unit" / "test_schedule_sync.py").exists()


class TestDocsLintGate:
    """The markdown gate keeps the mandatory changed-file lint and adds the recursive target."""

    def test_quality_ci_runs_the_repository_markdown_target(self) -> None:
        text = (WORKFLOW_DIR / "quality.yml").read_text(encoding="utf-8")
        assert "make lint-md" in text
        # The changed-file gate stays: the recursive ratchet is added, not swapped in.
        lint_slice = text.split("name: Markdown lint", maxsplit=1)[1].split("name: MkDocs build", maxsplit=1)[0]
        assert "pymarkdown" in lint_slice
        for page in (
            "docs/developer/architecture/data-flow.md",
            "docs/developer/architecture/pipeline-contracts.md",
            "docs/user-guide/ingestion.md",
        ):
            assert page in lint_slice


@pytest.mark.parametrize("workflow", [path.name for path in _workflow_paths()])
def test_workflow_file_is_non_empty_and_has_a_name(workflow: str) -> None:
    document = _load(WORKFLOW_DIR / workflow)
    assert isinstance(document.get("name"), str) and document["name"].strip()


class TestReleaseReceiptAndRollback:
    """The publisher keeps a private receipt and a retained bundle, and can roll back to one."""

    def test_the_bundle_and_the_pages_artifact_are_retained_for_ninety_days(self) -> None:
        text = _code(PUBLISHER.read_text(encoding="utf-8"))

        assert text.count("retention-days: 90") == 2, text
        assert "retention-days: 3" not in text

    def test_a_private_receipt_is_written_outside_the_public_three_files(self) -> None:
        text = _code(PUBLISHER.read_text(encoding="utf-8"))

        assert "--receipt-out release/receipt.json" in text
        assert "--receipt-out dist/" not in text
        assert "name: h00-release-bundle" in text

    def test_rollback_downloads_and_reverifies_a_previous_bundle(self) -> None:
        text = _code(PUBLISHER.read_text(encoding="utf-8"))

        assert "rollback_run_id" in text
        assert "gh run download" in text
        # The retained bundle is ONE private artifact holding the three public files and
        # the receipt, and the verifier refuses a fourth entry in the allowlisted
        # directory. So the receipt has to leave that directory before it is checked:
        # the previous assertion here pinned `--check-receipt release/receipt.json`, which
        # is exactly the form that made every rollback refuse with "unexpected entries: 1".
        assert "mv release/receipt.json rollback-receipt.json" in text, text
        assert "--check-receipt rollback-receipt.json" in text
        assert "--check-receipt release/receipt.json" not in text

    def test_a_rollback_restores_its_receipt_so_the_lineage_chains(self) -> None:
        """The rollback path re-uploads a four-entry bundle, guarded before it goes out.

        `release/` is uploaded as the next `h00-release-bundle` on both paths, so a rollback
        that left the receipt outside would retain a three-entry bundle and the following
        rollback would have no receipt to check. The restage moves the original receipt
        back and a fail-closed shell guard proves the four-entry layout first.
        """
        steps = _load(PUBLISHER)["jobs"]["build"]["steps"]
        runs = [str(step.get("run", "")) for step in steps]
        checked = next(index for index, run in enumerate(runs) if "--check-receipt rollback-receipt.json" in run)
        restage = next(index for index, run in enumerate(runs) if "mv rollback-receipt.json release/receipt.json" in run)
        pages_stage = next(index for index, run in enumerate(runs) if "cp release/index.html" in run)
        retained = next(index for index, step in enumerate(steps) if "upload-artifact" in str(step.get("uses", "")))

        assert checked < restage < pages_stage < retained, "the restage belongs on the rollback path, before both uploads"
        assert steps[restage]["if"] == "inputs.rollback_run_id != ''"
        assert steps[pages_stage]["if"] == "inputs.rollback_run_id != ''"

        run = str(steps[restage]["run"])
        # The receipt is the original one: never rewritten, never re-checked here.
        assert "--receipt-out" not in run
        assert "--check-receipt" not in run
        # The retained layout guard is shell-only and fails closed on the four exact names.
        assert "set -euo pipefail" in run
        assert "index.html payload.json receipt.json sanitization-report.json" in run
        assert "! -type f" in run
        assert run.count("exit 1") >= 2
        assert steps[retained]["with"]["path"] == "release"

    def test_a_dispatch_input_is_never_interpolated_into_a_shell_command(self) -> None:
        """A workflow_dispatch input is string data; it reaches the shell through env only."""
        text = _code(PUBLISHER.read_text(encoding="utf-8"))

        assert "ROLLBACK_RUN_ID: ${{ inputs.rollback_run_id }}" in text
        assert '"$ROLLBACK_RUN_ID"' in text
        assert '"${{ inputs.rollback_run_id }}"' not in text

    def test_the_build_job_does_not_persist_checkout_credentials(self) -> None:
        """Artifact-uploading jobs must not keep a token in the workspace."""
        text = _code(PUBLISHER.read_text(encoding="utf-8"))

        assert "persist-credentials: false" in text


class TestPublisherSeedsItsOwnLake:
    """The build reads a canonical lake a fresh checkout does not have, so the job seeds it."""

    def test_the_lake_is_seeded_before_the_build_runs(self) -> None:
        steps = _load(PUBLISHER)["jobs"]["build"]["steps"]
        names = [str(step.get("name", "")) for step in steps]

        seed = next(index for index, name in enumerate(names) if "Seed the disposable synthetic lake" in name)
        place = next(index for index, name in enumerate(names) if "Place the seeded lake" in name)
        build = next(index for index, name in enumerate(names) if "Build the sanitized H00 artifact" in name)

        assert seed < place < build, "the lake must be seeded and placed before anything builds"

    def test_the_synthetic_window_is_pinned_rather_than_computed(self) -> None:
        """A computed date would make the seeded lake irreproducible run to run."""
        document = _load(PUBLISHER)
        assert document["env"]["FROZEN_LAKE_END_DATE"] == "2026-06-30"
        seed = next(step for step in document["jobs"]["build"]["steps"] if "Seed the disposable synthetic lake" in str(step.get("name", "")))
        assert '--end-date "$FROZEN_LAKE_END_DATE"' in seed["run"]
        assert "date -u" not in seed["run"] and "$(date" not in seed["run"]

    def test_the_seeded_path_reaches_the_shell_through_the_environment(self) -> None:
        """A step output is still string data; the path is exported, not interpolated."""
        document = _load(PUBLISHER)
        seed = next(step for step in document["jobs"]["build"]["steps"] if "Seed the disposable synthetic lake" in str(step.get("name", "")))
        place = next(step for step in document["jobs"]["build"]["steps"] if "Place the seeded lake" in str(step.get("name", "")))

        assert 'echo "LAKE_DIR=' in seed["run"]
        assert '"$LAKE_DIR/."' in place["run"]
        assert "${{ steps." not in place["run"], "the seeded path must not be interpolated into the shell"

    def test_a_rollback_run_skips_seeding_and_building(self) -> None:
        document = _load(PUBLISHER)
        for name in (
            "Seed the disposable synthetic lake",
            "Place the seeded lake where the builder reads it",
            "Build the sanitized H00 artifact",
        ):
            step = next(step for step in document["jobs"]["build"]["steps"] if str(step.get("name", "")) == name)
            assert step["if"] == "inputs.rollback_run_id == ''", name
