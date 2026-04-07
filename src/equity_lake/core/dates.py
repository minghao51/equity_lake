"""Date utilities for the equity pipeline."""

from datetime import date, datetime, timedelta


def resolve_trading_date(
    explicit_date: str | None,
    days_back: int = 1,
    today: date | None = None,
) -> date:
    """
    Resolve trading date from explicit string or relative to today.

    Args:
        explicit_date: Date string in YYYY-MM-DD format, or None for relative date
        days_back: Number of days to go back if explicit_date is None (default: 1)
        today: Optional base date for testing (default: date.today())

    Returns:
        Resolved date object

    Examples:
        >>> resolve_trading_date("2024-12-01")
        datetime.date(2024, 12, 1)
        >>> resolve_trading_date(None, days_back=1)  # yesterday
        datetime.date(2024, 12, 2)  # example
    """
    if explicit_date:
        return datetime.strptime(explicit_date, "%Y-%m-%d").date()
    base = today or date.today()
    return base - timedelta(days=days_back)


__all__ = ["resolve_trading_date"]
