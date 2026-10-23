"""Shared tenacity retry decorator factory.

Consolidates the three parallel copies of the project's standard retry shape
(``sources/base.py``, ``sources/macro.py``, ``ingestion/llm_base.py``) into one
factory. Each call site preserves its original parameters exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

WrappedFn = TypeVar("WrappedFn")


def _log_safe_retry(log: Any, retry_state: Any) -> None:
    """Log retry mechanics without exception text, URLs, or provider payloads."""
    outcome = retry_state.outcome
    exception = outcome.exception() if outcome is not None else None
    cause = getattr(exception, "__cause__", None)
    status_code = None
    for candidate in (exception, cause):
        if candidate is None:
            continue
        response = getattr(candidate, "response", None)
        status_code = getattr(candidate, "status_code", None)
        if status_code is None:
            status_code = getattr(response, "status_code", None)
        if status_code is not None:
            break
    if not isinstance(status_code, int) or isinstance(status_code, bool):
        status_code = None

    log.warning(
        "retrying",
        error_type=type(exception).__name__ if exception is not None else "unknown",
        status_code=status_code,
        attempt=retry_state.attempt_number,
        wait_seconds=retry_state.upcoming_sleep,
    )


def build_retry_decorator(
    *,
    attempts: int,
    wait_multiplier: float,
    wait_min: float,
    wait_max: float = 30.0,
    retry_on: type[BaseException] | tuple[type[BaseException], ...] | None = None,
    log: Any = None,
) -> Callable[[WrappedFn], WrappedFn]:
    """Build a tenacity retry decorator with the project's standard shape.

    Standard configuration: exponential backoff capped at ``wait_max``, a
    WARNING-level structured retry event with only exception type/status,
    attempt, and wait, and ``reraise=True`` so the final exception propagates.

    The returned decorator preserves the wrapped callable's signature (matching
    tenacity's own ``retry``), so decorated functions keep their declared types.

    Args:
        attempts: Maximum attempts (``stop_after_attempt``).
        wait_multiplier: ``wait_exponential`` multiplier.
        wait_min: ``wait_exponential`` minimum wait in seconds.
        wait_max: ``wait_exponential`` maximum wait in seconds (default 30.0).
        retry_on: Exception type(s) eligible for retry. ``None`` retries on any
            exception (tenacity's default predicate).
        log: Logger used for safe structured retry events. Defaults to a structlog module logger.

    Returns:
        A configured tenacity retry decorator.
    """
    if log is None:
        log = structlog.get_logger(__name__)

    kwargs: dict[str, Any] = {
        "stop": stop_after_attempt(attempts),
        "wait": wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
        "before_sleep": lambda retry_state: _log_safe_retry(log, retry_state),
        "reraise": True,
    }
    if retry_on is not None:
        kwargs["retry"] = retry_if_exception_type(retry_on)
    return cast("Callable[[WrappedFn], WrappedFn]", retry(**kwargs))
