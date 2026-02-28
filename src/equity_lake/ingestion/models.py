"""Shared ingestion model types."""

from typing import Any

from equity_lake.ingestion.parallel import MarketFetchResult

FilterConfig = dict[str, Any]

__all__ = ["FilterConfig", "MarketFetchResult"]
