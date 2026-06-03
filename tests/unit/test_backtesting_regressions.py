"""Regression tests for backtesting data/signal handling."""

from datetime import date

import pandas as pd

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import BacktestEngine
from equity_lake.backtesting.strategy.base import BaseStrategy


class _PerTickerStrategy(BaseStrategy):
    def initialize(self, data: pd.DataFrame) -> None:
        return None

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        index = data.index
        entries = pd.DataFrame(False, index=index, columns=["AAPL", "MSFT"])
        exits = pd.DataFrame(False, index=index, columns=["AAPL", "MSFT"])
        entries.loc[index[0], "AAPL"] = True
        exits.loc[index[-1], "AAPL"] = True
        return self.build_signal_frame(entries, exits)

    def finalize(self) -> None:
        return None


def test_backtest_engine_trades_only_signaled_ticker() -> None:
    """Per-ticker signals should not trigger portfolio-wide trades."""
    index = pd.to_datetime(["2026-06-01", "2026-06-02"])
    columns = pd.MultiIndex.from_tuples(
        [
            ("AAPL", "close"),
            ("AAPL", "volume"),
            ("MSFT", "close"),
            ("MSFT", "volume"),
        ],
        names=["ticker", "field"],
    )
    data = pd.DataFrame(
        [
            [10.0, 100, 50.0, 100],
            [12.0, 100, 55.0, 100],
        ],
        index=index,
        columns=columns,
    )

    engine = BacktestEngine(
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
    """Ticker/date filters must apply across every UNION branch."""
    us_dir = tmp_path / "us_equity"
    cn_dir = tmp_path / "cn_ashare"
    for market_dir in [us_dir, cn_dir]:
        partition = market_dir / "date=2026-06-01"
        partition.mkdir(parents=True)

    pd.DataFrame(
        [{"ticker": "AAPL", "date": "2026-06-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "adj_close": 1}],
    ).to_parquet(us_dir / "date=2026-06-01" / "2026-06-01.parquet", index=False)
    pd.DataFrame(
        [{"ticker": "OTHER", "date": "2026-06-01", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2, "adj_close": 2}],
    ).to_parquet(cn_dir / "date=2026-06-01" / "2026-06-01.parquet", index=False)

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
        wide_format=False,
    )

    assert set(data["ticker"]) == {"AAPL"}
