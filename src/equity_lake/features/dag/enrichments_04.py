"""Gold layer: external data enrichment joins.

Each external data merge (news, social, enriched sentiment, analyst, SEC, macro)
is its own ``@tag``'d Hamilton node, chained sequentially so that the catalog
shows real per-enrichment lineage and each step is independently selectable /
cacheable. The chain terminates at ``enriched_features``, the public request
target.

Enrichment flag defaults
------------------------
``enable_macro`` defaults to **True** so the feature frame retains a stable
macro schema. Macro values remain unavailable (null, with
``macro_available=0``) until persisted source vintages establish verified
point-in-time availability; observation dates or ingestion timestamps alone
do not prove when a value became available (ADR-0015).

All other enrichments (``enable_news_sentiment``, ``enable_social_sentiment``,
``enable_enriched_sentiment``, ``enable_analyst_ratings``,
``enable_sec_features``) default to **False** because they require specific
silver-layer tables that may not exist for all deployments. Callers opt in
via the ``include_*`` parameters on ``FeatureEngineer.generate_features()``.

Chain topology
--------------
``features_df`` → ``news_sentiment_enriched`` → ``social_sentiment_enriched``
→ ``enriched_sentiment_merged`` → ``analyst_ratings_enriched``
→ ``sec_extractions_enriched`` → ``cross_modal_features`` → ``macro_enriched``
→ ``enriched_features`` (terminal passthrough preserving the public name).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import structlog
from hamilton.function_modifiers import tag

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AvailabilityContractError,
    source_observable_session,
)
from equity_lake.core.instruments import IDENTITY_COLUMNS, InstrumentKey  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.paths import (
    SILVER_NEWS_SENTIMENT_DIR,
    SILVER_SEC_EXTRACTIONS_DIR,
    SILVER_SOCIAL_SENTIMENT_DIR,
)
from equity_lake.features.dag.raw_01 import normalize_eod_date_series

logger = structlog.get_logger()


def _identity_for_features(features_df: pl.DataFrame) -> InstrumentKey | None:
    if not set(IDENTITY_COLUMNS).issubset(features_df.columns):
        return None
    identities = features_df.select(*IDENTITY_COLUMNS).unique()
    if identities.height != 1:
        return None
    return InstrumentKey(**identities.row(0, named=True))


def _source_sessions(instrument: InstrumentKey, observations: pl.DataFrame, clock_column: str = "source_available_at") -> list[dict[str, Any]]:
    """Require trusted clocks for every candidate row and map to venue sessions."""
    if clock_column not in observations.columns:
        raise AvailabilityContractError(f"{clock_column} is absent; source availability is unknown")
    rows: list[dict[str, Any]] = []
    for observation in observations.to_dicts():
        source_time = observation.get(clock_column)
        if not isinstance(source_time, datetime) or source_time.tzinfo is None or source_time.utcoffset() is None:
            raise AvailabilityContractError("source availability clock is unknown")
        observation["_observable_session"] = source_observable_session(instrument, source_time)
        rows.append(observation)
    return rows


def _unavailable_columns(features_df: pl.DataFrame, columns: dict[str, Any], flag: str) -> pl.DataFrame:
    """Keep a stable feature schema while distinguishing unknown from neutral."""
    additions = [pl.lit(None, dtype=dtype).alias(name) for name, dtype in columns.items()]
    additions.append(pl.lit(0, dtype=pl.Int8).alias(flag))
    return features_df.with_columns(additions)


def _eod_keys_to_date(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    """Normalize EOD join keys in ``frame`` to ``pl.Date`` (D01/F01).

    Applied at every external-table query boundary so temporal joins never
    fail on ``Date``/``Datetime`` key mismatch (the pre-W01 defect silently
    zero-filled enrichments via the caught ``SchemaError``). Missing columns
    pass through untouched; malformed string keys raise.
    """
    expressions = []
    for column in columns:
        if column in frame.columns and frame.schema[column] != pl.Date:
            expressions.append(normalize_eod_date_series(frame[column]).alias(column))
    return frame.with_columns(expressions) if expressions else frame


# ---------------------------------------------------------------------------
# Public DAG nodes (linear enrichment chain)
# ---------------------------------------------------------------------------


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="news_sentiment_enriched",
    description="Left-join aggregated Finnhub news sentiment + EWMA onto features",
)
def news_sentiment_enriched(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_news_sentiment: bool = False,
) -> pl.DataFrame:
    """Merge aggregated news sentiment onto the feature frame (opt-in)."""
    if not enable_news_sentiment or features_df.is_empty():
        return features_df
    return _merge_news_sentiment(features_df, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="social_sentiment_enriched",
    description="Left-join aggregated social sentiment scores + momentum onto features",
)
def social_sentiment_enriched(
    news_sentiment_enriched: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_social_sentiment: bool = False,
) -> pl.DataFrame:
    """Merge aggregated social sentiment scores (opt-in)."""
    if not enable_social_sentiment or news_sentiment_enriched.is_empty():
        return news_sentiment_enriched
    return _merge_social_sentiment(news_sentiment_enriched, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="enriched_sentiment_merged",
    description="Left-join LLM-enriched article-ticker sentiment from silver layer",
)
def enriched_sentiment_merged(
    social_sentiment_enriched: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_enriched_sentiment: bool = False,
) -> pl.DataFrame:
    """Merge LLM-enriched article-ticker sentiment (opt-in)."""
    if not enable_enriched_sentiment or social_sentiment_enriched.is_empty():
        return social_sentiment_enriched
    return _merge_enriched_sentiment(social_sentiment_enriched, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="analyst_ratings_enriched",
    description="Left-join analyst consensus + price targets with EWMA and upside",
)
def analyst_ratings_enriched(
    enriched_sentiment_merged: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_analyst_ratings: bool = False,
) -> pl.DataFrame:
    """Merge analyst consensus + price targets (opt-in)."""
    if not enable_analyst_ratings or enriched_sentiment_merged.is_empty():
        return enriched_sentiment_merged
    return _merge_analyst_ratings(enriched_sentiment_merged, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="sec_extractions_enriched",
    description="ASOF-join SEC filing extractions (point-in-time risk/tone/guidance)",
)
def sec_extractions_enriched(
    analyst_ratings_enriched: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_sec_features: bool = False,
) -> pl.DataFrame:
    """ASOF-join SEC filing extractions (opt-in)."""
    if not enable_sec_features or analyst_ratings_enriched.is_empty():
        return analyst_ratings_enriched
    return _merge_sec_extractions(analyst_ratings_enriched, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="cross_modal_features",
    description="Derive cross-modal sentiment interaction features (always runs)",
)
def cross_modal_features(sec_extractions_enriched: pl.DataFrame) -> pl.DataFrame:
    """Derive cross-modal sentiment features from any prior enrichment columns."""
    return _add_cross_modal(sec_extractions_enriched)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="macro_enriched",
    description="Fail closed: macro values remain null until persisted source vintages verify point-in-time availability (ADR-0015)",
)
def macro_enriched(
    cross_modal_features: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_macro: bool = True,
) -> pl.DataFrame:
    """Expose macro values as unavailable until persisted source vintages prove point-in-time availability."""
    if not enable_macro or cross_modal_features.is_empty():
        return cross_modal_features
    return _merge_macro(cross_modal_features, duckdb_conn, start_date, end_date)


@tag(  # type: ignore[untyped-decorator]
    layer="gold",
    category="enrichment",
    produces="enriched_features",
    description="Terminal enriched feature frame assembling all enabled enrichments",
)
def enriched_features(macro_enriched: pl.DataFrame) -> pl.DataFrame:
    """Terminal node returning the fully enriched feature frame.

    Preserved as a named terminal so the request list ``["enriched_features"]``
    and downstream catalog/tests can target a stable public name regardless of
    how many enrichment nodes are enabled upstream.
    """
    return macro_enriched


# ---------------------------------------------------------------------------
# Private enrichment functions (migrated from FeatureEngineer + standalone modules)
# ---------------------------------------------------------------------------


def _merge_news_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Join only composite-identity news with trusted clocks and 3 articles/7d."""
    empty = _add_empty_news_sentiment_columns(features_df)
    instrument = _identity_for_features(features_df)
    if instrument is None:
        return empty
    query_start = start_date - timedelta(days=7)
    try:
        observations = pl.read_delta(SILVER_NEWS_SENTIMENT_DIR).filter(
            (pl.col("market") == instrument.market)
            & (pl.col("mic") == instrument.mic)
            & (pl.col("symbol") == instrument.symbol)
            & pl.col("date").cast(pl.Date, strict=False).is_between(query_start, end_date)
        )
        rows = _source_sessions(instrument, observations)
    except (OSError, pl.exceptions.PolarsError, AvailabilityContractError, ValueError) as exc:
        logger.warning("news_sentiment_unavailable", instrument=instrument.display_key, error_type=type(exc).__name__)
        return empty
    invalid_score = any(
        not isinstance(row.get("url"), str) or not math.isfinite(float(row["sentiment_score"]))
        for row in rows
        if row.get("sentiment_score") is not None
    )
    if invalid_score:
        return empty

    metrics: dict[str, list[object]] = {
        name: []
        for name in (
            "avg_daily_sentiment",
            "news_count",
            "positive_count",
            "negative_count",
            "neutral_count",
            "sentiment_std",
            "sentiment_ewma_3d",
            "sentiment_ewma_7d",
            "sentiment_ewma_30d",
            "news_coverage_7d",
            "news_source_age_days",
            "news_available",
        )
    }
    for day in features_df["date"].to_list():
        in_window = [row for row in rows if day - timedelta(days=6) <= row["_observable_session"] <= day]
        by_url: dict[str, dict[str, Any]] = {}
        for row in in_window:
            score = row.get("sentiment_score")
            url = row.get("url")
            if score is None or not isinstance(url, str) or not url:
                continue
            prior = by_url.get(url)
            if prior is None or row["source_available_at"] > prior["source_available_at"]:
                by_url[url] = row
            elif row["source_available_at"] == prior["source_available_at"] and float(score) != float(prior["sentiment_score"]):
                by_url = {}
                break
        scored = list(by_url.values())
        scores: list[float] = [float(row["sentiment_score"]) for row in scored]
        enough = len(scored) >= 3
        metrics["news_available"].append(1 if enough else 0)
        metrics["news_coverage_7d"].append(len(scored))
        metrics["news_count"].append(len(scored) if scored else None)
        metrics["news_source_age_days"].append((day - max(row["_observable_session"] for row in scored)).days if scored else None)
        mean_score = sum(scores) / len(scores) if scores else None
        sample_std: float | None = None
        if enough and len(scores) > 1:
            mean_for_std = sum(scores) / len(scores)
            sample_std = math.sqrt(sum((score - mean_for_std) ** 2 for score in scores) / (len(scores) - 1))
        metrics["avg_daily_sentiment"].append(mean_score if enough else None)
        metrics["sentiment_std"].append(sample_std)
        for label in ("positive", "negative", "neutral"):
            metrics[f"{label}_count"].append(sum(row.get("sentiment_label") == label for row in scored) if enough else None)
        for name in ("sentiment_ewma_3d", "sentiment_ewma_7d", "sentiment_ewma_30d"):
            metrics[name].append(None)
    integer_columns = {"news_count", "positive_count", "negative_count", "neutral_count", "news_coverage_7d", "news_source_age_days"}
    typed = [
        pl.Series(
            name,
            values,
            dtype=pl.Int8 if name == "news_available" else pl.Int32 if name in integer_columns else pl.Float64,
        )
        for name, values in metrics.items()
    ]
    return features_df.with_columns(typed)


