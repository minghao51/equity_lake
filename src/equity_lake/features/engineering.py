#!/usr/bin/env python3
"""
Feature Engineering for Equity ML Models

Computes technical indicators, momentum features, and time-based features
from raw OHLCV data for machine learning models.

Usage:
    python -m equity_lake.features.engineering --date 2026-01-23
    python -m equity_lake.features.engineering --tickers AAPL,GOOGL --start 2024-01-01 --end 2024-12-31
"""

from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import structlog
from tqdm import tqdm

from equity_lake.core.paths import LAKE_DIR
from equity_lake.features.pipeline import FeaturePipeline

# Logger configuration - use structlog for consistency
logger = structlog.get_logger()


def _scan_for(market_path: Path) -> str:
    from deltalake import DeltaTable

    if DeltaTable.is_deltatable(str(market_path)):
        return f"delta_scan('{market_path}')"
    return f"read_parquet('{market_path}/**/*.parquet', hive_partitioning=1)"


# =============================================================================
# Feature Engineer
# =============================================================================


class FeatureEngineer:
    """Computes ML features from raw OHLCV data."""

    def __init__(self, db_path: str | None = ":memory:"):
        """
        Initialize the feature engineer.

        Args:
            db_path: Path to DuckDB database file (default: in-memory)
        """
        self.db_path: str = db_path if db_path is not None else ":memory:"
        self.conn = duckdb.connect(str(self.db_path))
        self.feature_pipeline = FeaturePipeline()

        self._setup_views()

    def __enter__(self) -> "FeatureEngineer":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _setup_views(self) -> None:
        """Set up DuckDB views for accessing OHLCV data."""
        from deltalake import DeltaTable

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
            scan = f"delta_scan('{path}')" if DeltaTable.is_deltatable(str(path)) else f"read_parquet('{path}/**/*.parquet', hive_partitioning=1)"
            union_parts.append(f"SELECT '{label}' as market, ticker, date, open, high, low, close, volume FROM {scan}")

        if union_parts:
            sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(union_parts)
            self.conn.execute(sql)
        logger.info("DuckDB views created successfully")

    # -------------------------------------------------------------------------
    # Main Feature Generation
    # -------------------------------------------------------------------------

    def generate_features(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        compute_target: bool = True,
        include_sentiment: bool = False,
        include_social_sentiment: bool = False,
    ) -> pd.DataFrame:
        """
        Generate all features for specified tickers and date range.

        Args:
            tickers: List of ticker symbols
            start_date: Start date for feature computation
            end_date: End date for feature computation
            compute_target: Whether to compute target variable (next-day return)
            include_sentiment: Whether to include sentiment features from news
            include_social_sentiment: Whether to include social sentiment features

        Returns:
            DataFrame with all features computed
        """
        logger.info(f"Generating features for {len(tickers)} tickers from {start_date} to {end_date}")

        # Load OHLCV data from DuckDB
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
        df = self.conn.execute(query, [tickers, start_date, end_date]).df()

        if df.empty:
            logger.warning(f"No data found for tickers: {tickers}")
            return df

        logger.info(f"Loaded {len(df)} rows of OHLCV data")

        # Compute all features by ticker group
        result_dfs = []
        ticker_groups = {ticker: ticker_df.copy() for ticker, ticker_df in df.groupby("ticker", sort=False)}
        for ticker in tqdm(tickers, desc="Computing features"):
            ticker_df = ticker_groups.get(ticker)
            if ticker_df is None:
                continue

            # Skip if not enough data
            if len(ticker_df) < 60:  # Minimum 60 days for rolling calculations
                logger.warning(f"Skipping {ticker}: only {len(ticker_df)} days of data")
                continue

            ticker_df = self.feature_pipeline.compute(ticker_df)
            if not compute_target and "next_day_return" in ticker_df.columns:
                ticker_df = ticker_df.drop(columns=["next_day_return"])

            result_dfs.append(ticker_df)

        # Combine all tickers
        if not result_dfs:
            logger.error("No features generated - all tickers were skipped (insufficient data)")
            return pd.DataFrame()

        features_df = pd.concat(result_dfs, ignore_index=True)

        # Remove rows with NaN in critical columns
        critical_cols = ["close", "volume", "rsi_14", "macd"]
        features_df = features_df.dropna(subset=critical_cols, how="any")

        # Merge sentiment features if requested
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

        logger.info(f"Generated {len(features_df)} rows of features with {len(features_df.columns)} columns")
        logger.debug(f"Feature columns: {list(features_df.columns)}")

        return features_df

    # -------------------------------------------------------------------------
    # Sentiment Feature Merging
    # -------------------------------------------------------------------------

    def merge_sentiment_features(
        self,
        features_df: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Merge aggregated sentiment scores into features DataFrame.

        Computes daily sentiment metrics from news data and joins with
        existing features. Missing sentiment (no news that day) is filled
        with neutral values (0.0).

        Args:
            features_df: DataFrame with existing features (must have ticker, date columns)
            start_date: Start date for sentiment data
            end_date: End date for sentiment data

        Returns:
            DataFrame with additional sentiment columns:
                - avg_daily_sentiment: Average sentiment score for the day
                - news_count: Number of news articles for the day
                - positive_count: Number of positive articles
                - negative_count: Number of negative articles
                - neutral_count: Number of neutral articles
                - sentiment_std: Standard deviation of sentiment scores
        """
        if features_df.empty:
            logger.warning("Empty features DataFrame, skipping sentiment merge")
            return features_df

        logger.info(
            "Merging sentiment features for %s tickers from %s to %s",
            features_df["ticker"].nunique(),
            start_date,
            end_date,
        )
        ticker_filter = tuple(sorted(features_df["ticker"].unique()))

        # Load sentiment data from news parquet files
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
            FROM {_scan_for(LAKE_DIR / "us_news")}
            WHERE ticker IN {ticker_filter}
            AND date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).df()

            if sentiment_df.empty:
                logger.warning("No sentiment data found, adding neutral sentiment columns")
                features_df["avg_daily_sentiment"] = 0.0
                features_df["news_count"] = 0
                features_df["positive_count"] = 0
                features_df["negative_count"] = 0
                features_df["neutral_count"] = 0
                features_df["sentiment_std"] = 0.0
                return features_df

            logger.info(f"Loaded {len(sentiment_df)} sentiment data points")

            # Merge with features
            merged_df = features_df.merge(
                sentiment_df,
                on=["ticker", "date"],
                how="left",
            )

            # Fill missing sentiment (no news that day) with neutral values
            merged_df["avg_daily_sentiment"] = merged_df["avg_daily_sentiment"].fillna(0.0)
            merged_df["news_count"] = merged_df["news_count"].fillna(0).astype(int)
            merged_df["positive_count"] = merged_df["positive_count"].fillna(0).astype(int)
            merged_df["negative_count"] = merged_df["negative_count"].fillna(0).astype(int)
            merged_df["neutral_count"] = merged_df["neutral_count"].fillna(0).astype(int)
            merged_df["sentiment_std"] = merged_df["sentiment_std"].fillna(0.0)

            logger.info(
                "Merged sentiment features: %s rows with news, %s rows without news",
                (merged_df["news_count"] > 0).sum(),
                (merged_df["news_count"] == 0).sum(),
            )

            return merged_df

        except Exception as e:
            logger.error(f"Failed to merge sentiment features: {e}")
            # Return original features without sentiment
            return features_df

    def merge_social_sentiment_features(
        self,
        features_df: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Merge aggregated social sentiment scores into features DataFrame.

        Computes daily social sentiment metrics from Reddit/Twitter data and joins
        with existing features. Missing sentiment (no data that day) is filled
        with neutral values (0.0 mentions).

        Args:
            features_df: DataFrame with existing features (must have ticker, date columns)
            start_date: Start date for social sentiment data
            end_date: End date for social sentiment data

        Returns:
            DataFrame with additional social sentiment columns:
                - social_mention_count: Total number of mentions (Reddit + Twitter)
                - social_sentiment_score: Average normalized sentiment (-1.0 to 1.0)
                - social_positive_score: Total positive mentions
                - social_negative_score: Total negative mentions
                - social_reddit_mentions: Reddit mention count
                - social_twitter_mentions: Twitter mention count
                - social_momentum: 5-day change in mention count
                - social_sentiment_momentum: 5-day change in sentiment score
        """
        if features_df.empty:
            logger.warning("Empty features DataFrame, skipping social sentiment merge")
            return features_df

        logger.info(
            "Merging social sentiment features for %s tickers from %s to %s",
            features_df["ticker"].nunique(),
            start_date,
            end_date,
        )
        ticker_filter = tuple(sorted(features_df["ticker"].unique()))

        # Load social sentiment data from parquet files
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
            FROM {_scan_for(LAKE_DIR / "us_social_sentiment")}
            WHERE ticker IN {ticker_filter}
            AND date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).df()

            if sentiment_df.empty:
                logger.warning("No social sentiment data found, adding neutral social sentiment columns")
                # Add neutral social sentiment columns
                features_df["social_mention_count"] = 0
                features_df["social_sentiment_score"] = 0.0
                features_df["social_positive_score"] = 0.0
                features_df["social_negative_score"] = 0.0
                features_df["social_reddit_mentions"] = 0
                features_df["social_twitter_mentions"] = 0
                features_df["social_momentum"] = 0.0
                features_df["social_sentiment_momentum"] = 0.0
                return features_df

            logger.info(f"Loaded {len(sentiment_df)} social sentiment data points")

            # Merge with features
            merged_df = features_df.merge(
                sentiment_df,
                on=["ticker", "date"],
                how="left",
            )

            # Fill missing social sentiment (no data that day) with neutral values
            merged_df["social_mention_count"] = merged_df["social_mention_count"].fillna(0).astype(int)
            merged_df["social_sentiment_score"] = merged_df["social_sentiment_score"].fillna(0.0)
            merged_df["social_positive_score"] = merged_df["social_positive_score"].fillna(0.0)
            merged_df["social_negative_score"] = merged_df["social_negative_score"].fillna(0.0)
            merged_df["social_reddit_mentions"] = merged_df["social_reddit_mentions"].fillna(0).astype(int)
            merged_df["social_twitter_mentions"] = merged_df["social_twitter_mentions"].fillna(0).astype(int)

            # Compute momentum metrics (5-day change)
            merged_df = merged_df.sort_values(["ticker", "date"])
            merged_df["social_momentum"] = merged_df.groupby("ticker")["social_mention_count"].pct_change(5)
            merged_df["social_sentiment_momentum"] = merged_df.groupby("ticker")["social_sentiment_score"].diff(5)

            # Fill NaN momentum values for first 5 days
            merged_df["social_momentum"] = merged_df["social_momentum"].fillna(0.0)
            merged_df["social_sentiment_momentum"] = merged_df["social_sentiment_momentum"].fillna(0.0)

            logger.info(
                "Merged social sentiment features: %s rows with data, %s rows without data",
                (merged_df["social_mention_count"] > 0).sum(),
                (merged_df["social_mention_count"] == 0).sum(),
            )

            return merged_df

        except Exception as e:
            logger.error(f"Failed to merge social sentiment features: {e}")
            # Return original features without social sentiment
            return features_df

    def add_cross_modal_sentiment_features(self, features_df: pd.DataFrame) -> pd.DataFrame:
        """Add minimal cross-modal sentiment features on top of merged sentiment columns."""
        if features_df.empty:
            return features_df

        enriched = features_df.sort_values(["ticker", "date"]).copy()
        log_volume = np.log1p(enriched["volume"].clip(lower=0))

        if "avg_daily_sentiment" in enriched.columns:
            enriched["sentiment_x_log_volume"] = enriched["avg_daily_sentiment"].fillna(0.0) * log_volume
            enriched["news_sentiment_momentum_5d"] = enriched.groupby("ticker")["avg_daily_sentiment"].diff(5).fillna(0.0)

        if "social_sentiment_score" in enriched.columns:
            enriched["social_sentiment_x_log_volume"] = enriched["social_sentiment_score"].fillna(0.0) * log_volume
            enriched["social_sentiment_momentum_5d"] = enriched.groupby("ticker")["social_sentiment_score"].diff(5).fillna(0.0)

        if {"avg_daily_sentiment", "social_sentiment_score"}.issubset(enriched.columns):
            enriched["news_social_sentiment_gap"] = enriched["avg_daily_sentiment"].fillna(0.0) - enriched["social_sentiment_score"].fillna(0.0)

        if {"news_count", "social_mention_count"}.issubset(enriched.columns):
            enriched["news_social_mentions_gap"] = enriched["news_count"].fillna(0) - enriched["social_mention_count"].fillna(0)

        return enriched

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
