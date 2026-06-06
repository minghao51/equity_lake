"""Base ingestion source adapters."""

import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.fetch_macro import MacroIndicatorFetcher

logger = structlog.get_logger()


class MarketDataFetcher:
    """Base class for market data fetchers."""

    market: str = ""

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        stock_limit: int = 100,
    ):
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.ticker_config = ticker_config
        self.stock_limit = stock_limit

    def _get_configured_tickers(self, market: str) -> list[str]:
        """Load the configured ticker universe for deterministic daily runs."""
        if self.ticker_config is None:
            return []
        tickers = self.ticker_config.get_tickers_for_market(market, active_only=True)
        configured_count = len(tickers)

        if configured_count == 0:
            logger.warning(
                f"{market}_configured_tickers_empty",
                message=f"No configured {market} tickers",
            )
            return []

        selected = tickers[: self.stock_limit]
        logger.info(
            f"{market}_configured_tickers_loaded",
            configured_ticker_count=configured_count,
            selected_ticker_count=len(selected),
            stock_limit=self.stock_limit,
        )
        return selected

    def load_tickers_from_config(
        self,
        ticker_config: TickerConfig | None,
        filters: dict[str, Any] | None,
        fallback_list: list[str] | None = None,
    ) -> list[str]:
        """Load tickers from config with optional filtering and fallback."""
        try:
            config = ticker_config or TickerConfig()
        except Exception as exc:
            logger.warning("Failed to load ticker config: %s. Using fallback list.", exc)
            return fallback_list or []

        if filters:
            return self._apply_filters(config, filters)

        tickers = config.get_tickers_for_market(self.market, active_only=True)
        if not tickers:
            logger.warning(
                "No active %s tickers found in config. Using FALLBACK ticker list. Check config/tickers.yaml for proper configuration.",
                self.market.upper(),
            )
            return fallback_list or []

        logger.info("Loaded %s tickers from config for %s market", len(tickers), self.market.upper())
        return tickers

    def _apply_filters(self, config: TickerConfig, filters: dict[str, Any]) -> list[str]:
        """Apply config-based ticker filters."""
        if "tags" in filters:
            tags = filters["tags"]
            if isinstance(tags, list):
                match_all = bool(filters.get("match_all_tags", False))
                tickers = config.get_tickers_by_tags(tags, match_all=match_all, market=self.market)
                logger.info("Filtered by tags %s: %s tickers", tags, len(tickers))
                return tickers

        if "sectors" in filters:
            sectors = filters["sectors"]
            if isinstance(sectors, list):
                ticker_set = {ticker for sector in sectors for ticker in config.get_tickers_by_sector(str(sector), market=self.market)}
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
                tickers = config.get_tickers_for_market(self.market, active_only=True, min_priority=min_priority)
                logger.info("Filtered by min_priority %s: %s tickers", min_priority, len(tickers))
                return tickers

        return config.get_tickers_for_market(self.market, active_only=True)

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch data for a specific date."""
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Retry API calls with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                result: Any = func(*args, **kwargs)
                if result is None:
                    return pd.DataFrame()
                if isinstance(result, pd.DataFrame):
                    return result
                if isinstance(result, pd.Series):
                    return result.to_frame().T
                return pd.DataFrame()
            except Exception as exc:
                last_error = exc
                if attempt < self.retry_attempts - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        "Attempt %s failed: %s. Retrying in %.1fs...",
                        attempt + 1,
                        exc,
                        wait_time,
                    )
                    time.sleep(wait_time)
                else:
                    logger.error("All %s attempts failed: %s", self.retry_attempts, exc)
                    raise last_error from exc
        return pd.DataFrame()


__all__ = ["MacroIndicatorFetcher", "MarketDataFetcher"]
