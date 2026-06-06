"""Shared ingestion model types."""

from typing import Any

from pydantic import BaseModel

FilterConfig = dict[str, Any]


class MarketFetchResult(BaseModel):
    """Result of a market data fetch operation."""

    market: str
    trading_date: str
    ticker_count: int = 0
    success: bool = True
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True}


__all__ = ["FilterConfig", "MarketFetchResult"]
