"""Tests for the smart update engine."""

from equity_lake.updates.engine import UpdateEngine, UpdateStrategy
from equity_lake.updates.history import UpdateHistory


def test_smart_strategy_defaults_to_recent_window_when_no_data(tmp_path) -> None:
    engine = UpdateEngine(history=UpdateHistory(path=tmp_path / "history.parquet"))
    engine.get_last_date = lambda source, symbol: None  # type: ignore[method-assign]
    start_date, end_date = engine._determine_date_range(
        "us_equity",
        "AAPL",
        UpdateStrategy.SMART,
    )

    assert end_date >= start_date
    assert (end_date - start_date).days >= 7
