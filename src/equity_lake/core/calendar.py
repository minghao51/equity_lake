"""Exchange-specific trading calendar utilities.

Wraps ``exchange_calendars`` to provide market-aware trading-day checks.
Each supported market maps to an exchange MIC code with its holiday calendar
and timezone.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import structlog

logger = structlog.get_logger()

# Each market maps to one or more exchange MIC codes with their holiday
# calendars and timezones. Short keys ("us", "cn", ...) mirror the ingestion
# market vocabulary (ingestion.types.REQUIRED_PRICE_MARKETS / MARKET_DIR_REVERSE)
# so calendar lookups accept both forms.
_MARKET_TO_EXCHANGE: dict[str, list[str]] = {
    "us_equity": ["XNYS"],
    "us": ["XNYS"],
    "cn_ashare": ["XSHG"],
    "cn": ["XSHG"],
    "hk_sg_equity": ["XHKG", "XSES"],
    "hk_sg": ["XHKG", "XSES"],
    "jpx_equity": ["JPX"],
    "jpx": ["JPX"],
    "krx_equity": ["XKRX"],
    "krx": ["XKRX"],
}

_MARKET_TZ: dict[str, str] = {
    "us_equity": "America/New_York",
    "us": "America/New_York",
    "cn_ashare": "Asia/Shanghai",
    "cn": "Asia/Shanghai",
    "hk_sg_equity": "Asia/Hong_Kong",
    "hk_sg": "Asia/Hong_Kong",
    "jpx_equity": "Asia/Tokyo",
    "jpx": "Asia/Tokyo",
    "krx_equity": "Asia/Seoul",
    "krx": "Asia/Seoul",
}

_calendar_cache: dict[str, xc.ExchangeCalendar] = {}


def _get_calendar(exchange: str) -> xc.ExchangeCalendar:
    if exchange not in _calendar_cache:
        _calendar_cache[exchange] = xc.get_calendar(exchange)
    return _calendar_cache[exchange]


def is_trading_day(market: str, d: date) -> bool:
    """True when ANY exchange mapped to the market holds a session on ``d``.

    Union semantics: answers "is this market active somewhere today?", which
    is what orchestration and freshness checks need. Gap detection instead
    uses the stricter intersection semantics of :func:`trading_days_between`.
    """
    return any(_get_calendar(exchange).is_session(d) for exchange in _MARKET_TO_EXCHANGE.get(market, []))


def trading_days_between(market: str, start: date, end: date) -> list[date]:
    """Sessions between ``start`` and ``end`` common to ALL of the market's exchanges.

    Intersection semantics: a date is included only when every exchange mapped
    to the market holds a session. Gap detection uses this to build the set of
    dates expected for *every* ticker in a table; for the mixed-exchange
    ``hk_sg_equity`` market (XHKG + XSES), first-exchange-only or union
    semantics would flag false gaps for ``.SI`` tickers on Hong Kong-only
    sessions (e.g. Singapore National Day, when XHKG trades but XSES is
    closed) — and vice versa for ``.HK`` tickers. The trade-off: dates where
    only one exchange trades are never expected for any ticker, so a genuine
    single-exchange gap is not reported; gap-filling here prioritizes zero
    false positives. This intentionally differs from :func:`is_trading_day`.
    """
    exchanges = _MARKET_TO_EXCHANGE.get(market, [])
    if not exchanges:
        return []
    session_sets = [set(_get_calendar(exchange).sessions_in_range(start, end)) for exchange in exchanges]
    common = set.intersection(*session_sets)
    return sorted(session.date() for session in common)


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
    "count_trading_days",
    "is_trading_day",
    "market_now",
    "market_timezone",
    "trading_days_between",
]
