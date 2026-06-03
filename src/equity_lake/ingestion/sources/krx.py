"""South Korea (KRX) market source adapter."""

from datetime import date, timedelta

import pandas as pd
import structlog

from equity_lake.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()

# Retry delay is higher for KRX due to API rate limits
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

    def __init__(
        self,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
    ):
        super().__init__(retry_attempts, retry_delay)
        if tickers is not None:
            self.tickers = tickers
            logger.info("Using explicit ticker list: %s tickers", len(tickers))
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

        tickers = config.get_tickers_for_market("krx", active_only=True)
        if not tickers:
            logger.warning("No active KRX tickers found in config. Using FALLBACK ticker list. Check config/tickers.yaml for proper configuration.")
            return self._get_fallback_tickers()

        logger.info("Loaded %s tickers from config for KRX market", len(tickers))
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
                    market="krx",
                )
                logger.info("Filtered by tags %s: %s tickers", tags, len(tickers))
                return tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                ticker_set = {ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market="krx")}
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
                    "krx",
                    active_only=True,
                    min_priority=min_priority,
                )
                logger.info(
                    "Filtered by min_priority %s: %s tickers",
                    min_priority,
                    len(tickers),
                )
                return tickers

        return config.get_tickers_for_market("krx", active_only=True)

    def _get_fallback_tickers(self) -> list[str]:
        """Return a curated fallback list of major KRX-listed companies."""
        fallback_list = [
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
        logger.warning(
            "Using FALLBACK ticker list for KRX market (%s tickers). Config-based approach recommended - check config/tickers.yaml",
            len(fallback_list),
        )
        return fallback_list

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch KRX equity data for a trading date using FinanceDataReader."""
        logger.info(
            "Fetching KRX equity data for %s (%s tickers)",
            trading_date,
            len(self.tickers),
        )

        def _fetch() -> pd.DataFrame:
            try:
                import FinDataReader as fdr
            except ImportError:
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
                    # FinanceDataReader uses Korean market code prefix
                    # For KRX, we just use the numeric ticker
                    data = fdr.StockDataReader(
                        ticker,
                        start_date,
                        end_date,
                    ).read()

                    if data is None or data.empty:
                        continue

                    data = data.reset_index()
                    data["ticker"] = ticker
                    all_frames.append(data)

                except Exception as exc:
                    logger.debug(
                        "Failed to fetch data for %s: %s",
                        ticker,
                        exc,
                    )
                    continue

            if not all_frames:
                logger.warning("No data returned for KRX equities on %s", trading_date)
                return pd.DataFrame()

            frame = pd.concat(all_frames, ignore_index=True)
            frame.columns = [str(column).lower() for column in frame.columns]

            # Standardize column names
            frame = frame.rename(
                columns={
                    "date": "date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                    "adj close": "adj_close",
                    "adjclose": "adj_close",
                }
            )

            # Ensure date is date type
            if "date" in frame.columns:
                frame["date"] = pd.to_datetime(frame["date"]).dt.date

            available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
            frame = frame[available_cols]
            frame = frame.dropna(how="all")

            unique_tickers = frame["ticker"].nunique() if "ticker" in frame else 0
            logger.info(
                "Fetched %s rows for %s unique KRX tickers",
                len(frame),
                unique_tickers,
            )
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["KRXEquityFetcher"]
