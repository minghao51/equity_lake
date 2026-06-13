"""Tests for core.calendar trading-day utilities."""

from datetime import date

from equity_lake.core.calendar import (
    count_trading_days,
    is_trading_day,
    market_now,
    market_timezone,
    trading_days_between,
)


class TestIsTradingDay:
    def test_us_weekday_is_trading_day(self) -> None:
        assert is_trading_day("us_equity", date(2026, 6, 2)) is True

    def test_us_weekend_is_not_trading_day(self) -> None:
        assert is_trading_day("us_equity", date(2026, 6, 7)) is False

    def test_us_holiday_is_not_trading_day(self) -> None:
        assert is_trading_day("us_equity", date(2026, 1, 1)) is False

    def test_unknown_market_returns_false(self) -> None:
        assert is_trading_day("unknown", date(2026, 6, 2)) is False


class TestTradingDaysBetween:
    def test_returns_list_of_dates(self) -> None:
        days = trading_days_between("us_equity", date(2026, 6, 1), date(2026, 6, 5))
        assert all(isinstance(d, date) for d in days)

    def test_excludes_weekends(self) -> None:
        days = trading_days_between("us_equity", date(2026, 6, 1), date(2026, 6, 7))
        weekdays = [d for d in days if d.weekday() < 5]
        assert len(days) == len(weekdays)

    def test_unknown_market_returns_empty(self) -> None:
        days = trading_days_between("unknown", date(2026, 6, 1), date(2026, 6, 5))
        assert days == []


class TestCountTradingDays:
    def test_counts_correctly(self) -> None:
        days = trading_days_between("us_equity", date(2026, 6, 1), date(2026, 6, 5))
        assert count_trading_days("us_equity", date(2026, 6, 1), date(2026, 6, 5)) == len(days)


class TestMarketTimezone:
    def test_us_timezone(self) -> None:
        tz = market_timezone("us_equity")
        assert str(tz) == "America/New_York"

    def test_unknown_returns_utc(self) -> None:
        tz = market_timezone("unknown")
        assert str(tz) == "UTC"


class TestMarketNow:
    def test_returns_date(self) -> None:
        result = market_now("us_equity")
        assert isinstance(result, date)
