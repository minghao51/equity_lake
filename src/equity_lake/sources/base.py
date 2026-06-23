"""Base ingestion source adapters."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, cast

import httpx
import pandas as pd
import polars as pl
import requests
import structlog
import yfinance as yf
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from equity_lake.core.config import TickerConfig
from equity_lake.core.polars_utils import ensure_polars, normalize_temporal_columns
from equity_lake.core.schemas import STANDARD_COLUMNS
from equity_lake.sources.macro import MacroIndicatorFetcher

logger = structlog.get_logger()


class TransientError(Exception):
    """Raised for retryable failures (network, timeout, 5xx).

    Non-retryable errors (4xx, bad config) should be raised as a different
    exception type so the tenacity decorator does not waste retries.
    """


def _empty_frame() -> pl.DataFrame:
    return pl.DataFrame()


def _coerce_to_polars(result: Any) -> pl.DataFrame:
    if result is None:
        return _empty_frame()
    if isinstance(result, pd.DataFrame | pl.DataFrame):
        return ensure_polars(result)
    if isinstance(result, pd.Series):
        return pl.from_pandas(result.to_frame().T)
    return _empty_frame()


def standardize_columns(
    frame: pd.DataFrame | pl.DataFrame,
    *,
    rename: dict[str, str] | None = None,
    columns: list[str],
    date_columns: tuple[str, ...] = ("date",),
    datetime_columns: tuple[str, ...] = ("datetime",),
) -> pl.DataFrame:
    """Lowercase, rename, normalize temporal columns, and select a known schema."""
    result = ensure_polars(frame)
    result = result.rename({column: str(column).lower() for column in result.columns})

    if rename:
        applicable = {key: value for key, value in rename.items() if key in result.columns}
        if applicable:
            result = result.rename(applicable)

    result = normalize_temporal_columns(result, date_columns=date_columns, datetime_columns=datetime_columns)

    temporal_exprs: list[pl.Expr] = []
    for column in date_columns:
        if column in result.columns and result.schema[column] == pl.Datetime:
            temporal_exprs.append(pl.col(column).dt.date().alias(column))
    if temporal_exprs:
        result = result.with_columns(temporal_exprs)

    available = [column for column in columns if column in result.columns]
    if not available:
        return _empty_frame()
    selected = result.select(available)
    return selected.filter(~pl.all_horizontal(pl.all().is_null()))


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
        self._retry_decorator = retry(
            retry=retry_if_exception_type(TransientError),
            stop=stop_after_attempt(retry_attempts),
            wait=wait_exponential(multiplier=retry_delay, min=retry_delay, max=30.0),
            before_sleep=before_sleep_log(logger, 30),  # WARNING
            reraise=True,
        )

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

    def fetch(self, trading_date: date) -> pl.DataFrame:
        """Fetch data for a specific date."""
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Retry API calls with exponential backoff via tenacity.

        Only ``TransientError`` (network/timeout/5xx) triggers retries.
        4xx errors propagate immediately without retry.
        """

        @self._retry_decorator
        def _wrapped() -> Any:
            try:
                return func(*args, **kwargs)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    raise TransientError(str(exc)) from exc
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as exc:
                raise TransientError(str(exc)) from exc
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code >= 500:
                    raise TransientError(str(exc)) from exc
                raise
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise TransientError(str(exc)) from exc

        return _wrapped()


class YFinanceBaseFetcher(MarketDataFetcher):
    """Base class for yfinance-based market data fetchers.

    Provides batching, download, MultiIndex/flat-frame handling, and
    column standardization. Subclasses provide market name, fallback
    tickers, and optional column-rename overrides.
    """

    market: str = ""
    DEFAULT_BATCH_SIZE = 500

    def __init__(
        self,
        *,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: TickerConfig | None = None,
        filters: dict[str, Any] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        fallback_tickers: list[str] | None = None,
    ):
        super().__init__(retry_attempts, retry_delay, ticker_config=ticker_config)
        self.batch_size = batch_size
        self._fallback_tickers = fallback_tickers or []
        if tickers is not None:
            self.tickers = tickers
            logger.info("Using explicit ticker list: %s tickers (batch_size=%s)", len(tickers), batch_size)
        else:
            self.tickers = self.load_tickers_from_config(ticker_config, filters, self._fallback_tickers)

    @staticmethod
    def _chunked(iterable: list[str], chunk_size: int) -> list[list[str]]:
        chunk_list = list(iterable)
        if not chunk_list:
            return []
        return [chunk_list[i : i + chunk_size] for i in range(0, len(chunk_list), chunk_size)]

    def _download_batch(self, ticker_batch: list[str], start_date: str, end_date: str) -> list[pd.DataFrame]:
        data = yf.download(
            ticker_batch,
            start=start_date,
            end=end_date,
            group_by="ticker",
            progress=False,
            auto_adjust=False,
        )
        if data is None or (hasattr(data, "empty") and data.empty):
            return []

        frames: list[pd.DataFrame] = []
        if not isinstance(data.columns, pd.MultiIndex):
            base_frame = data.reset_index()
            tickers = ticker_batch if len(ticker_batch) > 1 else [ticker_batch[0]]
            for ticker in tickers:
                frame = base_frame.copy()
                frame["ticker"] = ticker
                frames.append(frame)
        else:
            for ticker in ticker_batch:
                if ticker in data.columns:
                    ticker_data = data[ticker].reset_index()
                    ticker_data["ticker"] = ticker
                    frames.append(ticker_data)
        return frames

    def _get_column_rename(self) -> dict[str, str]:
        return {"adj close": "adj_close", "datetime": "date", "index": "date"}

    def fetch(self, trading_date: date) -> pl.DataFrame:
        logger.info("Fetching %s data for %s (%s tickers)", self.market, trading_date, len(self.tickers))

        def _fetch() -> pl.DataFrame:
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            all_frames: list[pd.DataFrame] = []
            batches = self._chunked(self.tickers, self.batch_size)
            total_batches = len(batches)

            logger.info("Downloading in %s batches (batch_size=%s)", total_batches, self.batch_size)

            for batch_idx, batch in enumerate(batches, 1):
                frames = self._download_batch(batch, start_date, end_date)
                all_frames.extend(frames)
                logger.debug("Batch %s/%s: %s cumulative frames", batch_idx, total_batches, len(all_frames))

            if not all_frames:
                logger.warning("No data returned for %s on %s", self.market, trading_date)
                return _empty_frame()

            frame = pd.concat(all_frames, ignore_index=True)
            frame = standardize_columns(frame, rename=self._get_column_rename(), columns=STANDARD_COLUMNS)
            unique_tickers = frame["ticker"].n_unique() if "ticker" in frame.columns else 0
            logger.info("Fetched %s rows for %s unique %s tickers", frame.height, unique_tickers, self.market)
            return frame

        return cast(pl.DataFrame, self._retry_on_failure(_fetch))


__all__ = [
    "MacroIndicatorFetcher",
    "MarketDataFetcher",
    "TransientError",
    "YFinanceBaseFetcher",
    "_coerce_to_polars",
    "_empty_frame",
    "standardize_columns",
]
