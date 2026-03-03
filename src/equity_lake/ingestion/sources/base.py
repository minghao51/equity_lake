"""Base ingestion source adapters."""

import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import structlog

from equity_lake.fetch_macro import MacroIndicatorFetcher

logger = structlog.get_logger()


class MarketDataFetcher:
    """Base class for market data fetchers."""

    def __init__(self, retry_attempts: int = 3, retry_delay: float = 1.0):
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch data for a specific date."""
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Retry API calls with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                result: Any = func(*args, **kwargs)
                if result is None:
                    return pd.DataFrame()
                if isinstance(result, pd.DataFrame):
                    return result
                if isinstance(result, pd.Series):
                    return result.to_frame().T
                return pd.DataFrame()
            except Exception as exc:
                last_error = exc
                if attempt < self.retry_attempts - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        "Attempt %s failed: %s. Retrying in %.1fs...",
                        attempt + 1,
                        exc,
                        wait_time,
                    )
                    time.sleep(wait_time)
                else:
                    logger.error("All %s attempts failed: %s", self.retry_attempts, exc)
                    raise last_error from exc
        return pd.DataFrame()


__all__ = ["MacroIndicatorFetcher", "MarketDataFetcher"]
