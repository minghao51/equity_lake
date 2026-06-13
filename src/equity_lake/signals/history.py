"""Signal history storage with Parquet."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from equity_lake.signals.models import Signal

SIGNALS_DIR = Path("data/signals")


def save_signals_to_parquet(signals: list[Signal], target_date: date) -> None:
    """Save signals to partitioned Parquet storage."""
    if not signals:
        return

    partition_dir = SIGNALS_DIR / f"date={target_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for signal in signals:
        records.append(
            {
                "ticker": signal.ticker,
                "date": signal.date,
                "signal_type": signal.signal_type,
                "action": signal.action,
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                **signal.metadata,
            }
        )

    pl.DataFrame(records).write_parquet(partition_dir / "signals.parquet")


def load_signals_from_parquet(target_date: date) -> list[Signal]:
    """Load signals from Parquet storage."""
    partition_path = SIGNALS_DIR / f"date={target_date.isoformat()}" / "signals.parquet"
    if not partition_path.exists():
        return []

    frame = pl.read_parquet(partition_path)
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
