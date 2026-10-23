"""Regression guards for signal generators' canonical lake-reader paths."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from equity_lake.core.availability import ActionCoverageProof  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.core.paths import US_NEWS_DIR  # type: ignore[import-untyped]
from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA  # type: ignore[import-untyped]
from equity_lake.storage import lake_reader  # type: ignore[import-untyped]
from equity_lake.storage.delta import write_delta  # type: ignore[import-untyped]
from equity_lake.storage.lake_reader import AdjustmentMethod  # type: ignore[import-untyped]


class TestA3CallSitesUseLakeReader:
    def test_backtest_signal_generator_uses_strict_delta_and_point_in_time_reader(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
        target_date = date(2024, 1, 5)
        lake_dir = tmp_path / "lake"
        price_table = "01_bronze/market_data/us_equity"
        prices = pl.DataFrame(
            {
                "market": [instrument.market] * 5,
                "mic": [instrument.mic] * 5,
                "symbol": [instrument.symbol] * 5,
                "ticker": [instrument.symbol] * 5,
                "date": [date(2024, 1, day) for day in range(1, 6)],
                "open": [100.0, 100.0, 100.0, 100.0, 103.0],
                "high": [101.0, 101.0, 101.0, 101.0, 104.0],
                "low": [99.0, 99.0, 99.0, 99.0, 102.0],
                "close": [100.0, 100.0, 100.0, 100.0, 103.0],
                "volume": [1000] * 5,
            }
        )
        assert write_delta(prices, price_table, lake_dir=lake_dir)

        source_time = datetime(2024, 1, 2, 14, 0, tzinfo=UTC)
        action_proof = ActionCoverageProof(
            market=instrument.market,
            mic=instrument.mic,
            symbol=instrument.symbol,
            source="synthetic-fixture",
            coverage_start=date(2024, 1, 1),
            coverage_end=target_date,
            source_available_at=source_time,
            source_valid_until=datetime(2024, 1, 6, tzinfo=UTC),
        )
        actions = pl.DataFrame(schema=CORPORATE_ACTION_SCHEMA)
        coverage = pl.DataFrame([action_proof.as_row()])
        assert write_delta(actions, "02_silver/corporate_actions/us_equity", lake_dir=lake_dir, partition_by=["ex_date"])
        assert write_delta(
            coverage,
            "02_silver/corporate_action_coverage/us_equity",
            lake_dir=lake_dir,
            partition_by=["coverage_end"],
        )

        captured: list[tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame | Sequence[ActionCoverageProof] | None]] = []
        adjust_prices = lake_reader.with_price_adjustment

        def observe_adjustment(
            source_prices: pl.DataFrame,
            source_actions: pl.DataFrame,
            *,
            coverage: pl.DataFrame | Sequence[ActionCoverageProof] | None = None,
            method: AdjustmentMethod = "split_only",
            as_of: date | None = None,
        ) -> pl.DataFrame:
            captured.append((source_prices, source_actions, coverage))
            return cast(pl.DataFrame, adjust_prices(source_prices, source_actions, coverage=coverage, method=method, as_of=as_of))

        monkeypatch.setattr("equity_lake.signals.generators.backtest.with_price_adjustment", observe_adjustment)

        def reject_raw_parquet(*_args: object, **_kwargs: object) -> None:
            pytest.fail("strict Delta backtest loading must not fall back to raw Parquet")

        monkeypatch.setattr(pl, "read_parquet", reject_raw_parquet)
        from equity_lake.signals.generators.backtest import BacktestSignalGenerator  # type: ignore[import-untyped]

        generator = BacktestSignalGenerator(
            {
                "lake_dir": lake_dir,
                "strategies": [{"name": "fixture", "lookback_days": 3, "buy_threshold": 0.01, "sell_threshold": -0.01}],
            }
        )
        outcome = generator.generate_outcome(instrument, target_date)

        assert outcome.status == "signal"
        assert outcome.signal is not None
        assert outcome.signal.action == "BUY"
        assert (outcome.signal.market, outcome.signal.mic) == (instrument.market, instrument.mic)
        assert len(captured) == 1
        loaded_prices, loaded_actions, loaded_coverage = captured[0]
        assert loaded_prices.select("market", "mic", "symbol").unique().rows() == [instrument.tuple]
        assert loaded_actions.is_empty()
        assert isinstance(loaded_coverage, pl.DataFrame)
        assert loaded_coverage.select("market", "mic", "symbol", "source").row(0) == (
            instrument.market,
            instrument.mic,
            instrument.symbol,
            "synthetic-fixture",
        )
        assert (lake_dir / price_table / "_delta_log").is_dir()
        assert (lake_dir / "02_silver/corporate_actions/us_equity/_delta_log").is_dir()
        assert (lake_dir / "02_silver/corporate_action_coverage/us_equity/_delta_log").is_dir()

    def test_sentiment_signal_generator_uses_lake_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Path] = []

        def fake_scan(p: Path) -> str:
            calls.append(p)
            return "read_parquet('/dev/null')"

        monkeypatch.setattr("equity_lake.signals.generators.sentiment.duckdb_scan_for", fake_scan)
        from equity_lake.signals.generators.sentiment import SentimentSignalGenerator  # type: ignore[import-untyped]

        SentimentSignalGenerator({})
        assert calls and calls[0] == US_NEWS_DIR
