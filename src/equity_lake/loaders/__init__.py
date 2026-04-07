"""Loader registry bootstrap."""

from equity_lake.loaders.options_flow_loader import OptionsFlowLoader
from equity_lake.loaders.reddit_loader import RedditSentimentLoader
from equity_lake.loaders.registry import registry
from equity_lake.loaders.sec_loader import SECFilingsLoader
from equity_lake.loaders.yfinance_loader import YFinanceLoader

registry.register("yfinance", YFinanceLoader)
registry.register("reddit_sentiment", RedditSentimentLoader)
registry.register("sec_filings", SECFilingsLoader)
registry.register("options_flow", OptionsFlowLoader)
registry.discover()

__all__ = [
    "OptionsFlowLoader",
    "RedditSentimentLoader",
    "SECFilingsLoader",
    "YFinanceLoader",
    "registry",
]
