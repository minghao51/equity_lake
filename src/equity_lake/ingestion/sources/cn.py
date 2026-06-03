"""China market source adapter."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import akshare as ak
import pandas as pd
import structlog

from equity_lake.config import TickerConfig
from equity_lake.core.logging import timer
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()


class CNAshareFetcher(MarketDataFetcher):
    """Fetch China A-share EOD data using akshare."""

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
        max_workers: int = 10,
        stock_limit: int = 100,
        adaptive_threshold: float = 0.2,
    ):
        super().__init__(
            retry_attempts,
            retry_delay,
            ticker_config=ticker_config or TickerConfig(),
            stock_limit=stock_limit,
        )
        self.filters = filters or {}
        self.max_workers = max_workers
        self.adaptive_threshold = adaptive_threshold

    def _fetch_single_stock(
        self,
        stock_code: str,
        date_str: str,
        trading_date: date,
    ) -> pd.DataFrame | None:
        """Fetch one China A-share."""
        try:
            stock_data = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=date_str,
                end_date=date_str,
                adjust="",
            )
            if stock_data.empty:
                return None
            stock_data["ticker"] = stock_code
            stock_data["date"] = trading_date
            return stock_data
        except Exception as exc:
            logger.debug("Failed to fetch %s: %s", stock_code, exc)
            return None

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch China A-share data for a trading date."""
        tickers = self._get_configured_tickers("cn")
        logger.info(
            "fetch_cn_ashare_started",
            date=str(trading_date),
            max_workers=self.max_workers,
            stock_limit=self.stock_limit,
            configured_ticker_count=len(tickers),
        )

        def _fetch() -> pd.DataFrame:
            date_str = trading_date.strftime("%Y%m%d")
            try:
                if not tickers:
                    logger.warning(
                        "cn_configured_tickers_unavailable",
                        date=str(trading_date),
                        message="Cannot fetch CN data without configured tickers",
                    )
                    return pd.DataFrame()

                frames: list[pd.DataFrame] = []
                success_count = 0
                failure_count = 0
                batch_size = len(tickers)
                check_interval = max(1, batch_size // 4)

                with (
                    timer("parallel_cn_stock_fetching", stock_count=len(tickers)),
                    ThreadPoolExecutor(max_workers=self.max_workers) as executor,
                ):
                    futures = {
                        executor.submit(
                            self._fetch_single_stock,
                            stock_code,
                            date_str,
                            trading_date,
                        ): stock_code
                        for stock_code in tickers
                    }
                    for i, future in enumerate(as_completed(futures)):
                        stock_code = futures[future]
                        try:
                            result = future.result(timeout=30)
                            if result is not None:
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

                        if (i + 1) % check_interval == 0 and (success_count + failure_count) > 0:
                            failure_rate = failure_count / (success_count + failure_count)
                            if failure_rate > self.adaptive_threshold:
                                new_workers = max(1, executor._max_workers - 2)
                                if new_workers < executor._max_workers:
                                    executor._max_workers = new_workers
                                    logger.warning(
                                        "adaptive_throttle",
                                        failure_rate=f"{failure_rate:.0%}",
                                        reduced_workers=new_workers,
                                    )

                logger.info(
                    "stock_fetch_completed",
                    success=success_count,
                    failures=failure_count,
                    total=len(tickers),
                )

                if not frames:
                    logger.warning(
                        "no_cn_ashare_data",
                        date=str(trading_date),
                        message="No data returned for China A-shares",
                    )
                    return pd.DataFrame()

                frame = pd.concat(frames, ignore_index=True)
                frame = frame.rename(
                    columns={
                        "开盘": "open",
                        "最高": "high",
                        "最低": "low",
                        "收盘": "close",
                        "成交量": "volume",
                    }
                )
                if "adj_close" not in frame.columns:
                    frame["adj_close"] = frame["close"]
                available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
                frame = frame[available_cols]
                frame = frame.dropna(how="all")
                unique_tickers = int(frame["ticker"].nunique()) if "ticker" in frame else 0
                logger.info(
                    "fetch_cn_ashare_completed",
                    rows=len(frame),
                    unique_tickers=unique_tickers,
                )
                return frame
            except Exception as exc:
                logger.error(
                    "fetch_cn_ashare_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    date=str(trading_date),
                )
                return pd.DataFrame()

        return self._retry_on_failure(_fetch)


__all__ = ["CNAshareFetcher"]
