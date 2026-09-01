"""Tests for the simplified application settings."""

from pathlib import Path

import pytest

from equity_lake.core.config import clear_settings_cache, load_settings


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
ingestion:
  retry_attempts: 5
schedule:
  cron: "0 6 * * 1-5"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(settings_file)

    assert settings.project.name == "test-lake"
    assert settings.ingestion.retry_attempts == 5
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


# =============================================================================
# ADR-0010: default_markets vocabulary
# =============================================================================


class TestDefaultMarketsNormalization:
    def test_default_is_canonical_long_keys(self) -> None:
        from equity_lake.core.settings import IngestionSettings

        assert IngestionSettings().default_markets == ["us_equity", "cn_ashare", "hk_sg_equity"]

    def test_short_aliases_normalize_to_long_keys(self) -> None:
        """Existing YAML/env input with short keys keeps loading (boundary alias)."""
        from equity_lake.core.settings import IngestionSettings

        settings = IngestionSettings(default_markets=["us", "cn", "hk_sg", "macro", "us_news"])
        assert settings.default_markets == ["us_equity", "cn_ashare", "hk_sg_equity", "macro", "us_news"]

    def test_unknown_market_is_rejected(self) -> None:
        """A config typo must fail loudly, not become a silent pipeline no-op."""
        from pydantic import ValidationError

        from equity_lake.core.settings import IngestionSettings

        with pytest.raises(ValidationError, match="Unknown market"):
            IngestionSettings(default_markets=["uss"])
