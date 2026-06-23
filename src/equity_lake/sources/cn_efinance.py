"""China market source adapter using efinance (modern, faster alternative)."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pandas as pd
import polars as pl
import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.core.logging import timer
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame, standardize_columns

try:
    import efinance
except ImportError:
    efinance = None

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
        filters: dict[str, Any] | None = None,
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
    ) -> pl.DataFrame | None:
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
        if "adj_close" not in stock_data.columns and "收盘" in stock_data.columns:
            stock_data["adj_close"] = stock_data["收盘"]

        return standardize_columns(stock_data, rename=column_mapping, columns=STANDARD_COLUMNS)

    def _fetch_history_batch(
        self,
        stock_codes: list[str],
        trading_date: date,
    ) -> list[pl.DataFrame]:
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
                if "股票代码" in history.columns and history["股票代码"].nunique() > 1:
                    frames: list[pl.DataFrame] = []
                    for stock_code, stock_data in history.groupby("股票代码"):
                        standardized = self._standardize_history_frame(stock_data, str(stock_code))
                        if standardized is not None:
                            frames.append(standardized)
                    return frames
                standardized = self._standardize_history_frame(history, stock_codes[0])
                return [standardized] if standardized is not None else []

            standardized_frames: list[pl.DataFrame] = []
            for stock_code, stock_data in history.items():
                standardized = self._standardize_history_frame(stock_data, stock_code)
                if standardized is not None:
                    standardized_frames.append(standardized)
            return standardized_frames

        except Exception as exc:
            logger.warning(
                "efinance_batch_request_failed",
                tickers=stock_codes,
                error=str(exc),
            )
            return []

    def fetch(self, trading_date: date) -> pl.DataFrame:
        """Fetch China A-share data for a trading date using efinance."""
        tickers = self._get_configured_tickers("cn")
        logger.info(
            "fetch_cn_ashare_efinance_started",
            date=str(trading_date),
            max_workers=self.max_workers,
            stock_limit=self.stock_limit,
            configured_ticker_count=len(tickers),
        )

        def _fetch() -> pl.DataFrame:
            try:
                if not tickers:
                    logger.warning(
                        "cn_configured_tickers_unavailable",
                        date=str(trading_date),
                        message="Cannot fetch CN data without configured tickers",
                    )
                    return _empty_frame()

                frames: list[pl.DataFrame] = []
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
                    return _empty_frame()

                frame = pl.concat(frames, how="vertical_relaxed")
                frame = frame.filter(~pl.all_horizontal(pl.all().is_null()))
                unique_tickers = int(frame["ticker"].n_unique()) if "ticker" in frame.columns else 0

                logger.info(
                    "fetch_cn_ashare_efinance_completed",
                    rows=frame.height,
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

        return cast(pl.DataFrame, self._retry_on_failure(_fetch))


__all__ = ["CNEfinanceFetcher"]
