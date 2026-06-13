"""Macro source adapters — fetchers, pipeline, writers, and schema validation."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, cast

import pandas as pd
import polars as pl
import structlog
import yfinance as yf
from fredapi import Fred

from equity_lake.core.config import get_project_config
from equity_lake.core.paths import MACRO_INDICATORS_DIR
from equity_lake.core.schemas import MACRO_COLUMNS, MACRO_INDICATOR_CONFIG

logger = structlog.get_logger(__name__)


class MacroIndicatorFetcher:
    def __init__(self, indicator_name: str, retry_attempts: int = 3, retry_delay: float = 1.0):
        self.indicator_name = indicator_name
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def fetch(self, trading_date: date) -> pd.DataFrame | None:
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> pd.DataFrame | None:
        for attempt in range(self.retry_attempts):
            try:
                result = func(*args, **kwargs)
                if result is None:
                    return None
                if isinstance(result, pd.DataFrame):
                    return result
                return None
            except Exception as e:
                if attempt < self.retry_attempts - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"All {self.retry_attempts} attempts failed: {e}")
        return None


class YFinanceFetcher(MacroIndicatorFetcher):
    def __init__(self, ticker: str, indicator_name: str, retry_attempts: int = 3):
        super().__init__(indicator_name, retry_attempts)
        self.ticker = ticker

    def fetch(self, trading_date: date) -> pd.DataFrame | None:
        def _fetch() -> pd.DataFrame | None:
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            try:
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

                df = pd.DataFrame(
                    {
                        "date": [trading_date],
                        "indicator": [self.indicator_name],
                        "value": [value],
                        "source": ["yfinance"],
                        "updated_at": [datetime.now()],
                    }
                )

                logger.info(f"Fetched {self.indicator_name}: {value:.4f} on {trading_date}")
                return df

            except Exception as e:
                logger.error(f"Error fetching {self.indicator_name}: {e}")
                return None

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

    def fetch(self, trading_date: date) -> pd.DataFrame | None:
        def _fetch() -> pd.DataFrame | None:
            try:
                data = self.fred.get_series(
                    self.series_id,
                    observation_start=trading_date.strftime("%Y-%m-%d"),
                    observation_end=trading_date.strftime("%Y-%m-%d"),
                )

                if data.empty:
                    logger.warning(f"No data for {self.indicator_name} ({self.series_id}) on {trading_date}")
                    return None

                value = float(data.iloc[0])

                df = pd.DataFrame(
                    {
                        "date": [trading_date],
                        "indicator": [self.indicator_name],
                        "value": [value],
                        "source": ["fred"],
                        "updated_at": [datetime.now()],
                    }
                )

                logger.info(f"Fetched {self.indicator_name}: {value:.4f} on {trading_date}")
                return df

            except Exception as e:
                logger.error(f"Error fetching {self.indicator_name} from FRED: {e}")
                return None

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

    def fetch_all(self, trading_date: date) -> pd.DataFrame:
        logger.info(f"Fetching macro indicators for {trading_date}")

        all_data = []

        for fetcher in self.indicators:
            try:
                result = fetcher.fetch(trading_date)
                if result is not None and not result.empty:
                    all_data.append(result)
            except Exception as e:
                logger.error(f"Failed to fetch {fetcher.indicator_name}: {e}")

        if not all_data:
            logger.warning(f"No macro data fetched for {trading_date}")
            return pd.DataFrame(columns=MACRO_COLUMNS)

        df = pd.concat(all_data, ignore_index=True)

        for col in MACRO_COLUMNS:
            if col not in df.columns:
                df[col] = None

        df = df[MACRO_COLUMNS]

        logger.info(f"Fetched {len(df)} macro indicators for {trading_date}")
        return df

    def fetch_with_fallback(self, trading_date: date, fallback_date: date | None = None) -> pd.DataFrame:
        df = self.fetch_all(trading_date)

        if df.empty and fallback_date:
            logger.info(f"Falling back to previous trading day: {fallback_date}")
            df = self.fetch_all(fallback_date)

        return df


def write_macro_to_parquet(
    df: pd.DataFrame,
    trading_date: date,
    dry_run: bool = False,
) -> bool:
    if df.empty:
        logger.warning(f"Empty DataFrame for macro indicators on {trading_date}")
        return False

    MACRO_INDICATORS_DIR.mkdir(parents=True, exist_ok=True)

    partition_dir = MACRO_INDICATORS_DIR / f"date={trading_date}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    output_file = partition_dir / f"{trading_date}.parquet"

    if output_file.exists():
        logger.info(f"File exists: {output_file}. Checking for duplicates...")
        try:
            existing_df = pd.read_parquet(output_file)
            existing_combos = set(existing_df.apply(lambda r: (r["indicator"],), axis=1).tolist())

            duplicate_mask = df.apply(lambda r: (r["indicator"],) in existing_combos, axis=1)

            duplicate_count = duplicate_mask.sum()
            if duplicate_count > 0:
                logger.warning(f"Found {duplicate_count} duplicate indicators, skipping...")
                df = df[~duplicate_mask]

            if df.empty:
                logger.warning("All indicators are duplicates. Skipping write.")
                return True

        except Exception as e:
            logger.error(f"Failed to check for duplicates: {e}")

    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(df)} indicators to {output_file}")
        return True

    try:
        df_write = df.copy()
        if "date" in df_write.columns:
            df_write["date"] = pd.to_datetime(df_write["date"])
        if "updated_at" in df_write.columns:
            df_write["updated_at"] = pd.to_datetime(df_write["updated_at"])

        df_write.to_parquet(output_file, index=False, compression="snappy")

        file_size = output_file.stat().st_size / 1024
        logger.info(f"Wrote {len(df_write)} indicators to {output_file} ({file_size:.1f} KB)")
        return True

    except Exception as e:
        logger.error(f"Failed to write Parquet file: {e}")
        return False


def validate_macro_schema(df: pd.DataFrame) -> bool:
    required_cols = ["date", "indicator", "value", "source"]
    missing_cols = set(required_cols) - set(df.columns)

    if missing_cols:
        logger.error(f"Missing required columns: {missing_cols}")
        return False

    for col in required_cols:
        if df[col].isnull().all():
            logger.warning(f"Column '{col}' is all null")

    return True


class MacroFetcher:
    market = "macro"

    def __init__(self, **kwargs: Any):
        self._pipeline = MacroDataPipeline()

    def fetch(self, trading_date: date) -> pl.DataFrame:
        import polars as pl

        from equity_lake.sources.base import _empty_frame

        df_pd = self._pipeline.fetch_with_fallback(trading_date)
        if df_pd is None or df_pd.empty:
            return _empty_frame()
        return pl.from_pandas(df_pd)


__all__ = [
    "FredFetcher",
    "MacroDataPipeline",
    "MacroFetcher",
    "MacroIndicatorFetcher",
    "YFinanceFetcher",
    "validate_macro_schema",
    "write_macro_to_parquet",
]
