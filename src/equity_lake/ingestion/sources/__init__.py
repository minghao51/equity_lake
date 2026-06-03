"""Market source adapters for ingestion."""

from equity_lake.fetch_macro import MacroIndicatorFetcher
from equity_lake.ingestion.sources.base import MarketDataFetcher
from equity_lake.ingestion.sources.cn import CNAshareFetcher
from equity_lake.ingestion.sources.cn_efinance import CNEfinanceFetcher
from equity_lake.ingestion.sources.cn_hybrid import CNHybridFetcher
from equity_lake.ingestion.sources.hk_sg import HKSGEquityFetcher
from equity_lake.ingestion.sources.jpx import JPXEquityFetcher
from equity_lake.ingestion.sources.krx import KRXEquityFetcher
from equity_lake.ingestion.sources.news import FinnhubNewsFetcher
from equity_lake.ingestion.sources.us import USEquityFetcher

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
]
