"""Contracts for safe, scheduler-driven Compose one-shot commands."""

from pathlib import Path
from typing import Any

import yaml

_COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_COMPOSE_TEXT = _COMPOSE_FILE.read_text(encoding="utf-8")
_SERVICES: dict[str, Any] = yaml.safe_load(_COMPOSE_TEXT)["services"]


def test_daily_pipeline_is_one_shot_for_an_external_scheduler() -> None:
    daily = _SERVICES["daily"]

    assert daily.get("restart", "no") == "no"
    assert "external scheduler" in _COMPOSE_TEXT.lower()
    assert "Compose does not schedule this service" in _COMPOSE_TEXT
    assert "Cron schedule:" not in _COMPOSE_TEXT


def test_query_service_uses_the_supported_all_queries_invocation() -> None:
    assert _SERVICES["query"]["command"] == ["equity", "query"]


def test_sync_s3_defaults_to_the_numbered_medallion_bucket_root() -> None:
    sync = _SERVICES["sync"]
    s3_environment = next(value for value in sync["environment"] if value.startswith("S3_BUCKET="))
    default_bucket = "${S3_BUCKET:-s3://your-bucket}"

    assert s3_environment == f"S3_BUCKET={default_bucket}"
    assert sync["command"][-1] == default_bucket
    assert "/us_equity" not in s3_environment
