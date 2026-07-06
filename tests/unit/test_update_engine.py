"""Tests for the smart update engine.

Includes a characterization integration test that exercises the live data-flow
(``update()`` → fetcher/loader → writer → history). It mocks only the external
boundary (``yfinance.download``) so it remains valid after the planned loaders/
consolidation onto the sources/ fetcher seam.
"""

from datetime import date
from unittest.mock import patch

import pandas as pd

from equity_lake.updates.engine import UpdateEngine, UpdateStrategy
from equity_lake.updates.history import UpdateHistory


def test_smart_strategy_defaults_to_recent_window_when_no_data(tmp_path) -> None:
    engine = UpdateEngine(history=UpdateHistory(path=tmp_path / "history"))
    engine.get_last_date = lambda source, symbol: None  # type: ignore[method-assign]
    start_date, end_date = engine._determine_date_range(
        "us_equity",
        "AAPL",
        UpdateStrategy.SMART,
    )

    assert end_date >= start_date
    assert (end_date - start_date).days >= 7


def test_unsupported_update_source_returns_failure(tmp_path) -> None:
    engine = UpdateEngine(history=UpdateHistory(path=tmp_path / "history"))

    result = engine.update("unknown_market")

    assert result.success is False
    assert result.errors


def test_cn_updates_reject_explicit_symbols(tmp_path) -> None:
    engine = UpdateEngine(history=UpdateHistory(path=tmp_path / "history"))
    engine._run_fetcher_updates = lambda *args, **kwargs: 0  # type: ignore[method-assign]

    result = engine.update("cn_ashare", symbols=["000001"])

    assert result.success is False
    assert "Explicit symbols" in result.errors[0]


def _mock_yf_download_two_days() -> pd.DataFrame:
    """A single-ticker, two-day OHLCV frame resembling a yfinance response."""
    idx = pd.date_range("2024-12-01", periods=2, freq="D", name="Date")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 100.0],
            "Close": [102.0, 103.0],
            "Adj Close": [102.0, 103.0],
            "Volume": [1_000_000, 1_100_000],
        },
        index=idx,
    )


def test_update_us_equity_range_fetch_writes_per_date_and_records_history(tmp_path) -> None:
    """Characterization: a range fetch issues one download, writes once per date, records history.

    Mocks ``yfinance.download`` at the canonical location so this test stays valid
    after the loader path is consolidated onto the sources/ fetcher seam.
    """
    history = UpdateHistory(path=tmp_path / "history")
    engine = UpdateEngine(history=history)

    write_calls: list[date] = []

    def _fake_write(frame, market, trading_date, **_kwargs):  # noqa: ANN001
        write_calls.append(trading_date)
        return True

    with (
        patch("yfinance.download", return_value=_mock_yf_download_two_days()) as mock_download,
        patch("equity_lake.updates.engine.write_to_partitioned_parquet", side_effect=_fake_write),
    ):
        result = engine.update("us_equity", symbols=["AAPL"], strategy=UpdateStrategy.FULL)

    assert result.success, f"expected success, got errors: {result.errors}"
    assert result.records_added == 2
    # Range semantics: exactly one download call covers the whole range.
    assert mock_download.call_count == 1
    # Writer invoked once per distinct trading date in the fetched frame.
    assert len(write_calls) == 2
    # History recorded the symbol.
    assert history.get_last_update("us_equity", "AAPL") is not None
