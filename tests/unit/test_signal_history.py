"""Test signal history storage (Delta-backed ACID merge)."""

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
    """Redirect the signal-history Delta table into an isolated temp directory."""
    temp_dir = Path(tempfile.mkdtemp())

    import equity_lake.signals.history as history_module

    original_data_dir = history_module.DATA_DIR
    original_signals_dir = history_module.SIGNALS_DIR
    history_module.DATA_DIR = temp_dir
    history_module.SIGNALS_DIR = temp_dir / "signals"

    yield history_module.SIGNALS_DIR

    history_module.DATA_DIR = original_data_dir
    history_module.SIGNALS_DIR = original_signals_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def _make_signal(ticker: str = "AAPL", signal_type: str = "backtest", confidence: float = 75.0) -> Signal:
    return Signal(
        ticker=ticker,
        date=date(2024, 12, 1),
        signal_type=signal_type,
        action="BUY",
        confidence=confidence,
        reasoning="Test signal",
        metadata={"strategy": "momentum"},
    )


def test_save_and_load_signals(temp_signals_dir):
    """Saved signals round-trip through the Delta-backed history."""
    test_date = date(2024, 12, 1)
    save_signals_to_parquet([_make_signal()], test_date)

    loaded = load_signals_from_parquet(test_date)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].action == "BUY"
    assert loaded[0].metadata["strategy"] == "momentum"


def test_merge_dedups_on_key(temp_signals_dir):
    """Re-saving the same (ticker, date, signal_type) upserts instead of duplicating."""
    test_date = date(2024, 12, 1)
    save_signals_to_parquet([_make_signal(confidence=75.0)], test_date)
    # Re-save with an updated confidence — should update, not append.
    save_signals_to_parquet([_make_signal(confidence=90.0)], test_date)

    loaded = load_signals_from_parquet(test_date)
    assert len(loaded) == 1
    assert loaded[0].confidence == 90.0


def test_distinct_keys_coexist(temp_signals_dir):
    """Different signal_type for the same ticker/date are kept as separate rows."""
    test_date = date(2024, 12, 1)
    save_signals_to_parquet([_make_signal("AAPL", "backtest"), _make_signal("AAPL", "ml")], test_date)

    loaded = load_signals_from_parquet(test_date)
    assert len(loaded) == 2
    assert {s.signal_type for s in loaded} == {"backtest", "ml"}


def test_load_empty_history(temp_signals_dir):
    """Loading when no history exists returns an empty list."""
    loaded = load_signals_from_parquet(date(2024, 12, 1))
    assert len(loaded) == 0
