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
            ("us", "us_equity"),
            ("cn", "cn_ashare"),
            ("hk_sg", "hk_sg_equity"),
            ("jpx", "jpx_equity"),
            ("krx", "krx_equity"),
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
        normalize_cross_sectional: bool = False,
    ) -> pl.DataFrame:
        """Generate all features for specified tickers and date range."""
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

        result_dfs: list[pl.DataFrame] = []
        for ticker in tqdm(tickers, desc="Computing features"):
            ticker_df = df.filter(pl.col("ticker") == ticker)
            if ticker_df.is_empty():
                continue
            if ticker_df.height < 60:
                logger.warning(f"Skipping {ticker}: only {ticker_df.height} days of data")
                continue

            computed = self.feature_pipeline.compute(ticker_df)
            if not compute_target and "next_day_return" in computed.columns:
                computed = computed.drop("next_day_return")
            result_dfs.append(computed)

        if not result_dfs:
            logger.error("No features generated - all tickers were skipped (insufficient data)")
            return pl.DataFrame()

        features_df = pl.concat(result_dfs, how="vertical_relaxed")
        critical_cols = ["close", "volume", "rsi_14", "macd"]
        features_df = features_df.filter(~pl.any_horizontal([pl.col(column).is_null() for column in critical_cols if column in features_df.columns]))

        if include_sentiment:
            features_df = self.merge_sentiment_features(
                features_df,
                start_date=start_date,
                end_date=end_date,
            )
        if include_social_sentiment:
            features_df = self.merge_social_sentiment_features(
                features_df,
                start_date=start_date,
                end_date=end_date,
            )

        features_df = self.add_cross_modal_sentiment_features(features_df)

        if include_macro:
            features_df = self.merge_macro_features(
                features_df,
                start_date=start_date,
                end_date=end_date,
            )

        if normalize_cross_sectional:
            features_df = self.zscore_cross_sectional(features_df)

        logger.info(f"Generated {features_df.height} rows of features with {len(features_df.columns)} columns")
        logger.debug(f"Feature columns: {list(features_df.columns)}")
        return features_df

    def merge_sentiment_features(
        self,
        features_df: FrameLike,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Merge aggregated sentiment scores into the feature frame."""
        features_df = ensure_polars(features_df)
        if features_df.is_empty():
            logger.warning("Empty features DataFrame, skipping sentiment merge")
            return features_df

        logger.info(
            "Merging sentiment features for %s tickers from %s to %s",
            features_df["ticker"].n_unique(),
            start_date,
            end_date,
        )
        ticker_filter = tuple(sorted(str(value) for value in features_df["ticker"].unique().to_list()))

        sentiment_query = f"""
            SELECT
                ticker,
                date,
                AVG(sentiment_score) as avg_daily_sentiment,
                COUNT(*) as news_count,
                SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) as positive_count,
                SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) as negative_count,
                SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) as neutral_count,
                STDDEV(sentiment_score) as sentiment_std
            FROM {duckdb_scan_for(LAKE_DIR / "us_news")}
            WHERE ticker IN {ticker_filter}
            AND date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).pl()
            if sentiment_df.is_empty():
                logger.warning("No sentiment data found, adding neutral sentiment columns")
                return features_df.with_columns(
                    pl.lit(0.0).alias("avg_daily_sentiment"),
                    pl.lit(0).alias("news_count"),
                    pl.lit(0).alias("positive_count"),
                    pl.lit(0).alias("negative_count"),
                    pl.lit(0).alias("neutral_count"),
                    pl.lit(0.0).alias("sentiment_std"),
                )

            logger.info(f"Loaded {sentiment_df.height} sentiment data points")
            merged_df = features_df.join(sentiment_df, on=["ticker", "date"], how="left").with_columns(
                pl.col("avg_daily_sentiment").fill_null(0.0),
                pl.col("news_count").fill_null(0).cast(pl.Int64),
                pl.col("positive_count").fill_null(0).cast(pl.Int64),
                pl.col("negative_count").fill_null(0).cast(pl.Int64),
                pl.col("neutral_count").fill_null(0).cast(pl.Int64),
                pl.col("sentiment_std").fill_null(0.0),
            )
            merged_df = merged_df.sort(["ticker", "date"])
            merged_df = merged_df.with_columns(
                pl.col("avg_daily_sentiment").ewm_mean(half_life=3.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_3d"),
                pl.col("avg_daily_sentiment").ewm_mean(half_life=7.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_7d"),
                pl.col("avg_daily_sentiment").ewm_mean(half_life=30.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_30d"),
            )

            logger.info(
                "Merged sentiment features: %s rows with news, %s rows without news",
                merged_df.filter(pl.col("news_count") > 0).height,
                merged_df.filter(pl.col("news_count") == 0).height,
            )
            return merged_df
        except Exception as exc:
            logger.error(f"Failed to merge sentiment features: {exc}")
            return features_df

    def merge_social_sentiment_features(
        self,
        features_df: FrameLike,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Merge aggregated social sentiment scores into the feature frame."""
        features_df = ensure_polars(features_df)
        if features_df.is_empty():
            logger.warning("Empty features DataFrame, skipping social sentiment merge")
            return features_df

        logger.info(
            "Merging social sentiment features for %s tickers from %s to %s",
            features_df["ticker"].n_unique(),
            start_date,
            end_date,
        )
        ticker_filter = tuple(sorted(str(value) for value in features_df["ticker"].unique().to_list()))

        sentiment_query = f"""
            SELECT
                ticker,
                date,
                SUM(mention_count) as social_mention_count,
                AVG(score) as social_sentiment_score,
                SUM(positive_score) as social_positive_score,
                SUM(negative_score) as social_negative_score,
                SUM(CASE WHEN source = 'reddit' THEN mention_count ELSE 0 END) as social_reddit_mentions,
                SUM(CASE WHEN source = 'twitter' THEN mention_count ELSE 0 END) as social_twitter_mentions
            FROM {duckdb_scan_for(LAKE_DIR / "us_social_sentiment")}
            WHERE ticker IN {ticker_filter}
            AND date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).pl()
            if sentiment_df.is_empty():
                logger.warning("No social sentiment data found, adding neutral social sentiment columns")
                return features_df.with_columns(
                    pl.lit(0).alias("social_mention_count"),
                    pl.lit(0.0).alias("social_sentiment_score"),
                    pl.lit(0.0).alias("social_positive_score"),
                    pl.lit(0.0).alias("social_negative_score"),
                    pl.lit(0).alias("social_reddit_mentions"),
                    pl.lit(0).alias("social_twitter_mentions"),
                    pl.lit(0.0).alias("social_momentum"),
                    pl.lit(0.0).alias("social_sentiment_momentum"),
                    pl.lit(0.0).alias("social_sentiment_ewma_3d"),
                    pl.lit(0.0).alias("social_sentiment_ewma_7d"),
                    pl.lit(0.0).alias("social_sentiment_ewma_30d"),
                )

            logger.info(f"Loaded {sentiment_df.height} social sentiment data points")
            merged_df = (
                features_df.join(sentiment_df, on=["ticker", "date"], how="left")
                .with_columns(
                    pl.col("social_mention_count").fill_null(0).cast(pl.Int64),
                    pl.col("social_sentiment_score").fill_null(0.0),
                    pl.col("social_positive_score").fill_null(0.0),
                    pl.col("social_negative_score").fill_null(0.0),
                    pl.col("social_reddit_mentions").fill_null(0).cast(pl.Int64),
                    pl.col("social_twitter_mentions").fill_null(0).cast(pl.Int64),
                )
                .sort(["ticker", "date"])
                .with_columns(
                    pl.col("social_mention_count").cast(pl.Float64).pct_change(5).over("ticker").fill_null(0.0).alias("social_momentum"),
                    pl.col("social_sentiment_score").diff(5).over("ticker").fill_null(0.0).alias("social_sentiment_momentum"),
                    pl.col("social_sentiment_score")
                    .ewm_mean(half_life=3.0, ignore_nulls=True)
                    .over("ticker")
                    .fill_null(0.0)
                    .alias("social_sentiment_ewma_3d"),
                    pl.col("social_sentiment_score")
                    .ewm_mean(half_life=7.0, ignore_nulls=True)
                    .over("ticker")
                    .fill_null(0.0)
                    .alias("social_sentiment_ewma_7d"),
                    pl.col("social_sentiment_score")
                    .ewm_mean(half_life=30.0, ignore_nulls=True)
                    .over("ticker")
                    .fill_null(0.0)
                    .alias("social_sentiment_ewma_30d"),
                )
            )

            logger.info(
                "Merged social sentiment features: %s rows with data, %s rows without data",
                merged_df.filter(pl.col("social_mention_count") > 0).height,
                merged_df.filter(pl.col("social_mention_count") == 0).height,
            )
            return merged_df
        except Exception as exc:
            logger.error(f"Failed to merge social sentiment features: {exc}")
            return features_df

    def add_cross_modal_sentiment_features(self, features_df: FrameLike) -> pl.DataFrame:
        """Add minimal cross-modal sentiment features on top of merged sentiment columns."""
        enriched = ensure_polars(features_df)
        if enriched.is_empty():
            return enriched

        enriched = enriched.sort(["ticker", "date"])
        log_volume = (pl.col("volume").cast(pl.Float64).clip(lower_bound=0) + 1).log()
        expressions: list[pl.Expr] = []

        if "avg_daily_sentiment" in enriched.columns:
            expressions.extend(
                [
                    (pl.col("avg_daily_sentiment").fill_null(0.0) * log_volume).alias("sentiment_x_log_volume"),
                    pl.col("avg_daily_sentiment").diff(5).over("ticker").fill_null(0.0).alias("news_sentiment_momentum_5d"),
                ]
            )

        if "social_sentiment_score" in enriched.columns:
            expressions.extend(
                [
                    (pl.col("social_sentiment_score").fill_null(0.0) * log_volume).alias("social_sentiment_x_log_volume"),
                    pl.col("social_sentiment_score").diff(5).over("ticker").fill_null(0.0).alias("social_sentiment_momentum_5d"),
                ]
            )

        if {"avg_daily_sentiment", "social_sentiment_score"}.issubset(enriched.columns):
            expressions.append(
                (pl.col("avg_daily_sentiment").fill_null(0.0) - pl.col("social_sentiment_score").fill_null(0.0)).alias("news_social_sentiment_gap")
            )

        if {"news_count", "social_mention_count"}.issubset(enriched.columns):
            expressions.append((pl.col("news_count").fill_null(0) - pl.col("social_mention_count").fill_null(0)).alias("news_social_mentions_gap"))

        return enriched.with_columns(expressions) if expressions else enriched

    def merge_macro_features(
        self,
        features_df: FrameLike,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Pivot macro indicators to wide format and as-of join on date.

        Macro data lives in ``MACRO_INDICATORS_DIR/date=YYYY-MM-DD/...parquet`` in
        long format ``(date, indicator, value, source, updated_at)``. We pivot to
        one column per indicator, forward-fill across the requested date range,
        and left-join onto the feature frame on date.
        """
        features_df = ensure_polars(features_df)
        if features_df.is_empty():
            logger.warning("Empty features DataFrame, skipping macro merge")
            return features_df

        macro_path = LAKE_DIR / "macro_indicators"
        if not macro_path.exists():
            logger.info("Macro indicators directory not found, skipping macro merge")
            return features_df

        try:
            macro_scan = duckdb_scan_for(macro_path)
            raw = self.conn.execute(
                f"""
                SELECT date, indicator, value
                FROM {macro_scan}
                WHERE date BETWEEN '{start_date}' AND '{end_date}'
                """
            ).pl()
        except Exception as exc:
            logger.warning("macro_load_failed", error=str(exc))
            return features_df

        if raw.is_empty():
            logger.info("No macro indicators found for date range")
            return features_df

        wide = raw.pivot(values="value", index="date", on="indicator").sort("date")
        numeric_cols = [col for col in wide.columns if col != "date"]
        wide = wide.with_columns([pl.col(col).cast(pl.Float64) for col in numeric_cols])

        full_dates = pl.DataFrame({"date": features_df["date"].unique().sort()})
        wide = full_dates.join(wide, on="date", how="left").sort("date")
        for col_name in numeric_cols:
            wide = wide.with_columns(pl.col(col_name).forward_fill().alias(col_name))

        derived_exprs: list[pl.Expr] = []
        if "vix" in wide.columns:
            derived_exprs.append((pl.col("vix") - pl.col("vix").shift(5)).alias("vix_change_5d"))
        if {"treasury_10y", "tips_yield"}.issubset(wide.columns):
            derived_exprs.append((pl.col("treasury_10y") - pl.col("tips_yield")).alias("yield_curve_slope"))
        if "dxy" in wide.columns:
            derived_exprs.append((pl.col("dxy") - pl.col("dxy").shift(5)).alias("dxy_change_5d"))
        if derived_exprs:
            wide = wide.with_columns(derived_exprs)

        macro_cols = [c for c in wide.columns if c != "date"]
        for col_name in macro_cols:
            wide = wide.with_columns(pl.col(col_name).fill_null(0.0))

        joined = features_df.join(wide, on="date", how="left")
        for col_name in macro_cols:
            if col_name in joined.columns:
                joined = joined.with_columns(pl.col(col_name).fill_null(0.0))
        logger.info(
            "merged_macro_features",
            indicator_count=len(macro_cols),
            rows=joined.height,
        )
        return joined

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
