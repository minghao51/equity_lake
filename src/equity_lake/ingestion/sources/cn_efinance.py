"""China market source adapter using efinance (modern, faster alternative)."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd  # type: ignore[import-untyped]
import structlog

try:
    import efinance  # type: ignore[import-untyped]
except ImportError:
    efinance = None  # type: ignore[assignment]

from equity_lake.config import TickerConfig
from equity_lake.core.logging import timer
from equity_lake.core.runtime import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()


class CNEfinanceFetcher(MarketDataFetcher):
    """Fetch China A-share EOD data using efinance.

    efinance is a modern, faster alternative to akshare for China market data.
    It provides better stability and real-time data capabilities.

    Key advantages over akshare:
    - Faster data retrieval (optimized API calls)
    - Better real-time data support
    - More stable connection handling
    - Cleaner data output (less post-processing needed)
    """

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
        max_workers: int = 10,
        stock_limit: int = 100,
    ):
        super().__init__(retry_attempts, retry_delay)

        if efinance is None:
            raise ImportError(
                "efinance is not installed. Install it with: uv pip install efinance"
            )

        self.ticker_config = ticker_config or TickerConfig()
        self.filters = filters or {}
        self.max_workers = max_workers
        self.stock_limit = stock_limit

    def _fetch_single_stock(
        self,
        stock_code: str,
        trading_date: date,
    ) -> pd.DataFrame | None:
        """Fetch one China A-share using efinance.

        efinance uses different API than akshare:
        - Uses stock code format directly (e.g., '000001' for Ping An)
        - Returns cleaner data structure
        - Better error handling
        """
        try:
            # efinance API for historical data
            assert efinance is not None
            stock_data = efinance.stock.get_quote_history(
                stock_code=stock_code,
                beg=trading_date.strftime("%Y-%m-%d"),
                end=trading_date.strftime("%Y-%m-%d"),
            )

            if stock_data is None or stock_data.empty:
                return None

            # efinance returns data with English column names already
            # Column mapping: 股票代码, 日期, 开盘, 收盘, 最高, 最低, 成交量
            stock_data["ticker"] = stock_code
            stock_data["date"] = pd.to_datetime(stock_data["日期"]).dt.date

            # Rename columns to standard format
            column_mapping = {
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }

            stock_data = stock_data.rename(columns=column_mapping)

            # Add adj_close if not present
            if "adj_close" not in stock_data.columns:
                stock_data["adj_close"] = stock_data["close"]

            return stock_data

        except Exception as exc:
            logger.debug("Failed to fetch %s via efinance: %s", stock_code, exc)
            return None

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch China A-share data for a trading date using efinance."""
        logger.info(
            "fetch_cn_ashare_efinance_started",
            date=str(trading_date),
            max_workers=self.max_workers,
            stock_limit=self.stock_limit,
        )

        def _fetch() -> pd.DataFrame:
            try:
                # Get all China A-share stock codes
                logger.info("fetching_stock_list_from_efinance")
                assert efinance is not None
                stock_list = efinance.stock.get_realtime_quotes()

                if stock_list is None or stock_list.empty:
                    logger.warning("efinance returned empty stock list")
                    return pd.DataFrame()

                # Extract stock codes (remove market suffix for fetching)
                all_stock_codes = stock_list["股票代码"].unique().tolist()
                logger.info(
                    "stock_list_fetched_from_efinance",
                    total_stocks=len(all_stock_codes),
                )

                # Sample stocks if limit is set
                sample_codes = all_stock_codes[: self.stock_limit]
                logger.info(
                    "stock_sample_selected",
                    sample_size=len(sample_codes),
                    total_stocks=len(all_stock_codes),
                )

                frames: list[pd.DataFrame] = []
                success_count = 0
                failure_count = 0

                with (
                    timer(
                        "parallel_cn_efinance_fetching",
                        stock_count=len(sample_codes),
                    ),
                    ThreadPoolExecutor(max_workers=self.max_workers) as executor,
                ):
                    futures = {
                        executor.submit(
                            self._fetch_single_stock,
                            stock_code,
                            trading_date,
                        ): stock_code
                        for stock_code in sample_codes
                    }

                    for future in as_completed(futures):
                        stock_code = futures[future]
                        try:
                            result = future.result(timeout=30)
                            if result is not None and not result.empty:
                                frames.append(result)
                                success_count += 1
                            else:
                                failure_count += 1
                        except Exception as exc:
                            logger.debug(
                                "stock_fetch_exception",
                                stock=stock_code,
                                error=str(exc),
                            )
                            failure_count += 1

                logger.info(
                    "efinance_stock_fetch_completed",
                    success=success_count,
                    failures=failure_count,
                    total=len(sample_codes),
                )

                if not frames:
                    logger.warning(
                        "no_cn_ashare_data",
                        date=str(trading_date),
                        message="No data returned for China A-shares via efinance",
                    )
                    return pd.DataFrame()

                # Concatenate all frames
                frame = pd.concat(frames, ignore_index=True)

                # Ensure standard column set
                available_cols = [
                    column for column in STANDARD_COLUMNS if column in frame.columns
                ]
                frame = frame[available_cols]
                frame = frame.dropna(how="all")

                unique_tickers = (
                    int(frame["ticker"].nunique()) if "ticker" in frame else 0
                )

                logger.info(
                    "fetch_cn_ashare_efinance_completed",
                    rows=len(frame),
                    unique_tickers=unique_tickers,
                )

                return frame

            except Exception as exc:
                logger.error(
                    "fetch_cn_ashare_efinance_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    date=str(trading_date),
                )
                raise

        return self._retry_on_failure(_fetch)


__all__ = ["CNEfinanceFetcher"]
