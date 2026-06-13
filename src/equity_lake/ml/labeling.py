"""Triple-barrier labeling helpers for v2 meta-label training."""

from __future__ import annotations

import polars as pl

from equity_lake.core.polars_utils import FrameLike, ensure_polars


def apply_triple_barrier_labels(
    candidates: FrameLike,
    full_df: FrameLike,
    *,
    vertical_barrier_days: int,
    pt_mult: float,
    sl_mult: float,
) -> pl.DataFrame:
    """Apply triple-barrier labeling to candidate trades.

    Returns the input frame with columns appended:
    ``meta_label``, ``barrier_outcome``, ``upper_barrier_return``,
    ``lower_barrier_return``, ``vertical_barrier_days``,
    ``barrier_start_idx`` and ``barrier_end_idx``.

    The ``barrier_start_idx``/``barrier_end_idx`` columns mark the
    row-index range over which each trade's outcome was evaluated; this
    is needed to compute de Prado sample-uniqueness weights downstream.
    """
    labeled = ensure_polars(candidates)
    if labeled.is_empty():
        return labeled

    indexed = ensure_polars(full_df).sort("date").with_row_index("row_idx")
    indexed_rows = indexed.iter_rows(named=True)
    rows = list(indexed_rows)
    index_by_date = {row["date"]: int(row["row_idx"]) for row in rows}

    meta_labels: list[int] = []
    outcomes: list[str] = []
    upper_returns: list[float] = []
    lower_returns: list[float] = []
    barrier_starts: list[int] = []
    barrier_ends: list[int] = []

    for row in labeled.iter_rows(named=True):
        row_idx = index_by_date.get(row["date"])
        if row_idx is None:
            meta_labels.append(0)
            outcomes.append("missing")
            upper_returns.append(0.0)
            lower_returns.append(0.0)
            barrier_starts.append(-1)
            barrier_ends.append(-1)
            continue

        entry_row = rows[row_idx]
        entry_price = float(entry_row["close"])
        base_vol = float(entry_row.get("volatility_20", 0.0) or 0.0)
        scaled_vol = max(base_vol, 0.005)
        pt_return = scaled_vol * pt_mult
        sl_return = scaled_vol * sl_mult
        end_idx = min(row_idx + vertical_barrier_days, len(rows) - 1)
        side = 1 if row["candidate_action"] == "BUY" else -1
        outcome = "time_loss"
        label = 0

        for future_idx in range(row_idx + 1, end_idx + 1):
            future_row = rows[future_idx]
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
            expiry_close = float(rows[end_idx]["close"])
            expiry_return = side * ((expiry_close / entry_price) - 1)
            label = int(expiry_return > 0)
            outcome = "time_profit" if label == 1 else "time_loss"

        meta_labels.append(label)
        outcomes.append(outcome)
        upper_returns.append(pt_return)
        lower_returns.append(sl_return)
        barrier_starts.append(row_idx)
        barrier_ends.append(end_idx)

    return labeled.with_columns(
        pl.Series("meta_label", meta_labels),
        pl.Series("barrier_outcome", outcomes),
        pl.Series("upper_barrier_return", upper_returns),
        pl.Series("lower_barrier_return", lower_returns),
        pl.lit(vertical_barrier_days).alias("vertical_barrier_days"),
        pl.Series("barrier_start_idx", barrier_starts),
        pl.Series("barrier_end_idx", barrier_ends),
    )
