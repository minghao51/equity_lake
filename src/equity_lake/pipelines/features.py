"""Hamilton nodes for feature engineering.

Reuses the existing hamilton_features module functions and adds
orchestration nodes for loading OHLCV, computing features per ticker,
and writing partitioned output.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import structlog

from equity_lake.core.paths import LAKE_DIR

logger = structlog.get_logger()
_FEATURE_LOOKBACK_DAYS = 120


def _available_market_sources() -> list[tuple[str, Path]]:
    """Return only market directories that currently contain parquet data."""
    market_paths = [
        ("us", LAKE_DIR / "us_equity"),
        ("cn", LAKE_DIR / "cn_ashare"),
        ("hk_sg", LAKE_DIR / "hk_sg_equity"),
        ("jpx", LAKE_DIR / "jpx_equity"),
        ("krx", LAKE_DIR / "krx_equity"),
    ]
    return [(market, path) for market, path in market_paths if path.exists() and any(path.rglob("*.parquet"))]


def _market_source_sql(path: Path, market: str) -> str:
    """Build the best available DuckDB source query for a market."""
    from deltalake import DeltaTable

    if DeltaTable.is_deltatable(str(path)):
        return f"""
        SELECT '{market}' as market, ticker, date, open, high, low, close, volume
        FROM delta_scan('{path}')
        """.strip()

    return f"""
    SELECT '{market}' as market, ticker, date, open, high, low, close, volume
    FROM read_parquet('{path}/**/*.parquet', hive_partitioning=1, union_by_name=true)
    """.strip()


def load_ohlcv_from_duckdb(
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Load OHLCV data from DuckDB views across all markets."""
    import duckdb

    market_sources = _available_market_sources()
    if not market_sources:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL delta; LOAD delta;")
    union_sql = "\n        UNION ALL\n        ".join(_market_source_sql(path, market) for market, path in market_sources)
    conn.execute(f"CREATE OR REPLACE VIEW equity_all AS {union_sql}")

    query = f"""
        SELECT ticker, date, open, high, low, close, volume
        FROM equity_all
        WHERE ticker IN {tuple(tickers)}
        AND date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY ticker, date
    """

    df = conn.execute(query).df()
    conn.close()
    return df


def compute_features_per_ticker(
    load_ohlcv_from_duckdb: pd.DataFrame,
    compute_target: bool = True,
    include_sentiment: bool = False,
    include_social_sentiment: bool = False,
) -> pd.DataFrame:
    """Compute features for each ticker using the Hamilton feature pipeline."""
    from tqdm import tqdm

    from equity_lake.features.pipeline import FeaturePipeline

    df = load_ohlcv_from_duckdb
    if df.empty:
        return df

    pipeline = FeaturePipeline()
    result_dfs: list[pd.DataFrame] = []

    ticker_groups = {ticker: ticker_df.copy() for ticker, ticker_df in df.groupby("ticker", sort=False)}
    tickers = [t for t in ticker_groups if len(ticker_groups[t]) >= 60]

    for ticker in tqdm(tickers, desc="Computing features"):
        ticker_df = ticker_groups[ticker]
        computed = pipeline.compute(ticker_df)
        if not compute_target and "next_day_return" in computed.columns:
            computed = computed.drop(columns=["next_day_return"])
        result_dfs.append(computed)

    if not result_dfs:
        return pd.DataFrame()

    features_df = pd.concat(result_dfs, ignore_index=True)
    critical_cols = ["close", "volume", "rsi_14", "macd"]
    features_df = features_df.dropna(subset=critical_cols, how="any")

    if include_sentiment or include_social_sentiment:
        from equity_lake.features.engineering import FeatureEngineer

        engineer = FeatureEngineer()
        try:
            if include_sentiment:
                features_df = engineer.merge_sentiment_features(
                    features_df,
                    start_date=features_df["date"].min().date(),
                    end_date=features_df["date"].max().date(),
                )
            if include_social_sentiment:
                features_df = engineer.merge_social_sentiment_features(
                    features_df,
                    start_date=features_df["date"].min().date(),
                    end_date=features_df["date"].max().date(),
                )
            features_df = engineer.add_cross_modal_sentiment_features(features_df)
        finally:
            engineer.close()

    return features_df


def write_feature_parquet(
    compute_features_per_ticker: pd.DataFrame,
    output_dir: Path | None = None,
) -> pd.DataFrame:
    """Write feature DataFrame to partitioned Parquet files."""
    features_df = compute_features_per_ticker
    if features_df.empty:
        return features_df

    out_path = output_dir or (LAKE_DIR / "features")
    out_path.mkdir(parents=True, exist_ok=True)

    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(out_path)):
            from equity_lake.storage.delta import merge_delta

            merge_delta(features_df, out_path.name, key_columns=["ticker", "date"], lake_dir=out_path.parent)
            logger.info("features_written", rows=len(features_df), path=str(out_path))
            return features_df
    except ImportError:
        pass

    for partition_date, group in features_df.groupby("date"):
        partition_dir = out_path / f"date={pd.Timestamp(partition_date).strftime('%Y-%m-%d')}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        output_file = partition_dir / f"{pd.Timestamp(partition_date).strftime('%Y-%m-%d')}.parquet"
        group.to_parquet(output_file, index=False)

    logger.info("features_written", rows=len(features_df), path=str(out_path))
    return features_df


def compute_features(
    price_data: pd.DataFrame,
    features: list[str] | None = None,
) -> pd.DataFrame:
    """Compute features from price data using the default Hamilton pipeline."""
    from equity_lake.features.pipeline import compute_features as _compute

    return _compute(price_data=price_data, features=features)


def run_feature_pipeline(
    tickers: list[str],
    output_start_date: date,
    output_end_date: date,
    output_dir: str | Path | None = None,
    compute_target: bool = True,
    include_sentiment: bool = False,
    include_social_sentiment: bool = False,
) -> pd.DataFrame:
    """Run the full feature engineering pipeline (replaces run_feature_job)."""
    output_path = Path(output_dir) if output_dir else LAKE_DIR / "features"
    query_start_date = output_start_date - pd.Timedelta(days=_FEATURE_LOOKBACK_DAYS)

    ohlcv = load_ohlcv_from_duckdb(
        tickers=tickers,
        start_date=query_start_date,
        end_date=output_end_date,
    )

    features_df = compute_features_per_ticker(
        load_ohlcv_from_duckdb=ohlcv,
        compute_target=compute_target,
        include_sentiment=include_sentiment,
        include_social_sentiment=include_social_sentiment,
    )

    if features_df.empty:
        raise ValueError("No features generated")

    output_df = features_df[(features_df["date"] >= pd.Timestamp(output_start_date)) & (features_df["date"] <= pd.Timestamp(output_end_date))].copy()

    if output_df.empty:
        raise ValueError("No features generated for the requested output window")

    write_feature_parquet(compute_features_per_ticker=output_df, output_dir=output_path)
    return output_df
