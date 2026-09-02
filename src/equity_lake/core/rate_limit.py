"""Client-side, per-source request throttling for source fetchers.

Optional by design: with no ``sources`` settings configured (the default),
:meth:`throttle` is a no-op and fetcher behavior is unchanged. When
``sources.default_rpm`` or ``sources.rpm_overrides`` are set, each fetch
attempt blocks until the source's sliding window has capacity.

Limiters are keyed by *provider-level* source names (e.g. ``"finnhub"``), so
fetchers that share a provider quota (``FinnhubNewsFetcher`` and
``FinnhubSocialSentimentFetcher``) share one limiter.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Thread-safe sliding-window requests-per-minute limiter.

    ``clock`` and ``sleep`` are injectable so tests can exercise waiting
    without real time.
    """

    def __init__(
        self,
        rpm: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rpm < 1:
            raise ValueError(f"rpm must be >= 1, got {rpm}")
        self.rpm = rpm
        self._clock = clock
        self._sleep = sleep
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until the window has capacity for one more request."""
        while True:
            with self._lock:
                now = self._clock()
                cutoff = now - _WINDOW_SECONDS
                while self._events and self._events[0] <= cutoff:
                    self._events.popleft()
                if len(self._events) < self.rpm:
                    self._events.append(now)
                    return
                wait = self._events[0] + _WINDOW_SECONDS - now
            if wait > 0:
                logger.debug("rate_limit_wait", wait_seconds=round(wait, 3))
                self._sleep(wait)


_limiters: dict[str, RateLimiter] = {}
_registry_lock = threading.Lock()


def _configured_rpm(source: str) -> int | None:
    """Resolve the RPM for *source* from settings (default: unthrottled)."""
    from equity_lake.core.config import get_settings

    sources = get_settings().sources
    override = sources.rpm_overrides.get(source)
    if override is not None:
        return override
    return sources.default_rpm


def throttle_with(source: str, rpm: int, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
    """Acquire one slot from *source*'s limiter at the given RPM.

    Test seam: explicit ``clock``/``sleep`` (limiter is rebuilt when the
    injected callables differ from the cached instance's).
    """
    with _registry_lock:
        limiter = _limiters.get(source)
        if limiter is None or limiter.rpm != rpm:
            limiter = RateLimiter(rpm, clock=clock, sleep=sleep)
            _limiters[source] = limiter
    limiter.acquire()


def throttle(source: str) -> None:
    """Block until *source* may issue another request per configured limits.

    No-op unless ``sources.default_rpm`` or ``sources.rpm_overrides[source]``
    is set in Settings.
    """
    rpm = _configured_rpm(source)
    if rpm is None:
        return
    throttle_with(source, rpm)


def reset_limiters() -> None:
    """Drop cached limiters (test isolation)."""
    with _registry_lock:
        _limiters.clear()
