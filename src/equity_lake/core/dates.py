"""Date utilities for the equity pipeline."""

from __future__ import annotations

from datetime import date, datetime


def _subtract_trading_days(base: date, days_back: int, market: str = "us_equity") -> date:
    from equity_lake.core.calendar import is_trading_day

    current = base
    remaining = max(days_back, 0)
    while remaining > 0:
        current -= __import__("datetime").timedelta(days=1)
        if is_trading_day(market, current):
            remaining -= 1
    return current


def resolve_trading_date(
    explicit_date: str | None,
    days_back: int = 1,
    today: date | None = None,
    market: str = "us_equity",
) -> date:
    """
    Resolve trading date from explicit string or relative to today.

    Args:
        explicit_date: Date string in YYYY-MM-DD format, or None for relative date
        days_back: Number of trading days to go back if explicit_date is None (default: 1)
        today: Optional base date for testing (default: date.today())
        market: Market identifier for exchange calendar (default: us_equity)

    Returns:
        Resolved date object

    Examples:
        >>> resolve_trading_date("2024-12-01")
        datetime.date(2024, 12, 1)
        >>> resolve_trading_date(None, days_back=1)  # last trading day
        datetime.date(2024, 12, 2)  # example
    """
    if explicit_date:
        return datetime.strptime(explicit_date, "%Y-%m-%d").date()
    base = today or date.today()
    return _subtract_trading_days(base, days_back, market)


__all__ = ["resolve_trading_date"]
