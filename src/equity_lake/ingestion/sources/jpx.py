"""Japan (JPX) market source adapter."""

from datetime import date, timedelta

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()

# Batch size for yfinance downloads
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

        tickers = config.get_tickers_for_market("jpx", active_only=True)
        if not tickers:
            logger.warning("No active JPX tickers found in config. Using FALLBACK ticker list. Check config/tickers.yaml for proper configuration.")
            return self._get_fallback_tickers()

        logger.info("Loaded %s tickers from config for JPX market", len(tickers))
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
                    market="jpx",
                )
                logger.info("Filtered by tags %s: %s tickers", tags, len(tickers))
                return tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                ticker_set = {ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market="jpx")}
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
                    "jpx",
                    active_only=True,
                    min_priority=min_priority,
                )
                logger.info(
                    "Filtered by min_priority %s: %s tickers",
                    min_priority,
                    len(tickers),
                )
                return tickers

        return config.get_tickers_for_market("jpx", active_only=True)

    def _chunked(self, iterable: list[str], chunk_size: int) -> list[list[str]]:
        """Split iterable into chunks of size chunk_size."""
        chunk_list = list(iterable)
        if not chunk_list:
            return []
        return [chunk_list[i : i + chunk_size] for i in range(0, len(chunk_list), chunk_size)]

    def _get_fallback_tickers(self) -> list[str]:
        """Return a curated fallback list of major JPX-listed companies."""
        fallback_list = self._get_fallback_list()
        logger.warning(
            "Using FALLBACK ticker list for JPX market (%s tickers). Config-based approach recommended - check config/tickers.yaml",
            len(fallback_list),
        )
        return fallback_list

    def _get_fallback_list(self) -> list[str]:
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
        logger.info(
            "Fetching JPX equity data for %s (%s tickers)",
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
                "Fetched %s rows for %s unique JPX tickers",
                len(frame),
                unique_tickers,
            )
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["JPXEquityFetcher"]
