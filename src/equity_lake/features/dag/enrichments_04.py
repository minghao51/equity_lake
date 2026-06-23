"""Gold layer: external data enrichment joins.

All external data merges (sentiment, analyst, SEC, macro) are consolidated
into a single ``enriched_features`` DAG node.  The node receives the
technically-computed feature frame plus a DuckDB connection and boolean
flags, then applies enabled enrichments sequentially.

Enrichment flag defaults
------------------------
``enable_macro`` defaults to **True** because macro indicators (VIX,
treasury yields, DXY) are cheap to load from a single compact table and
provide broad market context for every prediction.

All other enrichments (``enable_news_sentiment``, ``enable_social_sentiment``,
``enable_enriched_sentiment``, ``enable_analyst_ratings``,
``enable_sec_features``) default to **False** because they require specific
silver-layer tables that may not exist for all deployments. Callers opt in
via the ``include_*`` parameters on ``FeatureEngineer.generate_features()``.

This replaces the imperative merge calls that lived in
``FeatureEngineer.generate_features()`` and standalone modules
(``enriched_sentiment.py``, ``analyst_features.py``, ``sec_features.py``).
"""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import (
    BRONZE_MACRO_DIR,
    SILVER_ANALYST_RATINGS_DIR,
    SILVER_NEWS_SENTIMENT_DIR,
    SILVER_PROCESSED_ARTICLES_DIR,
    SILVER_SEC_EXTRACTIONS_DIR,
    SILVER_SOCIAL_SENTIMENT_DIR,
)
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()


def enriched_features(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_news_sentiment: bool = False,
    enable_social_sentiment: bool = False,
    enable_enriched_sentiment: bool = False,
    enable_analyst_ratings: bool = False,
    enable_sec_features: bool = False,
    enable_macro: bool = True,
) -> pl.DataFrame:
    """Apply all enabled external data enrichments sequentially.

    Each enrichment is a left join (or ASOF join for SEC) onto the feature
    frame.  Disabled enrichments are skipped entirely.
    """
    result = features_df
    if result.is_empty():
        return result

    if enable_news_sentiment:
        result = _merge_news_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_social_sentiment:
        result = _merge_social_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_enriched_sentiment:
        result = _merge_enriched_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_analyst_ratings:
        result = _merge_analyst_ratings(result, duckdb_conn, start_date, end_date)
    if enable_sec_features:
        result = _merge_sec_extractions(result, duckdb_conn, start_date, end_date)

    result = _add_cross_modal(result)

    if enable_macro:
        result = _merge_macro(result, duckdb_conn, start_date, end_date)

    return result


# ---------------------------------------------------------------------------
# Private enrichment functions (migrated from FeatureEngineer + standalone modules)
# ---------------------------------------------------------------------------


