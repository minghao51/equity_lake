"""Test signal history storage (Delta-backed ACID merge)."""

import shutil
import tempfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from equity_lake.signals.history import (
    load_signals,
    save_signals,
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
    save_signals([_make_signal()], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].action == "BUY"
    assert loaded[0].metadata["strategy"] == "momentum"


def test_merge_dedups_on_key(temp_signals_dir):
    """Re-saving the same (ticker, date, signal_type) upserts instead of duplicating."""
    test_date = date(2024, 12, 1)
    save_signals([_make_signal(confidence=75.0)], test_date)
    # Re-save with an updated confidence — should update, not append.
    save_signals([_make_signal(confidence=90.0)], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].confidence == 90.0


def test_distinct_keys_coexist(temp_signals_dir):
    """Different signal_type for the same ticker/date are kept as separate rows."""
    test_date = date(2024, 12, 1)
    save_signals([_make_signal("AAPL", "backtest"), _make_signal("AAPL", "ml")], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 2
    assert {s.signal_type for s in loaded} == {"backtest", "ml"}


def test_load_empty_history(temp_signals_dir):
    """Loading when no history exists returns an empty list."""
    loaded = load_signals(date(2024, 12, 1))
    assert len(loaded) == 0


# ---------------------------------------------------------------------------
# Closed write boundary (handoff 08, B4)
# ---------------------------------------------------------------------------


def test_save_ml_metadata_through_signal_record(temp_signals_dir):
    """ML metadata round-trips through the closed model; no 'confidence' collision."""
    from equity_lake.storage.delta import read_delta

    test_date = date(2024, 12, 1)
    save_signals(
        [
            Signal(
                ticker="AAPL",
                date=test_date,
                signal_type="ml",
                action="BUY",
                confidence=75.0,
                reasoning="ML predicts upside",
                metadata={"prediction": 1, "probability": 0.75, "horizon_days": 5, "model_mode": "v1_direction"},
            )
        ],
        test_date,
    )

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].metadata["prediction"] == 1
    assert loaded[0].confidence == 75.0

    frame = read_delta("signals", lake_dir=temp_signals_dir.parent)
    # whitelist keeps the Delta schema scalar-only: no dict->struct columns
    assert all(dtype not in (pl.Struct,) for dtype in frame.dtypes)


def test_save_meta_label_barrier_settings_stored_as_json_not_struct(temp_signals_dir):
    """barrier_settings dict must not become a Delta struct column (schema drift)."""
    from equity_lake.storage.delta import read_delta

    test_date = date(2024, 12, 1)
    barrier = {"vertical_barrier_days": 5, "pt_mult": 1.5, "sl_mult": 1.0}
    save_signals(
        [
            Signal(
                ticker="AAPL",
                date=test_date,
                signal_type="ml",
                action="BUY",
                confidence=80.0,
                reasoning="meta-label accepted",
                metadata={"candidate_action": "BUY", "barrier_settings": barrier},
            )
        ],
        test_date,
    )

    loaded = load_signals(test_date)
    assert loaded[0].metadata["barrier_settings"] == barrier

    frame = read_delta("signals", lake_dir=temp_signals_dir.parent)
    assert "barrier_settings_json" in frame.columns
    assert not any(isinstance(dtype, pl.Struct) for dtype in frame.dtypes)


def test_save_rejects_metadata_confidence_collision(temp_signals_dir):
    """A metadata key colliding with the base column must fail loudly, not drift."""
    import pytest as _pytest

    from equity_lake.signals.models import SignalRecord

    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="ml",
        action="BUY",
        confidence=75.0,
        reasoning="r",
        metadata={"confidence": 75.0},
    )
    with _pytest.raises(ValueError, match="collides"):
        SignalRecord.from_signal(signal)
