"""Macro source adapters — fetchers and pipeline (polars-first).

Macro data (FRED, yfinance) is fetched as pandas at the external-library
boundary and converted to polars immediately. The pipeline hands polars
DataFrames to the canonical Delta writer in ``ingestion/writers.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import polars as pl
import structlog
import yfinance as yf
from fredapi import Fred

from equity_lake.core.config import get_project_config
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
            logger.error(f"All {self.retry_attempts} attempts failed for {self.indicator_name}")
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
                logger.warning(f"No data for {self.indicator_name} on {trading_date}")
                return None

            if "Close" in data.columns:
                value = float(data["Close"].iloc[0])
            elif "Adj Close" in data.columns:
                value = float(data["Adj Close"].iloc[0])
            else:
                logger.warning(f"No close price found for {self.indicator_name}")
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

            logger.info(f"Fetched {self.indicator_name}: {value:.4f} on {trading_date}")
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
                logger.warning(f"No data for {self.indicator_name} ({self.series_id}) on {trading_date}")
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

            logger.info(f"Fetched {self.indicator_name}: {value:.4f} on {trading_date}")
            return df

        return self._retry_on_failure(_fetch)


class MacroDataPipeline:
    def __init__(self, config: dict | None = None):
        self.config = config or get_project_config()
        self.fred_api_key = self._get_fred_api_key()
        self.indicators = self._initialize_fetchers()

    def _get_fred_api_key(self) -> str:
        import os

        from dotenv import load_dotenv

        load_dotenv()

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
                        retry_attempts=cast(int, self.config.get("retry_attempts", 3)),
                    )
                    fetchers.append(fetcher)

                elif source == "fred":
                    if not self.fred_api_key:
                        logger.warning(f"Skipping {indicator_name} - no FRED API key")
                        continue

                    series_id = indicator_config["series"]
                    fred_fetcher = FredFetcher(
                        series_id=series_id,
                        indicator_name=indicator_name,
                        fred_api_key=self.fred_api_key,
                        retry_attempts=cast(int, self.config.get("retry_attempts", 3)),
                    )
                    fetchers.append(fred_fetcher)

                else:
                    logger.warning(f"Unknown source '{source}' for {indicator_name}")

            except Exception as e:
                logger.error(f"Failed to initialize fetcher for {indicator_name}: {e}")

        logger.info(f"Initialized {len(fetchers)} macro indicator fetchers")
        return fetchers

    def fetch_all(self, trading_date: date) -> pl.DataFrame:
        logger.info(f"Fetching macro indicators for {trading_date}")

        all_data: list[pl.DataFrame] = []

        for fetcher in self.indicators:
            try:
                result = fetcher.fetch(trading_date)
                if result is not None and not result.is_empty():
                    all_data.append(result)
            except Exception as e:
                logger.error(f"Failed to fetch {fetcher.indicator_name}: {e}")

        if not all_data:
            logger.warning(f"No macro data fetched for {trading_date}")
            return _empty_macro_frame()

        df = pl.concat(all_data).select(MACRO_COLUMNS)

        logger.info(f"Fetched {len(df)} macro indicators for {trading_date}")
        return df

    def fetch_with_fallback(self, trading_date: date, fallback_date: date | None = None) -> pl.DataFrame:
        df = self.fetch_all(trading_date)

        if df.is_empty() and fallback_date:
            logger.info(f"Falling back to previous trading day: {fallback_date}")
            df = self.fetch_all(fallback_date)

        return df


class MacroFetcher:
    market = "macro"

    def __init__(self, **kwargs: Any):
        self._pipeline = MacroDataPipeline()

    def fetch(self, trading_date: date) -> pl.DataFrame:
        return self._pipeline.fetch_with_fallback(trading_date)


__all__ = [
    "FredFetcher",
    "MacroDataPipeline",
    "MacroFetcher",
    "MacroIndicatorFetcher",
    "YFinanceFetcher",
]
