"""Tests for the simplified application settings."""

from pathlib import Path

import pytest

from equity_lake.config.settings import clear_settings_cache, load_settings


@pytest.fixture(autouse=True)
def reset_settings_cache():
    """Avoid cache leakage across tests."""
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_load_settings_from_yaml(tmp_path: Path) -> None:
    """Settings should load from a single YAML file."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        """
project:
  name: test-lake
storage:
  data_dir: runtime-data
schedule:
  cron: "0 6 * * 1-5"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(settings_file)

    assert settings.project.name == "test-lake"
    assert settings.storage.data_dir == "runtime-data"
    assert settings.schedule.cron == "0 6 * * 1-5"


def test_invalid_cron_is_rejected(tmp_path: Path) -> None:
    """Bad cron expressions should fail fast."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        """
schedule:
  cron: "not-a-cron"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_settings(settings_file)
