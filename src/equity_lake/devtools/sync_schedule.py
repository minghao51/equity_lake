"""Sync the GitHub Pages workflow cron with config/settings.yaml."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "config" / "settings.yaml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "pages.yml"
CRON_PATTERN = re.compile(r'(?m)^(\s+- cron:\s*)"([^"]+)"\s*$')


def load_expected_cron() -> str:
    """Read the canonical cron expression from settings.yaml."""
    with SETTINGS_PATH.open("r", encoding="utf-8") as file_obj:
        data = yaml.safe_load(file_obj) or {}
    return str(data["schedule"]["cron"])


def load_workflow() -> str:
    """Return the current workflow file contents."""
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def replace_cron(workflow_text: str, cron: str) -> str:
    """Replace the first schedule cron entry in the workflow."""
    if not CRON_PATTERN.search(workflow_text):
        raise ValueError("Could not find a cron line in the workflow.")
    return CRON_PATTERN.sub(rf'\1"{cron}"', workflow_text, count=1)


def check_sync() -> bool:
    """Return True when the workflow cron matches settings.yaml."""
    expected = load_expected_cron()
    current_text = load_workflow()
    synced_text = replace_cron(current_text, expected)
    return current_text == synced_text


def write_sync() -> None:
    """Rewrite the workflow file using the cron from settings.yaml."""
    expected = load_expected_cron()
    current_text = load_workflow()
    updated_text = replace_cron(current_text, expected)
    WORKFLOW_PATH.write_text(updated_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Keep the GitHub Pages workflow cron in sync with settings.yaml")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the workflow cron is out of sync",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    if args.check:
        if check_sync():
            return
        print(
            "Workflow cron is out of sync with config/settings.yaml.",
            file=sys.stderr,
        )
        sys.exit(1)

    write_sync()


if __name__ == "__main__":
    main()
