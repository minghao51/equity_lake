"""Candidate event generation helpers for ML training."""

from __future__ import annotations

from typing import Any

import polars as pl

from equity_lake.core.polars_utils import FrameLike, ensure_polars

DEFAULT_BACKTEST_STRATEGY: dict[str, Any] = {
    "name": "momentum",
    "lookback_days": 20,
    "buy_threshold": 0.02,
    "sell_threshold": -0.01,
}


def build_candidate_frame(df: FrameLike, candidate_strategies: list[dict[str, Any]]) -> pl.DataFrame:
    """Build candidate trade events from backtest-rule signals."""
    frame = ensure_polars(df)
    if frame.is_empty():
        return frame

    candidate_frames: list[pl.DataFrame] = []
    for strategy in candidate_strategies:
        lookback = int(strategy.get("lookback_days", 20))
        if frame.height < lookback:
            continue

        strategy_name = str(strategy.get("name", "momentum"))
        buy_thresh = float(strategy.get("buy_threshold", 0.02))
        sell_thresh = float(strategy.get("sell_threshold", -0.01))
        strategy_df = frame.with_columns(pl.col("close").cast(pl.Float64).rolling_mean(window_size=lookback).alias("sma")).with_columns(
            ((pl.col("close") - pl.col("sma")) / pl.col("sma")).alias("pct_diff")
        )

        buy_candidates = strategy_df.filter(pl.col("pct_diff") >= buy_thresh).with_columns(
            pl.lit("BUY").alias("candidate_action"),
            pl.col("pct_diff").abs().alias("candidate_score"),
            pl.lit(strategy_name).alias("candidate_source"),
        )
        sell_candidates = strategy_df.filter(pl.col("pct_diff") <= sell_thresh).with_columns(
            pl.lit("SELL").alias("candidate_action"),
            pl.col("pct_diff").abs().alias("candidate_score"),
            pl.lit(strategy_name).alias("candidate_source"),
        )
        candidate_frames.extend([buy_candidates, sell_candidates])

    if not candidate_frames:
        return pl.DataFrame()

    return (
        pl.concat(candidate_frames, how="vertical_relaxed")
        .sort(["date", "candidate_score"], descending=[False, True])
        .unique(subset=["date"], keep="first", maintain_order=True)
    )
