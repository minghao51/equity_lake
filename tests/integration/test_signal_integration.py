"""Integration tests for signal scanning against isolated synthetic data."""

from collections.abc import Generator
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path

import polars as pl
import pytest

import equity_lake.signals.generators.backtest as backtest_module  # type: ignore[import-untyped]
import equity_lake.signals.history as history_module  # type: ignore[import-untyped]
from equity_lake.core.availability import ActionCoverageProof  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.models import SignalConfig, Watchlist  # type: ignore[import-untyped]
from equity_lake.signals.scanner import SignalScanner  # type: ignore[import-untyped]

TARGET_DATE = date(2024, 1, 5)


@pytest.fixture
def synthetic_scanner(monkeypatch: pytest.MonkeyPatch, temp_partitioned_parquet: Path, tmp_path: Path) -> Generator[SignalScanner, None, None]:
    """Use isolated canonical Parquet prices and a source-issued empty-action proof."""
    lake_dir = tmp_path / "medallion"
    us_dir = lake_dir / "01_bronze" / "market_data" / "us_equity"
    us_dir.mkdir(parents=True)
    for partition in sorted(temp_partitioned_parquet.glob("date=*")):
        data_file = next(partition.glob("*.parquet"))
        frame = pl.read_parquet(data_file).with_columns(
            pl.lit("us_equity").alias("market"),
            pl.lit("XNAS").alias("mic"),
            pl.col("ticker").alias("symbol"),
        )
        target = us_dir / partition.name
        target.mkdir(parents=True)
        frame.write_parquet(target / data_file.name)
    monkeypatch.setattr(backtest_module, "LAKE_DIR", lake_dir)
    corporate_actions = import_module("equity_lake.ingestion.corporate_actions")

    def coverage(_market: str, *, lake_dir: Path | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
        del lake_dir
        proof = ActionCoverageProof(
            market="us_equity",
            mic="XNAS",
            symbol="AAPL",
            source="synthetic-fixture",
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 1, 5),
            source_available_at=datetime(2024, 1, 1, tzinfo=UTC),
            source_valid_until=datetime(2024, 1, 10, tzinfo=UTC),
        )
        actions = pl.DataFrame(schema={"ticker": pl.String, "ex_date": pl.Date, "action": pl.String, "value": pl.Float64})
        return actions, pl.DataFrame([proof.as_row()])

    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", coverage)
    config = SignalConfig(
        backtest={
            "enabled": True,
            "strategies": [
                {
                    "name": "sma_deviation_3d",
                    "lookback_days": 3,
                    "buy_threshold": 0.01,
                    "sell_threshold": -0.01,
                }
            ],
        },
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Synthetic", tickers=["AAPL"])
    scanner = SignalScanner(
        config,
        watchlist,
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
    )
    generator = scanner.generators[0]
    assert isinstance(generator, BacktestSignalGenerator)
    yield scanner


def test_full_scan_workflow(synthetic_scanner: SignalScanner) -> None:
    """The synthetic scan deterministically emits and formats a BUY signal."""
    signals = synthetic_scanner.scan(TARGET_DATE)

    assert len(signals) == 1
    assert signals[0].ticker == "AAPL"
    assert signals[0].date == TARGET_DATE
    assert signals[0].action == "BUY"

    json_output = synthetic_scanner.format_signals(signals, "json")
    md_output = synthetic_scanner.format_signals(signals, "md")
    table_output = synthetic_scanner.format_signals(signals, "table")

    assert "AAPL" in json_output and "BUY" in json_output
    assert "# Signal Report" in md_output
    assert "SIGNAL REPORT" in table_output


def test_signal_history_roundtrip(synthetic_scanner: SignalScanner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Signal history writes and reads only inside this test's temporary data root."""
    data_dir = tmp_path / "data"
    signals_dir = data_dir / "signals"
    monkeypatch.setattr(history_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_module, "SIGNALS_DIR", signals_dir)

    signals = synthetic_scanner.scan(TARGET_DATE)
    assert len(signals) == 1
    assert signals[0].action == "BUY"

    synthetic_scanner.save_history(signals)
    loaded = history_module.load_signals(TARGET_DATE)

    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].date == TARGET_DATE
    assert loaded[0].action == "BUY"
    assert loaded[0].market == "us_equity" and loaded[0].mic == "XNAS"
