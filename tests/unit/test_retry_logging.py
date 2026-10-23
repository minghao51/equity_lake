"""Retry events include bounded diagnostics and never exception text."""

from unittest.mock import MagicMock

import httpx

from equity_lake.core.retry import build_retry_decorator  # type: ignore[import-untyped]


def test_retry_logs_type_attempt_and_wait_without_exception_message() -> None:
    secret = "synthetic-provider-token-unique"
    logger = MagicMock()
    calls = 0

    def fail_once() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(f"provider rejected {secret}")
        return "ok"

    retrying_call = build_retry_decorator(
        attempts=2,
        wait_multiplier=0,
        wait_min=0,
        retry_on=ValueError,
        log=logger,
    )(fail_once)
    assert retrying_call() == "ok"
    logger.warning.assert_called_once()
    args, fields = logger.warning.call_args
    assert args == ("retrying",)
    assert set(fields) == {"error_type", "status_code", "attempt", "wait_seconds"}
    assert fields == {"error_type": "ValueError", "status_code": None, "attempt": 1, "wait_seconds": 0.0}
    assert secret not in repr(logger.warning.call_args)


def test_retry_logs_http_status_without_credential_bearing_url() -> None:
    secret = "synthetic-retry-token-unique"
    request = httpx.Request("GET", f"https://example.test/data?token={secret}")
    response = httpx.Response(503, request=request)
    provider_error = httpx.HTTPStatusError("request failed", request=request, response=response)
    logger = MagicMock()
    calls = 0

    def fail_once() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise provider_error
        return "ok"

    retrying_call = build_retry_decorator(
        attempts=2,
        wait_multiplier=0,
        wait_min=0,
        retry_on=httpx.HTTPStatusError,
        log=logger,
    )(fail_once)
    assert retrying_call() == "ok"
    fields = logger.warning.call_args.kwargs
    assert fields["error_type"] == "HTTPStatusError"
    assert fields["status_code"] == 503
    assert fields["attempt"] == 1
    assert fields["wait_seconds"] == 0.0
    assert secret not in repr(logger.warning.call_args)
