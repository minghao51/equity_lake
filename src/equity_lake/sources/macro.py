"""Macro source adapters — fetchers and pipeline (polars-first).

Macro data (FRED, yfinance) is fetched as pandas at the external-library
boundary and converted to polars immediately. The pipeline hands polars
DataFrames to the canonical Delta writer in ``ingestion/writers.py``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
import structlog
import yfinance as yf
from fredapi import Fred

from equity_lake.core.config import get_settings
from equity_lake.core.retry import build_retry_decorator
from equity_lake.core.schemas import MACRO_COLUMNS, MACRO_INDICATOR_CONFIG

logger = structlog.get_logger(__name__)


def _empty_macro_frame() -> pl.DataFrame:
    """Return an empty DataFrame carrying the MACRO_COLUMNS schema."""
    return pl.DataFrame(
        schema={
            "date": pl.Date,
            "indicator": pl.Utf8,
            "value": pl.Float64,
            "source": pl.Utf8,
            "updated_at": pl.Datetime("us", "UTC"),
        }
    )


class MacroIndicatorFetcher:
    def __init__(self, indicator_name: str, retry_attempts: int = 3, retry_delay: float = 1.0):
        self.indicator_name = indicator_name
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self._retry_decorator = build_retry_decorator(
            attempts=retry_attempts,
            wait_multiplier=retry_delay,
            wait_min=retry_delay,
            log=logger,
        )

    def fetch(self, trading_date: date) -> pl.DataFrame | None:
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(self, func: Callable[..., pl.DataFrame | None], *args: Any, **kwargs: Any) -> pl.DataFrame | None:
        @self._retry_decorator
        def _wrapped() -> pl.DataFrame | None:
            return func(*args, **kwargs)

        try:
            return _wrapped()
        except Exception:
            logger.error("macro_fetch_all_attempts_failed", indicator=self.indicator_name, retry_attempts=self.retry_attempts)
            return None


class YFinanceFetcher(MacroIndicatorFetcher):
    def __init__(self, ticker: str, indicator_name: str, retry_attempts: int = 3):
        super().__init__(indicator_name, retry_attempts)
        self.ticker = ticker

    def fetch(self, trading_date: date) -> pl.DataFrame | None:
        def _fetch() -> pl.DataFrame | None:
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            data = yf.download(
                self.ticker,
                start=start_date,
                end=end_date,
                progress=False,
            )

            if data is None or data.empty:
                logger.warning("yfinance_no_data", indicator=self.indicator_name, trading_date=str(trading_date))
                return None

            if "Close" in data.columns:
                value = float(data["Close"].iloc[0])
            elif "Adj Close" in data.columns:
                value = float(data["Adj Close"].iloc[0])
            else:
                logger.warning("yfinance_no_close_price", indicator=self.indicator_name)
                return None

            df = pl.DataFrame(
                {
                    "date": [trading_date],
                    "indicator": [self.indicator_name],
                    "value": [value],
                    "source": ["yfinance"],
                    "updated_at": [datetime.now(UTC)],
                }
            )

            logger.info("macro_indicator_fetched", indicator=self.indicator_name, value=round(value, 4), trading_date=str(trading_date))
            return df

        return self._retry_on_failure(_fetch)


class FredFetcher(MacroIndicatorFetcher):
    def __init__(
        self,
        series_id: str,
        indicator_name: str,
        fred_api_key: str,
        retry_attempts: int = 3,
    ):
        super().__init__(indicator_name, retry_attempts)
        self.series_id = series_id
        self.fred_api_key = fred_api_key
        self.fred = Fred(api_key=fred_api_key)

    def fetch(self, trading_date: date) -> pl.DataFrame | None:
        def _fetch() -> pl.DataFrame | None:
            data = self.fred.get_series(
                self.series_id,
                observation_start=trading_date.strftime("%Y-%m-%d"),
                observation_end=trading_date.strftime("%Y-%m-%d"),
            )

            if data.empty:
                logger.warning("fred_no_data", indicator=self.indicator_name, series_id=self.series_id, trading_date=str(trading_date))
                return None

            value = float(data.iloc[0])

            df = pl.DataFrame(
                {
                    "date": [trading_date],
                    "indicator": [self.indicator_name],
                    "value": [value],
                    "source": ["fred"],
                    "updated_at": [datetime.now(UTC)],
                }
            )

            logger.info("macro_indicator_fetched", indicator=self.indicator_name, value=round(value, 4), trading_date=str(trading_date))
            return df

        return self._retry_on_failure(_fetch)


class MacroDataPipeline:
    def __init__(self, retry_attempts: int | None = None, retry_delay: float | None = None):
        if retry_attempts is None or retry_delay is None:
            ingestion = get_settings().ingestion
            retry_attempts = ingestion.retry_attempts if retry_attempts is None else retry_attempts
            retry_delay = ingestion.retry_delay if retry_delay is None else retry_delay
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.fred_api_key = self._get_fred_api_key()
        self.indicators = self._initialize_fetchers()

    def _get_fred_api_key(self) -> str:
        # Env loading belongs to the dotenvx CLI seam; never call load_dotenv()
        # inside library code.
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            logger.warning(
                "FRED_API_KEY not set. FRED indicators will not be fetched. Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        return api_key

    def _initialize_fetchers(self) -> list[MacroIndicatorFetcher]:
        fetchers: list[MacroIndicatorFetcher] = []

        for indicator_name, indicator_config in MACRO_INDICATOR_CONFIG.items():
            source = indicator_config["source"]

            try:
                if source == "yfinance":
                    ticker = indicator_config["ticker"]
                    fetcher = YFinanceFetcher(
                        ticker=ticker,
                        indicator_name=indicator_name,
                        retry_attempts=self.retry_attempts,
                    )
                    fetchers.append(fetcher)

                elif source == "fred":
                    if not self.fred_api_key:
                        logger.warning("fred_skip_no_key", indicator=indicator_name)
                        continue

                    series_id = indicator_config["series"]
                    fred_fetcher = FredFetcher(
                        series_id=series_id,
                        indicator_name=indicator_name,
                        fred_api_key=self.fred_api_key,
                        retry_attempts=self.retry_attempts,
                    )
                    fetchers.append(fred_fetcher)

                else:
                    logger.warning("unknown_macro_source", source=source, indicator=indicator_name)

            except Exception as e:
                logger.error("fetcher_init_failed", indicator=indicator_name, error=str(e))

        logger.info("macro_fetchers_initialized", count=len(fetchers))
        return fetchers

    def fetch_all(self, trading_date: date) -> pl.DataFrame:
        logger.info("macro_fetch_start", trading_date=str(trading_date))

        all_data: list[pl.DataFrame] = []

        for fetcher in self.indicators:
            try:
                result = fetcher.fetch(trading_date)
                if result is not None and not result.is_empty():
                    all_data.append(result)
            except Exception as e:
                logger.error("macro_fetch_indicator_failed", indicator=fetcher.indicator_name, error=str(e))

        if not all_data:
            logger.warning("macro_fetch_empty", trading_date=str(trading_date))
            return _empty_macro_frame()

        df = pl.concat(all_data).select(MACRO_COLUMNS)

        logger.info("macro_fetch_complete", count=len(df), trading_date=str(trading_date))
        return df

    def fetch_with_fallback(self, trading_date: date, fallback_date: date | None = None) -> pl.DataFrame:
        df = self.fetch_all(trading_date)

        if df.is_empty() and fallback_date:
            logger.info("macro_fallback_date", fallback_date=str(fallback_date))
            df = self.fetch_all(fallback_date)

        return df


class MacroFetcher:
    market = "macro"

    def __init__(self, retry_attempts: int | None = None, retry_delay: float | None = None, **kwargs: Any):
        self._pipeline = MacroDataPipeline(retry_attempts=retry_attempts, retry_delay=retry_delay)

    def fetch(self, trading_date: date) -> pl.DataFrame:
        return self._pipeline.fetch_with_fallback(trading_date)


__all__ = [
    "FredFetcher",
    "MacroDataPipeline",
    "MacroFetcher",
    "MacroIndicatorFetcher",
    "YFinanceFetcher",
]
