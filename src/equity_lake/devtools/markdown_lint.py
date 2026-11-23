"""Recursive Markdown lint gate with a reviewed baseline ratchet.

Module CLI entrypoint (``python -m``, invoked by ``make lint-md``):
uses plain ``print``/``stderr`` output instead of structlog by devtool convention.

The scan is recursive (``-r``) so ``docs/`` subdirectories are covered; the
reviewed baseline (``config/markdown-lint-baseline.json``) records the
preexisting findings as ``file -> rule -> count``. ``--check`` fails on any
finding that is absent from the baseline or that raises a recorded count, and
also fails when a recorded finding no longer reproduces, so the baseline can
only ratchet down through an explicit ``--update-baseline``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class DisabledRule(BaseModel):
    """One rule this gate does not enforce, recorded so the gap is auditable.

    A blanket disable is invisible unless it is written down: recording the rule, why it
    is off, and what the measured legacy cost is keeps a reader from mistaking the gate
    for full coverage. Adding a second entry needs its own justification and cost.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    justification: str = Field(min_length=1)
    measured_legacy_findings: int = Field(ge=0)

    @field_validator("rule")
    @classmethod
    def _rule_is_a_markdownlint_id(cls, rule: str) -> str:
        if re.fullmatch(r"MD\d{3}", rule) is None:
            raise ValueError(f"disabled rule {rule!r} is not a markdownlint rule id")
        return rule