def _merge_social_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Join seven-day social context separately from scored news articles."""
    empty = _add_empty_social_sentiment_columns(features_df)
    instrument = _identity_for_features(features_df)
    if instrument is None:
        return empty
    try:
        observations = pl.read_delta(SILVER_SOCIAL_SENTIMENT_DIR).filter(
            (pl.col("market") == instrument.market)
            & (pl.col("mic") == instrument.mic)
            & (pl.col("symbol") == instrument.symbol)
            & pl.col("date").cast(pl.Date, strict=False).is_between(start_date - timedelta(days=7), end_date)
        )
        rows = _source_sessions(instrument, observations)
    except (OSError, pl.exceptions.PolarsError, AvailabilityContractError, ValueError) as exc:
        logger.warning("social_sentiment_unavailable", instrument=instrument.display_key, error_type=type(exc).__name__)
        return empty

    names = (
        "social_mention_count",
        "social_sentiment_score",
        "social_positive_score",
        "social_negative_score",
        "social_reddit_mentions",
        "social_twitter_mentions",
        "social_reddit_available",
        "social_twitter_available",
        "social_positive_available",
        "social_negative_available",
        "social_momentum",
        "social_sentiment_momentum",
        "social_sentiment_ewma_3d",
        "social_sentiment_ewma_7d",
        "social_sentiment_ewma_30d",
        "social_context_rows_7d",
        "social_source_age_days",
        "social_available",
    )
    values: dict[str, list[object]] = {name: [] for name in names}
    for day in features_df["date"].to_list():
        in_window = [row for row in rows if day - timedelta(days=6) <= row["_observable_session"] <= day]
        grouped: dict[tuple[object, object], list[dict[str, Any]]] = {}
        for row in in_window:
            grouped.setdefault((row.get("source"), row.get("datetime")), []).append(row)
        context: list[dict[str, Any]] = []
        for duplicates in grouped.values():
            latest_time = max(row["source_available_at"] for row in duplicates)
            latest = [row for row in duplicates if row["source_available_at"] == latest_time]
            measured = {(row.get("mention_count"), row.get("score"), row.get("positive_score"), row.get("negative_score")) for row in latest}
            if len(measured) == 1:
                context.append(latest[0])
            else:
                logger.warning("social_sentiment_conflicting_vintage", instrument=instrument.display_key, source=latest[0].get("source"))

        valid_scores = [row for row in context if row.get("score") is not None]
        measured_mentions = [row for row in context if row.get("mention_count") is not None]
        positive = [row for row in context if row.get("positive_score") is not None]
        negative = [row for row in context if row.get("negative_score") is not None]
        available = bool(valid_scores)
        values["social_available"].append(1 if available else 0)
        values["social_context_rows_7d"].append(len(valid_scores) if valid_scores else None)
        values["social_source_age_days"].append((day - max(row["_observable_session"] for row in valid_scores)).days if valid_scores else None)
        values["social_mention_count"].append(sum(int(row["mention_count"]) for row in measured_mentions) if measured_mentions else None)
        values["social_sentiment_score"].append(sum(float(row["score"]) for row in valid_scores) / len(valid_scores) if valid_scores else None)
        values["social_positive_score"].append(sum(float(row["positive_score"]) for row in positive) if positive else None)
        values["social_negative_score"].append(sum(float(row["negative_score"]) for row in negative) if negative else None)
        values["social_positive_available"].append(1 if positive else 0)
        values["social_negative_available"].append(1 if negative else 0)
        for source, mentions_name, available_name in (
            ("reddit", "social_reddit_mentions", "social_reddit_available"),
            ("twitter", "social_twitter_mentions", "social_twitter_available"),
        ):
            family = [row for row in measured_mentions if row.get("source") == source]
            values[available_name].append(1 if family else 0)
            values[mentions_name].append(sum(int(row["mention_count"]) for row in family) if family else None)
        for name in (
            "social_momentum",
            "social_sentiment_momentum",
            "social_sentiment_ewma_3d",
            "social_sentiment_ewma_7d",
            "social_sentiment_ewma_30d",
        ):
            values[name].append(None)
    integer_columns = {
        "social_mention_count",
        "social_reddit_mentions",
        "social_twitter_mentions",
        "social_context_rows_7d",
        "social_source_age_days",
    }
    flag_columns = {
        "social_available",
        "social_reddit_available",
        "social_twitter_available",
        "social_positive_available",
        "social_negative_available",
    }
    typed = [
        pl.Series(
            name,
            values[name],
            dtype=pl.Int8 if name in flag_columns else pl.Int32 if name in integer_columns else pl.Float64,
        )
        for name in names
    ]
    return features_df.with_columns(typed)


def _merge_enriched_sentiment(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Mark legacy LLM article enrichments unavailable without identity/clocks."""
    return _add_empty_enriched_columns(features_df)


