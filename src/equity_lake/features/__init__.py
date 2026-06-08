"""Feature engineering domain APIs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

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
    output_dir: str | Path | None = None,
    compute_target: bool = True,
    include_sentiment: bool = False,
    include_social_sentiment: bool = False,
) -> pd.DataFrame:
    """Generate features over a warm-up window, then persist the requested range."""
    feature_engineer_cls = _load_feature_engineer()

    output_path = Path(output_dir) if output_dir else Path("data/lake/features")
    query_start_date = output_start_date - pd.Timedelta(days=120)
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
        )
    finally:
        engineer.close()

    if features_df.empty:
        raise ValueError("No features generated")

    output_df = features_df[(features_df["date"] >= pd.Timestamp(output_start_date)) & (features_df["date"] <= pd.Timestamp(output_end_date))].copy()

    if output_df.empty:
        raise ValueError("No features generated for the requested output window")

    output_path.mkdir(parents=True, exist_ok=True)
    for partition_date, group in output_df.groupby("date"):
        partition_dir = output_path / f"date={partition_date.strftime('%Y-%m-%d')}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        output_file = partition_dir / f"{partition_date.strftime('%Y-%m-%d')}.parquet"
        group.to_parquet(output_file, index=False)

    return output_df


__all__ = ["FeatureEngineer", "run_feature_job"]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name == "FeatureEngineer":
        from equity_lake.features.engineering import FeatureEngineer

        return FeatureEngineer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
