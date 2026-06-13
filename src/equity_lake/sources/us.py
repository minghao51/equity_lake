"""US market source adapter."""

from typing import Any

import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.sources.base import YFinanceBaseFetcher

logger = structlog.get_logger()

DEFAULT_BATCH_SIZE = 500

_FALLBACK_TICKERS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "BRK-B",
    "LLY",
    "AVGO",
    "JPM",
    "V",
    "JNJ",
    "WMT",
    "MA",
    "PG",
    "COST",
    "UNH",
    "XOM",
    "HD",
    "CVX",
    "MRK",
    "ABBV",
    "BAC",
    "KO",
    "PEP",
    "CRM",
    "NFLX",
    "AMD",
    "TMO",
    "LIN",
    "ABT",
    "ORCL",
    "ADBE",
    "CMCSA",
    "WFC",
    "COP",
    "QCOM",
    "INTC",
    "DHR",
    "VZ",
    "IBM",
    "GE",
    "DIS",
    "BA",
    "NKE",
    "CAT",
]


class USEquityFetcher(YFinanceBaseFetcher):
    """Fetch US equity EOD data using yfinance."""

    market = "us"

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


__all__ = ["USEquityFetcher"]
