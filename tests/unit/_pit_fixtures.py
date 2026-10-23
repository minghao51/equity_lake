"""Synthetic point-in-time proof fixtures for focused ML and backtest tests.

These helpers issue explicitly synthetic source proofs with venue-session
publication/expiry clocks, then route them through the production validators
and the real backtest loader preparation path. They are test-only utilities.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, cast

import exchange_calendars as xc
import polars as pl

from equity_lake.backtesting.data_loader import BacktestDataLoader  # type: ignore[import-untyped]
from equity_lake.core.availability import (  # type: ignore[import-untyped]
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    ActionCoverageProof,
    action_coverage_feature_fields,
    decision_session_close,
    source_observable_session,
    validate_feature_action_coverage,
)
from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey  # type: ignore[import-untyped]

_SYNTHETIC_ACTION_SOURCE = "synthetic-test-source-issued-action-coverage"


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected a date-like fixture value, got {type(value).__name__}")


def synthetic_action_coverage_proof(instrument: InstrumentKey, start_date: date, end_date: date) -> ActionCoverageProof:
    """Issue a synthetic complete-window proof using exact venue-session clocks.

    The source publication is the prior venue close, and expiry is the close
    of the last covered decision date. Neither clock is inferred from a TTL.
    """
    if end_date < start_date:
        raise ValueError("Synthetic action-coverage end date precedes its start date")
    return ActionCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source=_SYNTHETIC_ACTION_SOURCE,
        coverage_start=start_date,
        coverage_end=end_date,
        source_available_at=decision_session_close(instrument, start_date - timedelta(days=1)),
        source_valid_until=decision_session_close(instrument, end_date),
    )


def synthetic_feature_proof_columns(
    instrument: InstrumentKey,
    decision_dates: Sequence[object],
    *,
    proof: ActionCoverageProof | None = None,
) -> dict[str, list[object]]:
    """Build strict feature metadata from one identity-bound source proof."""
    dates = [_as_date(value) for value in decision_dates]
    if not dates:
        raise ValueError("Cannot issue action-coverage metadata for an empty fixture")
    issued = proof or synthetic_action_coverage_proof(instrument, min(dates), max(dates))
    if issued.identity != instrument.tuple:
        raise ValueError("Synthetic action-coverage proof identity does not match its feature instrument")
    if issued.coverage_start > min(dates) or issued.coverage_end < max(dates):
        raise ValueError("Synthetic action-coverage proof does not cover all feature decisions")

    source_session = source_observable_session(instrument, issued.source_available_at)
    proof_fields = action_coverage_feature_fields(issued)
    columns: dict[str, list[object]] = {
        "market": [instrument.market] * len(dates),
        "mic": [instrument.mic] * len(dates),
        "symbol": [instrument.symbol] * len(dates),
        "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * len(dates),
        "ticker": [instrument.symbol] * len(dates),
        "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * len(dates),
        "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * len(dates),
        "action_coverage": [1] * len(dates),
        "action_source_age_days": [(decision_date - source_session).days for decision_date in dates],
        **{field: [value] * len(dates) for field, value in proof_fields.items()},
    }
    for index, decision_date in enumerate(dates):
        row = {field: values[index] for field, values in columns.items()}
        row["date"] = decision_date
        validate_feature_action_coverage(row)
    return columns


def with_synthetic_feature_proof(
    frame: pl.DataFrame,
    instrument: InstrumentKey,
    *,
    proof: ActionCoverageProof | None = None,
) -> pl.DataFrame:
    """Attach and validate a canonical feature proof without masking mismatches."""
    if "date" not in frame.columns:
        raise ValueError("Synthetic feature frame has no date column")
    for column, expected in {"market": instrument.market, "mic": instrument.mic, "symbol": instrument.symbol, "ticker": instrument.symbol}.items():
        if column in frame.columns:
            values = frame[column].drop_nulls().cast(pl.String).unique().to_list()
            if values != [expected] or frame[column].null_count():
                raise ValueError(f"Synthetic feature frame {column!r} does not match {instrument.display_key}")
    columns = synthetic_feature_proof_columns(instrument, frame["date"].to_list(), proof=proof)
    dtypes: dict[str, Any] = {
        "market": pl.String,
        "mic": pl.String,
        "symbol": pl.String,
        "identity_schema_version": pl.Int8,
        "ticker": pl.String,
        "price_adjustment_version": pl.String,
        "availability_schema_version": pl.Int8,
        "action_coverage": pl.Int8,
        "action_source_age_days": pl.Int32,
        "action_coverage_market": pl.String,
        "action_coverage_mic": pl.String,
        "action_coverage_symbol": pl.String,
        "action_coverage_source": pl.String,
        "action_coverage_start": pl.Date,
        "action_coverage_end": pl.Date,
        "action_source_available_at": pl.Datetime("us", "UTC"),
        "action_source_valid_until": pl.Datetime("us", "UTC"),
        "action_coverage_status": pl.String,
        "action_coverage_schema_version": pl.Int8,
    }
    return frame.with_columns([pl.Series(name, values, dtype=dtypes[name]) for name, values in columns.items()])


def us_equity_session_dates(start_date: date, count: int) -> list[date]:
    """Return deterministic XNAS sessions for synthetic raw-price bars."""
    if count < 0:
        raise ValueError("Session count cannot be negative")
    if count == 0:
        return []
    calendar = xc.get_calendar("XNAS")
    end_date = start_date + timedelta(days=count * 2 + 30)
    sessions = calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())
    dates = [date.fromisoformat(str(session)[:10]) for session in sessions[:count]]
    if len(dates) != count:
        raise ValueError(f"Could not find {count} XNAS sessions on or after {start_date}")
    return dates


def prepare_preloaded_backtest_data(raw_prices: pl.DataFrame, instrument: InstrumentKey) -> pl.DataFrame:
    """Prepare canonical raw bars through real loader/PIT validation for preloading."""
    required_prices = {"ticker", "date", "open", "high", "low", "close", "volume"}
    missing = required_prices - set(raw_prices.columns)
    if missing:
        raise ValueError(f"Synthetic backtest bars are missing {sorted(missing)}")
    tickers = raw_prices["ticker"].drop_nulls().cast(pl.String).unique().to_list()
    if tickers != [instrument.symbol] or raw_prices["ticker"].null_count():
        raise ValueError(f"Synthetic backtest bars do not match {instrument.display_key}")
    prices = raw_prices.with_columns(
        pl.col("date").cast(pl.Date),
        pl.lit(instrument.market).alias("market"),
        pl.lit(instrument.mic).alias("mic"),
        pl.lit(instrument.symbol).alias("symbol"),
        pl.lit(IDENTITY_SCHEMA_VERSION, dtype=pl.Int8).alias("identity_schema_version"),
    )
    dates = prices["date"].to_list()
    if not dates:
        raise ValueError("Synthetic backtest bars are empty")
    proof = synthetic_action_coverage_proof(instrument, min(dates), max(dates))
    actions = pl.DataFrame(
        schema={
            "market": pl.String,
            "mic": pl.String,
            "symbol": pl.String,
            "ticker": pl.String,
            "ex_date": pl.Date,
            "action": pl.String,
            "value": pl.Float64,
            "source_available_at": pl.Datetime("us", "UTC"),
        }
    )
    coverage = pl.DataFrame([proof.model_dump()])
    loader = BacktestDataLoader(action_inputs={instrument.tuple: (actions, coverage)})
    with_context = loader._attach_point_in_time_context(prices, [instrument.market])
    return cast(pl.DataFrame, loader._point_in_time_marks(with_context, [instrument.market], "splits"))


__all__ = [
    "prepare_preloaded_backtest_data",
    "synthetic_action_coverage_proof",
    "synthetic_feature_proof_columns",
    "us_equity_session_dates",
    "with_synthetic_feature_proof",
]
