"""US market source adapter."""

from datetime import date, timedelta

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()

# Batch size for yfinance downloads (avoid rate limits)
DEFAULT_BATCH_SIZE = 500


class USEquityFetcher(MarketDataFetcher):
    """Fetch US equity EOD data using yfinance."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.batch_size = batch_size
        if tickers is not None:
            self.tickers = tickers
            logger.info(
                "Using explicit ticker list: %s tickers (batch_size=%s)",
                len(tickers),
                batch_size,
            )
        else:
            self.tickers = self._load_tickers_from_config(ticker_config, filters)

    def _load_tickers_from_config(
        self,
        ticker_config: TickerConfig | None,
        filters: FilterConfig | None,
    ) -> list[str]:
        try:
            config = ticker_config or TickerConfig()
        except Exception as exc:
            logger.warning(
                "Failed to load ticker config: %s. Using fallback list.",
                exc,
            )
            return self._get_fallback_tickers()

        if filters:
            return self._apply_filters(config, filters)

        tickers = config.get_tickers_for_market("us", active_only=True)
        if not tickers:
            logger.warning("No active tickers found in config for US market")
            return self._get_fallback_tickers()

        logger.info("Loaded %s tickers from config for US market", len(tickers))
        return tickers

    def _apply_filters(self, config: TickerConfig, filters: FilterConfig) -> list[str]:
        """Apply config-based ticker filters."""
        if "tags" in filters:
            tags = filters["tags"]
            if isinstance(tags, list):
                match_all = bool(filters.get("match_all_tags", False))
                tickers = config.get_tickers_by_tags(
                    tags,
                    match_all=match_all,
                    market="us",
                )
                logger.info("Filtered by tags %s: %s tickers", tags, len(tickers))
                return tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                ticker_set = {ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market="us")}
                result = list(ticker_set)
                logger.info("Filtered by sectors %s: %s tickers", sectors, len(result))
                return result

        if "groups" in filters:
            groups = filters["groups"]
            if isinstance(groups, list):
                ticker_set = {ticker for group in groups for ticker in config.get_tickers_by_group(str(group))}
                result = list(ticker_set)
                logger.info("Filtered by groups %s: %s tickers", groups, len(result))
                return result

        if "min_priority" in filters:
            min_priority = filters["min_priority"]
            if isinstance(min_priority, int):
                tickers = config.get_tickers_for_market(
                    "us",
                    active_only=True,
                    min_priority=min_priority,
                )
                logger.info(
                    "Filtered by min_priority %s: %s tickers",
                    min_priority,
                    len(tickers),
                )
                return tickers

        return config.get_tickers_for_market("us", active_only=True)

    def _chunked(self, iterable: list[str], chunk_size: int) -> list[list[str]]:
        """Split iterable into chunks of size chunk_size."""
        chunk_list = list(iterable)
        if not chunk_list:
            return [[]]
        return [chunk_list[i : i + chunk_size] for i in range(0, len(chunk_list), chunk_size)]

    def _get_fallback_tickers(self) -> list[str]:
        """Return the legacy hardcoded fallback list."""
        logger.warning("Using fallback ticker list (config-based approach recommended)")
        return [
            "AAPL",
            "MSFT",
            "GOOGL",
            "AMZN",
            "NVDA",
            "META",
            "TSLA",
            "BRK-B",
            "LLY",
            "AVGO",
            "JPM",
            "V",
            "JNJ",
            "WMT",
            "MA",
            "PG",
            "COST",
            "UNH",
            "XOM",
            "HD",
            "CVX",
            "MRK",
            "ABBV",
            "BAC",
            "KO",
            "PEP",
            "CRM",
            "NFLX",
            "AMD",
            "TMO",
            "LIN",
            "ABT",
            "ORCL",
            "ADBE",
            "CMCSA",
            "WFC",
            "COP",
            "QCOM",
            "INTC",
            "DHR",
            "VZ",
            "IBM",
            "GE",
            "DIS",
            "BA",
            "NKE",
            "CAT",
        ]

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch US equity data for a trading date using batched downloads."""
        logger.info(
            "Fetching US equity data for %s (%s tickers)",
            trading_date,
            len(self.tickers),
        )

        def _fetch() -> pd.DataFrame:
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            all_frames: list[pd.DataFrame] = []
            ticker_batches = self._chunked(self.tickers, self.batch_size)
            total_batches = len(ticker_batches)

            logger.info(
                "Downloading in %s batches (batch_size=%s)",
                total_batches,
                self.batch_size,
            )

            for batch_idx, ticker_batch in enumerate(ticker_batches, 1):
                logger.debug(
                    "Processing batch %s/%s (%s tickers)",
                    batch_idx,
                    total_batches,
                    len(ticker_batch),
                )

                data = yf.download(
                    ticker_batch,
                    start=start_date,
                    end=end_date,
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                )

                if data is None or (hasattr(data, "empty") and data.empty):
                    logger.debug("No data for batch %s", batch_idx)
                    continue

                # yfinance can return either a MultiIndex frame (normal batched
                # response) or a flat frame. Handle both so we do not silently
                # drop valid data when the schema varies.
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

                logger.debug(
                    "Completed batch %s/%s (cumulative: %s frames)",
                    batch_idx,
                    total_batches,
                    len(all_frames),
                )

            if not all_frames:
                logger.warning("No data returned for US equities on %s", trading_date)
                return pd.DataFrame()

            frame = pd.concat(all_frames, ignore_index=True)
            frame.columns = [str(column).lower() for column in frame.columns]
            frame = frame.rename(
                columns={
                    "adj close": "adj_close",
                    "datetime": "date",
                    "index": "date",
                }
            )
            frame["date"] = pd.to_datetime(frame["date"]).dt.date
            available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
            frame = frame[available_cols]
            frame = frame.dropna(how="all")

            unique_tickers = frame["ticker"].nunique() if "ticker" in frame else 0
            logger.info(
                "Fetched %s rows for %s unique US tickers",
                len(frame),
                unique_tickers,
            )
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["USEquityFetcher"]
