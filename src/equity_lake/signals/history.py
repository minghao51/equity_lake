"""Signal history storage with Delta Lake (ACID merge)."""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from equity_lake.core.paths import DATA_DIR, SIGNALS_DIR
from equity_lake.signals.models import Signal
from equity_lake.storage.delta import merge_delta, migrate_parquet_to_delta, read_delta

logger = structlog.get_logger(__name__)


def _ensure_delta_table() -> None:
    """One-time migration of legacy Hive-partitioned Parquet to Delta (idempotent)."""
    if SIGNALS_DIR.exists() and not (SIGNALS_DIR / "_delta_log").exists():
        logger.info("signals_migrating_legacy_parquet_to_delta", path=str(SIGNALS_DIR))
        migrate_parquet_to_delta("signals", lake_dir=DATA_DIR)


def save_signals(signals: list[Signal], target_date: date) -> None:
    """Upsert signals into the Delta-backed signal history, keyed by (ticker, date, signal_type)."""
    if not signals:
        return

    _ensure_delta_table()

    records = [
        {
            "ticker": signal.ticker,
            "date": signal.date,
            "signal_type": signal.signal_type,
            "action": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            **signal.metadata,
        }
        for signal in signals
    ]

    frame = pl.DataFrame(records)
    merge_delta(
        frame,
        market="signals",
        key_columns=["ticker", "date", "signal_type"],
        lake_dir=DATA_DIR,
    )


def load_signals(target_date: date) -> list[Signal]:
    """Load signals for a target date from the Delta-backed signal history."""
    _ensure_delta_table()
    if not (SIGNALS_DIR / "_delta_log").exists():
        return []

    frame = read_delta("signals", lake_dir=DATA_DIR).filter(pl.col("date") == target_date)
    signals: list[Signal] = []
    base_cols = {"ticker", "date", "signal_type", "action", "confidence", "reasoning"}

    for row in frame.iter_rows(named=True):
        metadata = {key: value for key, value in row.items() if key not in base_cols and value is not None}
        signals.append(
            Signal(
                ticker=row["ticker"],
                date=row["date"],
                signal_type=row["signal_type"],
                action=row["action"],
                confidence=row["confidence"],
                reasoning=row["reasoning"],
                metadata=metadata,
            )
        )

    return signals
