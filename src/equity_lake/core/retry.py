"""Shared tenacity retry decorator factory.

Consolidates the three parallel copies of the project's standard retry shape
(``sources/base.py``, ``sources/macro.py``, ``ingestion/llm_base.py``) into one
factory. Each call site preserves its original parameters exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def build_retry_decorator(
    *,
    attempts: int,
    wait_multiplier: float,
    wait_min: float,
    wait_max: float = 30.0,
    retry_on: type[BaseException] | tuple[type[BaseException], ...] | None = None,
    log: Any = None,
) -> Callable[..., Any]:
    """Build a tenacity retry decorator with the project's standard shape.

    Standard configuration: exponential backoff capped at ``wait_max``, a
    WARNING-level (30) ``before_sleep_log``, and ``reraise=True`` so the final
    attempt's exception propagates unchanged.

    Args:
        attempts: Maximum attempts (``stop_after_attempt``).
        wait_multiplier: ``wait_exponential`` multiplier.
        wait_min: ``wait_exponential`` minimum wait in seconds.
        wait_max: ``wait_exponential`` maximum wait in seconds (default 30.0).
        retry_on: Exception type(s) eligible for retry. ``None`` retries on any
            exception (tenacity's default predicate).
        log: Logger passed to ``before_sleep_log``. Defaults to a structlog module logger.

    Returns:
        A configured tenacity retry decorator.
    """
    if log is None:
        log = structlog.get_logger(__name__)

    kwargs: dict[str, Any] = {
        "stop": stop_after_attempt(attempts),
        "wait": wait_exponential(multiplier=wait_multiplier, min=wait_min, max=wait_max),
        "before_sleep": before_sleep_log(log, 30),
        "reraise": True,
    }
    if retry_on is not None:
        kwargs["retry"] = retry_if_exception_type(retry_on)
    return cast("Callable[..., Any]", retry(**kwargs))
