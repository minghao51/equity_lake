"""Test signal history storage (Delta-backed ACID merge)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Literal

import polars as pl
import pytest

from equity_lake.signals.history import load_signals, save_signals  # type: ignore[import-untyped]
from equity_lake.signals.models import Signal  # type: ignore[import-untyped]


@pytest.fixture
def temp_signals_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect signal-history writes into a pytest-managed temporary directory."""
    history_module = import_module("equity_lake.signals.history")
    monkeypatch.setattr(history_module, "DATA_DIR", tmp_path)
    target = tmp_path / "signals"
    monkeypatch.setattr(history_module, "SIGNALS_DIR", target)
    yield target


def _make_signal(
    ticker: str = "AAPL",
    signal_type: Literal["backtest", "sentiment", "ml"] = "backtest",
    confidence: float = 75.0,
) -> Signal:
    return Signal(
        ticker=ticker,
        date=date(2024, 12, 1),
        signal_type=signal_type,
        action="BUY",
        confidence=confidence,
        reasoning="Test signal",
        metadata={"strategy": "momentum"},
        market="us_equity",
        mic="XNAS",
    )


def test_save_and_load_signals(temp_signals_dir: Path) -> None:
    """Saved signals round-trip through the Delta-backed history."""
    test_date = date(2024, 12, 1)
    save_signals([_make_signal()], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].action == "BUY"
    assert loaded[0].metadata["strategy"] == "momentum"


def test_merge_dedups_on_key(temp_signals_dir: Path) -> None:
    """Re-saving the same canonical signal key upserts instead of duplicating."""
    test_date = date(2024, 12, 1)
    save_signals([_make_signal(confidence=75.0)], test_date)
    save_signals([_make_signal(confidence=90.0)], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].confidence == 90.0


def test_distinct_keys_coexist(temp_signals_dir: Path) -> None:
    """Different signal types for one ticker/date remain separate rows."""
    test_date = date(2024, 12, 1)
    save_signals([_make_signal("AAPL", "backtest"), _make_signal("AAPL", "ml")], test_date)

    loaded = load_signals(test_date)
    assert len(loaded) == 2
    assert {signal.signal_type for signal in loaded} == {"backtest", "ml"}


def test_same_symbol_and_generator_on_different_venues_remain_distinct(temp_signals_dir: Path) -> None:
    """Canonical `(market, MIC, symbol)` keys cannot collapse equal ticker text."""
    test_date = date(2024, 12, 1)
    save_signals(
        [
            Signal("SHARED", test_date, "backtest", "BUY", 75.0, "NASDAQ venue", {}, market="us_equity", mic="XNAS"),
            Signal("SHARED", test_date, "backtest", "SELL", 60.0, "NYSE venue", {}, market="us_equity", mic="XNYS"),
        ],
        test_date,
    )

    loaded = load_signals(test_date)
    assert len(loaded) == 2
    assert {signal.mic for signal in loaded} == {"XNAS", "XNYS"}


def test_load_empty_history(temp_signals_dir: Path) -> None:
    """Loading when no history exists returns an empty list."""
    assert load_signals(date(2024, 12, 1)) == []


def test_save_ml_metadata_through_signal_record(temp_signals_dir: Path) -> None:
    """ML metadata round-trips through the closed model; no confidence collision."""
    read_delta = import_module("equity_lake.storage.delta").read_delta
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
                market="us_equity",
                mic="XNAS",
            )
        ],
        test_date,
    )

    loaded = load_signals(test_date)
    assert len(loaded) == 1
    assert loaded[0].metadata["prediction"] == 1
    assert loaded[0].confidence == 75.0
    frame = read_delta("signals", lake_dir=temp_signals_dir.parent)
    assert all(dtype not in (pl.Struct,) for dtype in frame.dtypes)


def test_save_meta_label_barrier_settings_stored_as_json_not_struct(temp_signals_dir: Path) -> None:
    """barrier_settings must not become a Delta struct column."""
    read_delta = import_module("equity_lake.storage.delta").read_delta
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
                market="us_equity",
                mic="XNAS",
            )
        ],
        test_date,
    )

    loaded = load_signals(test_date)
    assert loaded[0].metadata["barrier_settings"] == barrier
    frame = read_delta("signals", lake_dir=temp_signals_dir.parent)
    assert "barrier_settings_json" in frame.columns
    assert not any(isinstance(dtype, pl.Struct) for dtype in frame.dtypes)


def test_save_rejects_metadata_confidence_collision(temp_signals_dir: Path) -> None:
    """Metadata colliding with the base column fails loudly instead of drifting."""
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
    with pytest.raises(ValueError, match="collides"):
        SignalRecord.from_signal(signal)
