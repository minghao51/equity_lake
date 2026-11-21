"""Synthetic weak-basis (``vendor_events_no_clock``) Gold fixtures for ML tests.

Test-only utilities. These build Gold feature rows stamped under the ADR-0032 weak
basis — vendor-event completeness only, null clocks and null source age — then write
them through the real Delta merge seam so a reader test exercises the production
read path, not a lookalike. The strict counterpart reuses the existing synthetic
point-in-time fixtures, so one table can legitimately carry both bases and a reader's
mixed-basis refusal can be proved.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from runpy import run_path
from typing import Any

import numpy as np
import polars as pl

from equity_lake.core.availability import (  # type: ignore[import-untyped]
    ACTION_COVERAGE_SCHEMA_VERSION,
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    VendorEventCoverageProof,
    action_coverage_feature_fields,
)
from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey  # type: ignore[import-untyped]
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.storage.delta import merge_delta  # type: ignore[import-untyped]

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
with_synthetic_feature_proof = _pit_fixtures["with_synthetic_feature_proof"]
us_equity_session_dates = _pit_fixtures["us_equity_session_dates"]

WEAK_BASIS = "vendor_events_no_clock"
_KEYS = ["market", "mic", "symbol", "date"]
#: One coherent read window used by every R02-focused fixture.
R02_FIRST_SESSION = date(2024, 1, 2)
R02_LAST_SESSION = date(2024, 9, 30)
R02_PATH_SESSIONS = 190
#: The minimal technical feature set a synthetic weak row carries; the R02 feature
#: list is a superset, so a synthetic run only needs the columns it will read.
FIXTURE_FEATURES = ("roc_20", "roc_5", "rsi_14", "macd", "return_1d", "volatility_20")


def weak_feature_proof_columns(
    instrument: InstrumentKey,
    dates: Sequence[date],
    *,
    coverage_start: date = R02_FIRST_SESSION,
    coverage_end: date = R02_LAST_SESSION,
) -> dict[str, list[object]]:
    """Persisted proof fields for rows under the weak vendor-event basis only."""
    if not dates:
        raise ValueError("a weak feature fixture needs at least one decision date")
    if min(dates) < coverage_start or max(dates) > coverage_end:
        raise ValueError("the weak coverage window must reach every fixture decision date")
    proof = VendorEventCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source="synthetic-vendor-event-list",
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    proof_fields = action_coverage_feature_fields(proof)
    return {
        "market": [instrument.market] * len(dates),
        "mic": [instrument.mic] * len(dates),
        "symbol": [instrument.symbol] * len(dates),
        "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * len(dates),
        "ticker": [instrument.symbol] * len(dates),
        "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * len(dates),
        "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * len(dates),
        "action_coverage": [1] * len(dates),
        "action_source_age_days": [None] * len(dates),
        **{field: [value] * len(dates) for field, value in proof_fields.items()},
    }


def weak_gold_frame(
    instrument: InstrumentKey,
    dates: Sequence[date],
    *,
    seed: int = 7,
    features: Sequence[str] = FIXTURE_FEATURES,
) -> pl.DataFrame:
    """A deterministic weak-basis Gold feature frame for one instrument."""
    ordered = list(dates)
    generator = np.random.default_rng(seed + sum(ord(character) for character in instrument.symbol))
    rows = len(ordered)
    closes = 100.0 * np.cumprod(1.0 + generator.normal(0.0, 0.01, size=rows))
    columns: dict[str, Any] = {
        "date": pl.Series(ordered, dtype=pl.Date),
        "open": pl.Series(closes * 0.999, dtype=pl.Float64),
        "high": pl.Series(closes * 1.004, dtype=pl.Float64),
        "low": pl.Series(closes * 0.996, dtype=pl.Float64),
        "close": pl.Series(closes, dtype=pl.Float64),
        "volume": pl.Series(generator.integers(1_000_000, 5_000_000, size=rows).astype(np.float64), dtype=pl.Float64),
        "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * rows, dtype=pl.Int8),
    }
    for column in features:
        columns[column] = pl.Series(generator.normal(0.0, 1.0, size=rows), dtype=pl.Float64)
    proof = weak_feature_proof_columns(instrument, ordered)
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
        "action_coverage_status": pl.String,
        "action_coverage_schema_version": pl.Int8,
        "action_coverage_basis": pl.String,
        # A weak basis carries no source clock: null by design (ADR-0032 decision 5).
        "action_source_available_at": pl.Datetime("us", "UTC"),
        "action_source_valid_until": pl.Datetime("us", "UTC"),
    }
    columns.update({name: pl.Series(name, values, dtype=dtypes[name]) for name, values in proof.items()})
    if int(columns["action_coverage_schema_version"][0]) != ACTION_COVERAGE_SCHEMA_VERSION:
        raise ValueError("the weak fixture proof schema version drifted from the canonical constant")
    return pl.DataFrame(columns)


def write_gold_lake(root: Path, frames: Sequence[pl.DataFrame]) -> Path:
    """Merge feature frames into ``<root>/03_gold/features`` through the real seam."""
    for frame in frames:
        merge_delta(frame, "03_gold/features", key_columns=_KEYS, partition_by=["date"], lake_dir=root)
    return root / "03_gold" / "features"


__all__ = [
    "FIXTURE_FEATURES",
    "R02_FIRST_SESSION",
    "R02_LAST_SESSION",
    "R02_PATH_SESSIONS",
    "WEAK_BASIS",
    "us_equity_session_dates",
    "weak_feature_proof_columns",
    "weak_gold_frame",
    "with_synthetic_feature_proof",
    "write_gold_lake",
]
