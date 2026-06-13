"""Exchange-specific trading calendar utilities.

Wraps ``exchange_calendars`` to provide market-aware trading-day checks.
Each supported market maps to an exchange MIC code with its holiday calendar
and timezone.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import structlog

logger = structlog.get_logger()

MarketName = Literal["us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"]

_MARKET_TO_EXCHANGE: dict[str, list[str]] = {
    "us_equity": ["XNYS"],
    "cn_ashare": ["XSHG"],
    "hk_sg_equity": ["XHKG", "XSES"],
    "jpx_equity": ["JPX"],
    "krx_equity": ["XKRX"],
}

_MARKET_TZ: dict[str, str] = {
    "us_equity": "America/New_York",
    "cn_ashare": "Asia/Shanghai",
    "hk_sg_equity": "Asia/Hong_Kong",
    "jpx_equity": "Asia/Tokyo",
    "krx_equity": "Asia/Seoul",
}

_calendar_cache: dict[str, xc.ExchangeCalendar] = {}


def _get_calendar(exchange: str) -> xc.ExchangeCalendar:
    if exchange not in _calendar_cache:
        _calendar_cache[exchange] = xc.get_calendar(exchange)
    return _calendar_cache[exchange]


def is_trading_day(market: str, d: date) -> bool:
    return any(_get_calendar(exchange).is_session(d) for exchange in _MARKET_TO_EXCHANGE.get(market, []))


def trading_days_between(market: str, start: date, end: date) -> list[date]:
    exchanges = _MARKET_TO_EXCHANGE.get(market, [])
    if not exchanges:
        return []
    cal = _get_calendar(exchanges[0])
    sessions = cal.sessions_in_range(start, end)
    return [s.date() for s in sessions]


def count_trading_days(market: str, start: date, end: date) -> int:
    return len(trading_days_between(market, start, end))


def market_timezone(market: str) -> ZoneInfo:
    tz_name = _MARKET_TZ.get(market, "UTC")
    return ZoneInfo(tz_name)


def market_now(market: str) -> date:
    from datetime import datetime

    tz = market_timezone(market)
    return datetime.now(tz).date()


__all__ = [
    "MarketName",
    "count_trading_days",
    "is_trading_day",
    "market_now",
    "market_timezone",
    "trading_days_between",
]
