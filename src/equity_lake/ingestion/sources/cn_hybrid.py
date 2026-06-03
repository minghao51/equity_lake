"""Hybrid China market source with automatic fallback.

This module provides a multi-source fetcher that tries multiple data sources
in order and falls back to alternatives if one fails. This improves reliability
and data completeness.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, timedelta

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

# Import individual fetchers
try:
    from equity_lake.ingestion.sources.cn_efinance import CNEfinanceFetcher
except ImportError:
    CNEfinanceFetcher = None  # type: ignore[assignment,misc]

from equity_lake.ingestion.sources.cn import CNAshareFetcher

logger = structlog.get_logger()


class CNHybridFetcher(MarketDataFetcher):
    """Hybrid China A-share fetcher with automatic fallback.

    Fetch strategy (in order):
    1. efinance (primary) - faster, more stable
    2. akshare (fallback) - comprehensive but slower

    If efinance fails or returns insufficient data, automatically falls back
    to akshare to ensure data completeness.
    """

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
        max_workers: int = 10,
        stock_limit: int = 100,
        enable_efinance: bool = False,
        enable_akshare: bool = True,
        efinance_timeout_seconds: float = 45.0,
    ):
        """Initialize hybrid fetcher with configurable sources.

        Args:
            retry_attempts: Number of retry attempts for failed API calls
            retry_delay: Delay between retries in seconds
            ticker_config: Ticker configuration object
            filters: Filters to apply to ticker selection
            max_workers: Maximum parallel workers for fetching
            stock_limit: Maximum number of stocks to fetch
            enable_efinance: Use efinance as primary source
            enable_akshare: Use akshare as fallback source
            efinance_timeout_seconds: Maximum time to wait for efinance before
                falling back to akshare
        """
        super().__init__(retry_attempts, retry_delay)
        self.ticker_config = ticker_config or TickerConfig()
        self.filters = filters or {}
        self.max_workers = max_workers
        self.stock_limit = stock_limit
        self.enable_efinance = enable_efinance and CNEfinanceFetcher is not None
        self.enable_akshare = enable_akshare
        self.efinance_timeout_seconds = efinance_timeout_seconds
        configured_tickers = self.ticker_config.get_tickers_for_market("cn", active_only=True)
        self.configured_ticker_count = min(len(configured_tickers), self.stock_limit)

        # Initialize fetchers
        self.efinance_fetcher: CNEfinanceFetcher | None = None
        self.akshare_fetcher: CNAshareFetcher | None = None

        if self.enable_efinance:
            try:
                self.efinance_fetcher = CNEfinanceFetcher(
                    retry_attempts=max(retry_attempts, 4),
                    retry_delay=max(retry_delay, 2.0),
                    ticker_config=ticker_config,
                    filters=filters,
                    max_workers=max_workers,
                    stock_limit=stock_limit,
                )
                logger.info("efinance_fetcher_initialized")
            except Exception as exc:
                logger.warning(
                    "efinance_initialization_failed",
                    error=str(exc),
                    message="efinance will not be used",
                )
                self.efinance_fetcher = None
                self.enable_efinance = False

        if self.enable_akshare:
            try:
                self.akshare_fetcher = CNAshareFetcher(
                    retry_attempts=retry_attempts,
                    retry_delay=retry_delay,
                    ticker_config=ticker_config,
                    filters=filters,
                    max_workers=max_workers,
                    stock_limit=stock_limit,
                )
                logger.info("akshare_fetcher_initialized")
            except Exception as exc:
                logger.warning(
                    "akshare_initialization_failed",
                    error=str(exc),
                    message="akshare will not be used",
                )
                self.akshare_fetcher = None
                self.enable_akshare = False

        if not self.enable_efinance and not self.enable_akshare:
            raise RuntimeError("No China data source available. Install efinance or ensure akshare is working.")

    def _fetch_efinance_with_timeout(self, trading_date: date) -> pd.DataFrame:
        """Run efinance with a bounded wait so fallback is not delayed indefinitely."""
        if self.efinance_fetcher is None:
            return pd.DataFrame()

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.efinance_fetcher.fetch, trading_date)
        try:
            return future.result(timeout=self.efinance_timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            logger.warning(
                "efinance_fetch_timeout",
                timeout_seconds=self.efinance_timeout_seconds,
                message="Falling back to akshare",
            )
            raise TimeoutError(f"efinance exceeded {self.efinance_timeout_seconds}s timeout") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _cn_to_yahoo_symbol(code: str) -> str:
        """Map CN ticker code to Yahoo Finance symbol."""
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
        return f"{code}.SS"

    def _fetch_yfinance_fallback(self, trading_date: date) -> pd.DataFrame:
        """Fetch CN data from Yahoo Finance when primary providers return empty."""
        tickers = self._get_configured_tickers("cn")
        if not tickers:
            return pd.DataFrame()

        yf_symbols = [self._cn_to_yahoo_symbol(ticker) for ticker in tickers]
        symbol_to_code = dict(zip(yf_symbols, tickers, strict=False))
        start = trading_date.isoformat()
        end = (trading_date + timedelta(days=1)).isoformat()

        data = yf.download(
            yf_symbols,
            start=start,
            end=end,
            group_by="ticker",
            progress=False,
            auto_adjust=False,
            threads=True,
        )
        if data.empty:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        if len(yf_symbols) == 1:
            symbol = yf_symbols[0]
            frame = data.copy()
            frame["ticker"] = symbol_to_code[symbol]
            frame["date"] = trading_date
            frames.append(frame)
        else:
            symbols_available = set(data.columns.get_level_values(0))
            for symbol in yf_symbols:
                if symbol not in symbols_available:
                    continue
                frame = data[symbol].copy()
                frame["ticker"] = symbol_to_code[symbol]
                frame["date"] = trading_date
                frames.append(frame)

        if not frames:
            return pd.DataFrame()

        frame = pd.concat(frames, ignore_index=True)
        frame = frame.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
                "Adj Close": "adj_close",
            }
        )
        if "adj_close" not in frame.columns and "close" in frame.columns:
            frame["adj_close"] = frame["close"]
        return frame

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch China A-share data with automatic fallback.

        Strategy:
        1. Try efinance first (if enabled) - faster and more stable
        2. If efinance fails or returns <50% expected data, try akshare
        3. Return the best result (most data rows)

        Returns:
            DataFrame with standard columns (ticker, date, open, high, low, close, volume, adj_close)
        """
        logger.info(
            "fetch_cn_hybrid_started",
            date=str(trading_date),
            efinance_enabled=self.enable_efinance,
            akshare_enabled=self.enable_akshare,
        )

        results: list[tuple[str, pd.DataFrame]] = []

        # Try efinance first (primary source)
        if self.enable_efinance and self.efinance_fetcher:
            try:
                logger.info("trying_efinance_source")
                efinance_result = self._fetch_efinance_with_timeout(trading_date)
                row_count = len(efinance_result)

                logger.info(
                    "efinance_result",
                    rows=row_count,
                    unique_tickers=(efinance_result["ticker"].nunique() if "ticker" in efinance_result else 0),
                )

                results.append(("efinance", efinance_result))

                # If efinance returned good data, we can skip akshare
                # Consider "good" as >30% of configured target tickers.
                sufficient_threshold = max(1, int(self.configured_ticker_count * 0.3))
                if row_count >= sufficient_threshold:
                    logger.info(
                        "efinance_sufficient",
                        rows=row_count,
                        configured_ticker_count=self.configured_ticker_count,
                        sufficient_threshold=sufficient_threshold,
                        message="Skipping akshare fallback",
                    )
                    return self._standardize_output(efinance_result)

            except Exception as exc:
                logger.warning(
                    "efinance_fetch_failed",
                    error=str(exc),
                    message="Falling back to akshare",
                )

        # Try akshare (fallback source)
        if self.enable_akshare and self.akshare_fetcher:
            try:
                logger.info("trying_akshare_source")
                akshare_result = self.akshare_fetcher.fetch(trading_date)
                row_count = len(akshare_result)

                logger.info(
                    "akshare_result",
                    rows=row_count,
                    unique_tickers=(akshare_result["ticker"].nunique() if "ticker" in akshare_result else 0),
                )

                results.append(("akshare", akshare_result))

                if row_count == 0:
                    logger.warning(
                        "akshare_empty_falling_back_to_yfinance",
                        date=str(trading_date),
                    )
                    yfinance_result = self._fetch_yfinance_fallback(trading_date)
                    yfinance_rows = len(yfinance_result)
                    logger.info(
                        "yfinance_fallback_result",
                        rows=yfinance_rows,
                        unique_tickers=(yfinance_result["ticker"].nunique() if "ticker" in yfinance_result else 0),
                    )
                    if yfinance_rows > 0:
                        results.append(("yfinance", yfinance_result))

            except Exception as exc:
                logger.error(
                    "akshare_fetch_failed",
                    error=str(exc),
                    message="All China data sources failed",
                )

        # Return best result (most rows)
        if not results:
            logger.error("all_cn_sources_failed", date=str(trading_date))
            return pd.DataFrame()

        # Sort by row count (descending) and return best result
        best_source, best_result = max(results, key=lambda x: len(x[1]))

        logger.info(
            "fetch_cn_hybrid_completed",
            source=best_source,
            rows=len(best_result),
            sources_tried=[source for source, _ in results],
        )

        return self._standardize_output(best_result)

    def _standardize_output(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure output DataFrame has standard columns.

        Args:
            df: Input DataFrame

        Returns:
            DataFrame with STANDARD_COLUMNS only
        """
        if df.empty:
            return df

        available_cols = [column for column in STANDARD_COLUMNS if column in df.columns]
        result = df[available_cols].copy()
        result = result.dropna(how="all")

        return result

    def get_source_status(self) -> dict[str, bool]:
        """Get status of available data sources.

        Returns:
            Dict mapping source names to availability status
        """
        return {
            "efinance": self.enable_efinance,
            "akshare": self.enable_akshare,
        }


__all__ = ["CNHybridFetcher"]
