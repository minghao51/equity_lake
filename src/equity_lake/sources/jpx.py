"""Japan (JPX) market source adapter."""

from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.core.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher

logger = structlog.get_logger()

DEFAULT_BATCH_SIZE = 500


class JPXEquityFetcher(MarketDataFetcher):
    """Fetch Japanese equity (JPX) data using yfinance.

    Ticker format: Numeric code with .T suffix (e.g., 7203.T for Toyota,
    6758.T for Sony, 9984.T for SoftBank).

    Example tickers:
        7203.T  - Toyota Motor
        6758.T  - Sony Group
        9984.T  - SoftBank Group
        6861.T  - Keyence
        8306.T  - Mitsubishi UFJ Financial
        7974.T  - Nintendo
        9432.T  - Nippon Telegraph & Telephone
        8035.T  - Tokyo Electron
        4063.T  - Shin-Etsu Chemical
        6098.T  - Recruit Holdings
    """

    market = "jpx"

    def __init__(
        self,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: dict[str, Any] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.batch_size = batch_size
        if tickers is not None:
            self.tickers = tickers
            logger.info("Using explicit ticker list: %s tickers (batch_size=%s)", len(tickers), batch_size)
        else:
            self.tickers = self.load_tickers_from_config(ticker_config, filters, self._get_fallback_list())

    @staticmethod
    def _chunked(iterable: list[str], chunk_size: int) -> list[list[str]]:
        """Split iterable into chunks of size chunk_size."""
        chunk_list = list(iterable)
        if not chunk_list:
            return []
        return [chunk_list[i : i + chunk_size] for i in range(0, len(chunk_list), chunk_size)]

    @staticmethod
    def _get_fallback_list() -> list[str]:
        """Return the actual fallback ticker list."""
        return [
            "7203.T",  # Toyota Motor
            "6758.T",  # Sony Group
            "9984.T",  # SoftBank Group
            "6861.T",  # Keyence
            "8306.T",  # Mitsubishi UFJ Financial
            "7974.T",  # Nintendo
            "9432.T",  # Nippon Telegraph & Telephone
            "8035.T",  # Tokyo Electron
            "4063.T",  # Shin-Etsu Chemical
            "6098.T",  # Recruit Holdings
        ]

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch JPX equity data for a trading date."""
        logger.info("Fetching JPX equity data for %s (%s tickers)", trading_date, len(self.tickers))

        def _fetch() -> pd.DataFrame:
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            all_frames: list[pd.DataFrame] = []
            ticker_batches = self._chunked(self.tickers, self.batch_size)
            total_batches = len(ticker_batches)

            logger.info("Downloading in %s batches (batch_size=%s)", total_batches, self.batch_size)

            for _batch_idx, ticker_batch in enumerate(ticker_batches, 1):
                data = yf.download(
                    ticker_batch,
                    start=start_date,
                    end=end_date,
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                )

                if data is None or (hasattr(data, "empty") and data.empty):
                    continue

                if not isinstance(data.columns, pd.MultiIndex):
                    base_frame = data.reset_index()
                    tickers = ticker_batch if len(ticker_batch) > 1 else [ticker_batch[0]]
                    for ticker in tickers:
                        frame = base_frame.copy()
                        frame["ticker"] = ticker
                        all_frames.append(frame)
                else:
                    for ticker in ticker_batch:
                        if ticker in data.columns:
                            ticker_data = data[ticker].reset_index()
                            ticker_data["ticker"] = ticker
                            all_frames.append(ticker_data)

            if not all_frames:
                logger.warning("No data returned for JPX equities on %s", trading_date)
                return pd.DataFrame()

            frame = pd.concat(all_frames, ignore_index=True)
            frame.columns = [str(column).lower() for column in frame.columns]
            frame = frame.rename(columns={"adj close": "adj_close", "datetime": "date", "index": "date"})
            frame["date"] = pd.to_datetime(frame["date"]).dt.date

            available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
            frame = frame[available_cols]
            frame = frame.dropna(how="all")

            unique_tickers = frame["ticker"].nunique() if "ticker" in frame else 0
            logger.info("Fetched %s rows for %s unique JPX tickers", len(frame), unique_tickers)
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["JPXEquityFetcher"]
