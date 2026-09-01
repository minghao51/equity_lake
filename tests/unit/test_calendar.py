"""Tests for core.calendar trading-day utilities."""

from datetime import date

import exchange_calendars as xc
import pytest

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

    def test_hk_sg_intersects_all_exchanges(self) -> None:
        """hk_sg_equity must yield the XHKG ∩ XSES sessions, not just exchanges[0]."""
        start, end = date(2024, 1, 1), date(2024, 12, 31)
        xhkg = {s.date() for s in xc.get_calendar("XHKG").sessions_in_range(start, end)}
        xses = {s.date() for s in xc.get_calendar("XSES").sessions_in_range(start, end)}
        assert trading_days_between("hk_sg_equity", start, end) == sorted(xhkg & xses)

    def test_hk_sg_excludes_sg_only_holidays(self) -> None:
        """2024-08-09 (Singapore National Day): XHKG trades, XSES closed — must not be expected."""
        days = trading_days_between("hk_sg_equity", date(2024, 8, 5), date(2024, 8, 12))
        assert date(2024, 8, 9) not in days
        assert date(2024, 8, 8) in days
        assert date(2024, 8, 12) in days

    def test_hk_sg_excludes_hk_only_holidays(self) -> None:
        """2024-04-04 (Ching Ming Festival): XSES trades, XHKG closed — must not be expected."""
        days = trading_days_between("hk_sg_equity", date(2024, 4, 2), date(2024, 4, 8))
        assert date(2024, 4, 4) not in days
        assert date(2024, 4, 2) in days

    def test_short_market_keys_accepted(self) -> None:
        """Ingestion short keys (MARKET_DIR_REVERSE values) resolve like the long forms."""
        start, end = date(2026, 6, 1), date(2026, 6, 5)
        assert trading_days_between("us", start, end) == trading_days_between("us_equity", start, end)
        assert is_trading_day("hk_sg", date(2026, 6, 2)) is is_trading_day("hk_sg_equity", date(2026, 6, 2))


class TestIsTradingDayHkSg:
    """is_trading_day keeps union semantics (any exchange) — documented counterpart of the intersection above."""

    def test_union_across_exchanges(self) -> None:
        # 2024-08-09: XSES closed (SG National Day) but XHKG trades → union says True.
        assert is_trading_day("hk_sg_equity", date(2024, 8, 9)) is True
        # 2024-04-04: XHKG closed (Ching Ming) but XSES trades → union says True.
        assert is_trading_day("hk_sg_equity", date(2024, 4, 4)) is True
        # 2024-03-29 (Good Friday): both closed → False.
        assert is_trading_day("hk_sg_equity", date(2024, 3, 29)) is False


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


class TestResolveTradingDateGuard:
    """ADR-0010 loop guard: unknown market keys raise, never hang.

    The historical ``_subtract_trading_days`` infinite loop happened when a
    market key resolved to no trading calendar; ``resolve_trading_date`` now
    normalizes the market through ``canonical_market`` first, so every key that
    reaches the loop has a real calendar.
    """

    def test_unknown_market_raises_instead_of_hanging(self) -> None:
        from equity_lake.core.dates import resolve_trading_date

        with pytest.raises(ValueError, match="Unknown price market"):
            resolve_trading_date(None, days_back=1, today=date(2026, 6, 2), market="cn_ashar")

    def test_dataset_identifier_raises(self) -> None:
        """Enrichment ids have no trading calendar — a loud error, not a silent hang."""
        from equity_lake.core.dates import resolve_trading_date

        with pytest.raises(ValueError, match="Unknown price market"):
            resolve_trading_date(None, days_back=1, today=date(2026, 6, 2), market="us_news")

    def test_short_alias_resolves_on_that_market_calendar(self) -> None:
        from equity_lake.core.dates import resolve_trading_date

        # Both vocabularies resolve identically on the CN calendar.
        kwargs = {"days_back": 1, "today": date(2026, 6, 8)}
        assert resolve_trading_date(None, market="cn", **kwargs) == resolve_trading_date(None, market="cn_ashare", **kwargs)
