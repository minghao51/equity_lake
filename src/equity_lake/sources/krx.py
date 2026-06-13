"""South Korea (KRX) market source adapter."""

from datetime import date, timedelta
from typing import Any

import pandas as pd
import polars as pl
import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame, standardize_columns

logger = structlog.get_logger()

DEFAULT_RETRY_DELAY = 2.0


class KRXEquityFetcher(MarketDataFetcher):
    """Fetch South Korean equity (KRX) data using FinanceDataReader.

    Ticker format: 6-digit numeric code (e.g., 005930 for Samsung Electronics,
    000660 for SK Hynix, 035420 for Naver).

    Example tickers:
        005930  - Samsung Electronics
        000660  - SK Hynix
        035420  - Naver
        005380  - Hyundai Motor
        051910  - LG Health & Household
        035720  - Kakao
        068270  - Celltrion
        207940  - Samsung Biologics
        006400  - Samsung SDI
        028260  - Samsung C&T
    """

    market = "krx"

    def __init__(
        self,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        ticker_config: TickerConfig | None = None,
        filters: dict[str, Any] | None = None,
    ):
        super().__init__(retry_attempts, retry_delay)
        if tickers is not None:
            self.tickers = tickers
            logger.info("Using explicit ticker list: %s tickers", len(tickers))
        else:
            self.tickers = self.load_tickers_from_config(ticker_config, filters, self._get_fallback_list())

    @staticmethod
    def _get_fallback_list() -> list[str]:
        """Return the actual fallback ticker list."""
        return [
            "005930",  # Samsung Electronics
            "000660",  # SK Hynix
            "035420",  # Naver
            "005380",  # Hyundai Motor
            "051910",  # LG Health & Household
            "035720",  # Kakao
            "068270",  # Celltrion
            "207940",  # Samsung Biologics
            "006400",  # Samsung SDI
            "028260",  # Samsung C&T
        ]

    def fetch(self, trading_date: date) -> pl.DataFrame:
        """Fetch KRX equity data for a trading date using FinanceDataReader."""
        logger.info("Fetching KRX equity data for %s (%s tickers)", trading_date, len(self.tickers))

        def _fetch() -> pl.DataFrame:
            try:
                import FinanceDataReader as fdr
            except ImportError:
                msg = "FinanceDataReader is required for KRX market data. Install it with: pip install finance-datareader"
                raise ImportError(msg) from None

            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            all_frames: list[pd.DataFrame] = []

            for ticker in self.tickers:
                try:
                    data = fdr.DataReader(ticker, start_date, end_date)

                    if data is None or data.empty:
                        continue

                    data = data.reset_index()
                    data["ticker"] = ticker
                    all_frames.append(data)

                except Exception as exc:
                    logger.debug("Failed to fetch data for %s: %s", ticker, exc)
                    continue

            if not all_frames:
                logger.warning("No data returned for KRX equities on %s", trading_date)
                return _empty_frame()

            frame = pd.concat(all_frames, ignore_index=True)
            frame = standardize_columns(frame, rename={"adj close": "adj_close", "adjclose": "adj_close"}, columns=STANDARD_COLUMNS)
            unique_tickers = frame["ticker"].n_unique() if "ticker" in frame.columns else 0
            logger.info("Fetched %s rows for %s unique KRX tickers", frame.height, unique_tickers)
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["KRXEquityFetcher"]
