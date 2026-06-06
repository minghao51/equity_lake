"""Signal history storage with Parquet."""

from datetime import date
from pathlib import Path

import pandas as pd

from equity_lake.domain.signals.models import Signal

# History storage directory
SIGNALS_DIR = Path("data/signals")


def save_signals_to_parquet(signals: list[Signal], target_date: date) -> None:
    """Save signals to partitioned Parquet storage.

    Args:
        signals: List of Signal objects
        target_date: Date for partitioning
    """
    if not signals:
        return

    # Create partition directory
    partition_dir = SIGNALS_DIR / f"date={target_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    # Convert to DataFrame
    records = []
    for signal in signals:
        record = {
            "ticker": signal.ticker,
            "date": signal.date,
            "signal_type": signal.signal_type,
            "action": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            **signal.metadata,  # Flatten metadata into columns
        }
        records.append(record)

    df = pd.DataFrame(records)

    # Write to Parquet
    output_path = partition_dir / "signals.parquet"
    df.to_parquet(output_path, index=False)


def load_signals_from_parquet(target_date: date) -> list[Signal]:
    """Load signals from Parquet storage.

    Args:
        target_date: Date to load signals for

    Returns:
        List of Signal objects
    """
    partition_path = SIGNALS_DIR / f"date={target_date.isoformat()}" / "signals.parquet"

    if not partition_path.exists():
        return []

    df = pd.read_parquet(partition_path)

    # Convert DataFrame to Signal objects
    signals = []
    for _, row in df.iterrows():
        # Extract metadata columns
        base_cols = {
            "ticker",
            "date",
            "signal_type",
            "action",
            "confidence",
            "reasoning",
        }
        metadata = {k: v for k, v in row.items() if k not in base_cols and pd.notna(v)}

        signal = Signal(
            ticker=row["ticker"],
            date=row["date"],
            signal_type=row["signal_type"],
            action=row["action"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            metadata=metadata,
        )
        signals.append(signal)

    return signals
