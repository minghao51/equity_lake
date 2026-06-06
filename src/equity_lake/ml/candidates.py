"""Candidate event generation helpers for ML training."""

from __future__ import annotations

from typing import Any

import pandas as pd

DEFAULT_BACKTEST_STRATEGY: dict[str, Any] = {
    "name": "momentum",
    "lookback_days": 20,
    "buy_threshold": 0.02,
    "sell_threshold": -0.01,
}


def build_candidate_frame(df: pd.DataFrame, candidate_strategies: list[dict[str, Any]]) -> pd.DataFrame:
    """Build candidate trade events from backtest-rule signals."""
    if df.empty:
        return df

    candidate_frames: list[pd.DataFrame] = []
    for strategy in candidate_strategies:
        lookback = int(strategy.get("lookback_days", 20))
        if len(df) < lookback:
            continue

        strategy_df = df.copy()
        strategy_df["sma"] = strategy_df["close"].rolling(window=lookback).mean()
        strategy_df["pct_diff"] = (strategy_df["close"] - strategy_df["sma"]) / strategy_df["sma"]
        buy_thresh = float(strategy.get("buy_threshold", 0.02))
        sell_thresh = float(strategy.get("sell_threshold", -0.01))

        buy_candidates = strategy_df[strategy_df["pct_diff"] >= buy_thresh].copy()
        buy_candidates["candidate_action"] = "BUY"
        buy_candidates["candidate_score"] = buy_candidates["pct_diff"].abs()
        buy_candidates["candidate_source"] = str(strategy.get("name", "momentum"))

        sell_candidates = strategy_df[strategy_df["pct_diff"] <= sell_thresh].copy()
        sell_candidates["candidate_action"] = "SELL"
        sell_candidates["candidate_score"] = sell_candidates["pct_diff"].abs()
        sell_candidates["candidate_source"] = str(strategy.get("name", "momentum"))

        candidate_frames.extend([buy_candidates, sell_candidates])

    if not candidate_frames:
        return pd.DataFrame()

    candidates = pd.concat(candidate_frames, ignore_index=True)
    candidates = candidates.sort_values(["date", "candidate_score"], ascending=[True, False])
    candidates = candidates.drop_duplicates(subset=["date"], keep="first").reset_index(drop=True)
    return candidates
