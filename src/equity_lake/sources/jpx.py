"""Japan (JPX) market source adapter."""

from typing import Any

import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.sources.base import YFinanceBaseFetcher

logger = structlog.get_logger()

DEFAULT_BATCH_SIZE = 500

_FALLBACK_TICKERS = [
    "7203.T",
    "6758.T",
    "9984.T",
    "6861.T",
    "8306.T",
    "7974.T",
    "9432.T",
    "8035.T",
    "4063.T",
    "6098.T",
]


class JPXEquityFetcher(YFinanceBaseFetcher):
    """Fetch Japanese equity (JPX) data using yfinance.

    Ticker format: Numeric code with .T suffix (e.g., 7203.T for Toyota,
    6758.T for Sony, 9984.T for SoftBank).
    """

    market = "jpx"

    def __init__(
        self,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: dict[str, Any] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        super().__init__(
            tickers=tickers,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            filters=filters,
            batch_size=batch_size,
            fallback_tickers=_FALLBACK_TICKERS,
        )


__all__ = ["JPXEquityFetcher"]
