"""Tests for client-side source rate limiting (core/rate_limit.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from equity_lake.core.rate_limit import RateLimiter, reset_limiters, throttle, throttle_with
from equity_lake.sources.base import MarketDataFetcher


class FakeClock:
    """Controllable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestRateLimiter:
    def test_allows_rpm_requests_within_window_without_sleeping(self) -> None:
        clock, sleeps = FakeClock(), []
        limiter = RateLimiter(3, clock=clock, sleep=sleeps.append)
        for _ in range(3):
            limiter.acquire()
        assert sleeps == []

    def test_blocks_when_window_full(self) -> None:
        clock, sleeps = FakeClock(), []
        limiter = RateLimiter(2, clock=clock, sleep=lambda s: (sleeps.append(s), clock.advance(s)))
        limiter.acquire()
        limiter.acquire()
        clock.advance(10.0)  # both slots still occupied (window is 60s)
        limiter.acquire()
        # oldest event at t=1000 releases at t=1060; we are at 1010 -> wait 50s
        assert sleeps == [50.0]

    def test_sliding_window_releases_capacity(self) -> None:
        clock, sleeps = FakeClock(), []
        limiter = RateLimiter(2, clock=clock, sleep=sleeps.append)
        limiter.acquire()
        limiter.acquire()
        clock.advance(61.0)  # first event expired
        limiter.acquire()
        assert sleeps == []

    def test_rejects_rpm_below_one(self) -> None:
        with pytest.raises(ValueError, match="rpm"):
            RateLimiter(0)

    def test_thread_safety_smoke(self) -> None:
        import threading

        clock, sleeps = FakeClock(), []
        limiter = RateLimiter(100, clock=clock, sleep=sleeps.append)
        threads = [threading.Thread(target=limiter.acquire) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # no sleeps needed (50 < 100) and no corruption
        assert sleeps == []


class TestThrottle:
    def setup_method(self) -> None:
        reset_limiters()

    def teardown_method(self) -> None:
        reset_limiters()

    def test_noop_when_unconfigured(self) -> None:
        with patch("equity_lake.core.config.get_settings") as settings:
            settings.return_value.sources.default_rpm = None
            settings.return_value.sources.rpm_overrides = {}
            throttle("finnhub")  # must not raise or sleep

    def test_override_wins_over_default(self) -> None:
        with patch("equity_lake.core.config.get_settings") as settings:
            settings.return_value.sources.default_rpm = 60
            settings.return_value.sources.rpm_overrides = {"finnhub": 5}
            assert throttle.__module__  # sanity
            # resolved via _configured_rpm
            from equity_lake.core.rate_limit import _configured_rpm

            assert _configured_rpm("finnhub") == 5
            assert _configured_rpm("reddit") == 60

    def test_throttle_with_enforces_window(self) -> None:
        clock, sleeps = FakeClock(), []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        throttle_with("test_src", 1, clock=clock, sleep=fake_sleep)
        clock.advance(1.0)
        throttle_with("test_src", 1, clock=clock, sleep=fake_sleep)
        assert len(sleeps) == 1  # second call within a minute had to wait


class TestFetcherWiring:
    def test_rate_limit_source_prefers_source_name(self) -> None:
        class _F(MarketDataFetcher):
            market = "us"

        assert _F().rate_limit_source == "us"

        class _G(MarketDataFetcher):
            market = "us"
            source_name = "finnhub"

        assert _G().rate_limit_source == "finnhub"

    def test_fetch_attempts_are_throttled(self) -> None:
        class _H(MarketDataFetcher):
            market = "m"

            def fetch(self, trading_date):
                return "ok"

        fetcher = _H()
        with patch("equity_lake.sources.base.throttle") as throttler:
            assert fetcher._retry_on_failure(lambda: 42) == 42
        throttler.assert_called_once_with("m")

    def test_finnhub_fetchers_share_provider_key(self) -> None:
        from equity_lake.sources.news import FinnhubNewsFetcher
        from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

        assert FinnhubNewsFetcher.source_name == "finnhub"
        assert FinnhubSocialSentimentFetcher.source_name == "finnhub"
