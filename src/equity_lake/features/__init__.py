"""Feature engineering domain APIs."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from equity_lake.features.engineering import FeatureEngineer


def _load_feature_engineer() -> type[FeatureEngineer]:
    try:
        from equity_lake.features.engineering import FeatureEngineer
    except ImportError as exc:
        raise RuntimeError(
            "Feature engineering requires the optional 'ml' dependencies.",
        ) from exc
    return FeatureEngineer


def run_feature_job(
    *,
    tickers: list[str],
    output_start_date: date,
    output_end_date: date,
    compute_target: bool = True,
    include_sentiment: bool = False,
    include_social_sentiment: bool = False,
    include_enriched_sentiment: bool = False,
    include_analyst_ratings: bool = False,
    include_sec_features: bool = False,
) -> pl.DataFrame:
    """Generate features over a warm-up window, then persist the requested range."""
    feature_engineer_cls = _load_feature_engineer()

    query_start_date = output_start_date - timedelta(days=120)
    query_end_date = output_end_date

    engineer = feature_engineer_cls()
    try:
        features_df = engineer.generate_features(
            tickers=tickers,
            start_date=query_start_date,
            end_date=query_end_date,
            compute_target=compute_target,
            include_sentiment=include_sentiment,
            include_social_sentiment=include_social_sentiment,
            include_enriched_sentiment=include_enriched_sentiment,
            include_analyst_ratings=include_analyst_ratings,
            include_sec_features=include_sec_features,
        )
    finally:
        engineer.close()

    if features_df.is_empty():
        raise ValueError("No features generated")

    if "date" in features_df.columns and features_df.schema["date"] != pl.Date:
        features_df = features_df.with_columns(pl.col("date").cast(pl.Date))

    output_df = features_df.filter((pl.col("date") >= pl.lit(output_start_date)) & (pl.col("date") <= pl.lit(output_end_date)))

    if output_df.is_empty():
        raise ValueError("No features generated for the requested output window")

    from equity_lake.ingestion.writers import write_to_partitioned_parquet

    write_to_partitioned_parquet(output_df, "features", output_end_date)

    return output_df


__all__ = ["FeatureEngineer", "run_feature_job"]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name == "FeatureEngineer":
        from equity_lake.features.engineering import FeatureEngineer

        return FeatureEngineer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
