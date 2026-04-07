"""China market source adapter using efinance (modern, faster alternative)."""

from datetime import date

import pandas as pd
import structlog

try:
    import efinance
except ImportError:
    efinance = None

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
        batch_size: int = 50,
    ):
        super().__init__(
            retry_attempts,
            retry_delay,
            ticker_config=ticker_config or TickerConfig(),
            stock_limit=stock_limit,
        )

        if efinance is None:
            raise ImportError("efinance is not installed. Install it with: uv pip install efinance")

        self.filters = filters or {}
        self.max_workers = max_workers
        self.batch_size = batch_size

    def _chunked(self, tickers: list[str], batch_size: int) -> list[list[str]]:
        """Split configured CN tickers into bounded efinance batches."""
        return [tickers[index : index + batch_size] for index in range(0, len(tickers), batch_size)]

    def _standardize_history_frame(
        self,
        stock_data: pd.DataFrame,
        stock_code: str,
    ) -> pd.DataFrame | None:
        """Standardize a single efinance history frame."""
        if stock_data is None or stock_data.empty:
            return None

        stock_data = stock_data.copy()
        stock_data["ticker"] = stock_code
        stock_data["date"] = pd.to_datetime(stock_data["日期"]).dt.date

        column_mapping = {
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }
        stock_data = stock_data.rename(columns=column_mapping)

        if "adj_close" not in stock_data.columns:
            stock_data["adj_close"] = stock_data["close"]

        return stock_data

    def _fetch_history_batch(
        self,
        stock_codes: list[str],
        trading_date: date,
    ) -> list[pd.DataFrame]:
        """Fetch a batch of China A-shares using one efinance history request."""
        try:
            assert efinance is not None
            history = efinance.stock.get_quote_history(
                stock_codes=stock_codes,
                beg=trading_date.strftime("%Y-%m-%d"),
                end=trading_date.strftime("%Y-%m-%d"),
                suppress_error=True,
            )
            if isinstance(history, pd.DataFrame):
                standardized = self._standardize_history_frame(history, stock_codes[0])
                return [standardized] if standardized is not None else []

            frames: list[pd.DataFrame] = []
            for stock_code, stock_data in history.items():
                standardized = self._standardize_history_frame(stock_data, stock_code)
                if standardized is not None:
                    frames.append(standardized)
            return frames

        except Exception as exc:
            logger.warning(
                "efinance_batch_request_failed",
                tickers=stock_codes,
                error=str(exc),
            )
            return []

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch China A-share data for a trading date using efinance."""
        tickers = self._get_configured_tickers("cn")
        logger.info(
            "fetch_cn_ashare_efinance_started",
            date=str(trading_date),
            max_workers=self.max_workers,
            stock_limit=self.stock_limit,
            configured_ticker_count=len(tickers),
        )

        def _fetch() -> pd.DataFrame:
            try:
                if not tickers:
                    logger.warning(
                        "cn_configured_tickers_unavailable",
                        date=str(trading_date),
                        message="Cannot fetch CN data without configured tickers",
                    )
                    return pd.DataFrame()

                frames: list[pd.DataFrame] = []
                batch_size = max(1, min(self.batch_size, len(tickers)))
                ticker_batches = self._chunked(tickers, batch_size)

                with (
                    timer(
                        "parallel_cn_efinance_fetching",
                        stock_count=len(tickers),
                    ),
                ):
                    for ticker_batch in ticker_batches:
                        batch_frames = self._fetch_history_batch(ticker_batch, trading_date)
                        frames.extend(batch_frames)

                success_count = len(frames)
                failure_count = len(tickers) - success_count

                logger.info(
                    "efinance_stock_fetch_completed",
                    success=success_count,
                    failures=failure_count,
                    total=len(tickers),
                    batch_count=len(ticker_batches),
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
                available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
                frame = frame[available_cols]
                frame = frame.dropna(how="all")

                unique_tickers = int(frame["ticker"].nunique()) if "ticker" in frame else 0

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
