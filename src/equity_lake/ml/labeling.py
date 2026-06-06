"""Triple-barrier labeling helpers for v2 meta-label training."""

from __future__ import annotations

import pandas as pd


def apply_triple_barrier_labels(
    candidates: pd.DataFrame,
    full_df: pd.DataFrame,
    *,
    vertical_barrier_days: int,
    pt_mult: float,
    sl_mult: float,
) -> pd.DataFrame:
    """Apply triple-barrier labeling to candidate trades."""
    if candidates.empty:
        return candidates

    indexed = full_df.reset_index(drop=True).copy()
    index_by_date = {row.date: row.Index for row in indexed[["date"]].itertuples()}
    labeled = candidates.copy()
    meta_labels: list[int] = []
    outcomes: list[str] = []
    upper_returns: list[float] = []
    lower_returns: list[float] = []

    for row in labeled.itertuples():
        row_idx = index_by_date.get(row.date)
        if row_idx is None:
            meta_labels.append(0)
            outcomes.append("missing")
            upper_returns.append(0.0)
            lower_returns.append(0.0)
            continue

        entry_price = float(indexed.iloc[row_idx]["close"])
        base_vol = float(indexed.iloc[row_idx].get("volatility_20", 0.0) or 0.0)
        scaled_vol = max(base_vol, 0.005)
        pt_return = scaled_vol * pt_mult
        sl_return = scaled_vol * sl_mult
        end_idx = min(row_idx + vertical_barrier_days, len(indexed) - 1)
        side = 1 if row.candidate_action == "BUY" else -1
        outcome = "time_loss"
        label = 0

        for future_idx in range(row_idx + 1, end_idx + 1):
            future_row = indexed.iloc[future_idx]
            high_ret = (float(future_row["high"]) / entry_price) - 1
            low_ret = (float(future_row["low"]) / entry_price) - 1

            if side == 1:
                hit_profit = high_ret >= pt_return
                hit_stop = low_ret <= -sl_return
            else:
                hit_profit = low_ret <= -pt_return
                hit_stop = high_ret >= sl_return

            if hit_profit and hit_stop:
                outcome = "both_hit"
                label = 0
                break
            if hit_profit:
                outcome = "profit"
                label = 1
                break
            if hit_stop:
                outcome = "stop"
                label = 0
                break
        else:
            expiry_close = float(indexed.iloc[end_idx]["close"])
            expiry_return = side * ((expiry_close / entry_price) - 1)
            label = int(expiry_return > 0)
            outcome = "time_profit" if label == 1 else "time_loss"

        meta_labels.append(label)
        outcomes.append(outcome)
        upper_returns.append(pt_return)
        lower_returns.append(sl_return)

    labeled["meta_label"] = meta_labels
    labeled["barrier_outcome"] = outcomes
    labeled["upper_barrier_return"] = upper_returns
    labeled["lower_barrier_return"] = lower_returns
    labeled["vertical_barrier_days"] = vertical_barrier_days
    return labeled
