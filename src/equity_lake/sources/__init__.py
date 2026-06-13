"""Market source adapters for ingestion."""

from equity_lake.sources.base import MarketDataFetcher, YFinanceBaseFetcher
from equity_lake.sources.cn import CNAshareFetcher
from equity_lake.sources.cn_efinance import CNEfinanceFetcher
from equity_lake.sources.cn_hybrid import CNHybridFetcher
from equity_lake.sources.hk_sg import HKSGEquityFetcher
from equity_lake.sources.jpx import JPXEquityFetcher
from equity_lake.sources.krx import KRXEquityFetcher
from equity_lake.sources.macro import MacroIndicatorFetcher
from equity_lake.sources.news import FinnhubNewsFetcher
from equity_lake.sources.us import USEquityFetcher

__all__ = [
    "CNAshareFetcher",
    "CNEfinanceFetcher",
    "CNHybridFetcher",
    "FinnhubNewsFetcher",
    "HKSGEquityFetcher",
    "JPXEquityFetcher",
    "KRXEquityFetcher",
    "MacroIndicatorFetcher",
    "MarketDataFetcher",
    "USEquityFetcher",
    "YFinanceBaseFetcher",
]