def _merge_analyst_ratings(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Mark analyst data unavailable until identity and publication vintages exist."""
    return _add_empty_analyst_columns(features_df)


def _merge_sec_extractions(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Use identity-scoped SEC extracts only within their 95-day visible age."""
    empty = _add_empty_sec_columns(features_df)
    instrument = _identity_for_features(features_df)
    if instrument is None:
        return empty
    try:
        observations = pl.read_delta(SILVER_SEC_EXTRACTIONS_DIR).filter(
            (pl.col("market") == instrument.market)
            & (pl.col("mic") == instrument.mic)
            & (pl.col("symbol") == instrument.symbol)
            & pl.col("date").cast(pl.Date, strict=False).is_between(start_date - timedelta(days=95), end_date)
        )
        rows = _source_sessions(instrument, observations)
    except (OSError, pl.exceptions.PolarsError, AvailabilityContractError, ValueError) as exc:
        logger.warning("sec_context_unavailable", instrument=instrument.display_key, error_type=type(exc).__name__)
        return empty

    outputs: dict[str, list[object]] = {
        "sec_risk_sentiment": [],
        "sec_management_tone": [],
        "sec_guidance_positive": [],
        "sec_risk_change_flag": [],
        "sec_source_age_days": [],
        "sec_available": [],
    }
    for day in features_df["date"].to_list():
        latest = [
            row
            for row in rows
            if row["_observable_session"] <= day
            and (day - row["_observable_session"]).days <= 95
            and isinstance(row.get("filing_date"), date)
            and row["filing_date"] <= day
        ]
        selected = max(latest, key=lambda row: row["source_available_at"]) if latest else None
        outputs["sec_available"].append(1 if selected else 0)
        outputs["sec_source_age_days"].append((day - selected["_observable_session"]).days if selected else None)
        outputs["sec_risk_sentiment"].append(selected.get("risk_sentiment") if selected else None)
        outputs["sec_management_tone"].append(selected.get("management_tone") if selected else None)
        guidance = selected.get("guidance_direction") if selected else None
        change = selected.get("new_vs_repeated") if selected else None
        outputs["sec_guidance_positive"].append(1 if guidance == "positive" else 0 if guidance == "negative" else None)
        outputs["sec_risk_change_flag"].append(1 if change in {"new", "modified"} else 0 if change == "repeated" else None)
    integer_columns = {"sec_available", "sec_guidance_positive", "sec_risk_change_flag"}
    typed = [
        pl.Series(
            name,
            outputs[name],
            dtype=pl.Int8 if name in integer_columns else pl.Int32 if name == "sec_source_age_days" else pl.Float64,
        )
        for name in outputs
    ]
    return features_df.with_columns(typed)


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
                (pl.col("avg_daily_sentiment") * log_volume).alias("sentiment_x_log_volume"),
                pl.col("avg_daily_sentiment").diff(5).over("ticker").alias("news_sentiment_momentum_5d"),
            ]
        )

    if "social_sentiment_score" in enriched.columns:
        # Handoff 08 A6: ``social_sentiment_momentum_5d`` duplicated the exact
        # ``diff(5)`` expression already computed as ``social_sentiment_momentum``
        # (perfectly correlated twins). Reuse the computed column when present
        # (the DAG always provides it); only compute the fallback for direct
        # callers whose frame lacks it. Both output names stay in the schema.
        social_momentum_5d = (
            pl.col("social_sentiment_momentum")
            if "social_sentiment_momentum" in enriched.columns
            else pl.col("social_sentiment_score").diff(5).over("ticker")
        )
        expressions.extend(
            [
                (pl.col("social_sentiment_score") * log_volume).alias("social_sentiment_x_log_volume"),
                social_momentum_5d.alias("social_sentiment_momentum_5d"),
            ]
        )

    if {"avg_daily_sentiment", "social_sentiment_score"}.issubset(enriched.columns):
        expressions.append((pl.col("avg_daily_sentiment") - pl.col("social_sentiment_score")).alias("news_social_sentiment_gap"))

    if {"news_count", "social_mention_count"}.issubset(enriched.columns):
        expressions.append((pl.col("news_count") - pl.col("social_mention_count")).alias("news_social_mentions_gap"))

    return enriched.with_columns(expressions) if expressions else enriched


