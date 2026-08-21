"""Hong Kong and Singapore market source adapters."""

from typing import Any

import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.sources.base import YFinanceBaseFetcher, _apply_market_filters, resolve_tickers

logger = structlog.get_logger()


def _split_group_tickers(config: TickerConfig, groups: list[str]) -> tuple[list[str], list[str]]:
    """Resolve group tickers once, then split into HK/SG by symbol suffix."""
    combined: list[str] = []
    for group in groups:
        combined.extend(config.get_tickers_by_group(str(group)))
    hk_tickers = [t for t in combined if t.endswith(".HK")]
    sg_tickers = [t for t in combined if t.endswith(".SI")]
    return hk_tickers, sg_tickers


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

        if filters and "groups" in filters and isinstance(filters.get("groups"), list):
            hk_tickers, sg_tickers = _split_group_tickers(config, filters["groups"])
            logger.info("Filtered by groups: %s HK, %s SG", len(hk_tickers), len(sg_tickers))
            return hk_tickers, sg_tickers

        if filters:
            hk_tickers = _apply_market_filters(config, "hk", filters)
            sg_tickers = _apply_market_filters(config, "sg", filters)
            return hk_tickers, sg_tickers

        hk_tickers = resolve_tickers(config, "hk", None, _FALLBACK_HK_TICKERS)
        sg_tickers = resolve_tickers(config, "sg", None, _FALLBACK_SG_TICKERS)
        return hk_tickers, sg_tickers


__all__ = ["HKSGEquityFetcher"]
