"""Hong Kong and Singapore market source adapters."""

from typing import Any

import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.sources.base import YFinanceBaseFetcher

logger = structlog.get_logger()

_FALLBACK_HK_TICKERS = [
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
]

_FALLBACK_SG_TICKERS = [
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
]


class HKSGEquityFetcher(YFinanceBaseFetcher):
    """Fetch Hong Kong and Singapore equities using yfinance."""

    market = "hk_sg"

    def __init__(
        self,
        hk_tickers: list[str] | None = None,
        sg_tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: dict[str, Any] | None = None,
    ):
        if hk_tickers is not None or sg_tickers is not None:
            hk_list = hk_tickers or []
            sg_list = sg_tickers or []
        else:
            hk_list, sg_list = self._load_dual_tickers(ticker_config, filters)

        super().__init__(
            tickers=hk_list + sg_list,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            batch_size=self.DEFAULT_BATCH_SIZE,
        )
        self.hk_tickers = hk_list
        self.sg_tickers = sg_list
        logger.info("Using ticker lists: %s HK, %s SG", len(self.hk_tickers), len(self.sg_tickers))

    def _load_dual_tickers(
        self,
        ticker_config: TickerConfig | None,
        filters: dict[str, Any] | None,
    ) -> tuple[list[str], list[str]]:
        try:
            config = ticker_config or TickerConfig()
        except Exception as exc:
            logger.warning("Failed to load ticker config: %s. Using fallback lists.", exc)
            return _FALLBACK_HK_TICKERS, _FALLBACK_SG_TICKERS

        if filters:
            return self._apply_dual_filters(config, filters)

        hk_tickers = config.get_tickers_for_market("hk", active_only=True)
        sg_tickers = config.get_tickers_for_market("sg", active_only=True)
        if not hk_tickers and not sg_tickers:
            logger.warning("No active tickers found in config for HK/SG markets")
            return _FALLBACK_HK_TICKERS, _FALLBACK_SG_TICKERS

        logger.info("Loaded tickers from config: %s HK, %s SG", len(hk_tickers), len(sg_tickers))
        return hk_tickers, sg_tickers

    def _apply_dual_filters(
        self,
        config: TickerConfig,
        filters: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        hk_tickers = config.get_tickers_for_market("hk", active_only=True)
        sg_tickers = config.get_tickers_for_market("sg", active_only=True)

        if "tags" in filters:
            tags = filters["tags"]
            if isinstance(tags, list):
                match_all = bool(filters.get("match_all_tags", False))
                hk_tickers = config.get_tickers_by_tags(tags, match_all=match_all, market="hk")
                sg_tickers = config.get_tickers_by_tags(tags, match_all=match_all, market="sg")
                logger.info("Filtered by tags %s: %s HK, %s SG", tags, len(hk_tickers), len(sg_tickers))
                return hk_tickers, sg_tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                hk_tickers = list({ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market="hk")})
                sg_tickers = list({ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market="sg")})
                logger.info("Filtered by sectors %s: %s HK, %s SG", sectors, len(hk_tickers), len(sg_tickers))
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
                logger.info("Filtered by groups %s: %s HK, %s SG", groups, len(hk_tickers), len(sg_tickers))
                return hk_tickers, sg_tickers

        if "min_priority" in filters:
            min_priority = filters["min_priority"]
            if isinstance(min_priority, int):
                hk_tickers = config.get_tickers_for_market("hk", active_only=True, min_priority=min_priority)
                sg_tickers = config.get_tickers_for_market("sg", active_only=True, min_priority=min_priority)
                logger.info("Filtered by min_priority %s: %s HK, %s SG", min_priority, len(hk_tickers), len(sg_tickers))

        return hk_tickers, sg_tickers


__all__ = ["HKSGEquityFetcher"]
