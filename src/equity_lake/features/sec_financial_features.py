"""Feature engineering integration for SEC XBRL structured financials.

Reads the ``us_sec_financials`` Delta table and merges into the price
DataFrame using ASOF (point-in-time) join: each price row receives the
most recent filing **on or before** that date, preventing look-ahead bias.
"""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import SEC_FINANCIALS_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()

SEC_FINANCIAL_FEATURE_COLUMNS = [
    "sec_revenue",
    "sec_roe",
    "sec_roa",
    "sec_debt_to_equity",
    "sec_net_margin",
    "sec_operating_margin",
    "sec_eps",
]


def merge_sec_financial_features(
    conn: duckdb.DuckDBPyConnection,
    features_df: FrameLike,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge SEC structured financial features via point-in-time ASOF join.

    Each price row receives the most recent SEC financial filing dated
    **on or before** the price date. This prevents look-ahead bias.

    Args:
        conn: DuckDB connection (from FeatureEngineer).
        features_df: Price + technical feature DataFrame.
        start_date: Query start date.
        end_date: Query end date.

    Returns:
        DataFrame with SEC financial columns added via ASOF join.
    """
    features_df = ensure_polars(features_df)
    if features_df.is_empty():
        logger.warning("Empty features DataFrame, skipping SEC financial merge")
        return features_df

    sec_path = SEC_FINANCIALS_DIR
    if not sec_path.exists():
        logger.info("SEC financials directory not found, skipping merge")
        return _add_empty_sec_financial_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(sec_path)

    query = f"""
        SELECT
            ticker,
            date,
            revenue,
            roe,
            roa,
            debt_to_equity,
            net_margin,
            operating_margin,
            eps
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
    """

    try:
        sec_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except Exception as exc:
        logger.error("sec_financial_query_failed", error=str(exc))
        return _add_empty_sec_financial_columns(features_df)

    if sec_df.is_empty():
        logger.info("No SEC financial data found, adding neutral columns")
        return _add_empty_sec_financial_columns(features_df)

    logger.info("Loaded SEC financial data points", rows=sec_df.height)

    sec_aggregated = (
        sec_df.sort(["ticker", "date"])
        .group_by(["ticker", "date"])
        .agg(
            pl.col("revenue").mean().alias("revenue"),
            pl.col("roe").mean().alias("roe"),
            pl.col("roa").mean().alias("roa"),
            pl.col("debt_to_equity").mean().alias("debt_to_equity"),
            pl.col("net_margin").mean().alias("net_margin"),
            pl.col("operating_margin").mean().alias("operating_margin"),
            pl.col("eps").mean().alias("eps"),
        )
        .sort(["ticker", "date"])
    )

    merged_df = (
        features_df.sort(["ticker", "date"])
        .join_asof(
            sec_aggregated.rename(
                {
                    "revenue": "sec_revenue_raw",
                    "roe": "sec_roe_raw",
                    "roa": "sec_roa_raw",
                    "debt_to_equity": "sec_debt_to_equity_raw",
                    "net_margin": "sec_net_margin_raw",
                    "operating_margin": "sec_operating_margin_raw",
                    "eps": "sec_eps_raw",
                }
            ).sort(["ticker", "date"]),
            left_on="date",
            right_on="date",
            by="ticker",
            strategy="backward",
        )
        .with_columns(
            pl.col("sec_revenue_raw").fill_null(0.0).alias("sec_revenue"),
            pl.col("sec_roe_raw").fill_null(0.0).alias("sec_roe"),
            pl.col("sec_roa_raw").fill_null(0.0).alias("sec_roa"),
            pl.col("sec_debt_to_equity_raw").fill_null(0.0).alias("sec_debt_to_equity"),
            pl.col("sec_net_margin_raw").fill_null(0.0).alias("sec_net_margin"),
            pl.col("sec_operating_margin_raw").fill_null(0.0).alias("sec_operating_margin"),
            pl.col("sec_eps_raw").fill_null(0.0).alias("sec_eps"),
        )
        .drop(
            [
                "sec_revenue_raw",
                "sec_roe_raw",
                "sec_roa_raw",
                "sec_debt_to_equity_raw",
                "sec_net_margin_raw",
                "sec_operating_margin_raw",
                "sec_eps_raw",
            ]
        )
    )

    logger.info(
        "Merged SEC financial features",
        rows_with_data=merged_df.filter(pl.col("sec_revenue") > 0).height,
        rows_without_data=merged_df.filter(pl.col("sec_revenue") == 0).height,
    )
    return merged_df


def _add_empty_sec_financial_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add zero-filled SEC financial columns when no data exists."""
    return df.with_columns(
        pl.lit(0.0).alias("sec_revenue"),
        pl.lit(0.0).alias("sec_roe"),
        pl.lit(0.0).alias("sec_roa"),
        pl.lit(0.0).alias("sec_debt_to_equity"),
        pl.lit(0.0).alias("sec_net_margin"),
        pl.lit(0.0).alias("sec_operating_margin"),
        pl.lit(0.0).alias("sec_eps"),
    )


__all__ = ["SEC_FINANCIAL_FEATURE_COLUMNS", "merge_sec_financial_features"]