def _merge_news_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge aggregated Finnhub news sentiment onto feature frame."""
    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())

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
        FROM {duckdb_scan_for(SILVER_NEWS_SENTIMENT_DIR)}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
        GROUP BY ticker, date
    """

    try:
        sentiment_df = conn.execute(sentiment_query, [tickers, start_date, end_date]).pl()
        if sentiment_df.is_empty():
            return features_df.with_columns(
                pl.lit(0.0).alias("avg_daily_sentiment"),
                pl.lit(0).alias("news_count"),
                pl.lit(0).alias("positive_count"),
                pl.lit(0).alias("negative_count"),
                pl.lit(0).alias("neutral_count"),
                pl.lit(0.0).alias("sentiment_std"),
            )

        merged_df = features_df.join(sentiment_df, on=["ticker", "date"], how="left").with_columns(
            pl.col("avg_daily_sentiment").fill_null(0.0),
            pl.col("news_count").fill_null(0).cast(pl.Int64),
            pl.col("positive_count").fill_null(0).cast(pl.Int64),
            pl.col("negative_count").fill_null(0).cast(pl.Int64),
            pl.col("neutral_count").fill_null(0).cast(pl.Int64),
            pl.col("sentiment_std").fill_null(0.0),
        )
        merged_df = merged_df.sort(["ticker", "date"]).with_columns(
            pl.col("avg_daily_sentiment").ewm_mean(half_life=3.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_3d"),
            pl.col("avg_daily_sentiment").ewm_mean(half_life=7.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_7d"),
            pl.col("avg_daily_sentiment").ewm_mean(half_life=30.0, ignore_nulls=True).over("ticker").fill_null(0.0).alias("sentiment_ewma_30d"),
        )
        return merged_df
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.error("news_sentiment_merge_failed", error_type=type(exc).__name__, error=str(exc))
        return features_df


def _merge_social_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge aggregated social sentiment scores."""
    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())

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
        FROM {duckdb_scan_for(SILVER_SOCIAL_SENTIMENT_DIR)}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
        GROUP BY ticker, date
    """

    try:
        sentiment_df = conn.execute(sentiment_query, [tickers, start_date, end_date]).pl()
        if sentiment_df.is_empty():
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
        return merged_df
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.error("social_sentiment_merge_failed", error_type=type(exc).__name__, error=str(exc))
        return features_df


def _merge_enriched_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge LLM-enriched article-ticker sentiment from silver layer."""
    silver_path = SILVER_PROCESSED_ARTICLES_DIR
    if not silver_path.exists():
        return _add_empty_enriched_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(silver_path)

    query = f"""
        SELECT
            ticker,
            date,
            COUNT(*) as enriched_article_count,
            AVG(sentiment_score) as enriched_sentiment_mean,
            AVG(confidence) as enriched_confidence_mean,
            AVG(market_relevance) as enriched_relevance_mean,
            SUM(CASE WHEN sentiment_label = 'bullish' THEN 1.0 ELSE 0.0 END)
                / NULLIF(COUNT(*), 0) as bullish_ratio,
            SUM(CASE WHEN source_type IN ('reddit', 'stocktwits') THEN 1 ELSE 0 END) as social_volume,
            AVG(CASE WHEN source_type IN ('reddit', 'stocktwits') THEN sentiment_score ELSE NULL END) as social_sentiment_mean,
            MAX(CASE WHEN market_relevance > 0.8 AND impact_horizon = 'short' THEN 1 ELSE 0 END) as breaking_news_flag
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
        GROUP BY ticker, date
    """

    try:
        sentiment_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.error("enriched_sentiment_query_failed", error_type=type(exc).__name__, error=str(exc))
        return _add_empty_enriched_columns(features_df)

    if sentiment_df.is_empty():
        return _add_empty_enriched_columns(features_df)

    ewma_df = sentiment_df.sort(["ticker", "date"]).with_columns(
        pl.col("enriched_sentiment_mean")
        .ewm_mean(half_life=3.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("enriched_sentiment_ewma_3d"),
        pl.col("enriched_sentiment_mean")
        .ewm_mean(half_life=7.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("enriched_sentiment_ewma_7d"),
    )

    merged_df = features_df.join(ewma_df, on=["ticker", "date"], how="left").with_columns(
        pl.col("enriched_article_count").fill_null(0).cast(pl.Int64),
        pl.col("enriched_sentiment_mean").fill_null(0.0),
        pl.col("enriched_sentiment_ewma_3d").fill_null(0.0),
        pl.col("enriched_sentiment_ewma_7d").fill_null(0.0),
        pl.col("enriched_confidence_mean").fill_null(0.0),
        pl.col("enriched_relevance_mean").fill_null(0.0),
        pl.col("bullish_ratio").fill_null(0.0),
        pl.col("social_volume").fill_null(0).cast(pl.Int64),
        pl.col("social_sentiment_mean").fill_null(0.0),
        pl.col("breaking_news_flag").fill_null(0).cast(pl.Int64),
    )
    return merged_df


def _merge_analyst_ratings(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge analyst consensus + price targets."""
    ratings_path = SILVER_ANALYST_RATINGS_DIR
    if not ratings_path.exists():
        return _add_empty_analyst_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(ratings_path)

    query = f"""
        SELECT
            ticker,
            date,
            consensus_score AS analyst_consensus_score,
            price_target_count AS analyst_coverage_count,
            price_target_mean AS analyst_price_target_mean
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
    """

    try:
        ratings_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.error("analyst_rating_query_failed", error_type=type(exc).__name__, error=str(exc))
        return _add_empty_analyst_columns(features_df)

    if ratings_df.is_empty():
        return _add_empty_analyst_columns(features_df)

    ewma_df = ratings_df.sort(["ticker", "date"]).with_columns(
        pl.col("analyst_consensus_score")
        .ewm_mean(half_life=7.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("analyst_consensus_score_ewma_7d"),
    )

    merged_df = features_df.join(ewma_df, on=["ticker", "date"], how="left").with_columns(
        pl.col("analyst_consensus_score").fill_null(0.0),
        pl.col("analyst_consensus_score_ewma_7d").fill_null(0.0),
        pl.col("analyst_coverage_count").fill_null(0).cast(pl.Int64),
    )

    if "close" in merged_df.columns and "analyst_price_target_mean" in merged_df.columns:
        merged_df = merged_df.with_columns(
            ((pl.col("analyst_price_target_mean") - pl.col("close")) / pl.col("close").clip(lower_bound=1e-8)).alias("analyst_price_target_upside"),
        )
    else:
        merged_df = merged_df.with_columns(pl.lit(0.0).alias("analyst_price_target_upside"))

    merged_df = merged_df.with_columns(
        pl.col("analyst_price_target_mean").fill_null(0.0),
        pl.col("analyst_price_target_upside").fill_null(0.0),
    )
    return merged_df


def _merge_sec_extractions(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """ASOF join SEC filing extractions (point-in-time)."""
    sec_path = SILVER_SEC_EXTRACTIONS_DIR
    if not sec_path.exists():
        return _add_empty_sec_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(sec_path)

    query = f"""
        SELECT
            ticker,
            filing_date,
            risk_sentiment,
            management_tone,
            guidance_direction,
            new_vs_repeated
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
    """

    try:
        sec_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.error("sec_query_failed", error_type=type(exc).__name__, error=str(exc))
        return _add_empty_sec_columns(features_df)

    if sec_df.is_empty():
        return _add_empty_sec_columns(features_df)

    sec_df = sec_df.with_columns(
        pl.when(pl.col("guidance_direction") == "positive").then(1).otherwise(0).alias("sec_guidance_positive"),
        pl.when(pl.col("new_vs_repeated").is_in(["new", "modified"])).then(1).otherwise(0).alias("sec_risk_change_flag"),
    )

    sec_aggregated = (
        sec_df.sort("filing_date")
        .group_by("ticker", "filing_date")
        .agg(
            pl.col("risk_sentiment").mean().alias("risk_sentiment"),
            pl.col("management_tone").mean().alias("management_tone"),
            pl.col("sec_guidance_positive").max().alias("sec_guidance_positive"),
            pl.col("sec_risk_change_flag").max().alias("sec_risk_change_flag"),
        )
        .sort(["ticker", "filing_date"])
    )

    merged_df = (
        features_df.sort(["ticker", "date"])
        .join_asof(
            sec_aggregated,
            left_on="date",
            right_on="filing_date",
            by="ticker",
            strategy="backward",
        )
        .with_columns(
            pl.col("risk_sentiment").fill_null(0.0).alias("sec_risk_sentiment"),
            pl.col("management_tone").fill_null(0.0).alias("sec_management_tone"),
            pl.col("sec_guidance_positive").fill_null(0).cast(pl.Int64),
            pl.col("sec_risk_change_flag").fill_null(0).cast(pl.Int64),
        )
        .drop(["filing_date", "risk_sentiment", "management_tone"])
    )
    return merged_df


def _add_cross_modal(features_df: pl.DataFrame) -> pl.DataFrame:
    """Derived cross-modal sentiment features."""
    if features_df.is_empty():
        return features_df

    enriched = features_df.sort(["ticker", "date"])
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


def _merge_macro(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Pivot macro indicators to wide format and as-of join on date."""
    macro_path = BRONZE_MACRO_DIR
    if not macro_path.exists():
        return features_df

    try:
        macro_scan = duckdb_scan_for(macro_path)
        raw = conn.execute(
            f"""
            SELECT date, indicator, value
            FROM {macro_scan}
            WHERE date BETWEEN ? AND ?
            """,
            [start_date, end_date],
        ).pl()
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        logger.warning("macro_load_failed", error_type=type(exc).__name__, error=str(exc))
        return features_df

    if raw.is_empty():
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
    return joined


# ---------------------------------------------------------------------------
# Empty-column helpers
# ---------------------------------------------------------------------------


def _add_empty_enriched_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(0).alias("enriched_article_count"),
        pl.lit(0.0).alias("enriched_sentiment_mean"),
        pl.lit(0.0).alias("enriched_sentiment_ewma_3d"),
        pl.lit(0.0).alias("enriched_sentiment_ewma_7d"),
        pl.lit(0.0).alias("enriched_confidence_mean"),
        pl.lit(0.0).alias("enriched_relevance_mean"),
        pl.lit(0.0).alias("bullish_ratio"),
        pl.lit(0).alias("social_volume"),
        pl.lit(0.0).alias("social_sentiment_mean"),
        pl.lit(0).alias("breaking_news_flag"),
    )


def _add_empty_analyst_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(0.0).alias("analyst_consensus_score"),
        pl.lit(0.0).alias("analyst_consensus_score_ewma_7d"),
        pl.lit(0).alias("analyst_coverage_count"),
        pl.lit(0.0).alias("analyst_price_target_mean"),
        pl.lit(0.0).alias("analyst_price_target_upside"),
    )


def _add_empty_sec_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(0.0).alias("sec_risk_sentiment"),
        pl.lit(0.0).alias("sec_management_tone"),
        pl.lit(0).alias("sec_guidance_positive"),
        pl.lit(0).alias("sec_risk_change_flag"),
    )
