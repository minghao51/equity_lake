"""Hong Kong and Singapore market source adapters."""

from datetime import date, timedelta

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.config import TickerConfig
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.ingestion.models import FilterConfig
from equity_lake.ingestion.sources.base import MarketDataFetcher

logger = structlog.get_logger()


class HKSGEquityFetcher(MarketDataFetcher):
    """Fetch Hong Kong and Singapore equities using yfinance."""

    def __init__(
        self,
        hk_tickers: list[str] | None = None,
        sg_tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: FilterConfig | None = None,
    ):
        super().__init__(retry_attempts, retry_delay)
        if hk_tickers is not None or sg_tickers is not None:
            self.hk_tickers = hk_tickers or []
            self.sg_tickers = sg_tickers or []
            logger.info(
                "Using explicit ticker lists: %s HK, %s SG",
                len(self.hk_tickers),
                len(self.sg_tickers),
            )
        else:
            self.hk_tickers, self.sg_tickers = self._load_tickers_from_config(
                ticker_config,
                filters,
            )

    def _load_tickers_from_config(
        self,
        ticker_config: TickerConfig | None,
        filters: FilterConfig | None,
    ) -> tuple[list[str], list[str]]:
        try:
            config = ticker_config or TickerConfig()
        except Exception as exc:
            logger.warning(
                "Failed to load ticker config: %s. Using fallback lists.",
                exc,
            )
            return self._get_fallback_tickers()

        if filters:
            return self._apply_filters(config, filters)

        hk_tickers = config.get_tickers_for_market("hk", active_only=True)
        sg_tickers = config.get_tickers_for_market("sg", active_only=True)
        if not hk_tickers and not sg_tickers:
            logger.warning("No active tickers found in config for HK/SG markets")
            return self._get_fallback_tickers()

        logger.info(
            "Loaded tickers from config: %s HK, %s SG",
            len(hk_tickers),
            len(sg_tickers),
        )
        return hk_tickers, sg_tickers

    def _apply_filters(
        self,
        config: TickerConfig,
        filters: FilterConfig,
    ) -> tuple[list[str], list[str]]:
        hk_tickers = config.get_tickers_for_market("hk", active_only=True)
        sg_tickers = config.get_tickers_for_market("sg", active_only=True)

        if "tags" in filters:
            tags = filters["tags"]
            if isinstance(tags, list):
                match_all = bool(filters.get("match_all_tags", False))
                hk_tickers = config.get_tickers_by_tags(
                    tags,
                    match_all=match_all,
                    market="hk",
                )
                sg_tickers = config.get_tickers_by_tags(
                    tags,
                    match_all=match_all,
                    market="sg",
                )
                logger.info(
                    "Filtered by tags %s: %s HK, %s SG",
                    tags,
                    len(hk_tickers),
                    len(sg_tickers),
                )
                return hk_tickers, sg_tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                hk_tickers = list(
                    {
                        ticker
                        for sector in sectors
                        for ticker in config.get_tickers_by_sector(
                            str(sector),
                            market="hk",
                        )
                    }
                )
                sg_tickers = list(
                    {
                        ticker
                        for sector in sectors
                        for ticker in config.get_tickers_by_sector(
                            str(sector),
                            market="sg",
                        )
                    }
                )
                logger.info(
                    "Filtered by sectors %s: %s HK, %s SG",
                    sectors,
                    len(hk_tickers),
                    len(sg_tickers),
                )
                return hk_tickers, sg_tickers

        if "groups" in filters:
            groups = filters["groups"]
            if isinstance(groups, list):
                hk_grouped: set[str] = set()
                sg_grouped: set[str] = set()
                for group in groups:
                    for ticker in config.get_tickers_by_group(str(group)):
                        if ticker.endswith(".HK"):
                            hk_grouped.add(ticker)
                        elif ticker.endswith(".SI"):
                            sg_grouped.add(ticker)
                hk_tickers = list(hk_grouped)
                sg_tickers = list(sg_grouped)
                logger.info(
                    "Filtered by groups %s: %s HK, %s SG",
                    groups,
                    len(hk_tickers),
                    len(sg_tickers),
                )
                return hk_tickers, sg_tickers

        if "min_priority" in filters:
            min_priority = filters["min_priority"]
            if isinstance(min_priority, int):
                hk_tickers = config.get_tickers_for_market(
                    "hk",
                    active_only=True,
                    min_priority=min_priority,
                )
                sg_tickers = config.get_tickers_for_market(
                    "sg",
                    active_only=True,
                    min_priority=min_priority,
                )
                logger.info(
                    "Filtered by min_priority %s: %s HK, %s SG",
                    min_priority,
                    len(hk_tickers),
                    len(sg_tickers),
                )

        return hk_tickers, sg_tickers

    def _get_fallback_tickers(self) -> tuple[list[str], list[str]]:
        """Return the legacy hardcoded fallback lists."""
        logger.warning("Using fallback ticker lists (config-based approach recommended)")
        return (
            [
                "0700.HK",
                "9988.HK",
                "0941.HK",
                "1299.HK",
                "2318.HK",
                "0939.HK",
                "1398.HK",
                "0883.HK",
                "0857.HK",
                "1038.HK",
                "0027.HK",
                "0016.HK",
                "0005.HK",
                "0388.HK",
                "0011.HK",
            ],
            [
                "D05.SI",
                "O39.SI",
                "U11.SI",
                "Z74.SI",
                "C6L.SI",
                "S68.SI",
                "V03.SI",
                "BS6.SI",
                "G13.SI",
                "S63.SI",
            ],
        )

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch HK and SG equity data for a trading date."""
        logger.info("Fetching HK/SG equity data for %s", trading_date)

        def _fetch() -> pd.DataFrame:
            all_tickers = self.hk_tickers + self.sg_tickers
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")
            data = yf.download(
                all_tickers,
                start=start_date,
                end=end_date,
                group_by="ticker",
                progress=False,
                auto_adjust=False,
            )
            if data is None or (hasattr(data, "empty") and data.empty):
                logger.warning(
                    "No data returned for HK/SG equities on %s",
                    trading_date,
                )
                return pd.DataFrame()

            frames = []
            for ticker in all_tickers:
                if ticker in data.columns:
                    ticker_data = data[ticker].reset_index()
                    ticker_data["ticker"] = ticker
                    frames.append(ticker_data)
            if not frames:
                return pd.DataFrame()

            frame = pd.concat(frames, ignore_index=True)
            frame.columns = [str(column).lower() for column in frame.columns]
            frame = frame.rename(columns={"adj close": "adj_close"})
            frame["date"] = pd.to_datetime(frame["date"]).dt.date
            available_cols = [column for column in STANDARD_COLUMNS if column in frame.columns]
            frame = frame[available_cols]
            frame = frame.dropna(how="all")
            logger.info("Fetched %s rows for HK/SG equities", len(frame))
            return frame

        return self._retry_on_failure(_fetch)


__all__ = ["HKSGEquityFetcher"]
