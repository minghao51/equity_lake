"""The recursive Markdown lint gate: nested findings fail and the ratchet only tightens."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from equity_lake.devtools import markdown_lint  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[2]
NESTED_REL = "docs/guides/deep/nested/planted.md"
CLEAN_README = "# Title\n\nClean text.\n"
CLEAN_PAGE = "# Clean\n\nBody text.\n"
# MD026 (no-trailing-punctuation) is enabled by default; one such heading keeps the
# planted file's finding count at exactly 1, so an increment is unambiguous.
PLANTED_PAGE = "# Nested\n\n## Trailing punctuation.\n\nBody.\n"
SECOND_PLANTED_HEADING = "\n## Another trailing punctuation.\n\nMore body.\n"


def _make_tree(root: Path, nested_text: str | None) -> None:
    """Build a repo-shaped tree: a clean README plus a two-level nested docs page."""
    (root / "README.md").write_text(CLEAN_README, encoding="utf-8")
    guides = root / "docs" / "guides"
    (root / "docs" / "guides").mkdir(parents=True)
    (guides / "clean.md").write_text(CLEAN_PAGE, encoding="utf-8")
    nested = root / "docs" / "guides" / "deep" / "nested"
    nested.mkdir(parents=True)
    if nested_text is not None:
        (nested / "planted.md").write_text(nested_text, encoding="utf-8")


def _scan_counts(root: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = markdown_lint.count_findings(markdown_lint.run_scan(root), root)
    return counts


def _make_dry_run(target: str) -> str:
    completed = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout


def test_planted_violation_in_a_nested_docs_subdirectory_fails_the_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Recursion is the gate: a finding two directories below docs/ is counted and fails."""
    _make_tree(tmp_path, PLANTED_PAGE)
    baseline_path = tmp_path / "lint-baseline.json"
    markdown_lint.write_baseline({}, baseline_path)

    observed = _scan_counts(tmp_path)

    assert NESTED_REL in observed, observed
    assert observed[NESTED_REL] == {"MD026": 1}
    assert markdown_lint.check(tmp_path, baseline_path) == 1
    captured = capsys.readouterr()
    assert f"NEW   {NESTED_REL}: MD026 x1 is not in the reviewed baseline" in captured.err


def test_a_file_exceeding_its_recorded_count_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A recorded file may keep its debt but never grow it."""
    _make_tree(tmp_path, PLANTED_PAGE)
    baseline_path = tmp_path / "lint-baseline.json"
    markdown_lint.write_baseline(_scan_counts(tmp_path), baseline_path)
    assert markdown_lint.check(tmp_path, baseline_path) == 0

    planted = tmp_path / NESTED_REL
    planted.write_text(PLANTED_PAGE + SECOND_PLANTED_HEADING, encoding="utf-8")

    assert markdown_lint.check(tmp_path, baseline_path) == 1
    assert f"{NESTED_REL}: MD026 rose 1 -> 2" in capsys.readouterr().err


def test_an_unchanged_tree_matches_its_reviewed_baseline(tmp_path: Path) -> None:
    """The recorded debt passes untouched, including when it is empty."""
    _make_tree(tmp_path, PLANTED_PAGE)
    baseline_path = tmp_path / "lint-baseline.json"
    markdown_lint.write_baseline(_scan_counts(tmp_path), baseline_path)
    assert markdown_lint.check(tmp_path, baseline_path) == 0

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    _make_tree(clean_root, None)
    clean_baseline = tmp_path / "clean-baseline.json"
    markdown_lint.write_baseline({}, clean_baseline)
    assert markdown_lint.check(clean_root, clean_baseline) == 0


def test_a_baseline_row_that_no_longer_reproduces_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Fixing findings without refreshing the baseline must fail, never silently hide cleanup."""
    _make_tree(tmp_path, PLANTED_PAGE)
    baseline_path = tmp_path / "lint-baseline.json"
    markdown_lint.write_baseline({NESTED_REL: {"MD026": 2}}, baseline_path)

    assert markdown_lint.check(tmp_path, baseline_path) == 1
    assert f"STALE {NESTED_REL}: MD026 recorded 2 but now reports 1" in capsys.readouterr().err


def test_update_baseline_is_the_only_refresh_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--check` never rewrites the baseline; only `--update-baseline` may, and only explicitly."""
    _make_tree(tmp_path, PLANTED_PAGE)
    baseline_path = tmp_path / "lint-baseline.json"
    markdown_lint.write_baseline({}, baseline_path)
    recorded_before = baseline_path.read_text(encoding="utf-8")
    monkeypatch.setattr(markdown_lint, "ROOT", tmp_path)
    monkeypatch.setattr(markdown_lint, "BASELINE_PATH", baseline_path)

    with pytest.raises(SystemExit) as failed_check:
        markdown_lint.main(["--check"])

    assert failed_check.value.code == 1
    assert baseline_path.read_text(encoding="utf-8") == recorded_before

    markdown_lint.main(["--update-baseline"])

    refreshed = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert refreshed["files"] == {NESTED_REL: {"MD026": 1}}
    assert refreshed["total"] == 1
    with pytest.raises(SystemExit) as passing_check:
        markdown_lint.main(["--check"])
    assert passing_check.value.code == 0


def test_a_mode_is_required_so_the_gate_never_runs_as_a_silent_noop(tmp_path: Path) -> None:
    """There is no default mode: the CLI must be told to check or to refresh."""
    with pytest.raises(SystemExit) as missing_mode:
        markdown_lint.main([])

    assert missing_mode.value.code == 2


def test_unparsable_scan_output_fails_closed(tmp_path: Path) -> None:
    """Unrecognised output is an error, not a silently dropped finding."""
    with pytest.raises(markdown_lint.LintBaselineError, match="unparsed pymarkdown output line"):
        markdown_lint.count_findings("this line is not a finding", tmp_path)


def test_a_missing_baseline_refuses_to_pass(tmp_path: Path) -> None:
    """No reviewed baseline means no green gate, and the message names the explicit fix."""
    _make_tree(tmp_path, None)

    with pytest.raises(markdown_lint.LintBaselineError, match="--update-baseline"):
        markdown_lint.check(tmp_path, tmp_path / "absent-baseline.json")


def test_changed_file_linting_stays_mandatory_in_quality_ci() -> None:
    """The CI Markdown lint step still lints named files directly; the ratchet did not replace it."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    step = workflow.split("name: Markdown lint", 1)[1].split("name: MkDocs build", 1)[0]

    assert "pymarkdown" in step
    for changed_file in (
        "docs/developer/architecture/data-flow.md",
        "docs/developer/architecture/pipeline-contracts.md",
        "docs/user-guide/ingestion.md",
    ):
        assert changed_file in step


def test_make_targets_wire_the_gate_and_the_explicit_refresh() -> None:
    """`make lint-md` gates on --check; refreshing needs the separate baseline target."""
    gate = _make_dry_run("lint-md")
    assert "uv run python -m equity_lake.devtools.markdown_lint --check" in gate
    assert "--update-baseline" not in gate

    refresh = _make_dry_run("lint-md-baseline")
    assert "uv run python -m equity_lake.devtools.markdown_lint --update-baseline" in refresh


def test_the_committed_baseline_is_recursive_and_the_tree_matches_it() -> None:
    """The shipped gate scans recursively and the working tree reproduces its baseline exactly."""
    assert "-r" in markdown_lint.SCAN_ARGS
    assert markdown_lint.DISABLED_RULES == "md013"
    assert markdown_lint.check() == 0
