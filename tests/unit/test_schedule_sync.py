"""Tests for workflow schedule sync."""

from equity_lake.devtools.sync_schedule import replace_cron


def test_replace_cron_updates_first_schedule_entry() -> None:
    workflow = """
on:
  schedule:
    - cron: "0 1 * * 1-5"
""".strip()

    updated = replace_cron(workflow, "30 2 * * 1-5")

    assert '"30 2 * * 1-5"' in updated
    assert '"0 1 * * 1-5"' not in updated
