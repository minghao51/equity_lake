"""Tests for S3Syncer subprocess timeout configuration.

The sync subprocess wait used to be hard-coded to 600s, killing large syncs.
It is now configurable via ``EQUITY_S3_SYNC__TIMEOUT_SECONDS`` (nested
``S3SyncSettings``) with a 600s default, overridable per constructor call.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from equity_lake.core.config import clear_settings_cache
from equity_lake.storage.s3_sync import S3RetryableError, S3Syncer


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def _syncer(**kwargs: object) -> S3Syncer:
    """Build an S3Syncer without tool auto-detection (no subprocess probes)."""
    kwargs.setdefault("tool", "aws")
    return S3Syncer(bucket="s3://bucket/prefix/", target_dir=Path("/tmp/does-not-matter"), **kwargs)  # type: ignore[arg-type]


class TestS3SyncTimeout:
    def test_default_comes_from_settings(self) -> None:
        assert _syncer().timeout_seconds == 600

    def test_explicit_constructor_override_wins(self) -> None:
        assert _syncer(timeout_seconds=42).timeout_seconds == 42

    def test_env_override_via_nested_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EQUITY_S3_SYNC__TIMEOUT_SECONDS", "1800")
        assert _syncer().timeout_seconds == 1800

    def test_sync_waits_for_configured_timeout(self) -> None:
        syncer = _syncer(timeout_seconds=42)
        process = MagicMock()
        process.stdout = iter([])  # no output lines
        process.wait.side_effect = subprocess.TimeoutExpired(cmd="s5cmd", timeout=42)

        # Bypass tenacity retries so the test stays fast; assert the first
        # wait() call received the configured timeout, not a hard-coded 600.
        unwrapped = S3Syncer.sync_with_s5cmd.__wrapped__
        with patch("equity_lake.storage.s3_sync.subprocess.Popen", return_value=process), pytest.raises(S3RetryableError):
            unwrapped(syncer)

        assert process.wait.call_args_list[0] == call(timeout=42)

    def test_aws_cli_sync_passes_configured_timeout(self) -> None:
        """sync_with_aws_cli must honor timeout_seconds; its TimeoutExpired mapping is only reachable then."""
        syncer = _syncer(timeout_seconds=42)

        unwrapped = S3Syncer.sync_with_aws_cli.__wrapped__
        with (
            patch(
                "equity_lake.storage.s3_sync.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="aws", timeout=42),
            ) as mock_run,
            pytest.raises(S3RetryableError),
        ):
            unwrapped(syncer)

        assert mock_run.call_args.kwargs["timeout"] == 42