def _merge_macro(
    features_df: pl.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Expose macro context as unavailable until source vintages are recorded."""
    # Observation dates and local ingestion timestamps are not release/vintage
    # proofs. ADR-0015 forbids using them as if the value were known that day.
    return _add_empty_macro_columns(features_df)


# ---------------------------------------------------------------------------
# Empty-column helpers
# ---------------------------------------------------------------------------


def _add_empty_news_sentiment_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Unknown news remains null, with an explicit unavailable flag."""
    return _unavailable_columns(
        df,
        {
            "avg_daily_sentiment": pl.Float64,
            "news_count": pl.Int32,
            "positive_count": pl.Int32,
            "negative_count": pl.Int32,
            "neutral_count": pl.Int32,
            "sentiment_std": pl.Float64,
            "sentiment_ewma_3d": pl.Float64,
            "sentiment_ewma_7d": pl.Float64,
            "sentiment_ewma_30d": pl.Float64,
            "news_coverage_7d": pl.Int32,
            "news_source_age_days": pl.Int32,
        },
        "news_available",
    )


def _add_empty_social_sentiment_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Unknown social context remains null, separate from article coverage."""
    return _unavailable_columns(
        df,
        {
            "social_mention_count": pl.Int32,
            "social_sentiment_score": pl.Float64,
            "social_positive_score": pl.Float64,
            "social_negative_score": pl.Float64,
            "social_reddit_mentions": pl.Int32,
            "social_twitter_mentions": pl.Int32,
            "social_reddit_available": pl.Int8,
            "social_twitter_available": pl.Int8,
            "social_positive_available": pl.Int8,
            "social_negative_available": pl.Int8,
            "social_momentum": pl.Float64,
            "social_sentiment_momentum": pl.Float64,
            "social_sentiment_ewma_3d": pl.Float64,
            "social_sentiment_ewma_7d": pl.Float64,
            "social_sentiment_ewma_30d": pl.Float64,
            "social_context_rows_7d": pl.Int32,
            "social_source_age_days": pl.Int32,
        },
        "social_available",
    )


def _add_empty_enriched_columns(df: pl.DataFrame) -> pl.DataFrame:
    return _unavailable_columns(
        df,
        {
            "enriched_article_count": pl.Int32,
            "enriched_sentiment_mean": pl.Float64,
            "enriched_sentiment_ewma_3d": pl.Float64,
            "enriched_sentiment_ewma_7d": pl.Float64,
            "enriched_confidence_mean": pl.Float64,
            "enriched_relevance_mean": pl.Float64,
            "bullish_ratio": pl.Float64,
            "social_volume": pl.Int32,
            "social_sentiment_mean": pl.Float64,
            "breaking_news_flag": pl.Int8,
            "enriched_source_age_days": pl.Int32,
        },
        "enriched_available",
    )


def _add_empty_analyst_columns(df: pl.DataFrame) -> pl.DataFrame:
    return _unavailable_columns(
        df,
        {
            "analyst_consensus_score": pl.Float64,
            "analyst_consensus_score_ewma_7d": pl.Float64,
            "analyst_coverage_count": pl.Int32,
            "analyst_price_target_mean": pl.Float64,
            "analyst_price_target_upside": pl.Float64,
            "analyst_source_age_days": pl.Int32,
        },
        "analyst_available",
    )


def _add_empty_sec_columns(df: pl.DataFrame) -> pl.DataFrame:
    return _unavailable_columns(
        df,
        {
            "sec_risk_sentiment": pl.Float64,
            "sec_management_tone": pl.Float64,
            "sec_guidance_positive": pl.Int8,
            "sec_risk_change_flag": pl.Int8,
            "sec_source_age_days": pl.Int32,
        },
        "sec_available",
    )


def _add_empty_macro_columns(df: pl.DataFrame) -> pl.DataFrame:
    return _unavailable_columns(
        df,
        {
            "vix": pl.Float64,
            "treasury_10y": pl.Float64,
            "tips_yield": pl.Float64,
            "dxy": pl.Float64,
            "vix_change_5d": pl.Float64,
            "yield_curve_slope": pl.Float64,
            "dxy_change_5d": pl.Float64,
            "macro_source_age_days": pl.Int32,
        },
        "macro_available",
    )
