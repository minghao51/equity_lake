#!/usr/bin/env python3
"""
Feature Engineering for Equity ML Models

Computes technical indicators, momentum features, and time-based features
from raw OHLCV data for machine learning models.

Usage:
    python -m equity_lake.features.engineering --date 2026-01-23
    python -m equity_lake.features.engineering --tickers AAPL,GOOGL --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import polars as pl
import structlog
from tqdm import tqdm

from equity_lake.core.paths import LAKE_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.features.pipeline import FeaturePipeline
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()

NUMERIC_DTYPES = {pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64}
NON_NUMERIC_FOR_ZSCORE = {
    "next_day_return",
    "target",
    "meta_label",
    "barrier_outcome",
    "upper_barrier_return",
    "lower_barrier_return",
    "vertical_barrier_days",
    "candidate_score",
}


class FeatureEngineer:
    """Computes ML features from raw OHLCV data."""

    def __init__(self, db_path: str | None = ":memory:"):
        self.db_path: str = db_path if db_path is not None else ":memory:"
        self.conn = duckdb.connect(str(self.db_path))
        self.feature_pipeline = FeaturePipeline()
        self._setup_views()

    def __enter__(self) -> FeatureEngineer:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _setup_views(self) -> None:
        self.conn.execute("INSTALL delta; LOAD delta;")

        markets = [
            ("us", "01_bronze/market_data/us_equity"),
            ("cn", "01_bronze/market_data/cn_ashare"),
            ("hk_sg", "01_bronze/market_data/hk_sg_equity"),
            ("jpx", "01_bronze/market_data/jpx_equity"),
            ("krx", "01_bronze/market_data/krx_equity"),
        ]

        union_parts: list[str] = []
        for label, market_dir in markets:
            path = LAKE_DIR / market_dir
            if not path.exists():
                continue
            scan = duckdb_scan_for(path)
            union_parts.append(f"SELECT '{label}' as market, ticker, date, open, high, low, close, volume FROM {scan}")

        if union_parts:
            sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(union_parts)
            self.conn.execute(sql)
        logger.info("DuckDB views created successfully")

    @staticmethod
    def _date_scalar(value: object) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        raise TypeError(f"Expected date-like value, got {type(value)!r}")

    def generate_features(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        compute_target: bool = True,
        include_sentiment: bool = False,
        include_social_sentiment: bool = False,
        include_macro: bool = True,
        include_enriched_sentiment: bool = False,
        include_analyst_ratings: bool = False,
        include_sec_features: bool = False,
        normalize_cross_sectional: bool = False,
    ) -> pl.DataFrame:
        """Generate all features for specified tickers and date range.

        Two-phase execution:
        1. Per-ticker technical indicators via Hamilton DAG (``compute_technical``)
        2. Batch external-data enrichments via Hamilton DAG (``compute_enriched``)

        Enrichments (sentiment, social, analyst, SEC, macro) are handled
        automatically by :meth:`FeaturePipeline.compute_enriched` based on the
        ``include_*`` flags. The legacy ``merge_*`` methods on this class are
        deprecated and should not be called directly.
        """
        logger.info(f"Generating features for {len(tickers)} tickers from {start_date} to {end_date}")

        query = """
            SELECT
                ticker,
                date,
                open,
                high,
                low,
                close,
                volume
            FROM equity_all
            WHERE ticker IN (SELECT unnest($1::VARCHAR[]))
            AND date BETWEEN $2 AND $3
            ORDER BY ticker, date
        """

        logger.debug("Executing parameterized query for %d tickers", len(tickers))
        df = self.conn.execute(query, [tickers, start_date, end_date]).pl()

        if df.is_empty():
            logger.warning(f"No data found for tickers: {tickers}")
            return df

        logger.info(f"Loaded {df.height} rows of OHLCV data")

        if "date" in df.columns and df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))

        # Phase 1: per-ticker technical indicators
        result_dfs: list[pl.DataFrame] = []
        for ticker in tqdm(tickers, desc="Computing features"):
            ticker_df = df.filter(pl.col("ticker") == ticker)
            if ticker_df.is_empty():
                continue
            if ticker_df.height < 60:
                logger.warning(f"Skipping {ticker}: only {ticker_df.height} days of data")
                continue

            computed = self.feature_pipeline.compute_technical(ticker_df, include_target=compute_target)
            result_dfs.append(computed)

        if not result_dfs:
            logger.error("No features generated - all tickers were skipped (insufficient data)")
            return pl.DataFrame()

        features_df = pl.concat(result_dfs, how="vertical_relaxed")
        critical_cols = ["close", "volume", "rsi_14", "macd"]
        features_df = features_df.filter(~pl.any_horizontal([pl.col(column).is_null() for column in critical_cols if column in features_df.columns]))

        # Phase 2: batch external-data enrichments via DAG
        any_enrichment = any(
            [
                include_sentiment,
                include_social_sentiment,
                include_macro,
                include_enriched_sentiment,
                include_analyst_ratings,
                include_sec_features,
            ]
        )
        if any_enrichment:
            features_df = self.feature_pipeline.compute_enriched(
                features_df,
                duckdb_conn=self.conn,
                start_date=start_date,
                end_date=end_date,
                enable_news_sentiment=include_sentiment,
                enable_social_sentiment=include_social_sentiment,
                enable_enriched_sentiment=include_enriched_sentiment,
                enable_analyst_ratings=include_analyst_ratings,
                enable_sec_features=include_sec_features,
                enable_macro=include_macro,
            )

        if normalize_cross_sectional:
            features_df = self.zscore_cross_sectional(features_df)

        logger.info(f"Generated {features_df.height} rows of features with {len(features_df.columns)} columns")
        logger.debug(f"Feature columns: {list(features_df.columns)}")
        return features_df

    def zscore_cross_sectional(
        self,
        features_df: FrameLike,
        *,
        columns: list[str] | None = None,
        suffix: str = "_zscore",
        eps: float = 1e-8,
    ) -> pl.DataFrame:
        """Z-score numeric features cross-sectionally across tickers per date.

        For each date, compute (x - mean) / (std + eps) over all tickers.
        Adds new columns with ``suffix`` appended; original columns are kept
        unchanged. Skips columns that are non-numeric, contain nulls in the
        grouping key, or have zero variance.
        """
        frame = ensure_polars(features_df)
        if frame.is_empty():
            return frame

        skip = {
            "ticker",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "feature_schema_version",
            "candidate_action",
            "candidate_source",
        }
        if columns is None:
            columns = [col for col in frame.columns if col not in skip and col not in NON_NUMERIC_FOR_ZSCORE and frame.schema[col] in NUMERIC_DTYPES]

        expressions: list[pl.Expr] = []
        for col_name in columns:
            if col_name not in frame.columns:
                continue
            mean_expr = pl.col(col_name).fill_null(0.0).mean().over("date")
            std_expr = pl.col(col_name).fill_null(0.0).std().over("date")
            z_expr = ((pl.col(col_name).fill_null(0.0) - mean_expr) / (std_expr + eps)).alias(f"{col_name}{suffix}")
            expressions.append(z_expr)

        if not expressions:
            return frame
        return frame.with_columns(expressions)

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
