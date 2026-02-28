#!/usr/bin/env python3
"""
Feature Engineering for Equity ML Models

Computes technical indicators, momentum features, and time-based features
from raw OHLCV data for machine learning models.

Usage:
    python -m equity_lake.features.engineering --date 2026-01-23
    python -m equity_lake.features.engineering --tickers AAPL,GOOGL --start 2024-01-01 --end 2024-12-31
"""

import argparse
import sys
from datetime import date, datetime

import duckdb
import pandas as pd
import pandas_ta as ta
import structlog
from tqdm import tqdm

from equity_lake.core.logging import timer
from equity_lake.core.runtime import (
    LAKE_DIR,
    setup_logging,
)

# Logger configuration - use structlog for consistency
logger = structlog.get_logger()


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

        # Create views if not exists (using existing query_example.py logic)
        self._setup_views()

    def _setup_views(self):
        """Set up DuckDB views for accessing OHLCV data."""
        # Create unified view across all markets
        # Note: Using glob pattern that includes Hive partitioning (date=*/*.parquet)
        self.conn.execute(f"""
            CREATE OR REPLACE VIEW equity_all AS
            SELECT
                'us' as market, ticker, date, open, high, low, close, volume
            FROM read_parquet('{LAKE_DIR}/us_equity/**/*.parquet', hive_partitioning=1)
            UNION ALL
            SELECT
                'cn' as market, ticker, date, open, high, low, close, volume
            FROM read_parquet('{LAKE_DIR}/cn_ashare/**/*.parquet', hive_partitioning=1)
            UNION ALL
            SELECT
                'hk_sg' as market, ticker, date, open, high, low, close, volume
            FROM read_parquet('{LAKE_DIR}/hk_sg_equity/**/*.parquet', hive_partitioning=1)
        """)
        logger.info("DuckDB views created successfully")

    # -------------------------------------------------------------------------
    # Technical Indicators
    # -------------------------------------------------------------------------

    def compute_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute technical indicators (RSI, MACD, Bollinger Bands, ATR).

        Args:
            df: DataFrame with OHLCV data (must have open, high, low, close, volume columns)

        Returns:
            DataFrame with additional technical indicator columns
        """
        df = df.copy()

        # Sort by date to ensure correct calculations
        df = df.sort_values("date")

        # RSI (14-period) - Relative Strength Index
        df["rsi_14"] = ta.rsi(df["close"], length=14)

        # MACD (12, 26, 9) - Moving Average Convergence Divergence
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_histogram"] = macd["MACDh_12_26_9"]

        # Bollinger Bands (20-day, 2 standard deviations)
        bb = ta.bbands(df["close"], length=20, std=2)
        df["bb_upper"] = bb["BBU_20_2.0_2.0"]
        df["bb_middle"] = bb["BBM_20_2.0_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0_2.0"]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df[
            "bb_middle"
        ]  # Bandwidth
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / (
            df["bb_upper"] - df["bb_lower"]
        )  # %B

        # ATR (14-period) - Average True Range
        df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Rate of Change (5, 10, 20-day)
        for period in [5, 10, 20]:
            df[f"roc_{period}"] = ta.roc(df["close"], length=period)

        logger.debug(f"Computed technical indicators: {list(df.columns)}")
        return df

    # -------------------------------------------------------------------------
    # Return Features
    # -------------------------------------------------------------------------

    def compute_return_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute lagged returns and momentum features.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            DataFrame with additional return feature columns
        """
        df = df.copy()
        df = df.sort_values("date")

        # Lagged returns (1, 5, 10, 20-day)
        for lag in [1, 5, 10, 20]:
            df[f"return_{lag}d"] = df["close"].pct_change(lag)

        # Overnight return: (open - prev_close) / prev_close
        df["overnight_return"] = (df["open"] - df["close"].shift(1)) / df[
            "close"
        ].shift(1)

        # Intraday return: (close - open) / open
        df["intraday_return"] = (df["close"] - df["open"]) / df["open"]

        # High-Low range relative to close
        df["hl_range"] = (df["high"] - df["low"]) / df["close"]

        logger.debug(f"Computed return features: {list(df.columns)}")
        return df

    # -------------------------------------------------------------------------
    # Volume Features
    # -------------------------------------------------------------------------

    def compute_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute volume-based indicators.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            DataFrame with additional volume feature columns
        """
        df = df.copy()
        df = df.sort_values("date")

        # Volume moving average (20-day)
        df["volume_ma_20"] = df["volume"].rolling(window=20).mean()

        # Volume rate of change (5-day)
        df["volume_roc_5"] = df["volume"].pct_change(5)

        # On-Balance Volume (OBV)
        obv = [0]
        for i in range(1, len(df)):
            if df["close"].iloc[i] > df["close"].iloc[i - 1]:
                obv.append(obv[-1] + df["volume"].iloc[i])
            elif df["close"].iloc[i] < df["close"].iloc[i - 1]:
                obv.append(obv[-1] - df["volume"].iloc[i])
            else:
                obv.append(obv[-1])
        df["obv"] = obv

        # Volume relative to average (volume / volume_ma_20)
        df["volume_ratio"] = df["volume"] / df["volume_ma_20"]

        logger.debug(f"Computed volume features: {list(df.columns)}")
        return df

    # -------------------------------------------------------------------------
    # Time Features
    # -------------------------------------------------------------------------

    def compute_time_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute time-based features (seasonality).

        Args:
            df: DataFrame with date column

        Returns:
            DataFrame with additional time feature columns
        """
        df = df.copy()

        # Ensure date is datetime
        df["date"] = pd.to_datetime(df["date"])

        # Day of week (0=Monday, 6=Sunday)
        df["day_of_week"] = df["date"].dt.dayofweek

        # Day of month (1-31)
        df["day_of_month"] = df["date"].dt.day

        # Month (1-12)
        df["month"] = df["date"].dt.month

        # Quarter (1-4)
        df["quarter"] = df["date"].dt.quarter

        # Days to month-end
        month_end = df["date"] + pd.offsets.MonthEnd(0)
        df["days_to_month_end"] = (month_end - df["date"]).dt.days

        # Trading day of month (approximately 1-22)
        df["trading_day_of_month"] = (
            df.groupby([df["date"].dt.year, df["date"].dt.month]).cumcount() + 1
        )

        logger.debug(f"Computed time features: {list(df.columns)}")
        return df

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
    ) -> pd.DataFrame:
        """
        Generate all features for specified tickers and date range.

        Args:
            tickers: List of ticker symbols
            start_date: Start date for feature computation
            end_date: End date for feature computation
            compute_target: Whether to compute target variable (next-day return)
            include_sentiment: Whether to include sentiment features from news

        Returns:
            DataFrame with all features computed
        """
        logger.info(
            f"Generating features for {len(tickers)} tickers from {start_date} to {end_date}"
        )

        # Load OHLCV data from DuckDB
        query = f"""
            SELECT
                ticker,
                date,
                open,
                high,
                low,
                close,
                volume
            FROM equity_all
            WHERE ticker IN {tuple(tickers)}
            AND date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY ticker, date
        """

        logger.debug(f"Executing query: {query}")
        df = self.conn.execute(query).df()

        if df.empty:
            logger.warning(f"No data found for tickers: {tickers}")
            return df

        logger.info(f"Loaded {len(df)} rows of OHLCV data")

        # Compute all features by ticker group
        result_dfs = []
        for ticker in tqdm(tickers, desc="Computing features"):
            ticker_df = df[df["ticker"] == ticker].copy()

            # Skip if not enough data
            if len(ticker_df) < 60:  # Minimum 60 days for rolling calculations
                logger.warning(f"Skipping {ticker}: only {len(ticker_df)} days of data")
                continue

            # Compute features
            ticker_df = self.compute_technical_indicators(ticker_df)
            ticker_df = self.compute_return_features(ticker_df)
            ticker_df = self.compute_volume_features(ticker_df)
            ticker_df = self.compute_time_features(ticker_df)

            # Compute target variable (next-day return)
            if compute_target:
                ticker_df["next_day_return"] = (
                    ticker_df["close"].shift(-1) / ticker_df["close"] - 1
                )

            result_dfs.append(ticker_df)

        # Combine all tickers
        if not result_dfs:
            logger.error(
                "No features generated - all tickers were skipped (insufficient data)"
            )
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

        logger.info(
            f"Generated {len(features_df)} rows of features with {len(features_df.columns)} columns"
        )
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
            FROM read_parquet('{LAKE_DIR}/us_news/**/*.parquet', hive_partitioning=1)
            WHERE date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).df()

            if sentiment_df.empty:
                logger.warning(
                    "No sentiment data found, adding neutral sentiment columns"
                )
                # Add neutral sentiment columns
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
            merged_df["avg_daily_sentiment"] = merged_df["avg_daily_sentiment"].fillna(
                0.0
            )
            merged_df["news_count"] = merged_df["news_count"].fillna(0).astype(int)
            merged_df["positive_count"] = (
                merged_df["positive_count"].fillna(0).astype(int)
            )
            merged_df["negative_count"] = (
                merged_df["negative_count"].fillna(0).astype(int)
            )
            merged_df["neutral_count"] = (
                merged_df["neutral_count"].fillna(0).astype(int)
            )
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
            FROM read_parquet('{LAKE_DIR}/us_social_sentiment/**/*.parquet', hive_partitioning=1)
            WHERE date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY ticker, date
        """

        try:
            sentiment_df = self.conn.execute(sentiment_query).df()

            if sentiment_df.empty:
                logger.warning(
                    "No social sentiment data found, adding neutral social sentiment columns"
                )
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

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# =============================================================================
# CLI
# =============================================================================


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Generate ML features from OHLCV data")

    parser.add_argument(
        "--tickers",
        type=str,
        help='Comma-separated list of tickers (e.g., "AAPL,GOOGL,MSFT")',
    )

    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD format)")

    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD format)")

    parser.add_argument(
        "--date",
        type=str,
        help="Single date to generate features for (YYYY-MM-DD format)",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(LAKE_DIR / "features"),
        help=f"Output directory for feature Parquet files (default: {LAKE_DIR}/features)",
    )

    parser.add_argument(
        "--no-target",
        action="store_true",
        help="Do not compute target variable (next-day return)",
    )

    parser.add_argument(
        "--with-sentiment",
        action="store_true",
        help="Include sentiment features from news data",
    )

    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging
    log_level_str = "DEBUG" if args.verbose else "INFO"
    setup_logging(
        name="feature_engineering",
        level=log_level_str,
        log_file="feature_engineering.log",
    )

    # Determine output date range
    if args.date:
        output_start_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        output_end_date = output_start_date
    elif args.start and args.end:
        output_start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        output_end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        logger.error("Must specify --date OR both --start and --end")
        sys.exit(1)

    # Determine tickers
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        logger.error("Must specify --tickers")
        sys.exit(1)

    logger.info(
        f"Feature engineering for {len(tickers)} tickers "
        f"from {output_start_date} to {output_end_date}"
    )

    from equity_lake.features import run_feature_job

    with timer("feature_generation", ticker_count=len(tickers)):
        try:
            features_df = run_feature_job(
                tickers=tickers,
                output_start_date=output_start_date,
                output_end_date=output_end_date,
                output_dir=args.output_dir,
                compute_target=not args.no_target,
                include_sentiment=args.with_sentiment,
            )
        except Exception as e:
            logger.error(f"Feature generation failed: {e}")
            raise

    logger.info(f"✅ Features written successfully to {args.output_dir}")
    logger.info(f"   Total rows: {len(features_df):,}")
    logger.info(f"   Total tickers: {features_df['ticker'].nunique()}")
    logger.info(
        f"   Date range: {features_df['date'].min()} to {features_df['date'].max()}"
    )
    logger.info(f"   Features: {len(features_df.columns)} columns")

    if args.with_sentiment:
        logger.info(
            "   Sentiment features included: avg_daily_sentiment, news_count, positive_count, negative_count"
        )


if __name__ == "__main__":
    main()
