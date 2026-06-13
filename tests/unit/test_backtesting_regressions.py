"""Regression tests for backtesting data/signal handling."""

from datetime import date

import polars as pl
import pytest

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine
from equity_lake.backtesting.strategy.base import BaseStrategy


class _PerTickerStrategy(BaseStrategy):
    def initialize(self, data: pl.DataFrame) -> None:
        self._data = data

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        aapl_rows = data.filter(pl.col("ticker") == "AAPL")
        first_date = aapl_rows["date"].min()
        last_date = aapl_rows["date"].max()

        return data.with_columns(
            pl.when((pl.col("ticker") == "AAPL") & ((pl.col("date") == first_date) | (pl.col("date") == last_date)))
            .then(1.0)
            .otherwise(0.0)
            .alias("weight")
        ).select("date", "ticker", "weight")

    def finalize(self) -> None:
        return None


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_backtest_engine_trades_only_signaled_ticker() -> None:
    data = pl.DataFrame(
        {
            "date": [date(2026, 6, 1), date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 2)],
            "ticker": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "open": [10.0, 50.0, 12.0, 55.0],
            "high": [10.5, 50.5, 12.5, 55.5],
            "low": [9.5, 49.5, 11.5, 54.5],
            "close": [10.0, 50.0, 12.0, 55.0],
            "volume": [100, 100, 100, 100],
        }
    )

    engine = VectorBacktestEngine(
        strategy=_PerTickerStrategy(),
        tickers=["AAPL", "MSFT"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        initial_cash=1_000,
        preloaded_data=data,
    )

    result = engine.run()

    traded_tickers = {trade["ticker"] for trade in result.trades}
    assert traded_tickers == {"AAPL"}


def test_backtest_data_loader_filters_unioned_markets(tmp_path, monkeypatch) -> None:
    us_dir = tmp_path / "us_equity"
    cn_dir = tmp_path / "cn_ashare"
    for market_dir in [us_dir, cn_dir]:
        partition = market_dir / "date=2026-06-01"
        partition.mkdir(parents=True)

    pl.DataFrame(
        {"ticker": ["AAPL"], "date": ["2026-06-01"], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
    ).write_parquet(us_dir / "date=2026-06-01" / "2026-06-01.parquet")
    pl.DataFrame(
        {"ticker": ["OTHER"], "date": ["2026-06-01"], "open": [2], "high": [2], "low": [2], "close": [2], "volume": [2]},
    ).write_parquet(cn_dir / "date=2026-06-01" / "2026-06-01.parquet")

    monkeypatch.setattr(
        BacktestDataLoader,
        "MARKET_DIRS",
        {"us": us_dir, "cn": cn_dir},
    )

    loader = BacktestDataLoader()
    data = loader.load(
        tickers=["AAPL"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        markets=["us", "cn"],
    )

    assert set(data["ticker"].to_list()) == {"AAPL"}
