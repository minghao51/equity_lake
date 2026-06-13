"""Loader registry bootstrap."""

from equity_lake.loaders.registry import registry
from equity_lake.loaders.sec_loader import SECFilingsLoader
from equity_lake.loaders.yfinance_loader import YFinanceLoader

registry.register("yfinance", YFinanceLoader)
registry.register("sec_filings", SECFilingsLoader)
registry.discover()

__all__ = [
    "SECFilingsLoader",
    "YFinanceLoader",
    "registry",
]
