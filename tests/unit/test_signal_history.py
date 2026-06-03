"""Test signal history storage."""

import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest

from equity_lake.signals.history import (
    load_signals_from_parquet,
    save_signals_to_parquet,
)
from equity_lake.signals.models import Signal


@pytest.fixture
def temp_signals_dir():
    """Create temporary signals directory."""
    temp_dir = Path(tempfile.mkdtemp())

    # Mock SIGNALS_DIR
    import equity_lake.signals.history as history_module

    original_path = history_module.SIGNALS_DIR
    history_module.SIGNALS_DIR = temp_dir

    yield temp_dir

    # Cleanup
    history_module.SIGNALS_DIR = original_path
    shutil.rmtree(temp_dir)


def test_save_and_load_signals(temp_signals_dir):
    """Test saving and loading signals."""
    test_date = date(2024, 12, 1)
    signals = [
        Signal(
            ticker="AAPL",
            date=test_date,
            signal_type="backtest",
            action="BUY",
            confidence=75.0,
            reasoning="Test signal",
            metadata={"strategy": "momentum"},
        )
    ]

    # Save
    save_signals_to_parquet(signals, test_date)

    # Verify file exists
    partition_dir = temp_signals_dir / f"date={test_date.isoformat()}"
    assert partition_dir.exists()
    assert (partition_dir / "signals.parquet").exists()

    # Load
    loaded = load_signals_from_parquet(test_date)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].action == "BUY"
    assert loaded[0].metadata["strategy"] == "momentum"


def test_load_empty_history(temp_signals_dir):
    """Test loading when no history exists."""
    loaded = load_signals_from_parquet(date(2024, 12, 1))
    assert len(loaded) == 0
