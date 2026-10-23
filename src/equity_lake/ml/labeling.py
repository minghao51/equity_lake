"""Triple-barrier labeling helpers for v2 meta-label training."""

from __future__ import annotations

import math

import polars as pl

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
    validate_feature_action_coverage,
)
from equity_lake.core.polars_utils import FrameLike, ensure_polars


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _require_adjusted_input(frame: pl.DataFrame, label: str) -> None:
    required = {
        "market",
        "mic",
        "symbol",
        "price_adjustment_version",
        "availability_schema_version",
        "action_coverage",
        "action_source_age_days",
        "date",
        *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    }
    missing = required - set(frame.columns)
    if missing:
        raise AvailabilityContractError(f"{label} adjustment/availability contract is unknown: missing {sorted(missing)}")
    adjustment_versions = frame["price_adjustment_version"].drop_nulls().unique().to_list()
    availability_versions = frame["availability_schema_version"].drop_nulls().unique().to_list()
    if (
        adjustment_versions != [PRICE_ADJUSTMENT_VERSION]
        or availability_versions != [AVAILABILITY_SCHEMA_VERSION]
        or frame["action_coverage"].null_count()
        or any(value != 1 for value in frame["action_coverage"].to_list())
    ):
        raise AvailabilityContractError(f"{label} has unknown or incompatible point-in-time adjustment metadata")
    try:
        for row in frame.to_dicts():
            validate_feature_action_coverage(row)
    except AvailabilityContractError as exc:
        raise AvailabilityContractError(f"{label} action-coverage provenance is unavailable: {exc}") from exc


def apply_triple_barrier_labels(
    candidates: FrameLike,
    full_df: FrameLike,
    *,
    vertical_barrier_days: int,
    pt_mult: float,
    sl_mult: float,
) -> pl.DataFrame:
    """Apply triple-barrier labels without shortening immature edge events.

    Returns the input frame with ``meta_label``, outcome/barrier details,
    ``barrier_start_idx``, ``barrier_end_idx`` and ``barrier_end_date``. A
    right-edge event without a hit before the full vertical barrier remains
    pending (null label and maturity date). Mature event intervals end at the
    actual first hit or at the completed vertical barrier.
    """
    labeled = ensure_polars(candidates)
    if labeled.is_empty():
        return labeled

    _require_adjusted_input(labeled, "candidate")
    full = ensure_polars(full_df)
    _require_adjusted_input(full, "barrier price")
    indexed = full.sort("date").with_row_index("row_idx")
    rows = list(indexed.iter_rows(named=True))
    index_by_date = {row["date"]: int(row["row_idx"]) for row in rows}

    meta_labels: list[int | None] = []
    outcomes: list[str] = []
    upper_returns: list[float | None] = []
    lower_returns: list[float | None] = []
    barrier_starts: list[int] = []
    barrier_ends: list[int] = []
    barrier_end_dates: list[object | None] = []
    barrier_available_dates: list[object | None] = []

    for row in labeled.iter_rows(named=True):
        row_idx = index_by_date.get(row["date"])
        if row_idx is None:
            meta_labels.append(None)
            outcomes.append("missing")
            upper_returns.append(0.0)
            lower_returns.append(0.0)
            barrier_starts.append(-1)
            barrier_ends.append(-1)
            barrier_end_dates.append(None)
            barrier_available_dates.append(None)
            continue

        entry_row = rows[row_idx]
        raw_entry_price = entry_row.get("close")
        raw_base_vol = entry_row.get("volatility_20")
        entry_price = _finite_float(raw_entry_price)
        base_vol = _finite_float(raw_base_vol)
        if entry_price is None or base_vol is None:
            meta_labels.append(None)
            outcomes.append("unavailable")
            upper_returns.append(None)
            lower_returns.append(None)
            barrier_starts.append(row_idx)
            barrier_ends.append(-1)
            barrier_end_dates.append(None)
            barrier_available_dates.append(None)
            continue
        if entry_price <= 0 or base_vol < 0:
            meta_labels.append(None)
            outcomes.append("unavailable")
            upper_returns.append(None)
            lower_returns.append(None)
            barrier_starts.append(row_idx)
            barrier_ends.append(-1)
            barrier_end_dates.append(None)
            barrier_available_dates.append(None)
            continue
        scaled_vol = max(base_vol, 0.005)
        pt_return = scaled_vol * pt_mult
        sl_return = scaled_vol * sl_mult
        vertical_end_idx = row_idx + vertical_barrier_days
        observed_end_idx = min(vertical_end_idx, len(rows) - 1)
        side = 1 if row["candidate_action"] == "BUY" else -1
        outcome = "pending"
        label: int | None = None
        event_end_idx: int | None = None

        for future_idx in range(row_idx + 1, observed_end_idx + 1):
            future_row = rows[future_idx]
            high = future_row.get("high")
            low = future_row.get("low")
            high_value = _finite_float(high)
            low_value = _finite_float(low)
            if high_value is None or low_value is None:
                outcome = "unavailable"
                break
            high_ret = (high_value / entry_price) - 1
            low_ret = (low_value / entry_price) - 1

            if side == 1:
                hit_profit = high_ret >= pt_return
                hit_stop = low_ret <= -sl_return
            else:
                hit_profit = low_ret <= -pt_return
                hit_stop = high_ret >= sl_return

            if hit_profit and hit_stop:
                outcome = "both_hit"
                label = 0
                event_end_idx = future_idx
                break
            if hit_profit:
                outcome = "profit"
                label = 1
                event_end_idx = future_idx
                break
            if hit_stop:
                outcome = "stop"
                label = 0
                event_end_idx = future_idx
                break

        if event_end_idx is None and outcome != "unavailable" and vertical_end_idx < len(rows):
            event_end_idx = vertical_end_idx
            raw_expiry_close = rows[event_end_idx].get("close")
            expiry_close = _finite_float(raw_expiry_close)
            if expiry_close is None:
                outcome = "unavailable"
            else:
                expiry_return = side * ((expiry_close / entry_price) - 1)
                label = int(expiry_return > 0)
                outcome = "time_profit" if label == 1 else "time_loss"

        meta_labels.append(label)
        outcomes.append(outcome)
        upper_returns.append(pt_return if outcome != "unavailable" else None)
        lower_returns.append(sl_return if outcome != "unavailable" else None)
        barrier_starts.append(row_idx)
        # Pending rows retain their expected interval end for audit purposes,
        # but have no maturity date and are excluded from training/evaluation.
        barrier_ends.append(event_end_idx if event_end_idx is not None else vertical_end_idx)
        barrier_end_dates.append(rows[event_end_idx]["date"] if event_end_idx is not None and outcome != "unavailable" else None)
        barrier_available_dates.append(rows[event_end_idx]["date"] if event_end_idx is not None and label is not None else None)

    return labeled.with_columns(
        pl.Series("meta_label", meta_labels),
        pl.Series("barrier_outcome", outcomes),
        pl.Series("upper_barrier_return", upper_returns),
        pl.Series("lower_barrier_return", lower_returns),
        pl.lit(vertical_barrier_days).alias("vertical_barrier_days"),
        pl.Series("barrier_start_idx", barrier_starts),
        pl.Series("barrier_end_idx", barrier_ends),
        pl.Series("barrier_end_date", barrier_end_dates).cast(pl.Date, strict=False),
        pl.Series("barrier_available_date", barrier_available_dates).cast(pl.Date, strict=False),
    )
