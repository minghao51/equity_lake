"""Offline contract tests for strict market-aware signal generation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

import equity_lake.ingestion.corporate_actions as corporate_actions  # type: ignore[import-untyped]
from equity_lake.core.availability import ActionCoverageProof  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.backtest import BacktestSignalGenerator  # type: ignore[import-untyped]

TARGET = date(2024, 1, 5)
INSTRUMENT = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")


def _price_lake(tmp_path: Path) -> tuple[Path, pl.DataFrame]:
    """Write a real isolated Hive-Parquet price table with explicit identity."""
    lake_dir = tmp_path / "lake"
    table_path = lake_dir / "01_bronze" / "market_data" / INSTRUMENT.market
    table_path.mkdir(parents=True)
    rows = pl.DataFrame(
        {
            "market": [INSTRUMENT.market] * 5,
            "mic": [INSTRUMENT.mic] * 5,
            "symbol": [INSTRUMENT.symbol] * 5,
            "ticker": [INSTRUMENT.symbol] * 5,
            "date": [date(2024, 1, day) for day in range(1, 6)],
            "open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0, 101.0, 104.0],
            "low": [99.0, 99.0, 99.0, 99.0, 102.0],
            "close": [100.0, 100.0, 100.0, 100.0, 103.0],
            "volume": [1000] * 5,
        }
    )
    rows.write_parquet(table_path / "prices.parquet")
    return lake_dir, rows


def _coverage(*_args: object, **_kwargs: object) -> tuple[pl.DataFrame, pl.DataFrame]:
    proof = ActionCoverageProof(
        market=INSTRUMENT.market,
        mic=INSTRUMENT.mic,
        symbol=INSTRUMENT.symbol,
        source="synthetic-fixture",
        coverage_start=date(2024, 1, 1),
        coverage_end=TARGET,
        source_available_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_valid_until=datetime(2024, 1, 10, tzinfo=UTC),
    )
    actions = pl.DataFrame(schema={"ticker": pl.String, "ex_date": pl.Date, "action": pl.String, "value": pl.Float64})
    return actions, pl.DataFrame([proof.as_row()])


def test_backtest_generator_reports_enabled_configuration(tmp_path: Path) -> None:
    lake_dir, _ = _price_lake(tmp_path)
    generator = BacktestSignalGenerator({"enabled": True, "lake_dir": lake_dir, "strategies": []})

    assert generator.is_enabled() is True
    assert generator.strategies == []


def test_backtest_generator_uses_real_parquet_and_split_adjustment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lake_dir, _ = _price_lake(tmp_path)
    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", _coverage)
    generator = BacktestSignalGenerator(
        {
            "enabled": True,
            "lake_dir": lake_dir,
            "strategies": [{"name": "synthetic", "lookback_days": 3, "buy_threshold": 0.01, "sell_threshold": -0.01}],
        }
    )

    outcome = generator.generate_outcome(INSTRUMENT, TARGET)

    assert outcome.status == "signal"
    assert outcome.signal is not None
    assert outcome.signal.action == "BUY"
    assert (outcome.signal.market, outcome.signal.mic) == ("us_equity", "XNAS")
    assert outcome.signal.metadata["price"] > outcome.signal.metadata["sma"]


def test_backtest_generator_fails_closed_without_action_coverage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lake_dir, _ = _price_lake(tmp_path)
    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda *_args, **_kwargs: _coverage()[0:1] + (pl.DataFrame(),))
    generator = BacktestSignalGenerator(
        {
            "enabled": True,
            "lake_dir": lake_dir,
            "strategies": [{"name": "synthetic", "lookback_days": 3, "buy_threshold": 0.01}],
        }
    )

    outcome = generator.generate_outcome(INSTRUMENT, TARGET)

    assert outcome.status == "unavailable"
    assert "strict adjusted price coverage unavailable" in (outcome.detail or "")


def test_backtest_generator_does_not_infer_identity_from_legacy_prices(tmp_path: Path) -> None:
    lake_dir, rows = _price_lake(tmp_path)
    table_path = lake_dir / "01_bronze" / "market_data" / INSTRUMENT.market
    rows.drop("market", "mic", "symbol").write_parquet(table_path / "legacy.parquet")
    generator = BacktestSignalGenerator(
        {
            "enabled": True,
            "lake_dir": lake_dir,
            "strategies": [{"name": "synthetic", "lookback_days": 3}],
        }
    )

    outcome = generator.generate_outcome(INSTRUMENT, TARGET)

    assert outcome.status == "unavailable"


def test_backtest_generator_missing_table_is_unavailable(tmp_path: Path) -> None:
    generator = BacktestSignalGenerator(
        {
            "enabled": True,
            "lake_dir": tmp_path / "absent",
            "strategies": [{"name": "synthetic", "lookback_days": 3}],
        }
    )

    outcome = generator.generate_outcome(INSTRUMENT, TARGET)

    assert outcome.status == "unavailable"