class LintBaseline(BaseModel):
    """The reviewed baseline artifact: ``file -> rule -> count`` plus the recorded gaps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_by: str = Field(min_length=1)
    scan: list[str] = Field(min_length=1)
    total: int = Field(ge=0)
    disabled_rules: list[DisabledRule]
    files: dict[str, dict[str, int]]

    @field_validator("files")
    @classmethod
    def _counts_are_positive(cls, files: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        for file_path, rules in files.items():
            if not file_path or not rules:
                raise ValueError(f"baseline entry {file_path!r} is empty")
            for rule, count in rules.items():
                if re.fullmatch(r"MD\d{3}", rule) is None:
                    raise ValueError(f"baseline entry {file_path!r} has a non-markdownlint rule {rule!r}")
                if count < 1:
                    raise ValueError(f"baseline count {file_path!r}/{rule!r} must be at least 1")
        return files

    @model_validator(mode="after")
    def _totals_agree(self) -> LintBaseline:
        computed = sum(sum(rules.values()) for rules in self.files.values())
        if computed != self.total:
            raise ValueError(f"baseline total {self.total} does not equal the recorded findings ({computed})")
        return self


ROOT: Final = Path(__file__).resolve().parents[3]
BASELINE_PATH: Final = ROOT / "config" / "markdown-lint-baseline.json"
SCAN_TARGETS: Final = ("README.md", "docs")
# MD013 (line length) stays excluded exactly as the pre-ratchet Makefile target did; no
# other rule is disabled here and none is disabled by the baseline. The exclusion is a
# RECORDED entry in the baseline rather than an implied one, so the gate's coverage is
# auditable, and the measured cost of closing it is written beside it.
DISABLED_RULE_RECORDS: Final[tuple[DisabledRule, ...]] = (
    DisabledRule(
        rule="MD013",
        justification=(
            "line length was already excluded by the pre-ratchet Makefile target; re-enabling it is a documentation "
            "migration across the existing docs, not a lint-gate change, and it is not hidden by this ratchet"
        ),
        measured_legacy_findings=5250,
    ),
)
DISABLED_RULES: Final = ",".join(record.rule.lower() for record in DISABLED_RULE_RECORDS)
# `-r` is mandatory: without it pymarkdown lints only the named files and never
# descends into `docs/`, which is how the previous gate covered almost nothing.
SCAN_ARGS: Final = ("-d", DISABLED_RULES, "scan", "-r", *SCAN_TARGETS)
GENERATED_BY: Final = "uv run python -m equity_lake.devtools.markdown_lint --update-baseline"
FINDING_PATTERN: Final = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<column>\d+): (?P<rule>MD\d{3}): ")
ACCEPTED_SCAN_EXIT_CODES: Final = (0, 1)


class LintBaselineError(RuntimeError):
    """Raised when the scan output or a baseline file cannot be trusted."""


@dataclass(frozen=True)
class Deviation:
    """One ratchet violation: a finding that grew, or a baseline row that went stale."""

    path: str
    rule: str
    observed: int
    recorded: int

    def describe_growth(self) -> str:
        """Describe a finding that is missing from the baseline or exceeded it."""
        if self.recorded == 0:
            return f"{self.path}: {self.rule} x{self.observed} is not in the reviewed baseline"
        return f"{self.path}: {self.rule} rose {self.recorded} -> {self.observed}"

    def describe_stale(self) -> str:
        """Describe a recorded finding that no longer reproduces."""
        return f"{self.path}: {self.rule} recorded {self.recorded} but now reports {self.observed}"


def run_scan(root: Path) -> str:
    """Run the recursive pymarkdown scan under ``root`` and return its finding lines."""
    completed = subprocess.run(
        [sys.executable, "-m", "pymarkdown", *SCAN_ARGS],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in ACCEPTED_SCAN_EXIT_CODES:
        raise LintBaselineError(f"pymarkdown exited {completed.returncode}: {completed.stderr.strip()}")
    return completed.stdout


def _repo_relative(reported: str, root: Path) -> str:
    """Normalise a pymarkdown-reported path to a repo-relative POSIX path."""
    path = Path(reported)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise LintBaselineError(f"finding outside the scan root {root}: {reported}") from error


def count_findings(scan_output: str, root: Path) -> dict[str, dict[str, int]]:
    """Group finding lines into ``file -> rule -> count``.

    An unparsed non-empty line raises: silently dropping output could hide a
    finding, which is the failure mode the ratchet exists to prevent.
    """
    counts: dict[str, dict[str, int]] = {}
    for raw_line in scan_output.splitlines():
        if not raw_line.strip():
            continue
        match = FINDING_PATTERN.match(raw_line)
        if match is None:
            raise LintBaselineError(f"unparsed pymarkdown output line: {raw_line!r}")
        rule_counts = counts.setdefault(_repo_relative(match.group("file"), root), {})
        rule = match.group("rule")
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
    return counts


def _require_recorded_disabled_rules(recorded: Sequence[DisabledRule], path: Path) -> None:
    """The baseline must record exactly the disables this gate actually applies."""
    declared = {record.rule for record in DISABLED_RULE_RECORDS}
    on_file = {record.rule for record in recorded}
    if declared != on_file:
        raise LintBaselineError(
            f"{path}: recorded disabled rules {sorted(on_file)} do not match the gate's {sorted(declared)}; "
            "a disable must be recorded deliberately, never implied"
        )


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    """Read the reviewed baseline through its model, refusing a malformed or unrecorded-gap shape."""
    if not path.is_file():
        raise LintBaselineError(f"no reviewed baseline at {path}; create one explicitly with --update-baseline")
    try:
        baseline = LintBaseline.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise LintBaselineError(f"{path}: {error}") from error
    _require_recorded_disabled_rules(baseline.disabled_rules, path)
    return {file_path: dict(rules) for file_path, rules in baseline.files.items()}


def write_baseline(observed: dict[str, dict[str, int]], path: Path = BASELINE_PATH) -> None:
    """Rewrite the reviewed baseline from one scan; only ``--update-baseline`` calls this."""
    baseline = LintBaseline(
        generated_by=GENERATED_BY,
        scan=list(SCAN_ARGS),
        total=sum(sum(rules.values()) for rules in observed.values()),
        disabled_rules=list(DISABLED_RULE_RECORDS),
        files={file_path: dict(sorted(rules.items())) for file_path, rules in sorted(observed.items())},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def growths(observed: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> list[Deviation]:
    """Findings that are new relative to the baseline or exceed their recorded count."""
    found: list[Deviation] = []
    for file_path, rules in sorted(observed.items()):
        recorded_rules = baseline.get(file_path, {})
        for rule, count in sorted(rules.items()):
            recorded = recorded_rules.get(rule, 0)
            if count > recorded:
                found.append(Deviation(file_path, rule, count, recorded))
    return found


def stale_rows(observed: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> list[Deviation]:
    """Baseline rows whose finding count dropped: the ratchet must be tightened explicitly."""
    found: list[Deviation] = []
    for file_path, rules in sorted(baseline.items()):
        observed_rules = observed.get(file_path, {})
        for rule, recorded in sorted(rules.items()):
            count = observed_rules.get(rule, 0)
            if count < recorded:
                found.append(Deviation(file_path, rule, count, recorded))
    return found


def check(root: Path = ROOT, baseline_path: Path = BASELINE_PATH) -> int:
    """Return the gate exit code: 0 when the tree matches the reviewed baseline, else 1."""
    observed = count_findings(run_scan(root), root)
    baseline = load_baseline(baseline_path)
    growth = growths(observed, baseline)
    stale = stale_rows(observed, baseline)
    total = sum(sum(rules.values()) for rules in observed.values())
    if not growth and not stale:
        print(f"Markdown lint OK: {total} preexisting findings in {len(observed)} files match {baseline_path.name}; 0 new or expanded.")
        return 0
    for deviation in growth:
        print(f"NEW   {deviation.describe_growth()}", file=sys.stderr)
    for deviation in stale:
        print(f"STALE {deviation.describe_stale()}", file=sys.stderr)
    print(
        f"Markdown lint failed: {len(growth)} new/expanded and {len(stale)} stale baseline row(s) "
        f"against {baseline_path.name} ({total} findings in {len(observed)} files).",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Run the recursive Markdown lint scan and enforce the reviewed baseline ratchet")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero on any finding absent from, expanded beyond, or stale in the reviewed baseline",
    )
    mode.add_argument(
        "--update-baseline",
        action="store_true",
        help="Explicitly rewrite the reviewed baseline from the current scan (review the diff before committing)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        if args.update_baseline:
            observed = count_findings(run_scan(ROOT), ROOT)
            write_baseline(observed, BASELINE_PATH)
            total = sum(sum(rules.values()) for rules in observed.values())
            print(f"Wrote {total} findings in {len(observed)} files to {BASELINE_PATH}.")
            return
        sys.exit(check(ROOT, BASELINE_PATH))
    except LintBaselineError as error:
        print(f"Markdown lint error: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
